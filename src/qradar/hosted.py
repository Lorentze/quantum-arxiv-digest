"""Small, public hosted-analysis handoff and transactional inbox preparation.

The queue is an export, never production state. Submitted batches are validated
against fresh production state; receipts bind a filename to its exact bytes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import StateError, atomic_json, import_analyses, read_json


def _now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise StateError("Hosted handoff requires a timezone-aware time")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def analysis_health(state: dict, *, now: datetime | None = None) -> dict:
    current = _now(now)
    backlog = len(state.get("pending", [])) + len(state.get("publication_reviews_pending", []))
    value = state.get("last_analysis_run")
    age = None
    if value:
        try:
            last = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if last.tzinfo is None or last > current:
                raise ValueError
            age = (current - last).total_seconds() / 3600
        except (ValueError, TypeError, AttributeError) as exc:
            raise StateError("Invalid last_analysis_run timestamp") from exc
    stale = bool(backlog and (age is None or age > 48))
    return {"status": "stale" if stale else "ok", "stale": stale,
            "backlog": backlog, "age_hours": round(age, 2) if age is not None else None,
            "threshold_hours": 48}


def _priority(item: dict) -> tuple:
    paper = item["paper"]
    date = (paper.get("publication") or {}).get("published") or paper.get("published") or ""
    # Descending date and score; stable lexical id breaks exact ties.
    try:
        parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
        # Publishers commonly supply only a publication date. It still sorts
        # chronologically; midnight UTC is sufficient for day-level ordering.
        epoch = (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError, AttributeError):
        epoch = float("-inf")
    return (-epoch, -paper.get("keyword_score", 0), paper["id"], item["kind"])


def export_hosted_queue(state: dict, profile: dict, output_dir: str | Path, *,
                        now: datetime | None = None, max_chunk_bytes: int = 100 * 1024) -> dict:
    """Export exact queued metadata in bounded files with a balanced batch plan."""
    current = _now(now)
    if type(max_chunk_bytes) is not int or max_chunk_bytes < 1024:
        raise StateError("Hosted chunk limit must be an integer of at least 1024 bytes")
    records = [{"kind": "analysis", "paper": copy.deepcopy(p)} for p in state.get("pending", [])]
    papers = {p["id"]: p for p in state.get("papers", [])}
    for review in state.get("publication_reviews_pending", []):
        paper = papers.get(review["id"])
        if paper is None or (paper.get("publication") or {}).get("doi") != review.get("doi"):
            raise StateError("Queued publication review does not match the saved paper")
        records.append({"kind": "publication_review", "paper": copy.deepcopy(paper),
                        "review": copy.deepcopy(review)})
    records.sort(key=_priority)
    limit = profile["screening"].get("max_analyses_per_run", 30)
    if type(limit) is not int or limit < 1:
        raise StateError("Hosted batch limit must be a positive integer")
    journals = [r for r in records if r["kind"] == "publication_review" or r["paper"].get("publication")]
    reserved = journals[:min((limit + 2) // 3, len(journals))]
    chosen = {(r["kind"], r["paper"]["id"]) for r in reserved}
    recommended = reserved + [r for r in records if (r["kind"], r["paper"]["id"]) not in chosen][:limit - len(reserved)]
    chunks = []
    chunk = []
    for record in records:
        candidate = {"candidates": [*chunk, record]}
        if len(_bytes(candidate)) > max_chunk_bytes:
            if not chunk:
                raise StateError(f"Hosted queue item exceeds the chunk limit: {record['paper']['id']}")
            chunks.append({"candidates": chunk})
            chunk = [record]
            if len(_bytes({"candidates": chunk})) > max_chunk_bytes:
                raise StateError(f"Hosted queue item exceeds the chunk limit: {record['paper']['id']}")
        else:
            chunk.append(record)
    if chunk:
        chunks.append({"candidates": chunk})
    manifest = {"schema_version": 1, "generated_at": _stamp(current),
                "last_collection_run": state.get("last_collection_run"),
                "last_analysis_run": state.get("last_analysis_run"),
                "arxiv_source_status": copy.deepcopy(state.get("arxiv_source_status", {})),
                "journal_source_status": copy.deepcopy(state.get("journal_source_status", {})),
                "counts": {"published": len(state.get("papers", [])),
                           "pending": len(state.get("pending", [])),
                           "publication_reviews_pending": len(state.get("publication_reviews_pending", []))},
                "analysis_health": analysis_health(state, now=current), "chunks": [], "recommended_batch": []}
    record_paths = {}
    for i, value in enumerate(chunks, 1):
        name = f"queue-{i:04d}.json"
        manifest["chunks"].append({"path": name, "count": len(value["candidates"]), "bytes": len(_bytes(value))})
        for record in value["candidates"]:
            record_paths[(record["kind"], record["paper"]["id"])] = name
    for record in recommended:
        entry = {"kind": record["kind"], "id": record["paper"]["id"],
                 "chunk": record_paths[(record["kind"], record["paper"]["id"]) ]}
        entry["doi" if record["kind"] == "publication_review" else "version"] = (
            record["review"]["doi"] if record["kind"] == "publication_review" else record["paper"]["version"])
        manifest["recommended_batch"].append(entry)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for metadata, value in zip(manifest["chunks"], chunks):
        atomic_json(destination / metadata["path"], value)
    active = {x["path"] for x in manifest["chunks"]}
    for path in destination.glob("queue-*.json"):
        if path.name not in active and path.stem[6:].isdigit():
            path.unlink()
    atomic_json(destination / "manifest.json", manifest)
    return manifest


def prepare_inbox(state: dict, profile: dict, inbox_dir: str | Path,
                  receipts_dir: str | Path, *, now: datetime | None = None) -> tuple[dict, dict]:
    """Validate all unseen batches atomically; caller persists returned receipts.

    No production file or receipt is changed here. A receipt hash mismatch is a
    hard error, so changing an already processed filename cannot lose a batch.
    """
    current = _now(now)
    result = copy.deepcopy(state)
    receipts = {}
    for path in sorted(Path(inbox_dir).glob("*.json")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt_path = Path(receipts_dir) / path.name
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            if receipt.get("sha256") != digest:
                raise StateError(f"Processed inbox batch changed: {path.name}; use a new filename")
            continue
        batch = read_json(path)
        if not isinstance(batch, dict) or set(batch) - {"analyses", "publication_reviews"}:
            raise StateError(f"Invalid inbox batch: {path.name}")
        analyses, reviews = batch.get("analyses", []), batch.get("publication_reviews", [])
        if not isinstance(analyses, list) or not isinstance(reviews, list) or not (analyses or reviews):
            raise StateError(f"Empty or invalid inbox batch: {path.name}")
        pending = {p["id"]: p for p in result["pending"]}
        published = {p["id"]: p for p in result["papers"]}
        active = []
        skipped = []
        seen = set()
        for entry in analyses:
            pid = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(pid, str) or pid in seen:
                raise StateError("Every inbox analysis needs a unique paper id")
            seen.add(pid)
            if pid in pending:
                active.append(entry)
            elif pid in published and type(entry.get("version")) is int and entry["version"] == published[pid].get("version"):
                # Validate even a stale entry; malformed payloads must not be
                # hidden by filtering. Import into a disposable queue only.
                validation = copy.deepcopy(result)
                validation["papers"] = [p for p in validation["papers"] if p["id"] != pid]
                validation["pending"].append(copy.deepcopy(published[pid]))
                import_analyses(validation, profile, {"analyses": [entry]}, now=current)
                skipped.append(pid)
            else:
                raise StateError(f"Inbox analysis is unknown or has a changed version: {pid}")
        published_before = len(result["papers"])
        if active or reviews:
            result = import_analyses(result, profile, {"analyses": active, "publication_reviews": reviews}, now=current)
        selected = len(result["papers"]) - published_before
        receipts[path.name] = {"schema_version": 1, "batch": path.name, "sha256": digest,
                              "processed_at": _stamp(current), "analyzed": len(active),
                              "selected": selected, "rejected": len(active) - selected,
                              "publication_reviews": len(reviews), "skipped_already_analyzed": skipped}
    return result, receipts
