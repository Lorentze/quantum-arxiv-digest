"""Official journal RSS and publisher-deposited Crossref metadata.

RSS is a rolling list, not a complete publication-day API.  Crossref's
``published-online`` date supplements short feeds; deposit/index dates never
stand in for publication.  A failed journal cannot abort arXiv collection.
Public abstracts/full text only: no authentication or anti-bot workarounds.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .sources import SourceError


JOURNALS = {
    "nature": {"journal": "Nature", "issn": "1476-4687", "feed_urls": ["https://www.nature.com/nature.rss"], "article_prefix": "s41586-"},
    "science": {"journal": "Science", "issn": "1095-9203", "feed_urls": ["https://feeds.science.org/rss/science.xml", "https://feeds.science.org/rss/science-aop.xml"]},
    "nature_physics": {"journal": "Nature Physics", "issn": "1745-2481", "feed_urls": ["https://www.nature.com/nphys.rss"], "article_prefix": "s41567-"},
    "prx": {"journal": "Physical Review X", "issn": "2160-3308", "feed_urls": ["https://feeds.aps.org/rss/recent/prx.xml"], "aps_slug": "prx"},
    "prl": {"journal": "Physical Review Letters", "issn": "1079-7114", "feed_urls": ["https://feeds.aps.org/rss/recent/prl.xml"], "aps_slug": "prl"},
    "prx_quantum": {"journal": "PRX Quantum", "issn": "2691-3399", "feed_urls": ["https://feeds.aps.org/rss/recent/prxquantum.xml"], "aps_slug": "prxquantum"},
    "nature_communications": {"journal": "Nature Communications", "issn": "2041-1723", "feed_urls": ["https://www.nature.com/ncomms.rss"], "article_prefix": "s41467-"},
    "npj_quantum_information": {"journal": "npj Quantum Information", "issn": "2056-6387", "feed_urls": ["https://www.nature.com/npjqi.rss"], "article_prefix": "s41534-"},
}

_lock = threading.Lock()
_last_request: float | None = None
_MAX_BYTES = 15 * 1024 * 1024
_INTERVAL = 1.1
_DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"]+", re.I)
_TOPIC = re.compile(r"quantum|qubit|clifford|stabilizer|magic state|random circuit|unitary design|error correct|entangl|nonlocal|bell measurement", re.I)
_NONRESEARCH = re.compile(r"^(?:author correction|publisher correction|correction|erratum|retraction|editorial|comment on|reply to)\b", re.I)


class AcceptedOnlyError(SourceError):
    """An accepted manuscript is not a confirmed formal publication."""


def normalize_doi(value: str) -> str:
    value = html.unescape(str(value)).strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    match = _DOI.fullmatch(value)
    if not match or len(value) > 255:
        raise SourceError("Journal record has an invalid DOI")
    return value.lower()


def _allowed(url: str, *, redirect: bool = False) -> bool:
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or p.username or p.password or p.port not in {None, 443} or p.fragment:
            return False
    except ValueError:
        return False
    if p.hostname == "www.nature.com":
        return p.path in {"/nature.rss", "/nphys.rss", "/ncomms.rss", "/npjqi.rss"} or bool(re.fullmatch(r"/articles/[A-Za-z0-9-]+", p.path))
    if p.hostname == "idp.nature.com":
        # Nature's public cookie redirect is permitted only during an official
        # article request; no account, credential, or session is supplied.
        return redirect and p.path == "/authorize"
    if p.hostname == "feeds.aps.org":
        return p.path in {"/rss/recent/prx.xml", "/rss/recent/prl.xml", "/rss/recent/prxquantum.xml"}
    if p.hostname == "journals.aps.org":
        return bool(re.fullmatch(r"/(?:prx|prl|prxquantum)/(?:abstract|accepted|pdf)/10\.1103/[A-Za-z0-9._-]+", p.path))
    if p.hostname == "feeds.science.org":
        return p.path in {"/rss/science.xml", "/rss/science-aop.xml"}
    if p.hostname == "www.science.org":
        return bool(re.fullmatch(r"/doi/(?:abs/|full/)?10\.1126/science\.[A-Za-z0-9]+", p.path))
    if p.hostname == "api.crossref.org":
        return (p.path in {f"/journals/{j['issn']}/works" for j in JOURNALS.values()}
                or bool(re.fullmatch(r"/works/10\.\d{4,9}/[A-Za-z0-9._%()/-]+", p.path)))
    return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl, redirect=True):
            raise SourceError("Journal endpoint redirected outside the official allowlist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str) -> bytes:
    """One bounded attempt, serialized and throttled; 403/429 are not retried."""
    global _last_request
    if not _allowed(url):
        raise SourceError("Journal downloads require an allowlisted official HTTPS endpoint")
    request = urllib.request.Request(url, headers={
        "User-Agent": os.environ.get("JOURNAL_USER_AGENT", "QuantumPaperRadar/1.0 (public research feeds)"),
        "Accept": "application/json, application/rss+xml, application/atom+xml, text/html;q=0.8",
    })
    try:
        with _lock:
            elapsed = time.monotonic() - _last_request if _last_request is not None else _INTERVAL
            if elapsed < _INTERVAL:
                time.sleep(_INTERVAL - elapsed)
            _last_request = time.monotonic()
            with urllib.request.build_opener(_SafeRedirect()).open(request, timeout=25) as response:
                data = response.read(_MAX_BYTES + 1)
                if len(data) > _MAX_BYTES:
                    raise SourceError("Journal response exceeds the 15 MiB limit")
                return data
    except urllib.error.HTTPError as exc:
        raise SourceError(f"Journal endpoint returned HTTP {exc.code}; skipped without retry") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise SourceError(f"Journal endpoint unavailable: {exc}") from exc


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"p", "div", "br", "section", "h1", "h2", "h3", "jats:p", "jats:title"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
        if tag in {"p", "div", "section", "jats:p", "jats:title"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _plain(value: str) -> str:
    parser = _HTMLText()
    parser.feed(str(value))
    return re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()


def _text(node: ET.Element | None) -> str:
    return "".join(node.itertext()).strip() if node is not None else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _date(value: str) -> str:
    value = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError) as exc:
            raise SourceError("Journal record is missing a valid publication date") from exc
    if parsed is None:
        raise SourceError("Journal record is missing a valid publication date")
    if parsed.tzinfo is None:
        return parsed.date().isoformat()
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _crossref_date(record: dict) -> tuple[str, str]:
    for field in ("published-online", "published-print", "published"):
        parts = record.get(field, {}).get("date-parts", [])
        if parts and len(parts[0]) == 3:
            try:
                return date(*parts[0]).isoformat(), f"crossref_{field}"
            except (ValueError, TypeError):
                pass
    raise SourceError("Crossref record has no complete publication date; deposit/index dates were ignored")


def _article_url(journal_id: str, doi: str) -> str:
    info = JOURNALS[journal_id]
    if "aps_slug" in info:
        return f"https://journals.aps.org/{info['aps_slug']}/abstract/{doi}"
    if journal_id == "science":
        return f"https://www.science.org/doi/{doi}"
    return "https://www.nature.com/articles/" + doi.split("/", 1)[1]


def _is_research(journal_id: str, doi: str, title: str) -> bool:
    if _NONRESEARCH.search(title):
        return False
    info = JOURNALS[journal_id]
    if "article_prefix" in info:
        return doi.startswith("10.1038/" + info["article_prefix"])
    if journal_id == "science":
        return doi.startswith("10.1126/science.") and title.lower() not in {"in other journals", "research highlights", "this week in science"}
    return doi.startswith("10.1103/")


def _paper(journal_id: str, doi: str, title: str, published: str, date_basis: str,
           abstract: str = "", authors: list[str] | None = None, abstract_basis: str = "missing",
           abstract_source_url: str = "") -> dict:
    url = _article_url(journal_id, doi)
    return {"id": "doi:" + doi, "version": 1, "title": title, "authors": authors or [],
            "abstract": abstract, "abstract_basis": abstract_basis, "abstract_source_url": abstract_source_url,
            "url": url, "pdf_url": "",
            "published": published, "updated": published, "categories": [], "sources": [journal_id],
            "publication": {"journal_id": journal_id, "journal": JOURNALS[journal_id]["journal"],
                            "doi": doi, "url": url, "published": published, "date_basis": date_basis}}


def parse_journal_feed(data: bytes | str, journal_id: str, source_url: str = "") -> list[dict]:
    """Parse RSS 1 RDF, RSS 2, and Atom without treating HTML errors as empty."""
    if journal_id not in JOURNALS:
        raise SourceError("Unknown journal")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SourceError("Journal feed is malformed XML") from exc
    if _local(root.tag) not in {"RDF", "rss", "feed"}:
        raise SourceError("Journal endpoint returned neither RSS nor Atom")
    output = []
    for item in root.iter():
        if _local(item.tag) not in {"item", "entry"}:
            continue
        fields: dict[str, list[str]] = {}
        for child in item:
            key = _local(child.tag)
            val = child.get("href", "") if key == "link" and child.get("href") else _text(child)
            fields.setdefault(key, []).append(val)
        first = lambda key: next(iter(fields.get(key, [])), "")
        title = _plain(first("title")).removesuffix(" | Science").strip()
        raw_doi = first("doi") or first("identifier")
        if not raw_doi:
            match = _DOI.search(first("link"))
            raw_doi = match.group() if match else ""
        if not raw_doi or not title:
            continue  # Science's magazine/news entries have no research DOI.
        doi = normalize_doi(raw_doi)
        if not _is_research(journal_id, doi, title):
            continue
        published = _date(first("publicationDate") or first("date") or first("pubDate") or first("published"))
        excerpt = _plain(first("summary") or first("encoded") or first("description"))
        # Publisher feeds commonly contain an editor summary or truncated
        # abstract, not the full abstract. Never silently label either full.
        excerpt = re.sub(r"^.*?doi:10\.\d{4,9}/\S+\s*", "", excerpt, count=1).strip()
        excerpt = re.sub(r"^Author\(s\):.*?(?=\[|$)", "", excerpt) if "<p>" not in (first("encoded") or first("description")) else excerpt
        if first("encoded").startswith("<p>Author(s):"):
            parts = re.findall(r"<p>(.*?)</p>", first("encoded"), flags=re.S)
            excerpt = _plain(parts[1]) if len(parts) > 1 and not _plain(parts[1]).startswith("[") else ""
        if not excerpt or _plain(excerpt).casefold() == title.casefold():
            excerpt = ""
        output.append(_paper(journal_id, doi, title, published, "publisher_rss", excerpt,
                             fields.get("creator", []) or fields.get("author", []),
                             "publisher_feed_excerpt" if excerpt else "missing",
                             source_url or JOURNALS[journal_id]["feed_urls"][0]))
    return output


def parse_crossref_records(data: bytes | str, journal_id: str, source_url: str = "") -> tuple[list[dict], int]:
    try:
        message = json.loads(data)["message"]
        records = message["items"]
        total = int(message["total-results"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("Crossref returned invalid journal metadata") from exc
    output = []
    info = JOURNALS[journal_id]
    for record in records:
        title = _plain(next(iter(record.get("title", [])), ""))
        doi = normalize_doi(record.get("DOI", ""))
        if record.get("type") != "journal-article" or not title or not _is_research(journal_id, doi, title):
            continue
        if info["issn"] not in record.get("ISSN", [info["issn"]]):
            raise SourceError("Crossref record ISSN disagrees with the requested journal")
        try:
            published, basis = _crossref_date(record)
        except SourceError:
            continue
        abstract = re.sub(r"^Abstract\s+", "", _plain(record.get("abstract", "")))
        if abstract.casefold() == title.casefold():
            abstract = ""
        authors = [" ".join(str(a.get(k, "")) for k in ("given", "family")).strip() or a.get("name", "")
                   for a in record.get("author", [])]
        output.append(_paper(journal_id, doi, title, published, basis, abstract, authors,
                             "crossref_abstract" if abstract else "missing",
                             source_url or f"https://api.crossref.org/works/{doi}"))
    return output, total


class _PublisherHTML(HTMLParser):
    """Capture exact abstract/full-text containers and DOI identity metadata."""
    _void = {"meta", "link", "br", "img", "hr", "input", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.meta = {}
        self.abstract = []
        self.fulltext = []
        self.kind = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = a.get("name", a.get("property", ""))
            if key:
                self.meta.setdefault(key, []).append(a.get("content", ""))
        if tag in self._void:
            return
        if len(self.stack) > 256:
            raise SourceError("Publisher HTML nesting exceeds the parser limit")
        parent = self.stack[-1] if self.stack else ""
        identity = a.get("id", "")
        mode = parent
        if identity in {"abstract-section-content", "Abs1-content"} or (tag == "div" and a.get("class", "") == "c-article-section__content" and a.get("id") == "Abs1-content"):
            mode = "abstract"
        scientific_section = a.get("data-title", "")
        if identity in {"fulltext", "fulltext-section-content", "article-body"} or (
                tag == "section" and re.match(r"^(?:Main|Introduction|Results|Discussion|Methods|Conclusions?|Theor(?:y|etical)|Experimental|Mathematical|Proofs?|Model|Analysis|Quantum|Resource|Sample complexity)\b", scientific_section, re.I)):
            mode = "fulltext"
        if tag in {"script", "style", "nav"}:
            mode = "skip"
        self.stack.append(mode)
        if mode == "abstract":
            self.abstract.append(" ")
        elif mode == "fulltext":
            self.fulltext.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in self._void and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        mode = self.stack[-1] if self.stack else ""
        if mode == "abstract":
            self.abstract.append(data)
        elif mode == "fulltext":
            self.fulltext.append(data)


def parse_publisher_text(data: bytes | str, expected_doi: str) -> dict:
    parser = _PublisherHTML()
    document = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    parser.feed(document)
    doi = next(iter(parser.meta.get("citation_doi", [])), "")
    accepted = bool(re.search(r'"pageType"\s*:\s*"accepted_paper"', document))
    if not doi and accepted:
        match = re.search(r'"content"\s*:\s*\{\s*"id"\s*:\s*"(10\.1103/[^"\s]+)"', document)
        doi = match.group(1) if match else ""
    if not doi or normalize_doi(doi) != normalize_doi(expected_doi):
        raise SourceError("Publisher page did not confirm the exact article DOI")
    abstract = _plain(" ".join(parser.abstract))
    if not abstract:
        # Nature provides the full abstract in dc.description. APS's generic
        # description may instead be an editor blurb, and is never used here.
        abstract = _plain(next(iter(parser.meta.get("dc.description", [])), ""))
    fulltext = _plain(" ".join(parser.fulltext))
    return {"abstract": abstract, "fulltext": fulltext,
            "publication_stage": "accepted_manuscript" if accepted else "journal_article"}


def _read_publisher(paper: dict) -> tuple[dict, str]:
    publication = paper["publication"]
    url = publication["url"]
    try:
        return parse_publisher_text(_download(url), publication["doi"]), url
    except SourceError:
        if publication.get("journal_id") not in {"prx", "prl", "prx_quantum"} or "/abstract/" not in url:
            raise
        # New APS DOI registrations can describe an accepted manuscript, whose
        # public page precedes the Version of Record. Preserve that distinction.
        accepted_url = url.replace("/abstract/", "/accepted/", 1)
        return parse_publisher_text(_download(accepted_url), publication["doi"]), accepted_url


def enrich_journal_paper(paper: dict) -> dict:
    """Fill a journal abstract from its exact official article page."""
    result = dict(paper)
    result["publication"] = dict(paper["publication"])
    publication = result["publication"]
    parsed, actual_url = _read_publisher(paper)
    if parsed["abstract"]:
        result["abstract"] = parsed["abstract"]
        result["abstract_basis"] = "publisher_abstract"
        result["abstract_source_url"] = actual_url
        publication["url"] = actual_url
        publication["stage"] = parsed["publication_stage"]
        result["url"] = actual_url
    return result


def _crossref_article_abstract(paper: dict) -> tuple[str, str]:
    """Read the exact DOI record rather than citing an unread article page."""
    publication = paper["publication"]
    doi = normalize_doi(publication["doi"])
    endpoint = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")
    try:
        record = json.loads(_download(endpoint))["message"]
        if normalize_doi(record.get("DOI", "")) != doi:
            raise SourceError("Crossref article response returned a different DOI")
        abstract = re.sub(r"^Abstract\s+", "", _plain(record.get("abstract", "")))
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("Crossref exact-DOI record is malformed") from exc
    if not abstract or abstract.casefold() == _plain(paper.get("title", "")).casefold():
        raise SourceError("Crossref exact-DOI record contains no full abstract")
    return abstract, endpoint


def get_journal_text(paper: dict, max_chars: int = 60000) -> dict:
    """Read available public article text, otherwise return labeled evidence."""
    publication = paper.get("publication", {})
    if publication.get("stage") == "accepted_manuscript":
        raise AcceptedOnlyError("Accepted manuscript only; formal journal publication is unconfirmed")
    warnings = []
    source_url = paper.get("abstract_source_url") or publication.get("url", paper.get("url", ""))
    abstract_basis = paper.get("abstract_basis")
    try:
        parsed, actual_url = _read_publisher(paper)
        if parsed["publication_stage"] == "accepted_manuscript":
            raise AcceptedOnlyError("Accepted manuscript only; formal journal publication is unconfirmed")
        text = parsed["fulltext"]
        if len(text) >= 1000:
            text = parsed["abstract"] + "\n\n" + text
            warnings.append("Public publisher HTML excerpt; equations/figures may be incomplete and supplementary material was not reviewed.")
            if len(text) > max_chars:
                warnings.append(f"Article excerpt limited to {max_chars} characters.")
            return {"text": text[:max_chars], "evidence_level": "full_text_excerpt", "source_url": actual_url, "warnings": warnings}
        abstract = parsed["abstract"]
        if parsed["abstract"]:
            source_url = actual_url
            abstract_basis = "publisher_abstract"
    except AcceptedOnlyError:
        raise
    except (SourceError, KeyError) as exc:
        warnings.append(f"Public publisher text unavailable: {exc}")
        abstract = ""
    if not abstract:
        try:
            abstract, source_url = _crossref_article_abstract(paper)
            abstract_basis = "crossref_abstract"
        except SourceError as exc:
            warnings.append(f"Exact-DOI abstract unavailable: {exc}")
            # A collected publisher/Crossref abstract retains its provenance;
            # an arXiv abstract does not become a published-version abstract.
            if abstract_basis in {"publisher_feed_excerpt", "publisher_abstract", "crossref_abstract"}:
                abstract = paper.get("abstract", "")
            elif not str(paper.get("id", "")).startswith("doi:") and re.fullmatch(r"\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9.-]*/\d{7}", str(paper.get("id", ""))):
                abstract = paper.get("abstract", "")
                source_url = f"https://arxiv.org/abs/{paper['id']}v{paper.get('version', 1)}"
                warnings.append("Using the preprint abstract; equivalence to the published version has not been verified.")
            else:
                abstract = ""
    if abstract_basis == "publisher_feed_excerpt":
        warnings.append("Publisher-feed excerpt may be truncated or an editor summary; a complete abstract was unavailable.")
    if not abstract:
        raise SourceError("Journal paper has no usable abstract or public full text")
    warnings.append("Abstract-only evidence; proofs and publication decisions were not verified.")
    return {"text": abstract[:max_chars], "evidence_level": "abstract", "source_url": source_url, "warnings": warnings}


def fetch_journals(settings: dict, now: datetime | None = None, lookback_days: int | None = None) -> dict:
    """Collect each enabled journal independently, with explicit partial status.

    ``settings`` can be source.journals or a full profile. Sources default to the
    eight verified feeds. A small publication-date overlap catches late updates.
    Metadata supplement and abstract enrichment are bounded, sequential reads.
    """
    if "source" in settings:
        settings = settings.get("source", {}).get("journals", {})
    elif "journals" in settings:
        settings = settings["journals"]
    if not settings.get("enabled", True):
        return {"papers": [], "source_status": {}}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    days = lookback_days if lookback_days is not None else settings.get("lookback_days", 3)
    if type(days) is not int or not 1 <= days <= 30:
        raise SourceError("Journal lookback_days must be between 1 and 30")
    lower = current.date() - timedelta(days=days)
    upper = current.date()
    sources = settings.get("sources", [{"id": key, "enabled": True} for key in JOURNALS])
    output = {}
    statuses = {}
    for source in sources:
        journal_id = source.get("id", "")
        if journal_id not in JOURNALS:
            statuses[journal_id] = {"status": "skipped", "error": "Unknown journal source"}
            continue
        if not source.get("enabled", True):
            statuses[journal_id] = {"status": "disabled"}
            continue
        info = JOURNALS[journal_id]
        status = {"journal": info["journal"], "status": "ok", "window_start": lower.isoformat(),
                  "window_end": upper.isoformat(), "checked_at": current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "date_basis": "publication_date", "feeds": [], "warnings": []}
        records = {}
        successes = 0
        for feed_url in source.get("feed_urls", info["feed_urls"]):
            try:
                parsed = parse_journal_feed(_download(feed_url), journal_id, feed_url)
                successes += 1
                status["feeds"].append({"url": feed_url, "status": "ok", "research_records": len(parsed)})
                for paper in parsed:
                    records[paper["id"]] = paper
            except SourceError as exc:
                status["feeds"].append({"url": feed_url, "status": "skipped", "error": str(exc)})
                status["warnings"].append(str(exc))
        if settings.get("crossref_supplement", True):
            endpoint = f"https://api.crossref.org/journals/{info['issn']}/works?" + urllib.parse.urlencode({
                "filter": f"from-online-pub-date:{lower.isoformat()},until-online-pub-date:{upper.isoformat()},type:journal-article",
                "rows": 1000, "sort": "published-online", "order": "desc",
            })
            try:
                parsed, total = parse_crossref_records(_download(endpoint), journal_id, endpoint)
                successes += 1
                status["crossref"] = {"status": "ok", "total_results": total, "returned_research_records": len(parsed),
                                      "date_basis": "published-online"}
                if total > 1000:
                    status["warnings"].append("Crossref result cap reached; this journal window is incomplete.")
                for paper in parsed:
                    previous = records.get(paper["id"])
                    if previous:
                        if paper["abstract"]:
                            previous["abstract"] = paper["abstract"]
                            previous["abstract_basis"] = paper["abstract_basis"]
                            previous["abstract_source_url"] = paper["abstract_source_url"]
                        if not previous["authors"]:
                            previous["authors"] = paper["authors"]
                    else:
                        records[paper["id"]] = paper
            except SourceError as exc:
                status["crossref"] = {"status": "skipped", "error": str(exc)}
                status["warnings"].append(str(exc))
        recent = [p for p in records.values() if lower <= date.fromisoformat(p["published"][:10]) <= upper]
        recent.sort(key=lambda p: (p["published"], p["id"]), reverse=True)
        enrich_budget = settings.get("max_abstract_enrichments_per_source", 12)
        enrich_attempts = 0
        for paper in recent:
            # Avoid downloading hundreds of unrelated bioscience articles.
            # All metadata is still returned for the caller's research filter.
            if (paper["abstract_basis"] not in {"crossref_abstract", "publisher_abstract"}
                    and _TOPIC.search(paper["title"] + " " + paper["abstract"])
                    and enrich_attempts < enrich_budget):
                enrich_attempts += 1
                try:
                    paper = enrich_journal_paper(paper)
                except SourceError as exc:
                    paper["metadata_warnings"] = [str(exc)]
            output[paper["id"]] = paper
        if successes == 0:
            status["status"] = "skipped"
        elif status["warnings"]:
            status["status"] = "partial"
        status["collected"] = len(recent)
        status["abstract_enrichment_attempts"] = enrich_attempts
        status["coverage"] = "Rolling publisher RSS, supplemented by publisher-deposited online publication dates; delayed deposits or feed truncation can omit articles."
        statuses[journal_id] = status
    return {"papers": list(output.values()), "source_status": statuses}
