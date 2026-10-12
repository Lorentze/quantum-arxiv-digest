"""Incremental ingestion with explicit queues and transactional state writes."""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .analysis import AnalysisError, ConfigurationError, _validate_analysis, analyze_paper
from .screening import screen_paper
from .sources import SourceError, fetch_recent, fetch_scirate, get_paper_text


class StateError(ValueError):
    pass


def read_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise StateError(f"{path} must contain a JSON object")
    return value


def atomic_json(path: str | Path, value: dict | list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            name = file.name
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def validate_profile(profile: dict) -> None:
    for key in ("source", "screening", "ai"):
        if not isinstance(profile.get(key), dict):
            raise ConfigurationError(f"Profile {key} must be an object")
    source = profile["source"]
    if not isinstance(source.get("categories"), list) or not source["categories"]:
        raise ConfigurationError("Provide source.categories")
    scirate = source.get("scirate", {"enabled": True, "range_days": 3, "min_votes_exclusive": 10})
    if (not isinstance(scirate, dict) or type(scirate.get("enabled", True)) is not bool
            or type(scirate.get("range_days", 3)) is not int or scirate.get("range_days", 3) != 3
            or type(scirate.get("min_votes_exclusive", 10)) is not int or scirate.get("min_votes_exclusive", 10) != 10):
        raise ConfigurationError("source.scirate must specify enabled true/false, range_days 3, and min_votes_exclusive 10")
    for obj, key, default, maximum in (
        (source, "lookback_days", 7, 3650),
        (source, "max_results", 2500, 30000),
        (source, "max_catchup_days", 30, 3650),
        (profile["screening"], "max_analyses_per_run", 30, 500),
    ):
        value = obj.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ConfigurationError(f"{key} must be an integer between 1 and {maximum}")
    for key, default, maximum in (("min_keyword_score", 3, 10000), ("min_ai_score", 70, 100)):
        value = profile["screening"].get(key, default)
        if type(value) is not int or not 0 <= value <= maximum:
            raise ConfigurationError(f"{key} must be an integer between 0 and {maximum}")
    if type(profile["screening"].get("prefetch_fulltext", True)) is not bool:
        raise ConfigurationError("prefetch_fulltext must be true or false")
    topics = profile.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ConfigurationError("Profile must include research topics")
    ids = set()
    import re
    for topic in topics:
        if not isinstance(topic, dict) or not re.fullmatch(r"[a-z][a-z0-9_]*", topic.get("id", "")):
            raise ConfigurationError("Topic id must use lower-case letters, digits and underscores")
        if topic["id"] in ids:
            raise ConfigurationError("Topic ids must be unique")
        ids.add(topic["id"])
        words = topic.get("keywords")
        if not isinstance(words, list) or not words or any(not isinstance(w, str) or not w.strip() for w in words):
            raise ConfigurationError("Topic keywords must be non-empty strings")
        if type(topic.get("weight", 3)) is not int or not 1 <= topic.get("weight", 3) <= 100:
            raise ConfigurationError("Topic weight must be a positive integer at most 100")


def empty_state(profile: dict) -> dict:
    return {"schema_version": 1, "profile_title": profile.get("title", "Quantum Paper Radar"),
            "papers": [], "pending": [], "screened": {}, "last_successful_run": None,
            "last_run": {}, "is_demo": False}


def load_state(path: str | Path, profile: dict) -> dict:
    state = read_json(path) if Path(path).exists() else empty_state(profile)
    if state.get("schema_version") != 1:
        raise StateError("Unsupported state schema_version")
    if state.get("is_demo"):
        raise StateError("A demo archive cannot be used as production state")
    for key in ("papers", "pending"):
        if not isinstance(state.get(key), list):
            raise StateError(f"State {key} must be an array")
        ids = [paper.get("id") for paper in state[key] if isinstance(paper, dict)]
        if len(ids) != len(state[key]) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise StateError(f"State {key} has invalid or duplicate paper ids")
    if not isinstance(state.get("screened"), dict):
        raise StateError("State screened must be an object")
    if {p["id"] for p in state["papers"]} & {p["id"] for p in state["pending"]}:
        raise StateError("State papers and pending contain overlapping ids")
    return state


def lookback_for(state: dict, profile: dict, now: datetime, override: int | None = None) -> int:
    if override is not None:
        if type(override) is not int or override < 1:
            raise ConfigurationError("--lookback-days must be a positive integer")
        return override
    days = profile["source"].get("lookback_days", 7)
    last = state.get("last_arxiv_collection_run", state.get("last_successful_run"))
    if last:
        try:
            date = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if date.tzinfo is None or date > now:
                raise ValueError("timezone missing or future timestamp")
        except (TypeError, ValueError) as exc:
            raise StateError("Invalid last_successful_run timestamp") from exc
        # Two-day overlap covers announcement lag at the catch-up boundary.
        days = max(days, math.ceil((now - date).total_seconds() / 86400) + 2)
    if days > profile["source"].get("max_catchup_days", 30):
        raise SourceError("Catch-up exceeds max_catchup_days; explicitly increase that limit or use --lookback-days to choose the interval. No records were dropped.")
    return days


def is_scirate_candidate(paper: dict) -> bool:
    """SciRate is a separate public-interest selection route, not a topic score."""
    nomination = paper.get("scirate")
    return (isinstance(nomination, dict) and type(nomination.get("votes")) is int
            and nomination["votes"] > 10 and nomination.get("range_days") == 3)


def _source_annotations(paper: dict, incoming: dict, source: str) -> None:
    existing_sources = paper.get("sources", []) or ["arxiv"]
    paper["sources"] = list(dict.fromkeys([*existing_sources, source]))
    if source == "scirate":
        paper["scirate"] = copy.deepcopy(incoming["scirate"])


def make_update(state: dict, profile: dict, *, lookback_days: int | None = None,
                now: datetime | None = None, analyze_candidates: bool = True,
                source: str = "arxiv") -> dict:
    """Return new state after all attempted analyses succeed; no writes on failure.

    Unanalyzed candidates remain in pending, including those outside a future
    fetch window. Cross-listings and daily overlap deduplicate on base arXiv ID.
    A paper is analyzed once; replacement-only updates are not monitored.
    """
    validate_profile(profile)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    timestamp = current.isoformat(timespec="seconds").replace("+00:00", "Z")
    if source not in {"arxiv", "scirate"}:
        raise ConfigurationError("source must be arxiv or scirate")
    if source == "scirate":
        if not profile["source"].get("scirate", {}).get("enabled", True):
            raise ConfigurationError("SciRate collection is disabled in this profile")
        if lookback_days is not None:
            raise ConfigurationError("SciRate uses the fixed three-day source window; omit --lookback-days")
        days = 3
        fetched = fetch_scirate(profile["source"].get("max_results", 2500), now=current,
                                known_papers=[*state["papers"], *state["pending"]])
    else:
        days = lookback_for(state, profile, current, lookback_days)
        fetched = fetch_recent(profile["source"]["categories"], days,
                               profile["source"].get("max_results", 2500), now=current)
    result = copy.deepcopy(state)
    result["profile_title"] = profile.get("title", "Quantum Paper Radar")
    result["topic_labels"] = {topic["id"]: topic.get("label", topic["id"]) for topic in profile["topics"]}
    selected_papers = {paper["id"]: paper for paper in result["papers"]}
    pending = {paper["id"]: paper for paper in result["pending"]}
    screened = result["screened"]
    minimum = profile["screening"].get("min_keyword_score", 3)
    candidates = 0
    for raw in fetched:
        paper = copy.deepcopy(raw)
        paper_id = paper["id"]
        if source == "scirate" and not is_scirate_candidate(paper):
            raise SourceError("SciRate returned a nomination that does not exceed ten scites")
        if paper_id in selected_papers:
            _source_annotations(selected_papers[paper_id], paper, source)
            continue
        if paper_id in pending:
            # Preserve the queued metadata/version while attaching the second
            # source. A duplicate cannot invalidate an in-progress analysis.
            _source_annotations(pending[paper_id], paper, source)
            continue
        prior = screened.get(paper_id, {})
        if paper_id in screened and (source == "arxiv" or prior.get("scirate_reviewed_at")):
            continue
        paper.update(screen_paper(paper, profile))
        _source_annotations(paper, paper, source)
        if source == "arxiv" and paper["keyword_score"] < minimum:
            # Do not retain unselected abstracts in public state. This small
            # decision ledger prevents repeated paid analysis after overlap.
            screened[paper_id] = {"status": "keyword_rejected", "score": paper["keyword_score"], "at": timestamp}
            continue
        candidates += 1
        if source == "scirate":
            paper["selection_reason"] = "scirate_high_votes"
        paper["first_seen_at"] = pending.get(paper_id, {}).get("first_seen_at", timestamp)
        pending[paper_id] = paper
    # FIFO by first-seen day, ranked within that day's batch. The finite cap is
    # a per-run budget, not a deletion policy. Backlog size appears on the site.
    ordered = sorted(pending.values(), key=lambda p: (p.get("first_seen_at", ""), -p.get("keyword_score", 0), p["id"]))
    if source == "scirate":
        ordered = [paper for paper in ordered if is_scirate_candidate(paper)]
    batch = ordered[:profile["screening"].get("max_analyses_per_run", 30)] if analyze_candidates else []
    count_selected = 0
    for paper in batch:
        if profile["screening"].get("prefetch_fulltext", True):
            reading_source = get_paper_text(paper, max_chars=profile["ai"].get("max_source_chars", 60000))
        else:
            reading_source = {"text": paper["abstract"], "evidence_level": "abstract", "source_url": paper["url"],
                      "warnings": ["Only the supplied abstract was reviewed; proofs and detailed assumptions were not checked."]}
        analysis = analyze_paper(paper, profile, reading_source, profile["ai"])
        if is_scirate_candidate(paper) or analysis["relevance_score"] >= profile["screening"].get("min_ai_score", 70):
            paper["analysis"] = analysis
            paper["selected_at"] = timestamp
            if is_scirate_candidate(paper):
                paper["selection_reason"] = "scirate_high_votes"
            result["papers"].append(paper)
            count_selected += 1
        else:
            screened[paper["id"]] = {"status": "ai_rejected", "score": analysis["relevance_score"], "at": timestamp}
        if is_scirate_candidate(paper):
            screened[paper["id"]] = {"status": "selected", "source": "scirate", "scirate_reviewed_at": timestamp}
        del pending[paper["id"]]
    result["pending"] = list(pending.values())
    result["papers"].sort(key=lambda p: (p.get("published", ""), p["id"]), reverse=True)
    if source == "arxiv":
        result["last_successful_run"] = timestamp
        result["last_arxiv_collection_run"] = timestamp
    else:
        result["last_scirate_collection_run"] = timestamp
    result["last_collection_run"] = timestamp
    if batch:
        result["last_analysis_run"] = timestamp
    result["last_run"] = {"fetched": len(fetched), "keyword_candidates": candidates,
                          "analyzed": len(batch), "selected": count_selected, "pending": len(pending),
                          "lookback_days": days, "source": source,
                          "date_basis": "scirate_announcement_window" if source == "scirate" else "initial_submission"}
    result["is_demo"] = False
    return result


def collect_update(profile_path: str | Path, state_path: str | Path, output: str | Path,
                   site_url: str, *, lookback_days: int | None = None, source: str = "arxiv") -> dict:
    """Collect and queue candidates without a model, token, or synthetic summary."""
    from .render import build_site
    profile = read_json(profile_path)
    validate_profile(profile)
    state = load_state(state_path, profile)
    result = make_update(state, profile, lookback_days=lookback_days, analyze_candidates=False, source=source)
    build_site(result, output, site_url)
    atomic_json(state_path, result)
    return result


def import_analyses(state: dict, profile: dict, batch: dict, *, now: datetime | None = None) -> dict:
    """Validate externally generated summaries before committing any of them.

    This path does not call a model. Source metadata must already be in pending;
    a batch cannot invent paper identities or silently reanalyze archive entries.
    """
    validate_profile(profile)
    entries = batch.get("analyses") if isinstance(batch, dict) else None
    if not isinstance(entries, list) or not entries:
        raise StateError("Analysis batch must contain a non-empty analyses array")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    timestamp = current.isoformat(timespec="seconds").replace("+00:00", "Z")
    result = copy.deepcopy(state)
    pending = {paper["id"]: paper for paper in result["pending"]}
    seen = set()
    selected = 0
    text_fields = ("summary", "problem", "setting")
    list_fields = ("main_results", "techniques", "assumptions", "limitations", "relevance", "follow_up")
    core_fields = text_fields + list_fields + ("relevance_score",)
    allowed = set(core_fields) | {"evidence_level", "source_url", "model", "analyzed_at", "status", "token_usage"}
    from urllib.parse import urlsplit
    for entry in entries:
        paper_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(paper_id, str) or paper_id not in pending or paper_id in seen:
            raise StateError("Every analysis must have a unique id currently in pending")
        seen.add(paper_id)
        paper = pending[paper_id]
        if type(entry.get("version")) is not int or entry["version"] != paper["version"]:
            raise StateError(f"Analysis version does not match queued paper {paper_id}")
        value = entry.get("analysis")
        if not isinstance(value, dict) or set(value) - allowed:
            raise AnalysisError("Imported analysis contains unexpected fields")
        analysis = _validate_analysis({key: copy.deepcopy(value[key]) for key in core_fields if key in value})
        if value.get("status") != "analyzed" or value.get("evidence_level") not in {"abstract", "full_text_excerpt"}:
            raise AnalysisError("Imported analysis must be analyzed with an explicit evidence level")
        if not isinstance(value.get("model"), str) or not value["model"].strip():
            raise AnalysisError("Imported analysis must identify the actual analysis source/model")
        try:
            date = datetime.fromisoformat(value.get("analyzed_at", "").replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError("timezone missing")
        except (ValueError, AttributeError, TypeError) as exc:
            raise AnalysisError("Imported analyzed_at must be a timezone-aware ISO timestamp") from exc
        url = value.get("source_url", "")
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.hostname not in {"arxiv.org", "export.arxiv.org"}
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in {f"/abs/{paper_id}", f"/abs/{paper_id}v{paper['version']}",
                                           f"/html/{paper_id}v{paper['version']}", f"/pdf/{paper_id}v{paper['version']}"}):
                raise ValueError("source mismatch")
        except (ValueError, TypeError) as exc:
            raise AnalysisError("Imported source_url must identify this queued paper on official arXiv") from exc
        analysis.update({"status": "analyzed", "evidence_level": value["evidence_level"],
                         "source_url": url, "model": value["model"].strip(),
                         "analyzed_at": date.astimezone(timezone.utc).isoformat(timespec="seconds")})
        if analysis["evidence_level"] == "abstract":
            note = "本条分析仅依据论文摘要，未核查正文证明和摘要中未说明的假设。"
            if note not in analysis["limitations"]:
                analysis["limitations"].append(note)
        if is_scirate_candidate(paper) or analysis["relevance_score"] >= profile["screening"].get("min_ai_score", 70):
            paper["analysis"] = analysis
            paper["selected_at"] = timestamp
            result["papers"].append(paper)
            selected += 1
        else:
            result["screened"][paper_id] = {"status": "ai_rejected", "score": analysis["relevance_score"], "at": timestamp}
        if is_scirate_candidate(paper):
            paper["selection_reason"] = "scirate_high_votes"
            result["screened"][paper_id] = {"status": "selected", "source": "scirate", "scirate_reviewed_at": timestamp}
        del pending[paper_id]
    result["pending"] = list(pending.values())
    result["papers"].sort(key=lambda p: (p.get("published", ""), p["id"]), reverse=True)
    result["last_analysis_run"] = timestamp
    result["last_analysis_batch"] = {"analyzed": len(entries), "selected": selected, "pending": len(pending)}
    result["last_run"] = {**result.get("last_run", {}), **result["last_analysis_batch"]}
    result["topic_labels"] = {topic["id"]: topic.get("label", topic["id"]) for topic in profile["topics"]}
    return result


def import_analysis_update(batch_path: str | Path, profile_path: str | Path, state_path: str | Path,
                           output: str | Path, site_url: str) -> dict:
    from .render import build_site
    profile = read_json(profile_path)
    state = load_state(state_path, profile)
    result = import_analyses(state, profile, read_json(batch_path))
    build_site(result, output, site_url)
    atomic_json(state_path, result)
    return result


def run_update(profile_path: str | Path, state_path: str | Path, output: str | Path,
               site_url: str, *, lookback_days: int | None = None, source: str = "arxiv") -> dict:
    # Fail before any fetch or state mutation when API access is unavailable.
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ConfigurationError("OPENAI_API_KEY is required for live updates; use qradar demo for the offline preview.")
    from .render import build_site
    profile = read_json(profile_path)
    validate_profile(profile)
    state = load_state(state_path, profile)
    result = make_update(state, profile, lookback_days=lookback_days, source=source)
    # Rendering must succeed before state is committed. GitHub only uploads
    # successful artifacts, so a failed run preserves the public site.
    build_site(result, output, site_url)
    atomic_json(state_path, result)
    return result
