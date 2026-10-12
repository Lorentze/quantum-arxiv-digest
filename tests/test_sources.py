"""Source integrity tests: an incomplete run must never look like no papers."""

import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from qradar import sources


def atom(entries, total=None, start=0):
    total = len(entries) if total is None else total
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>{total}</opensearch:totalResults>
      <opensearch:startIndex>{start}</opensearch:startIndex>
      <opensearch:itemsPerPage>{len(entries)}</opensearch:itemsPerPage>
      {''.join(entries)}</feed>'''.encode()


def entry(paper_id="2610.00001", version=1, unversioned=False):
    identifier = paper_id if unversioned else f"{paper_id}v{version}"
    return f'''<entry><id>http://arxiv.org/abs/{identifier}</id>
      <title>Quantum\n  certification</title><summary>Explicit assumptions.</summary>
      <author><name>Alice Example</name></author>
      <published>2026-10-07T15:00:00Z</published>
      <updated>2026-10-08T15:00:00Z</updated>
      <link href="http://arxiv.org/pdf/{paper_id}v{version}" title="pdf"/>
      <category term="quant-ph"/><category term="quant-ph"/>
      </entry>'''


def rss(link="https://arxiv.org/abs/2610.00001", guid="oai:arXiv.org:2610.00001v3"):
    return f'''<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom"><channel><item>
      <title>Quantum certification</title><link>{link}</link><guid>{guid}</guid>
      <description>arXiv:2610.00001v3 Announce Type: replace
      Abstract: A &lt;b&gt;technical&lt;/b&gt; result.</description>
      <pubDate>Thu, 08 Oct 2026 00:00:00 -0400</pubDate>
      <category>quant-ph</category><dc:creator>Alice Example, Bob Example</dc:creator>
      <arxiv:announce_type>replace</arxiv:announce_type></item></channel></rss>'''


def scirate_page(records, next_page=None):
    # Fixture faithfully renders SciRate's MIT-licensed upstream Slim templates:
    # app/views/papers/_paper.html.slim, feeds/show.html.slim, and
    # feeds/_next_prev_links.html.slim at scirate/scirate main, read 2026-10-12.
    # The live Cloudflare 403 page is tested separately as a source failure.
    papers = []
    for paper_id, votes in records:
        papers.append(f'''<li class="paper tex2jax"><div class="row">
        <div class="title"><a href="/arxiv/{paper_id}">Quantum result</a></div>
        <div class="uid">Oct 09 2026 <a href="/arxiv/quant-ph">quant-ph</a>
        arXiv:{paper_id}v1</div>
        <div class="scite-toggle" data-paper-uid="{paper_id}">
        <button class="btn scite">Scite!</button><div class="scites-count">
        <a href="/arxiv/{paper_id}/scites" title="Number of Scites">
        <button class="btn btn-default count">{votes}</button></a></div>
        </div></div></li>''')
    content = ''.join(papers) if papers else '<p>No papers found!</p>'
    pagination = (f'<a class="next_page" rel="next" href="/arxiv/quant-ph?range=3&amp;page={next_page}">Next</a>'
                  if next_page else '<span class="next_page disabled">Next</span>')
    return f'''<html><div id="feed-page"><header class="feed-header">
    <h1>Quantum Physics (quant-ph)</h1></header><div class="paperlist">
    <ul class="papers">{content}</ul>{pagination}</div><nav class="timenav">
    <h3>Date Published</h3><p>Oct 08 2026 &mdash; Oct 10 2026<br>UTC</p>
    <a href="/arxiv/quant-ph?range=3">3d</a></nav></div></html>'''.encode()


class SciRateTests(unittest.TestCase):
    def test_real_selectors_window_and_strict_threshold(self):
        page = scirate_page([("2610.00001", 11), ("2610.00002", 10)])
        parsed = sources.parse_scirate(page)
        self.assertEqual(parsed["window_start"], "2026-10-08")
        self.assertEqual(parsed["window_end"], "2026-10-10")
        with patch.object(sources, "_download", side_effect=[page, atom([entry("2610.00001", version=3)])]) as download:
            papers = sources.fetch_scirate(now=datetime(2026, 10, 12, tzinfo=timezone.utc))
        self.assertEqual([paper["id"] for paper in papers], ["2610.00001"])
        self.assertEqual(papers[0]["version"], 3)
        self.assertEqual(papers[0]["scirate"]["votes"], 11)
        self.assertEqual(papers[0]["scirate"]["observed_at"], "2026-10-12T00:00:00Z")
        self.assertEqual(parse_qs(urlsplit(download.call_args_list[1].args[0]).query)["id_list"], ["2610.00001"])

    def test_empty_has_explicit_feed_evidence_and_needs_no_arxiv(self):
        for page in (scirate_page([]), scirate_page([("2610.00001", 10)])):
            with patch.object(sources, "_download", return_value=page) as download:
                self.assertEqual(sources.fetch_scirate(), [])
                self.assertEqual(download.call_count, 1)

    def test_blocked_changed_or_wrong_window_is_not_empty(self):
        invalid = [b'<html><title>Just a moment...</title></html>',
                   scirate_page([]).replace(b'No papers found!', b''),
                   scirate_page([]).replace(b'Oct 08 2026', b'Oct 07 2026'),
                   scirate_page([]).replace(b'Quantum Physics (quant-ph)', b'Computer Science (cs.CC)'),
                   scirate_page([("2610.00001", 11)]).replace(b'>11</button>', b'>unknown</button>'),
                   scirate_page([("2610.00001", 11)]).replace(b'data-paper-uid="2610.00001"', b'data-paper-uid="2610.00002"')]
        for data in invalid:
            with self.subTest(data=data[:120]), self.assertRaises(sources.SourceError):
                sources.parse_scirate(data)

    def test_complete_pagination_is_pinned_to_same_publication_window(self):
        pages = [scirate_page([("2610.00001", 40)], next_page=2),
                 scirate_page([("2610.00002", 20)]),
                 atom([entry("2610.00001"), entry("2610.00002")])]
        with patch.object(sources, "_download", side_effect=pages) as download:
            result = sources.fetch_scirate()
        self.assertEqual(len(result), 2)
        query = parse_qs(urlsplit(download.call_args_list[1].args[0]).query)
        self.assertEqual(query, {"range": ["3"], "date": ["2026-10-10"], "page": ["2"]})

    def test_existing_identity_preserves_version_without_metadata_refetch(self):
        known = sources.parse_atom(atom([entry("2610.00001", version=1)]))
        with patch.object(sources, "_download", return_value=scirate_page([("2610.00001", 19)])) as download:
            result = sources.fetch_scirate(known_papers=known)
        self.assertEqual(result[0]["version"], 1)
        self.assertEqual(download.call_count, 1)
        self.assertNotIn("scirate", known[0])

    def test_missing_official_metadata_or_paging_fault_aborts(self):
        invalid = [
            [scirate_page([("2610.00001", 11)]), atom([])],
            [scirate_page([("2610.00001", 11)], next_page=2), scirate_page([("2610.00001", 11)])],
            [scirate_page([("2610.00001", 11)], next_page=3)],
            [scirate_page([("2610.00001", 11)], next_page=2), scirate_page([]).replace(b'Oct 08', b'Oct 09').replace(b'Oct 10', b'Oct 11')],
        ]
        for pages in invalid:
            with self.subTest(pages=len(pages)), patch.object(sources, "_download", side_effect=pages), self.assertRaises(sources.SourceError):
                sources.fetch_scirate()

    def test_budget_is_explicit_and_untrusted_pagination_never_downloaded(self):
        with patch.object(sources, "_download", return_value=scirate_page([("2610.00001", 11), ("2610.00002", 12)])), self.assertRaises(sources.ResultLimitExceeded):
            sources.fetch_scirate(max_results=1)
        bad_link = scirate_page([("2610.00001", 11)], next_page=2).replace(b'href="/arxiv/quant-ph?range=3&amp;page=2"', b'href="https://evil.example/path?range=3&amp;page=2"')
        with self.assertRaises(sources.SourceError):
            sources.parse_scirate(bad_link)

    def test_downloader_allows_only_documented_scirate_feed(self):
        for url in ('https://scirate.com/api/scite/2610.00001',
                    'https://scirate.com/arxiv/quant-ph?range=7',
                    'https://scirate.com/arxiv/quant-ph?range=3&redirect=https://evil.example'):
            with self.subTest(url=url), self.assertRaises(sources.SourceError):
                sources._download(url)


class AtomTests(unittest.TestCase):
    def test_unversioned_id_uses_versioned_document(self):
        paper = sources.parse_atom(atom([entry(version=3, unversioned=True)]))[0]
        self.assertEqual(paper["version"], 3)
        self.assertEqual(paper["title"], "Quantum certification")
        self.assertEqual(paper["categories"], ["quant-ph"])
        self.assertEqual(paper["pdf_url"], "https://arxiv.org/pdf/2610.00001v3")
        self.assertEqual(paper["published"], "2026-10-07T15:00:00Z")

    def test_source_errors_are_not_empty_feeds(self):
        invalid = [b"<html>blocked</html>", b"not XML", atom([
            '<entry><id>http://arxiv.org/api/errors</id><title>Error</title>'
            '<summary>Invalid query</summary></entry>'
        ])]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(sources.SourceError):
                sources.parse_atom(data)

    def test_mismatched_document_id_rejected(self):
        data = atom([entry(unversioned=True).replace(
            "pdf/2610.00001v1", "pdf/2610.00002v1"
        )])
        with self.assertRaisesRegex(sources.SourceError, "disagree"):
            sources.parse_atom(data)

    def test_missing_timezone_is_not_guessed(self):
        data = atom([entry().replace("2026-10-07T15:00:00Z", "2026-10-07T15:00:00")])
        with self.assertRaisesRegex(sources.SourceError, "published"):
            sources.parse_atom(data)


class PaginationTests(unittest.TestCase):
    def fetch(self, pages, max_results=10):
        with patch.object(sources, "PAGE_SIZE", 2), patch.object(
            sources, "_download", side_effect=pages
        ) as download:
            papers = sources.fetch_recent(
                ["quant-ph", "cs.IT"], 7, max_results,
                now=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc),
            )
        return papers, download

    def test_every_page_is_fetched_and_query_is_encoded(self):
        pages = [
            atom([entry("2610.00001"), entry("2610.00002")], total=5, start=0),
            atom([entry("2610.00003"), entry("2610.00004")], total=5, start=2),
            atom([entry("2610.00005")], total=5, start=4),
        ]
        papers, download = self.fetch(pages)
        self.assertEqual(len(papers), 5)
        queries = [parse_qs(urlsplit(call.args[0]).query) for call in download.call_args_list]
        self.assertEqual([q["start"][0] for q in queries], ["0", "2", "4"])
        self.assertEqual(queries[0]["search_query"], [
            "(cat:quant-ph OR cat:cs.IT) AND submittedDate:[202610020100 TO 202610090100]"
        ])

    def test_valid_zero_total_is_empty(self):
        self.assertEqual(self.fetch([atom([], total=0)])[0], [])

    def test_budget_breach_is_explicit(self):
        with self.assertRaises(sources.ResultLimitExceeded):
            self.fetch([atom([entry()], total=20)], max_results=5)

    def test_paging_faults_rejected(self):
        first = atom([entry("2610.00001"), entry("2610.00002")], total=3)
        for last in [
            atom([], total=3, start=2),
            atom([entry("2610.00003")], total=3, start=1),
            atom([entry("2610.00003")], total=4, start=2),
            atom([entry("2610.00001")], total=3, start=2),
        ]:
            with self.subTest(last=last), self.assertRaises(sources.SourceError):
                self.fetch([first, last])


class RssTests(unittest.TestCase):
    def test_real_feed_guid_preserves_replacement_version(self):
        paper = sources.parse_rss(rss())[0]
        self.assertEqual(paper["version"], 3)
        self.assertEqual(paper["abstract"], "A technical result.")
        self.assertEqual(paper["published"], "2026-10-08T04:00:00Z")
        self.assertEqual(paper["date_basis"], "announcement")
        self.assertEqual(paper["announcement_type"], "replace")
        self.assertEqual(paper["authors"], ["Alice Example", "Bob Example"])

    def test_disagreeing_identifiers_rejected(self):
        with self.assertRaisesRegex(sources.SourceError, "disagree"):
            sources.parse_rss(rss(guid="oai:arXiv.org:2610.00002v3"))

    def test_title_required(self):
        with self.assertRaisesRegex(sources.SourceError, "no title"):
            sources.parse_rss(rss().replace("<title>Quantum certification</title>", ""))


class FullTextTests(unittest.TestCase):
    paper = {"id": "2610.00001", "version": 3, "abstract": "Supplied abstract."}

    def test_html_preserves_tex_and_omits_ui_and_hidden_text(self):
        text = sources.extract_html_text('''<html><nav>UI</nav>
          <article class="ltx_document"><h1>Result</h1>
          <p>Visible <math alttext="F_e = 1-r"><mi>duplicate</mi></math>.</p>
          <span hidden>Hidden</span><script>Code</script></article></html>''')
        self.assertIn("F_e = 1-r", text)
        for omitted in ["UI", "duplicate", "Hidden", "Code"]:
            self.assertNotIn(omitted, text)

    def test_abstract_page_is_not_full_text(self):
        with self.assertRaises(sources.SourceError):
            sources.extract_html_text("<html><main>Abstract only</main></html>")

    def test_pinned_html_excerpt_reports_truncation(self):
        html = ("<article>" + "Result. " * 150 + "</article>").encode()
        with patch.object(sources, "_download", return_value=html) as download:
            result = sources.get_paper_text(self.paper, max_chars=700)
        self.assertEqual(len(result["text"]), 700)
        self.assertEqual(result["evidence_level"], "full_text_excerpt")
        self.assertEqual(download.call_args.args[0], "https://arxiv.org/html/2610.00001v3")
        self.assertTrue(any("truncated" in warning for warning in result["warnings"]))

    def test_failed_full_text_falls_back_with_disclosed_limits(self):
        with patch.object(sources, "_download", side_effect=sources.SourceError("Unavailable")), \
             patch.dict(sys.modules, {"pypdf": None}):
            result = sources.get_paper_text(self.paper)
        self.assertEqual(result["evidence_level"], "abstract")
        self.assertEqual(result["text"], self.paper["abstract"])
        self.assertEqual(result["source_url"], "https://arxiv.org/abs/2610.00001v3")
        self.assertTrue(any("proofs have not been checked" in w for w in result["warnings"]))

    def test_pdf_fallback_reports_omitted_pages(self):
        page = SimpleNamespace(extract_text=lambda: "PDF theorem. " * 100)
        fake_pypdf = SimpleNamespace(PdfReader=lambda _: SimpleNamespace(pages=[page] * 3))
        with patch.object(sources, "_download", side_effect=[
            sources.SourceError("No HTML"), b"%PDF-1.7\nfake"
        ]), patch.dict(sys.modules, {"pypdf": fake_pypdf}):
            result = sources.get_paper_text(self.paper, max_chars=700)
        self.assertEqual(result["evidence_level"], "full_text_excerpt")
        self.assertEqual(result["source_url"], "https://arxiv.org/pdf/2610.00001v3")
        self.assertEqual(len(result["text"]), 700)
        self.assertTrue(any("later sections may be omitted" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
