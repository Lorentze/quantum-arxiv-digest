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
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET


_ASSETS = Path(__file__).with_name("assets")
_ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", _ATOM)
_TEXT_FIELDS = ("summary", "problem", "setting", "evidence_level", "source_url", "model", "analyzed_at", "status")
_LIST_FIELDS = ("main_results", "techniques", "assumptions", "limitations", "relevance", "follow_up")
_TITLE = "Quantum arXiv Digest"


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


def _article_path(paper_id: str) -> str:
    """Modern arXiv IDs remain readable; arbitrary/legacy IDs cannot escape."""
    filename = paper_id if re.fullmatch(r"[0-9]{4}\.[0-9]{4,5}", paper_id) else "paper-" + hashlib.sha256(paper_id.encode("utf-8")).hexdigest()[:16]
    return "articles/" + filename + ".html"


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
    result["article_path"] = _article_path(result["id"])
    result["sources"] = [name for name in _strings(paper.get("sources")) if name in {"arxiv", "scirate"}]
    nomination = paper.get("scirate")
    if isinstance(nomination, dict) and type(nomination.get("votes")) is int and nomination["votes"] > 10:
        result["scirate"] = {"votes": nomination["votes"]}
        for key in ("observed_at", "window_start", "window_end"):
            result["scirate"][key] = _text(nomination.get(key))
        if nomination.get("range_days") == 3:
            result["scirate"]["range_days"] = 3
        nomination_url = _safe_url(_text(nomination.get("url")))
        if nomination_url and urlsplit(nomination_url).hostname == "scirate.com":
            result["scirate"]["url"] = nomination_url
    return result


def _is_analyzed(paper: dict) -> bool:
    analysis = paper.get("analysis")
    return (isinstance(analysis, dict)
            and isinstance(analysis.get("summary"), str)
            and bool(analysis["summary"].strip())
            and analysis.get("status") not in {"pending", "queued", "unanalyzed", "unanalysed"})


def _description(paper: dict, demo: bool, base: str) -> str:
    """The outer reading layer contains the original English abstract."""
    esc = html.escape
    chunks = []
    if demo:
        chunks.append("<p><strong>Reading preview; not an automatically updated digest.</strong></p>")
    if paper.get("abstract"):
        chunks.append(f'<p lang="en">{esc(paper["abstract"])}</p>')
    else:
        chunks.append("<p>The original abstract is not available in this entry.</p>")
    authors = ", ".join(paper["authors"])
    chunks.append(f"<p>{esc(authors)} · arXiv:{esc(paper['id'])}v{paper['version']}</p>")
    date_label = "arXiv announcement" if "announcement" in paper.get("date_basis", "") else "First submitted"
    chunks.append(f"<p>{date_label}: {esc(paper['published'])}</p>")
    if paper["url"]:
        chunks.append(f'<p><a href="{esc(paper["url"], quote=True)}">arXiv</a></p>')
    chunks.append(f'<p><a href="{esc(base + paper["article_path"], quote=True)}">Read the Chinese AI report</a></p>')
    return "\n".join(chunks)


def _report_sections(paper: dict) -> str:
    """AI output is always text, including markup-looking mathematical text."""
    esc = html.escape
    analysis = paper["analysis"]
    sections = []
    for key, label in (("summary", "这篇论文做了什么"), ("problem", "研究问题与动机"),
                       ("setting", "模型、任务与评价指标"), ("main_results", "主要贡献与结果"),
                       ("techniques", "核心技术路线"), ("assumptions", "结论成立的条件"),
                       ("limitations", "局限与证据边界"), ("relevance", "相关研究与意义"),
                       ("follow_up", "进一步阅读与开放问题")):
        value = analysis.get(key)
        if not value:
            continue
        if isinstance(value, list):
            content = "<ul>" + "".join("<li>" + esc(item) + "</li>" for item in value) + "</ul>"
        else:
            content = "".join("<p>" + esc(paragraph) + "</p>" for paragraph in re.split(r"\n\s*\n", value) if paragraph.strip())
        sections.append(f'<section class="report-section" aria-labelledby="{key}"><h2 id="{key}">{label}</h2>{content}</section>')
    return "\n".join(sections)


def _article_html(paper: dict, base: str, demo: bool) -> str:
    esc = html.escape
    analysis = paper["analysis"]
    scope = "论文正文节选" if analysis.get("evidence_level") == "full_text_excerpt" else "论文摘要" if analysis.get("evidence_level") == "abstract" else "已标注的论文来源"
    links = ['<a href="../">全部文章</a>', '<a href="../feed.xml">RSS</a>']
    if paper["url"]:
        links.append(f'<a href="{esc(paper["url"], quote=True)}" target="_blank" rel="noopener noreferrer">arXiv ↗</a>')
    if paper["pdf_url"]:
        links.append(f'<a href="{esc(paper["pdf_url"], quote=True)}" target="_blank" rel="noopener noreferrer">PDF ↗</a>')
    date = (_date(paper["published"]).date().isoformat() if _date(paper["published"]) else paper["published"])
    provenance = [f"arXiv:{esc(paper['id'])}v{paper['version']}", "依据" + scope]
    if analysis.get("analyzed_at"):
        provenance.append("分析时间 " + esc(analysis["analyzed_at"]))
    if analysis.get("model"):
        provenance.append("分析来源 " + esc(analysis["model"]))
    source = analysis.get("source_url")
    if source:
        provenance.append(f'<a href="{esc(source, quote=True)}" target="_blank" rel="noopener noreferrer">本次阅读的论文版本 ↗</a>')
    preview = '<p class="notice">真实论文阅读预览 · 非自动更新</p>' if demo else ""
    nomination = paper.get("scirate") or {}
    nomination_text = f'SciRate · {nomination["votes"]} votes' if nomination.get("votes") else ""
    if nomination_text and nomination.get("url"):
        nomination_text = f'<a href="{esc(nomination["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{nomination_text} ↗</a>'
    nomination_meta = f'<p class="paper-meta" lang="en">{nomination_text}</p>' if nomination_text else ""
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{esc(analysis.get('summary', '')[:180], quote=True)}">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'">
  <title>{esc(paper['title'])} · {_TITLE}</title>
  <link rel="canonical" href="{esc(base + paper['article_path'], quote=True)}">
  <link rel="alternate" type="application/rss+xml" title="{_TITLE} RSS" href="{esc(base + 'feed.xml', quote=True)}">
  <link rel="stylesheet" href="../style.css">
</head>
<body>
  <a class="skip-link" href="#report">跳到中文报告</a>
  <header class="site-header"><div class="header-inner"><a class="brand" href="../">{_TITLE}</a><nav aria-label="文章导航">{''.join(links)}</nav></div></header>
  <main class="article-shell">
    <article>
      <header class="article-heading">
        <p class="eyebrow">AI 中文技术阅读报告</p>
        <h1 lang="en">{esc(paper['title'])}</h1>
        <p class="authors" lang="en">{esc(', '.join(paper['authors']))}</p>
        <p class="paper-meta">arXiv:{esc(paper['id'])}v{paper['version']} · {esc(date)} · 依据{scope}</p>
        {nomination_meta}
      </header>
      {preview}
      <div id="report" class="report">{_report_sections(paper)}</div>
      <details class="original-abstract"><summary>Original abstract</summary><p lang="en">{esc(paper['abstract']) if paper['abstract'] else 'The original abstract is not available in this entry.'}</p></details>
      <footer class="article-provenance">{' · '.join(provenance)}</footer>
    </article>
  </main>
  <footer class="site-footer article-shell"><a href="../">← 全部文章</a><a href="../feed.xml">RSS</a></footer>
</body>
</html>
'''


def _write_feed(path: Path, papers: list[dict], data: dict, base: str, feed_path: str, label: str = "") -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    title = _TITLE + (f" · {label}" if label else "")
    if data["is_demo"]:
        title += " [阅读预览 · 非自动更新]"
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = base
    ET.SubElement(channel, "description").text = "Quantum research papers: original English abstracts and linked Chinese AI technical reports."
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "ttl").text = "30"
    ET.SubElement(channel, f"{{{_ATOM}}}link", href=base + feed_path, rel="self", type="application/rss+xml")
    # A collection-only run does not publish new analysis. Legacy states set
    # last_analysis_run from last_successful_run in build_site below.
    last_date = _date(data.get("last_analysis_run"))
    if last_date:
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(last_date, usegmt=True)
    for paper in papers:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = paper["title"]
        ET.SubElement(item, "link").text = base + paper["article_path"]
        ET.SubElement(item, "guid", isPermaLink="false").text = "urn:arxiv:" + paper["id"]
        # RSS publication is the first digest selection, not the original arXiv
        # submission: a catch-up run may add an old paper to the feed today.
        date = _date(paper.get("selected_at")) or _date(paper["analysis"].get("analyzed_at")) or _date(paper["published"])
        if date:
            ET.SubElement(item, "pubDate").text = format_datetime(date, usegmt=True)
        for topic in paper["topics"]:
            ET.SubElement(item, "category").text = data["topic_labels"].get(topic, topic)
        ET.SubElement(item, "description").text = _description(paper, data["is_demo"], base)
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
        "profile_title": _TITLE,
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
    article_dir = destination / "articles"
    article_dir.mkdir(exist_ok=True)
    active_articles = {Path(paper["article_path"]).name for paper in papers}
    for path in article_dir.glob("*.html"):
        if path.name not in active_articles and re.fullmatch(r"(?:[0-9]{4}\.[0-9]{4,5}|paper-[a-f0-9]{16})\.html", path.name):
            path.unlink()
    for paper in papers:
        (destination / paper["article_path"]).write_text(_article_html(paper, base, data["is_demo"]), encoding="utf-8")
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
    snapshot = snapshot.replace('  <script src="./app.js" defer></script>', "  <script>window.QRADAR_SNAPSHOT=true;window.QRADAR_DATA=" + embedded_data + ";</script>")
    snapshot = snapshot.replace("</body>", "  <script>" + (_ASSETS / "app.js").read_text(encoding="utf-8") + "</script>\n</body>")
    (destination / "snapshot.html").write_text(snapshot, encoding="utf-8")
