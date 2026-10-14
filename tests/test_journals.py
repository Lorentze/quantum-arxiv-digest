"""Journal source identity, evidence, date, and failure-isolation checks."""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from qradar import journals
from qradar.sources import SourceError


def rdf(title="Quantum certification", doi="10.1038/s41534-026-12345-x", excerpt=None, published="2026-10-13"):
    excerpt = title if excerpt is None else excerpt
    return f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
      xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <item><title>{title}</title><link>https://www.nature.com/articles/{doi.split('/')[1]}</link>
      <dc:date>{published}</dc:date><prism:doi>{doi}</prism:doi>
      <dc:creator>Alice</dc:creator><content:encoded>&lt;p&gt;Published online; doi:{doi}&lt;/p&gt;{excerpt}</content:encoded>
      </item></rdf:RDF>'''.encode()


def crossref(records, total=None):
    return json.dumps({"message": {"items": records, "total-results": len(records) if total is None else total}}).encode()


def record(doi="10.1038/s41534-026-12345-x", title="Quantum certification", abstract="", published="2026-10-13"):
    r = {"DOI": doi, "title": [title], "type": "journal-article", "ISSN": ["2056-6387"],
         "published-online": {"date-parts": [[int(x) for x in published.split('-')]]},
         "deposited": {"date-parts": [[2026, 10, 14]]}}
    if abstract:
        r["abstract"] = abstract
    return r


class JournalTests(unittest.TestCase):
    def test_rdf_dates_doi_and_no_title_as_abstract(self):
        p = journals.parse_journal_feed(rdf(), "npj_quantum_information")[0]
        self.assertEqual(p["id"], "doi:10.1038/s41534-026-12345-x")
        self.assertEqual(p["abstract"], "")
        self.assertEqual(p["abstract_basis"], "missing")
        self.assertEqual(p["publication"]["published"], "2026-10-13")
        self.assertEqual(p["publication"]["date_basis"], "publisher_rss")
        self.assertEqual(p["sources"], ["npj_quantum_information"])

    def test_rss2_science_news_and_abstract_excerpt(self):
        data = b'''<rss version="2.0"><channel><item><title>Science news</title>
        <link>https://www.science.org/content/article/news</link><pubDate>Tue, 13 Oct 2026 09:00:00 -0400</pubDate></item>
        <item><title>High-fidelity quantum gates | Science</title><link>https://www.science.org/doi/10.1126/science.test123</link>
        <pubDate>Tue, 13 Oct 2026 09:00:00 -0400</pubDate><description>A new protocol...</description></item></channel></rss>'''
        papers = journals.parse_journal_feed(data, "science")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["title"], "High-fidelity quantum gates")
        self.assertEqual(papers[0]["published"], "2026-10-13T13:00:00Z")
        self.assertEqual(papers[0]["abstract_basis"], "publisher_feed_excerpt")

    def test_atom_supported(self):
        data = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Quantum benchmark</title>
        <link href="https://journals.aps.org/prl/abstract/10.1103/ab12-cd34"/>
        <published>2026-10-13T12:00:00Z</published><summary>A protocol.</summary></entry></feed>'''
        p = journals.parse_journal_feed(data, "prl")[0]
        self.assertEqual(p["id"], "doi:10.1103/ab12-cd34")

    def test_research_filters_and_malformed_xml(self):
        self.assertEqual(journals.parse_journal_feed(rdf(title="Author Correction: Quantum certification"), "npj_quantum_information"), [])
        self.assertEqual(journals.parse_journal_feed(rdf(doi="10.1038/d41586-026-12345-x"), "nature"), [])
        for data in (b"<html>Access denied</html>", b"not xml"):
            with self.assertRaises(SourceError):
                journals.parse_journal_feed(data, "nature")

    def test_crossref_uses_publication_and_ignores_deposit_date(self):
        r = record(abstract="<jats:title>Abstract</jats:title><jats:p>A full scientific abstract.</jats:p>")
        parsed, total = journals.parse_crossref_records(crossref([r]), "npj_quantum_information")
        self.assertEqual(parsed[0]["published"], "2026-10-13")
        self.assertEqual(parsed[0]["abstract"], "A full scientific abstract.")
        self.assertEqual(parsed[0]["abstract_basis"], "crossref_abstract")
        r.pop("published-online")
        self.assertEqual(journals.parse_crossref_records(crossref([r]), "npj_quantum_information")[0], [])

    def test_each_source_failure_isolated(self):
        settings = {"sources": [{"id": "nature"}, {"id": "npj_quantum_information"}],
                    "crossref_supplement": False, "max_abstract_enrichments_per_source": 0}
        with patch.object(journals, "_download", side_effect=[SourceError("HTTP 403"), rdf(excerpt="A quantum result.")]):
            result = journals.fetch_journals(settings, datetime(2026, 10, 14, tzinfo=timezone.utc))
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(result["source_status"]["nature"]["status"], "skipped")
        self.assertEqual(result["source_status"]["npj_quantum_information"]["status"], "ok")

    def test_overlap_supplement_dedupe_and_publication_date_window(self):
        settings = {"sources": [{"id": "npj_quantum_information"}], "max_abstract_enrichments_per_source": 0}
        metadata = crossref([record(abstract="Complete abstract."),
                             record("10.1038/s41534-026-oldxx-x", published="2026-09-01"),
                             record("10.1038/s41534-026-newxx-x", published="2026-10-12")])
        with patch.object(journals, "_download", side_effect=[rdf(), metadata]) as download:
            result = journals.fetch_journals(settings, datetime(2026, 10, 14, tzinfo=timezone.utc))
        self.assertEqual(len(result["papers"]), 2)
        p = next(p for p in result["papers"] if p["id"].endswith("12345-x"))
        self.assertEqual(p["abstract"], "Complete abstract.")
        self.assertIn("from-online-pub-date", download.call_args_list[1].args[0])
        self.assertNotIn("deposit-date", download.call_args_list[1].args[0])

    def test_crossref_failure_is_partial_and_rss_survives(self):
        settings = {"sources": [{"id": "npj_quantum_information"}], "max_abstract_enrichments_per_source": 0}
        with patch.object(journals, "_download", side_effect=[rdf(), SourceError("HTTP 429")]):
            result = journals.fetch_journals(settings, datetime(2026, 10, 14, tzinfo=timezone.utc))
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(result["source_status"]["npj_quantum_information"]["status"], "partial")

    def test_exact_publisher_doi_abstract_and_no_generic_blurb(self):
        doi = "10.1103/ab12-cd34"
        data = f'''<html><meta name="citation_doi" content="{doi}"/><meta name="description" content="Editor blurb"/>
        <div id="abstract-section-content"><p>Full scientific abstract.</p></div>
        <div id="fulltext-section-content"><p>Article scientific content.</p></div></html>'''
        result = journals.parse_publisher_text(data, doi)
        self.assertEqual(result["abstract"], "Full scientific abstract.")
        self.assertEqual(result["fulltext"], "Article scientific content.")
        with self.assertRaises(SourceError):
            journals.parse_publisher_text(data, "10.1103/wrong-id")
        no_abstract = data.replace('<div id="abstract-section-content"><p>Full scientific abstract.</p></div>', '')
        self.assertEqual(journals.parse_publisher_text(no_abstract, doi)["abstract"], "")

    def test_publisher_blocked_falls_back_with_evidence_warning(self):
        p = journals.parse_journal_feed(rdf(excerpt="A quantum result..."), "npj_quantum_information")[0]
        with patch.object(journals, "_download", side_effect=SourceError("HTTP 403")):
            result = journals.get_journal_text(p)
        self.assertEqual(result["evidence_level"], "abstract")
        self.assertTrue(any("truncated" in w for w in result["warnings"]))
        p["abstract"] = ""
        with patch.object(journals, "_download", side_effect=SourceError("HTTP 403")), self.assertRaises(SourceError):
            journals.get_journal_text(p)

    def test_publisher_blocked_uses_exact_doi_abstract(self):
        p = journals.parse_journal_feed(rdf(), "npj_quantum_information")[0]
        exact = json.dumps({"message": record(abstract="<jats:p>Complete DOI-specific abstract.</jats:p>")}).encode()
        with patch.object(journals, "_download", side_effect=[SourceError("HTTP 403"), exact]) as download:
            reading = journals.get_journal_text(p)
        self.assertEqual(reading["text"], "Complete DOI-specific abstract.")
        self.assertEqual(reading["source_url"], "https://api.crossref.org/works/10.1038/s41534-026-12345-x")
        self.assertEqual(download.call_args_list[1].args[0], reading["source_url"])

    def test_unverified_preprint_never_cites_unread_publisher(self):
        p = journals.parse_journal_feed(rdf(), "npj_quantum_information")[0]
        p.update(id="2610.00001", version=2, abstract="Preprint abstract.", url="https://arxiv.org/abs/2610.00001")
        p.pop("abstract_basis")
        p.pop("abstract_source_url")
        with patch.object(journals, "_download", side_effect=SourceError("HTTP 403")):
            reading = journals.get_journal_text(p)
        self.assertEqual(reading["source_url"], "https://arxiv.org/abs/2610.00001v2")
        self.assertTrue(any("equivalence" in warning for warning in reading["warnings"]))

    def test_accepted_only_is_detected_and_not_analyzed_as_publication(self):
        doi = "10.1103/ab12-cd34"
        data = f'''<script>window.apsPageData={{"pageType":"accepted_paper","content":{{"id":"{doi}"}}}};</script>
        <div id="abstract-section-content"><p>Accepted abstract.</p></div>'''.encode()
        p = journals._paper("prx_quantum", doi, "Quantum resources", "2026-10-13", "crossref_published-online")
        with patch.object(journals, "_download", side_effect=[SourceError("HTTP 404"), data]):
            enriched = journals.enrich_journal_paper(p)
        self.assertEqual(enriched["publication"]["stage"], "accepted_manuscript")
        self.assertIn("/accepted/", enriched["abstract_source_url"])
        with self.assertRaises(journals.AcceptedOnlyError):
            journals.get_journal_text(enriched)
        with patch.object(journals, "_download", side_effect=[SourceError("HTTP 404"), data]), self.assertRaises(journals.AcceptedOnlyError):
            journals.get_journal_text(p)

    def test_footer_and_recommendations_are_not_scientific_fulltext(self):
        data = '''<meta name="citation_doi" content="10.1038/s41534-026-12345-x"/>
        <meta name="dc.description" content="The actual abstract."/>
        <section data-title="Author information">Many author affiliations.</section>
        <section data-title="Inline Recommendations">Other articles, unrelated bodies.</section>'''
        result = journals.parse_publisher_text(data, "10.1038/s41534-026-12345-x")
        self.assertEqual(result["abstract"], "The actual abstract.")
        self.assertEqual(result["fulltext"], "")

    def test_allowlist_prevents_credentials_and_unrelated_hosts(self):
        for url in ("https://evil.example/feed", "https://user:pass@www.nature.com/nature.rss",
                    "http://feeds.aps.org/rss/recent/prx.xml", "https://api.crossref.org/journals/unknown/works",
                    "https://www.science.org/anything", "https://idp.nature.com/authorize"):
            with self.subTest(url=url), self.assertRaises(SourceError):
                journals._download(url)


if __name__ == "__main__":
    unittest.main()
