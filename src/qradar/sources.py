"""Small, polite arXiv clients with explicit evidence and completeness boundaries.

API documentation: https://info.arxiv.org/help/api/user-manual.html
Terms and rate limits: https://info.arxiv.org/help/api/tou.html

Only metadata and derived summaries should be published. Downloaded full text is
used transiently for analysis; this module never writes PDFs or HTML to disk.
"""

from __future__ import annotations

import io
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser


ATOM = "{http://www.w3.org/2005/Atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV = "{http://arxiv.org/schemas/atom}"
API_URL = "https://export.arxiv.org/api/query"
REQUEST_INTERVAL = 3.1
PAGE_SIZE = 500
REQUEST_TIMEOUT = 45
MAX_ATTEMPTS = 3
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
MAX_PDF_PAGES = 40
SCIRATE_URL = "https://scirate.com/arxiv/quant-ph?range=3"
_request_lock = threading.Lock()
_last_request_at: float | None = None
_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9.-]*/\d{7})(?:v([1-9]\d*))?$")
_CATEGORY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)?$")


class SourceError(RuntimeError):
    """A source could not be read reliably; this is never a successful empty run."""


class ResultLimitExceeded(SourceError):
    """The catch-up interval contains more results than the configured budget."""


def _download(url: str) -> bytes:
    """Serialize allowlisted source requests, including retries and full text."""
    global _last_request_at
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {
        "arxiv.org", "export.arxiv.org", "rss.arxiv.org", "scirate.com"
    } or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise SourceError("Downloads must use an allowlisted arXiv or SciRate HTTPS endpoint")
    label = "SciRate" if parsed.hostname == "scirate.com" else "arXiv"
    if label == "SciRate":
        query = urllib.parse.parse_qs(parsed.query)
        if (parsed.path != "/arxiv/quant-ph" or query.get("range") != ["3"]
                or set(query) - {"range", "date", "page"} or parsed.fragment
                or ("page" in query and (len(query["page"]) != 1 or not re.fullmatch(r"[1-9]\d*", query["page"][0])))
                or ("date" in query and (len(query["date"]) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", query["date"][0])))):
            raise SourceError("SciRate downloads must use its public three-day quant-ph feed")
    user_agent = os.environ.get("ARXIV_USER_AGENT", "QuantumArxivDigest/1.0 (research discovery)")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    for attempt in range(MAX_ATTEMPTS):
        retry_delay = 0.0
        try:
            # The lock covers response reading, not just connection creation.
            # Run one scheduled producer: this per-process lock cannot coordinate
            # independent machines or independent Python processes.
            with _request_lock:
                elapsed = time.monotonic() - _last_request_at if _last_request_at is not None else REQUEST_INTERVAL
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)
                _last_request_at = time.monotonic()
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    data = response.read(MAX_DOWNLOAD_BYTES + 1)
                    if len(data) > MAX_DOWNLOAD_BYTES:
                        raise SourceError(f"{label} response exceeds the 40 MiB download limit")
                    return data
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise SourceError(f"{label} returned HTTP {exc.code} for {parsed.path}; no empty collection was accepted") from exc
            if attempt == MAX_ATTEMPTS - 1:
                raise SourceError(f"{label} returned HTTP {exc.code} after {MAX_ATTEMPTS} attempts") from exc
            header = exc.headers.get("Retry-After") if exc.headers else None
            if header:
                try:
                    retry_delay = max(0.0, float(header))
                except ValueError:
                    try:
                        retry_delay = max(0.0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError):
                        retry_delay = 0.0
                if retry_delay > 60:
                    raise SourceError(f"{label} requests a retry after {retry_delay:.0f}s; defer this run") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise SourceError(f"{label} connection failed after {MAX_ATTEMPTS} attempts: {exc}") from exc
        time.sleep(max(retry_delay, REQUEST_INTERVAL * 2**attempt))
    raise SourceError(f"{label} request did not complete")  # defensive, unreachable


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _xml_text(element: ET.Element | None) -> str:
    return _plain("".join(element.itertext())) if element is not None else ""


def _identifier(value: str) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("arXiv:"):
        value = value[6:]
    if value.startswith("oai:arXiv.org:"):
        value = value[len("oai:arXiv.org:"):]
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.hostname not in {"arxiv.org", "export.arxiv.org"}:
            raise SourceError("Feed contains a non-arXiv paper identifier")
        value = re.sub(r"^/(?:abs|pdf|html)/", "", parsed.path)
    value = value.removesuffix(".pdf")
    match = _ID_RE.fullmatch(value)
    if not match:
        raise SourceError(f"Invalid arXiv paper identifier: {value!r}")
    return match.group(1), int(match.group(2) or 1)


def _iso_date(value: str, field: str) -> str:
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("timezone missing")
        return date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError as exc:
        raise SourceError(f"Invalid {field} timestamp in arXiv response") from exc


def _parse_atom_feed(data: bytes | str) -> tuple[list[dict], dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SourceError("arXiv returned malformed XML, not a valid Atom feed") from exc
    if root.tag != ATOM + "feed":
        raise SourceError("arXiv response is not an Atom feed")
    metadata = {}
    for key in ("totalResults", "startIndex", "itemsPerPage"):
        value = root.findtext(OPENSEARCH + key)
        if value is not None:
            try:
                metadata[key] = int(value)
                if metadata[key] < 0:
                    raise ValueError("negative")
            except ValueError as exc:
                raise SourceError(f"Invalid OpenSearch {key}") from exc
    papers = []
    for entry in root.findall(ATOM + "entry"):
        raw_id = _xml_text(entry.find(ATOM + "id"))
        title = _xml_text(entry.find(ATOM + "title"))
        summary = _xml_text(entry.find(ATOM + "summary"))
        if "/api/errors" in raw_id or title.lower() == "error":
            raise SourceError(f"arXiv API error: {summary or raw_id}")
        paper_id, version = _identifier(raw_id)
        # Older API responses use an unversioned entry ID but versioned links.
        if not re.search(r"v\d+$", raw_id):
            for link in entry.findall(ATOM + "link"):
                href = link.get("href", "")
                if link.get("title") == "pdf" or link.get("rel") == "alternate":
                    linked_id, linked_version = _identifier(href)
                    if linked_id != paper_id:
                        raise SourceError("arXiv entry ID and document link disagree")
                    version = max(version, linked_version)
        if not title:
            raise SourceError(f"arXiv entry {paper_id} has no title")
        papers.append({
            "id": paper_id,
            "version": version,
            "title": title,
            "authors": [_xml_text(a.find(ATOM + "name")) for a in entry.findall(ATOM + "author")],
            "abstract": summary,
            "url": f"https://arxiv.org/abs/{paper_id}",
            "pdf_url": f"https://arxiv.org/pdf/{paper_id}v{version}",
            "published": _iso_date(_xml_text(entry.find(ATOM + "published")), "published"),
            "updated": _iso_date(_xml_text(entry.find(ATOM + "updated")), "updated"),
            "categories": list(dict.fromkeys(c.get("term", "") for c in entry.findall(ATOM + "category") if c.get("term"))),
        })
    return papers, metadata


def parse_atom(data: bytes | str) -> list[dict]:
    """Parse arXiv Atom metadata, raising on error entries or invalid content."""
    return _parse_atom_feed(data)[0]


class _SciRateHTML(HTMLParser):
    """Minimal DOM for the site's public feed, without third-party dependencies."""

    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "root", "attrs": {}, "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > 256:
            raise SourceError("SciRate HTML nesting exceeds the parser limit")
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in self._void:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def _html_nodes(node):
    yield node
    for child in node["children"]:
        if isinstance(child, dict):
            yield from _html_nodes(child)


def _html_words(node):
    return " ".join(child if isinstance(child, str) else _html_words(child)
                    for child in node["children"])


def _has_class(node, name):
    return name in (node["attrs"].get("class") or "").split()


def parse_scirate(data: bytes | str) -> dict:
    """Parse the real public quant-ph feed layout; challenges are source errors.

    Selectors and window semantics follow SciRate's public upstream templates:
    https://github.com/scirate/scirate/blob/main/app/views/papers/_paper.html.slim
    https://github.com/scirate/scirate/blob/main/app/controllers/feeds_controller.rb
    ``range=3`` means three publication dates ending at the feed's latest date.
    Counts are current total scites, not votes cast within those three dates.
    """
    parser = _SciRateHTML()
    try:
        parser.feed(data.decode("utf-8") if isinstance(data, bytes) else data)
    except (UnicodeError, TypeError, ValueError) as exc:
        raise SourceError("SciRate returned unreadable HTML") from exc
    nodes = list(_html_nodes(parser.root))
    feeds = [node for node in nodes if node["attrs"].get("id") == "feed-page"]
    lists = [node for node in nodes if node["tag"] == "ul" and _has_class(node, "papers")]
    navs = [node for node in nodes if node["tag"] == "nav" and _has_class(node, "timenav")]
    headers = [node for node in nodes if node["tag"] == "header" and _has_class(node, "feed-header")]
    if (len(feeds) != 1 or len(lists) != 1 or not navs or not headers
            or not any("quant-ph" in _html_words(node) for node in headers)):
        raise SourceError("SciRate response is not a recognizable quant-ph feed; it may be blocked or its layout changed")
    windows = set()
    for nav in navs:
        dates = re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{4}\b", _html_words(nav))
        if len(dates) < 2 or "UTC" not in _html_words(nav):
            raise SourceError("SciRate did not identify its three-day UTC publication window")
        try:
            start, end = (datetime.strptime(value, "%b %d %Y").date() for value in dates[:2])
        except ValueError as exc:
            raise SourceError("SciRate publication window contains an invalid date") from exc
        if (end - start).days != 2:
            raise SourceError("SciRate publication window is not three calendar dates")
        windows.add((start.isoformat(), end.isoformat()))
    if len(windows) != 1:
        raise SourceError("SciRate returned conflicting publication windows")
    records = []
    for node in _html_nodes(lists[0]):
        if node["tag"] != "li" or not _has_class(node, "paper"):
            continue
        descendants = list(_html_nodes(node))
        toggles = [item for item in descendants if _has_class(item, "scite-toggle")]
        counts = [item for item in descendants if item["tag"] == "button" and _has_class(item, "count")]
        titles = [item for item in descendants if _has_class(item, "title")]
        if len(toggles) != 1 or len(counts) != 1 or len(titles) != 1:
            raise SourceError("SciRate paper is missing its identity, title, or scite count")
        paper_id, _ = _identifier(toggles[0]["attrs"].get("data-paper-uid") or "")
        title_links = [item for item in _html_nodes(titles[0]) if item["tag"] == "a"]
        if len(title_links) != 1:
            raise SourceError("SciRate paper title does not identify one paper")
        href = title_links[0]["attrs"].get("href") or ""
        if not href.startswith("/arxiv/") or _identifier(href[len("/arxiv/"):])[0] != paper_id:
            raise SourceError("SciRate title and scite identity disagree")
        count = _plain(_html_words(counts[0]))
        if not re.fullmatch(r"\d+", count):
            raise SourceError("SciRate returned an invalid scite count")
        records.append({"id": paper_id, "votes": int(count)})
    if not records and "No papers found!" not in _plain(_html_words(lists[0])):
        raise SourceError("SciRate empty page lacks the explicit no-papers marker")
    next_pages = []
    for node in nodes:
        if node["tag"] != "a" or not (_has_class(node, "next_page") or node["attrs"].get("rel") == "next"):
            continue
        url = urllib.parse.urlsplit(urllib.parse.urljoin(SCIRATE_URL, node["attrs"].get("href") or ""))
        query = urllib.parse.parse_qs(url.query)
        page = query.get("page", [""])[0]
        if (url.scheme != "https" or url.hostname != "scirate.com" or url.path != "/arxiv/quant-ph"
                or query.get("range") != ["3"] or not re.fullmatch(r"[1-9]\d*", page)):
            raise SourceError("SciRate returned an invalid next-page link")
        next_pages.append(int(page))
    if len(set(next_pages)) > 1:
        raise SourceError("SciRate returned conflicting next-page links")
    start, end = windows.pop()
    return {"papers": records, "window_start": start, "window_end": end,
            "next_page": next_pages[0] if next_pages else None}


def fetch_scirate(max_results: int = 2500, *, now: datetime | None = None,
                  known_papers: list[dict] | None = None) -> list[dict]:
    """Nominate strictly >10-scite papers, then obtain authoritative arXiv metadata.

    Existing identities use their already-pinned metadata and analysis version.
    Every unknown nomination must be returned by the official API; partial
    responses or a blocked SciRate page abort the whole update.
    """
    if type(max_results) is not int or max_results < 1:
        raise ValueError("max_results must be a positive integer")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    observed_at = current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    records = {}
    page_number, window, seen_count = 1, None, 0
    while True:
        url = SCIRATE_URL if page_number == 1 else (SCIRATE_URL + "&" + urllib.parse.urlencode({"date": window[1], "page": page_number}))
        page = parse_scirate(_download(url))
        bounds = (page["window_start"], page["window_end"])
        if window is not None and bounds != window:
            raise SourceError("SciRate publication window changed during pagination")
        window = bounds
        seen_count += len(page["papers"])
        if seen_count > max_results:
            raise ResultLimitExceeded("SciRate result budget exceeded; no partial collection was accepted")
        for record in page["papers"]:
            if record["id"] in records:
                raise SourceError("SciRate pagination repeated a paper identity")
            records[record["id"]] = record
        next_page = page["next_page"]
        if next_page is None:
            break
        if next_page != page_number + 1 or not page["papers"]:
            raise SourceError("SciRate pagination is incomplete or out of order")
        page_number = next_page
    nominations = {paper_id: record for paper_id, record in records.items() if record["votes"] > 10}
    known = {paper["id"]: paper for paper in (known_papers or [])}
    metadata = {paper_id: known[paper_id] for paper_id in nominations if paper_id in known}
    unknown = [paper_id for paper_id in nominations if paper_id not in metadata]
    for start in range(0, len(unknown), 100):
        ids = unknown[start:start + 100]
        query = urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
        papers = parse_atom(_download(API_URL + "?" + query))
        returned = [paper["id"] for paper in papers]
        if set(returned) != set(ids) or len(returned) != len(ids):
            raise SourceError("Official arXiv metadata is incomplete for SciRate nominations")
        if any("quant-ph" not in paper["categories"] or not paper["abstract"].strip() for paper in papers):
            raise SourceError("SciRate nomination lacks official quant-ph metadata or an abstract")
        metadata.update({paper["id"]: paper for paper in papers})
    import copy
    result = []
    for paper_id, nomination in nominations.items():
        paper = copy.deepcopy(metadata[paper_id])
        paper["sources"] = list(dict.fromkeys([*paper.get("sources", []), "scirate"]))
        paper["scirate"] = {"votes": nomination["votes"], "range_days": 3, "min_votes_exclusive": 10,
                            "observed_at": observed_at, "window_start": window[0], "window_end": window[1],
                            "url": f"https://scirate.com/arxiv/{paper_id}", "query_url": SCIRATE_URL,
                            "date_basis": "scirate_announcement_window"}
        result.append(paper)
    return result


def fetch_recent(
    categories: list[str],
    lookback_days: int = 7,
    max_results: int = 1500,
    now: datetime | None = None,
) -> list[dict]:
    """Fetch all initial submissions in the UTC look-back interval.

    Cross-listings are included through OR-ed category filters and deduplicated.
    Replacement-only updates outside this submission interval are not included.
    The submission date is NOT the announcement date. Overlap daily runs and
    deduplicate downstream to cover announcement delays and missed runs.

    A cap breach, inconsistent paging, unavailable API, or malformed response
    raises SourceError. A genuine zero totalResults returns an empty list.
    No fallback can silently replace multi-day API coverage with today's RSS.
    """
    if not categories or any(not _CATEGORY_RE.fullmatch(c) for c in categories):
        raise ValueError("Provide at least one valid arXiv category")
    if not isinstance(lookback_days, int) or lookback_days < 1:
        raise ValueError("lookback_days must be a positive integer")
    if not isinstance(max_results, int) or not 1 <= max_results <= 30000:
        raise ValueError("max_results must be between 1 and 30000")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    start_date = current - timedelta(days=lookback_days)
    category_query = " OR ".join(f"cat:{c}" for c in dict.fromkeys(categories))
    query = f"({category_query}) AND submittedDate:[{start_date:%Y%m%d%H%M} TO {current:%Y%m%d%H%M}]"
    start = 0
    total: int | None = None
    papers_by_id: dict[str, dict] = {}
    while total is None or start < total:
        params = urllib.parse.urlencode({
            "search_query": query,
            "start": start,
            "max_results": min(PAGE_SIZE, max_results - start),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
        page, metadata = _parse_atom_feed(_download(f"{API_URL}?{params}"))
        if "totalResults" not in metadata or "startIndex" not in metadata:
            raise SourceError("arXiv API response lacks required pagination metadata")
        if metadata["startIndex"] != start:
            raise SourceError("arXiv API returned an unexpected page offset")
        reported_total = metadata["totalResults"]
        if reported_total > max_results:
            raise ResultLimitExceeded(
                f"arXiv query matches {reported_total} papers, exceeding max_results={max_results}; "
                "increase the budget or narrow categories/look-back interval"
            )
        if total is None:
            total = reported_total
        elif reported_total != total:
            raise SourceError("arXiv result count changed during pagination; retry later")
        if not page:
            if start < total:
                raise SourceError("arXiv API returned an empty page before the advertised end")
            return []
        if start + len(page) > total or len(page) > min(PAGE_SIZE, max_results - start):
            raise SourceError("arXiv page size disagrees with the advertised result count")
        for paper in page:
            if paper["id"] in papers_by_id:
                raise SourceError("arXiv API repeated a paper across pages; catch-up may be incomplete")
            papers_by_id[paper["id"]] = paper
        start += len(page)
    return list(papers_by_id.values())


class _TextExtractor(HTMLParser):
    """Discard UI/script content and preserve equations as available TeX text."""

    _ignored = {"script", "style", "head", "nav", "footer", "form", "button", "aside", "noscript"}
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _block = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "figure"}

    def __init__(self, article_only: bool = False):
        super().__init__(convert_charrefs=True)
        self.article_only = article_only
        self.article_found = False
        self.stack: list[tuple[str, bool, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = (attrs_dict.get("class") or "").split()
        parent_skip = self.stack[-1][1] if self.stack else False
        in_article = self.stack[-1][2] if self.stack else False
        if tag == "article" or "ltx_document" in classes:
            in_article = True
            self.article_found = True
        skipped = parent_skip or tag in self._ignored or any(
            c.startswith(("ltx_page_nav", "ltx_page_footer", "ltx_page_header", "ltx_MathText"))
            for c in classes
        ) or attrs_dict.get("aria-hidden") == "true" or "hidden" in attrs_dict
        style = (attrs_dict.get("style") or "").replace(" ", "").lower()
        skipped = skipped or "display:none" in style or "visibility:hidden" in style
        visible = not skipped and (in_article or not self.article_only)
        if visible:
            if tag in self._block or tag in {"br", "hr"}:
                self.parts.append("\n")
            if tag == "math" and attrs_dict.get("alttext"):
                self.parts.append(" " + attrs_dict["alttext"] + " ")
                skipped = True  # alttext replaces verbose/repeated MathML tokens
            if tag == "img" and attrs_dict.get("alt"):
                self.parts.append(" " + attrs_dict["alt"] + " ")
        if tag not in self._void:
            self.stack.append((tag, skipped, in_article))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                skipped, in_article = self.stack[index][1:]
                if not skipped and (in_article or not self.article_only) and tag in self._block:
                    self.parts.append("\n")
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self.stack:
            _, skipped, in_article = self.stack[-1]
        else:
            skipped, in_article = False, False
        if not skipped and (in_article or not self.article_only):
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(line for line in (_plain(line) for line in "".join(self.parts).splitlines()) if line)


def extract_html_text(html: str) -> str:
    """Extract recognizable arXiv article HTML; reject abstract/error pages."""
    parser = _TextExtractor(article_only=True)
    parser.feed(html)
    parser.close()
    if not parser.article_found:
        raise SourceError("HTML response does not contain an arXiv full-text article")
    return parser.text()


def parse_rss(data: bytes | str) -> list[dict]:
    """Parse current arXiv RSS as *announcement* metadata, never catch-up data.

    This standalone utility is not used as a fetch_recent fallback. RSS dates
    describe feed announcements, not verified original submission timestamps;
    the returned source/date_basis fields make that distinction explicit.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SourceError("arXiv returned malformed RSS XML") from exc
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "RDF"}:
        raise SourceError("Response is not an RSS feed")
    items = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "item":
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: _xml_text(child) for child in item}
        # Current arXiv RSS uses unversioned links but versioned OAI GUIDs.
        # Reading only the link silently mislabels replacements as version 1.
        identifiers = [fields[key] for key in ("arxiv_id", "link", "guid") if fields.get(key)]
        paper_id, version = _identifier(identifiers[0] if identifiers else "")
        for identifier in identifiers[1:]:
            linked_id, linked_version = _identifier(identifier)
            if linked_id != paper_id:
                raise SourceError("arXiv RSS item identifiers disagree")
            version = max(version, linked_version)
        if fields.get("arxiv_version"):
            raw_version = fields["arxiv_version"].removeprefix("v")
            if not raw_version.isdigit() or int(raw_version) < 1:
                raise SourceError("Invalid arXiv version in RSS item")
            version = int(raw_version)
        if not fields.get("title"):
            raise SourceError(f"arXiv RSS item {paper_id} has no title")
        description = _TextExtractor()
        description.feed(fields.get("description", ""))
        abstract = description.text()
        if "Abstract:" in abstract:
            abstract = abstract.split("Abstract:", 1)[1].strip()
        timestamp = fields.get("pubDate") or fields.get("date", "")
        try:
            date = parsedate_to_datetime(timestamp)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            published = date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            published = _iso_date(timestamp, "RSS announcement") if timestamp else ""
        categories = [c.text.strip() for c in item if c.tag.rsplit("}", 1)[-1] == "category" and c.text]
        if fields.get("primary_category"):
            categories.insert(0, fields["primary_category"])
        items.append({
            "id": paper_id, "version": version, "title": fields.get("title", ""),
            "authors": [a.strip() for a in re.split(r",|\s+and\s+", fields.get("creator", "")) if a.strip()],
            "abstract": abstract, "url": f"https://arxiv.org/abs/{paper_id}",
            "pdf_url": f"https://arxiv.org/pdf/{paper_id}v{version}",
            "published": published, "updated": published,
            "categories": list(dict.fromkeys(categories)),
            "source": "rss", "date_basis": "announcement",
            "announcement_type": fields.get("announce_type", "unknown"),
        })
    return items


def get_paper_text(paper: dict, max_chars: int = 60000) -> dict:
    """Try version-pinned HTML, then optional pypdf, then the supplied abstract.

    Full-text evidence is always labeled an excerpt. Character/page limits and
    extraction failures are exposed through warnings, so AI cannot claim to have
    examined omitted proofs. Raw full text is never persisted by this function.
    """
    if not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    paper_id, embedded_version = _identifier(str(paper["id"]))
    version = int(paper.get("version", embedded_version))
    if version < 1:
        raise ValueError("Paper version must be positive")
    versioned_id = f"{paper_id}v{version}"
    warnings: list[str] = []
    html_url = f"https://arxiv.org/html/{versioned_id}"
    try:
        text = extract_html_text(_download(html_url).decode("utf-8", errors="replace"))
        if len(text) < 600:
            raise SourceError("HTML article has too little extractable text for technical analysis")
        warnings.append("Automatically extracted arXiv HTML; equations and figures may be incomplete.")
        if len(text) > max_chars:
            warnings.append(f"HTML excerpt truncated at {max_chars} characters; later sections may be omitted.")
        return {"text": text[:max_chars], "evidence_level": "full_text_excerpt", "source_url": html_url, "warnings": warnings}
    except (SourceError, UnicodeError, ValueError) as exc:
        warnings.append(f"HTML full text unavailable: {exc}")
    pdf_url = f"https://arxiv.org/pdf/{versioned_id}"
    try:
        from pypdf import PdfReader
    except ImportError:
        warnings.append("PDF extraction unavailable: install the optional pypdf dependency.")
    else:
        try:
            pdf = _download(pdf_url)
            if not pdf.lstrip().startswith(b"%PDF-"):
                raise SourceError("arXiv PDF endpoint returned non-PDF content")
            reader = PdfReader(io.BytesIO(pdf))
            chunks: list[str] = []
            chars = 0
            pages_read = 0
            for page in list(reader.pages)[:MAX_PDF_PAGES]:
                chunk = page.extract_text() or ""
                chunks.append(chunk)
                pages_read += 1
                chars += len(chunk) + 2
                if chars > max_chars:
                    break
            text = "\n\n".join(chunks).strip()
            if len(text) < 600:
                raise SourceError("PDF has too little extractable text (possibly scanned)")
            warnings.append("Automatically extracted PDF text; equations, figures, and reading order may be incomplete.")
            if len(text) > max_chars or pages_read < len(reader.pages):
                warnings.append(f"PDF excerpt limited to {max_chars} characters and at most {MAX_PDF_PAGES} pages; later sections may be omitted.")
            return {"text": text[:max_chars], "evidence_level": "full_text_excerpt", "source_url": pdf_url, "warnings": warnings}
        except Exception as exc:
            warnings.append(f"PDF full text unavailable: {type(exc).__name__}: {exc}")
    abstract = str(paper.get("abstract", ""))
    if len(abstract) > max_chars:
        warnings.append(f"Abstract truncated at {max_chars} characters.")
    warnings.append("Abstract-only evidence; theorem assumptions and proofs have not been checked.")
    return {
        "text": abstract[:max_chars], "evidence_level": "abstract",
        "source_url": f"https://arxiv.org/abs/{paper_id}v{version}", "warnings": warnings,
    }
