"""Portable static reader and RSS feeds. No templates, CDNs, or runtime server."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET


_ASSETS = Path(__file__).with_name("assets")
_ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", _ATOM)
_TEXT_FIELDS = ("summary", "problem", "setting", "evidence_level", "source_url", "model", "analyzed_at", "status")
_LIST_FIELDS = ("main_results", "techniques", "assumptions", "limitations", "relevance", "follow_up")


def _text(value) -> str:
    # XML 1.0 disallows most control characters, even when HTML-escaped.
    return "".join(c for c in str(value or "") if c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)


def _strings(value) -> list[str]:
    return [_text(v) for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme == "https" and parts.hostname and parts.username is None and parts.password is None:
            return value
    except ValueError:
        pass
    return ""


def _site_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        local_http = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if not (parts.scheme == "https" or local_http) or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("site_url must be an absolute HTTPS URL (HTTP is permitted for localhost), without credentials, query, or fragment") from None
    return value.rstrip("/") + "/"


def _slug(topic: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", topic):
        return topic
    return "topic-" + hashlib.sha256(topic.encode("utf-8")).hexdigest()[:16]


def _date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def _public_paper(paper: dict) -> dict:
    result = {key: _text(paper.get(key)) for key in ("id", "title", "abstract", "published", "updated", "selected_at", "date_basis")}
    result["version"] = paper.get("version", 1) if type(paper.get("version", 1)) is int else 1
    result.update({key: _strings(paper.get(key)) for key in ("authors", "categories", "topics")})
    result.update({key: _safe_url(_text(paper.get(key))) for key in ("url", "pdf_url")})
    source = paper.get("analysis") or {}
    analysis = {key: _text(source.get(key)) for key in _TEXT_FIELDS}
    analysis.update({key: _strings(source.get(key)) for key in _LIST_FIELDS})
    score = source.get("relevance_score", 0)
    analysis["relevance_score"] = max(0, min(100, score)) if type(score) in (int, float) and score == score and abs(score) != float("inf") else 0
    analysis["source_url"] = _safe_url(analysis["source_url"])
    result["analysis"] = analysis
    return result


def _is_analyzed(paper: dict) -> bool:
    analysis = paper.get("analysis")
    return (isinstance(analysis, dict)
            and isinstance(analysis.get("summary"), str)
            and bool(analysis["summary"].strip())
            and analysis.get("status") not in {"pending", "queued", "unanalyzed", "unanalysed"})


def _description(paper: dict, demo: bool) -> str:
    """The XML text contains safe HTML for conventional RSS readers."""
    analysis = paper["analysis"]
    esc = html.escape
    chunks = []
    if demo:
        chunks.append("<p><strong>真实论文阅读预览 · 非自动更新。这些技术摘要依据真实论文来源整理，不是每日自动筛选结果。</strong></p>")
    scope = "全文节选" if analysis.get("evidence_level") == "full_text_excerpt" else "论文摘要" if analysis.get("evidence_level") == "abstract" else "未注明"
    chunks.append(f"<p>分析依据：{scope}。技术摘要概述作者的主张，不代表证明已被独立核验。</p>")
    if analysis.get("summary"):
        chunks.append(f"<p>{esc(analysis['summary'])}</p>")
    for key, label in (("problem", "研究问题"), ("setting", "任务与访问模型")):
        if analysis.get(key):
            chunks.append(f"<h3>{label}</h3><p>{esc(analysis[key])}</p>")
    for key, label in (("main_results", "主要结果"), ("techniques", "方法"), ("assumptions", "必要假设"), ("limitations", "局限与未确认细节"), ("relevance", "与你研究的联系"), ("follow_up", "后续问题 · 读者建议")):
        if analysis.get(key):
            chunks.append(f"<h3>{label}</h3><ul>" + "".join(f"<li>{esc(v)}</li>" for v in analysis[key]) + "</ul>")
    if paper.get("abstract"):
        chunks.append(f"<details><summary>原文 abstract</summary><p>{esc(paper['abstract'])}</p></details>")
    authors = ", ".join(paper["authors"])
    chunks.append(f"<p>{esc(authors)} · arXiv:{esc(paper['id'])}v{paper['version']}</p>")
    date_label = "arXiv 公告日期" if "announcement" in paper.get("date_basis", "") else "arXiv 首次提交日期"
    chunks.append(f"<p>{date_label}：{esc(paper['published'])}；版本更新时间：{esc(paper['updated'])}</p>")
    if paper["url"]:
        chunks.append(f'<p><a href="{esc(paper["url"], quote=True)}">arXiv 原文</a></p>')
    if analysis.get("model"):
        chunks.append(f"<p>分析模型：{esc(analysis['model'])}；分析时间：{esc(analysis.get('analyzed_at', ''))}</p>")
    return "\n".join(chunks)


def _write_feed(path: Path, papers: list[dict], data: dict, base: str, feed_path: str, label: str = "") -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    title = "Quantum Paper Radar · 量子论文雷达" + (f" · {label}" if label else "")
    if data["is_demo"]:
        title += " [阅读预览 · 非自动更新]"
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = base
    ET.SubElement(channel, "description").text = _text(data["profile_title"]) + "：问题、假设、主要结果与研究联系的技术阅读摘要。"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "ttl").text = "1440"
    ET.SubElement(channel, f"{{{_ATOM}}}link", href=base + feed_path, rel="self", type="application/rss+xml")
    # A collection-only run does not publish new analysis. Legacy states set
    # last_analysis_run from last_successful_run in build_site below.
    last_date = _date(data.get("last_analysis_run"))
    if last_date:
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(last_date, usegmt=True)
    for paper in papers:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = paper["title"]
        ET.SubElement(item, "link").text = paper["url"] or base + "#paper-" + quote(paper["id"], safe="")
        ET.SubElement(item, "guid", isPermaLink="false").text = "urn:arxiv:" + paper["id"]
        # RSS publication is the first digest selection, not the original arXiv
        # submission: a catch-up run may add an old paper to the feed today.
        date = _date(paper.get("selected_at")) or _date(paper["analysis"].get("analyzed_at")) or _date(paper["published"])
        if date:
            ET.SubElement(item, "pubDate").text = format_datetime(date, usegmt=True)
        for topic in paper["topics"]:
            ET.SubElement(item, "category").text = data["topic_labels"].get(topic, topic)
        ET.SubElement(item, "description").text = _description(paper, data["is_demo"])
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_site(state: dict, output: Path | str, site_url: str) -> None:
    """Write static HTML, local assets, public JSON and stable-id RSS.

    Topics are profile IDs. Safe ASCII IDs retain their filenames; other names
    use deterministic hashes. ``papers.json.topic_feeds`` is authoritative.
    Arbitrary operational state (including provider settings) is not published.
    """
    base = _site_url(site_url)
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    papers = [_public_paper(p) for p in state.get("papers", []) if _is_analyzed(p)]
    papers.sort(key=lambda p: (p.get("published", ""), p["id"]), reverse=True)
    topics = sorted({topic for paper in papers for topic in paper["topics"]})
    labels = state.get("topic_labels") or {}
    split_runs = "last_collection_run" in state or "last_analysis_run" in state
    collection_time = state.get("last_collection_run", state.get("last_successful_run"))
    analysis_time = state.get("last_analysis_run") if split_runs else state.get("last_successful_run")
    data = {
        "papers": papers,
        "profile_title": _text(state.get("profile_title") or "量子信息理论 · Guoding Liu"),
        "is_demo": bool(state.get("is_demo")),
        "last_successful_run": _text(state.get("last_successful_run")) or None,
        "last_collection_run": _text(collection_time) or None,
        "last_analysis_run": _text(analysis_time) or None,
        "last_run": {key: value for key in ("fetched", "keyword_candidates", "analyzed", "selected", "pending") if type(value := (state.get("last_run") or {}).get(key)) is int},
        "site_url": base,
        "topic_labels": {topic: _text(labels.get(topic, topic)) for topic in topics},
        "topic_feeds": {topic: "feeds/" + _slug(topic) + ".xml" for topic in topics},
    }
    (destination / "papers.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    index = (_ASSETS / "index.html").read_text(encoding="utf-8")
    index = index.replace("{{SITE_URL}}", html.escape(base, quote=True))
    index = index.replace("{{PROFILE_TITLE}}", html.escape(data["profile_title"]))
    (destination / "index.html").write_text(index, encoding="utf-8")
    for asset in ("app.js", "style.css"):
        shutil.copyfile(_ASSETS / asset, destination / asset)
    _write_feed(destination / "feed.xml", papers, data, base, "feed.xml")
    feed_dir = destination / "feeds"
    feed_dir.mkdir(exist_ok=True)
    # Remove old topic files after profile changes so obsolete feeds do not keep
    # silently serving an archived selection; unrelated files are untouched.
    active = {Path(path).name for path in data["topic_feeds"].values()}
    for path in feed_dir.glob("*.xml"):
        if path.name not in active:
            path.unlink()
    for topic, relative_path in data["topic_feeds"].items():
        _write_feed(destination / relative_path, [p for p in papers if topic in p["topics"]], data, base, relative_path, data["topic_labels"][topic])
    # An additional self-contained artifact opens directly from disk. Embed
    # escaped JSON rather than executing paper text; app.js still uses only DOM
    # text nodes. The hosted version retains its restrictive external-file CSP.
    snapshot = re.sub(r'  <meta http-equiv="Content-Security-Policy"[^>]*>\n', "", index)
    snapshot = snapshot.replace('  <link rel="stylesheet" href="./style.css">', "  <style>" + (_ASSETS / "style.css").read_text(encoding="utf-8") + "</style>")
    embedded_data = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    snapshot = snapshot.replace('  <script src="./app.js" defer></script>', "  <script>window.QRADAR_DATA=" + embedded_data + ";</script>")
    snapshot = snapshot.replace("</body>", "  <script>" + (_ASSETS / "app.js").read_text(encoding="utf-8") + "</script>\n</body>")
    (destination / "snapshot.html").write_text(snapshot, encoding="utf-8")
