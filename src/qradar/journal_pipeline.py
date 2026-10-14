"""Journal collection, cross-source identity merging and publication reviews."""
from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from .analysis import AnalysisError
from .screening import screen_paper


def normalize_doi(value: str) -> str:
    value = unquote(str(value or "")).strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else ""


def title_key(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def _doi(paper: dict) -> str:
    return normalize_doi((paper.get("publication") or {}).get("doi") or paper.get("doi")
                         or (paper["id"][4:] if paper.get("id", "").startswith("doi:") else ""))


def same_paper(left: dict, right: dict) -> bool:
    ld, rd = _doi(left), _doi(right)
    if ld and rd:
        return ld == rd
    if left.get("id") == right.get("arxiv_id") or right.get("id") == left.get("arxiv_id"):
        return True
    a, b = title_key(left.get("title", "")), title_key(right.get("title", ""))
    if len(a) < 25 or a != b:
        return False
    def surnames(paper):
        names = paper.get("authors") or []
        return {title_key(name.split(",")[0] if "," in name else name.split()[-1])
                for name in names if isinstance(name, str) and name.strip()}
    left_names, right_names = surnames(left), surnames(right)
    return bool(left_names and right_names and left_names & right_names)


def merge_journal_collection(state: dict, profile: dict, collection: dict,
                             *, now: datetime | None = None) -> dict:
    result = copy.deepcopy(state)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    reviews = {x["id"]: x for x in result.get("publication_reviews_pending", [])}
    added = 0
    merged = 0
    for incoming in collection["papers"]:
        paper = copy.deepcopy(incoming)
        if (paper.get("publication", {}).get("stage") or paper.get("publication", {}).get("publication_stage")) == "accepted_manuscript":
            result["screened"][paper["id"]] = {"status": "awaiting_publication", "at": stamp}
            continue
        matches = [x for x in [*result["papers"], *result["pending"]] if same_paper(x, paper)]
        if len(matches) == 1:
            existing = matches[0]
            default = [] if existing["id"].startswith("doi:") else ["arxiv"]
            existing["sources"] = list(dict.fromkeys([*existing.get("sources", default), *paper.get("sources", [])]))
            existing["publication"] = copy.deepcopy(paper["publication"])
            existing["doi"] = paper["publication"]["doi"]
            if existing in result["papers"] and not existing.get("analysis", {}).get("publication_significance"):
                reviews[existing["id"]] = {"id": existing["id"], "doi": existing["doi"],
                    "first_seen_at": reviews.get(existing["id"], {}).get("first_seen_at", stamp)}
            if existing in result["pending"] and paper.get("abstract"):
                existing["abstract"] = paper["abstract"]
                for key in ("abstract_basis", "abstract_source_url", "authors", "metadata_warnings"):
                    if paper.get(key):
                        existing[key] = copy.deepcopy(paper[key])
                existing.update(screen_paper(existing, profile))
            merged += 1
            continue
        fingerprint = hashlib.sha256((paper.get("title", "") + "\n" + paper.get("abstract", "")).encode()).hexdigest()
        prior = result["screened"].get(paper["id"])
        if prior and (prior.get("status") not in {"keyword_rejected", "awaiting_publication"} or prior.get("metadata_fingerprint") == fingerprint):
            continue
        paper.update(screen_paper(paper, profile))
        if paper["keyword_score"] < profile["screening"].get("min_keyword_score", 3):
            result["screened"][paper["id"]] = {"status": "keyword_rejected", "score": paper["keyword_score"], "at": stamp, "metadata_fingerprint": fingerprint}
            continue
        paper["first_seen_at"] = stamp
        result["pending"].append(paper)
        added += 1
    result["publication_reviews_pending"] = list(reviews.values())
    result["journal_source_status"] = collection["source_status"]
    successes = [x for x in collection["source_status"].values() if x.get("status") in {"ok", "partial"}]
    if successes:
        result["last_journal_collection_run"] = stamp
        result["last_collection_run"] = stamp
    result["last_journal_run"] = {"fetched": len(collection["papers"]), "queued": added,
        "merged": merged, "publication_reviews_pending": len(reviews), "successful_sources": len(successes)}
    result["last_run"] = {**result.get("last_run", {}), "source": "journals", "journal_fetched": len(collection["papers"]),
                          "journal_queued": added, "pending": len(result["pending"])}
    return result


def valid_source_url(paper: dict, url: str) -> bool:
    try:
        u = urlsplit(url)
        if u.scheme != "https" or u.username or u.password or u.port not in {None, 443} or u.fragment:
            return False
        arxiv_id = paper.get("arxiv_id") or (paper["id"] if not paper["id"].startswith("doi:") else "")
        if arxiv_id and u.hostname in {"arxiv.org", "export.arxiv.org"} and not u.query:
            v = paper.get("arxiv_version", paper.get("version", 1))
            return u.path in {f"/abs/{arxiv_id}", f"/abs/{arxiv_id}v{v}", f"/html/{arxiv_id}v{v}", f"/pdf/{arxiv_id}v{v}"}
        pub = paper.get("publication") or {}
        doi = normalize_doi(pub.get("doi"))
        if not doi:
            return False
        if u.hostname == "doi.org" and not u.query:
            return normalize_doi(u.path.lstrip("/")) == doi
        if url == paper.get("abstract_source_url"):
            from .journals import JOURNALS
            jid = pub.get("journal_id")
            info = JOURNALS.get(jid, {})
            if url in info.get("feed_urls", []):
                return True
            if u.hostname == "api.crossref.org" and u.path == f"/journals/{info.get('issn')}/works":
                return True
        if u.hostname == "api.crossref.org" and not u.query:
            return u.path.startswith("/works/") and normalize_doi(u.path[7:]) == doi
        expected = urlsplit(pub.get("url", ""))
        if (u.hostname == expected.hostname == "www.nature.com" and not u.query
                and u.path in {expected.path + ".pdf", expected.path + "_reference.pdf"}):
            return True
        return (u.hostname in {"www.nature.com", "nature.com", "www.science.org", "science.org", "journals.aps.org", "link.aps.org"}
                and u.hostname == expected.hostname and u.path == expected.path and not u.query)
    except (TypeError, ValueError):
        return False


def apply_publication_reviews(state: dict, entries: list[dict], *, now: datetime) -> dict:
    result = copy.deepcopy(state)
    queued = {x["id"]: x for x in result.get("publication_reviews_pending", [])}
    papers = {x["id"]: x for x in result["papers"]}
    seen = set()
    for review in entries:
        if not isinstance(review, dict) or set(review) != {"id", "doi", "publication_significance", "evidence_level", "source_url", "analyzed_at", "model"}:
            raise AnalysisError("Publication review has missing or unexpected fields")
        pid = review["id"]
        if pid not in queued or pid not in papers or pid in seen:
            raise AnalysisError("Publication review must identify a unique queued published paper")
        seen.add(pid)
        paper = papers[pid]
        if normalize_doi(review["doi"]) != _doi(paper) or _doi(paper) != normalize_doi(queued[pid]["doi"]):
            raise AnalysisError("Publication review DOI does not match current publication")
        values = review["publication_significance"]
        if not isinstance(values, list) or not 1 <= len(values) <= 6 or any(not isinstance(x, str) or not x.strip() or len(x) > 12000 for x in values):
            raise AnalysisError("Publication review requires a non-empty significance list")
        if review["evidence_level"] not in {"abstract", "full_text_excerpt"} or not valid_source_url(paper, review["source_url"]):
            raise AnalysisError("Publication review source/evidence does not identify this paper")
        if not isinstance(review["model"], str) or not review["model"].strip():
            raise AnalysisError("Publication review must identify its analysis source")
        try:
            date = datetime.fromisoformat(review["analyzed_at"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise AnalysisError("Publication review needs timezone-aware analyzed_at") from exc
        paper["analysis"]["publication_significance"] = [x.strip() for x in values]
        paper["publication_review"] = {k: review[k] for k in ("doi", "evidence_level", "source_url", "analyzed_at", "model")}
        del queued[pid]
    result["publication_reviews_pending"] = list(queued.values())
    if entries:
        result["last_analysis_run"] = now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return result
