import copy
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from qradar.render import build_site


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.output = Path(self.directory.name)
        self.paper = {
            "id": "2209.09267", "version": 2, "title": "Syndrome learning & logical errors",
            "authors": ["A. Author", "B. Author"], "abstract": "A < B, with Pauli noise.",
            "url": "https://arxiv.org/abs/2209.09267", "pdf_url": "https://arxiv.org/pdf/2209.09267v2",
            "published": "2022-09-19T00:00:00Z", "updated": "2023-01-01T00:00:00Z",
            "selected_at": "2026-10-09T01:00:00Z", "topics": ["logical_qec"], "categories": ["quant-ph"],
            "analysis": {"summary": "学习 syndrome statistics，保留噪声假设。", "problem": "估计 logical error。",
                         "setting": "Independent Pauli components.", "main_results": ["逻辑错误可识别。"],
                         "techniques": ["Fourier methods"], "assumptions": ["Correctability"],
                         "limitations": ["Finite-sample bounds are not stated."], "relevance": ["用于 logical benchmarking。"],
                         "follow_up": ["如何给出样本复杂度？"], "relevance_score": 91,
                         "evidence_level": "abstract", "status": "analyzed", "model": "test-model",
                         "source_url": "https://arxiv.org/abs/2209.09267", "analyzed_at": "2026-10-09T01:01:00Z"},
        }
        self.state = {"papers": [self.paper], "is_demo": False, "profile_title": "PRIVATE_PROFILE_SENTINEL · 量子信息理论",
                      "topic_labels": {"logical_qec": "逻辑 QEC"}, "last_successful_run": "2026-10-09T01:02:00Z",
                      "last_run": {"fetched": 100, "selected": 1}}

    def tearDown(self):
        self.directory.cleanup()

    def test_portable_site_and_subpath_feed(self):
        build_site(self.state, self.output, "https://user.github.io/quantum-arxiv-digest")
        for filename in ("index.html", "snapshot.html", "style.css", "app.js", "papers.json", "feed.xml", "feeds/logical_qec.xml", "articles/2209.09267.html"):
            self.assertTrue((self.output / filename).is_file(), filename)
        root = ET.parse(self.output / "feed.xml").getroot()
        channel = root.find("channel")
        self.assertEqual(channel.find("{http://www.w3.org/2005/Atom}link").get("href"), "https://user.github.io/quantum-arxiv-digest/feed.xml")
        item = channel.find("item")
        self.assertEqual(item.findtext("guid"), "urn:arxiv:2209.09267")
        self.assertEqual(item.findtext("link"), "https://user.github.io/quantum-arxiv-digest/articles/2209.09267.html")
        self.assertEqual(item.findtext("pubDate"), "Fri, 09 Oct 2026 01:00:00 GMT")
        self.assertIn("2022-09-19", item.findtext("description"))
        self.assertEqual(item.findtext("category"), "逻辑 QEC")
        self.assertIn("A &lt; B, with Pauli noise.", item.findtext("description"))
        self.assertIn('href="https://arxiv.org/abs/2209.09267"', item.findtext("description"))
        self.assertIn("Read the Chinese AI report", item.findtext("description"))
        self.assertNotIn(self.paper["analysis"]["summary"], item.findtext("description"))
        self.assertEqual(channel.findtext("language"), "en")
        article = (self.output / "articles/2209.09267.html").read_text()
        self.assertIn('href="https://user.github.io/quantum-arxiv-digest/articles/2209.09267.html"', article)
        self.assertIn('href="../style.css"', article)
        self.assertIn(self.paper["analysis"]["summary"], article)
        self.assertIn("核心技术路线", article)
        self.assertIn("结论成立的条件", article)
        self.assertNotIn("<script", article)
        index = (self.output / "index.html").read_text()
        self.assertIn('href="./style.css"', index)
        self.assertIn('src="./app.js"', index)
        self.assertIn('href="https://user.github.io/quantum-arxiv-digest/feed.xml"', index)

    def test_stable_guid_on_version_change_and_reanalysis(self):
        build_site(self.state, self.output, "http://localhost:8000")
        original = ET.parse(self.output / "feed.xml").findtext("channel/item/guid")
        self.paper["version"] = 3
        self.paper["analysis"]["analyzed_at"] = "2026-10-10T01:00:00Z"
        build_site(self.state, self.output, "http://localhost:8000")
        channel = ET.parse(self.output / "feed.xml").find("channel")
        self.assertEqual(channel.findtext("item/guid"), original)
        self.assertEqual(channel.findtext("item/pubDate"), "Fri, 09 Oct 2026 01:00:00 GMT")

    def test_html_payload_is_text_and_unsafe_urls_are_removed(self):
        payload = '</script><img src=x onerror="alert(1)">&\x00'
        self.paper["title"] = payload
        self.paper["abstract"] = payload
        self.paper["analysis"]["summary"] = payload
        self.paper["url"] = "javascript:alert(1)"
        self.paper["pdf_url"] = "data:text/html,boom"
        self.paper["analysis"]["source_url"] = "javascript:alert(1)"
        self.state["profile_title"] = payload
        self.state["api_key"] = "MUST-NOT-BE-PUBLISHED"
        self.paper["analysis"]["token_usage"] = {"private_marker": "MUST-NOT-BE-PUBLISHED"}
        build_site(self.state, self.output, "https://user.github.io/project")
        data = json.loads((self.output / "papers.json").read_text())
        self.assertEqual(data["papers"][0]["url"], "")
        self.assertEqual(data["papers"][0]["analysis"]["source_url"], "")
        self.assertNotIn("MUST-NOT-BE-PUBLISHED", (self.output / "papers.json").read_text())
        item = ET.parse(self.output / "feed.xml").find("channel/item")
        self.assertEqual(item.findtext("title"), payload.replace("\x00", ""))
        description = item.findtext("description")
        self.assertNotIn("<img", description)
        self.assertIn("&lt;img", description)
        self.assertNotIn("javascript:", description)
        index = (self.output / "index.html").read_text()
        self.assertNotIn('<img src=x', index)
        self.assertNotIn("&lt;img", index)  # Private profile is not interpolated.
        article = (self.output / "articles/2209.09267.html").read_text()
        self.assertNotIn('<img src=x', article)
        self.assertIn("&lt;img", article)
        self.assertNotIn("javascript:", article)
        snapshot = (self.output / "snapshot.html").read_text()
        self.assertIn("\\u003c/script\\u003e", snapshot)
        self.assertNotIn('</script><img src=x', snapshot)
        script = (self.output / "app.js").read_text()
        self.assertNotIn("innerHTML", script)
        self.assertIn("textContent", script)

    def test_topic_ids_cannot_escape_output_and_empty_archive_is_real(self):
        self.paper["topics"] = ["../../outside", "量子学习"]
        build_site(self.state, self.output, "https://user.github.io/project/")
        data = json.loads((self.output / "papers.json").read_text())
        for path in data["topic_feeds"].values():
            self.assertNotIn("..", path)
            self.assertTrue((self.output / path).is_file())
        self.state["papers"] = []
        build_site(self.state, self.output, "https://user.github.io/project/")
        self.assertEqual(ET.parse(self.output / "feed.xml").findall("channel/item"), [])
        self.assertEqual(list((self.output / "feeds").glob("*.xml")), [])
        self.assertEqual(list((self.output / "articles").glob("*.html")), [])
        self.assertFalse(json.loads((self.output / "papers.json").read_text())["is_demo"])

    def test_demo_is_labelled_in_rss_and_data(self):
        self.state["is_demo"] = True
        self.paper["date_basis"] = "rss_announcement"
        build_site(self.state, self.output, "http://localhost:8000")
        channel = ET.parse(self.output / "feed.xml").find("channel")
        self.assertIn("阅读预览", channel.findtext("title"))
        self.assertIn("not an automatically updated digest", channel.findtext("item/description"))
        self.assertIn("arXiv announcement", channel.findtext("item/description"))
        self.assertIn("真实论文阅读预览", (self.output / "articles/2209.09267.html").read_text())

    def test_public_reader_has_no_private_profile_or_dashboard(self):
        build_site(self.state, self.output, "https://user.github.io/project")
        for path in (self.output / "index.html", self.output / "snapshot.html", self.output / "papers.json", self.output / "feed.xml", self.output / "articles/2209.09267.html"):
            content = path.read_text()
            self.assertNotIn("PRIVATE_PROFILE_SENTINEL", content)
            self.assertNotIn("与你研究", content)
        index = (self.output / "index.html").read_text()
        for obsolete in ("status-panel", "profile-title", "total-count", "run-summary", "scope-box"):
            self.assertNotIn(obsolete, index)
        self.assertIn('id="local-workbench" hidden', index)

    def test_arbitrary_ids_get_safe_article_paths(self):
        self.paper["id"] = "../../outside"
        build_site(self.state, self.output, "https://user.github.io/project")
        public = json.loads((self.output / "papers.json").read_text())["papers"][0]
        self.assertRegex(public["article_path"], r"^articles/paper-[a-f0-9]{16}\.html$")
        self.assertTrue((self.output / public["article_path"]).is_file())
        self.assertEqual(ET.parse(self.output / "feed.xml").findtext("channel/item/link"), "https://user.github.io/project/" + public["article_path"])

    def test_snapshot_has_inline_report_navigation_without_article_dependency(self):
        build_site(self.state, self.output, "https://user.github.io/project")
        snapshot = (self.output / "snapshot.html").read_text()
        self.assertIn("window.QRADAR_SNAPSHOT=true", snapshot)
        self.assertIn("snapshotReport(paper)", snapshot)
        self.assertIn('report.open = true', snapshot)
        self.assertIn("核心技术路线", snapshot)
        self.assertNotIn('src="./app.js"', snapshot)
        script = (self.output / "app.js").read_text()
        self.assertIn("articlePath(paper.article_path)", script)
        self.assertNotIn("title.append(externalLink", script)

    def test_scirate_is_removed_and_legacy_records_default_to_arxiv(self):
        self.paper["sources"] = ["arxiv", "scirate", "private-name"]
        self.paper["scirate"] = {"votes": 12, "range_days": 3, "url": "https://scirate.com/arxiv/2209.09267", "private_name": "MUST-NOT-BE-PUBLISHED"}
        build_site(self.state, self.output, "https://user.github.io/project")
        public = json.loads((self.output / "papers.json").read_text())["papers"][0]
        self.assertEqual(public["sources"], ["arxiv"])
        self.assertNotIn("scirate", public)
        self.assertNotIn("SciRate", (self.output / "articles/2209.09267.html").read_text())
        self.paper.pop("sources")
        build_site(self.state, self.output, "https://user.github.io/project")
        self.assertEqual(json.loads((self.output / "papers.json").read_text())["papers"][0]["sources"], ["arxiv"])

    def test_journal_only_identity_source_feeds_and_publication_assessment(self):
        self.paper.update({"id": "doi:10.1038/s41567-026-00001-2", "version": 1,
                           "url": "https://www.nature.com/articles/s41567-026-00001-2", "pdf_url": "",
                           "published": "2026-10-14T00:00:00Z", "sources": ["nature_physics"],
                           "publication": {"journal_id": "nature_physics", "journal": "Nature Physics",
                                           "doi": "10.1038/s41567-026-00001-2", "published": "2026-10-14T00:00:00Z",
                                           "date_basis": "publisher_rss", "url": "https://www.nature.com/articles/s41567-026-00001-2"}})
        self.paper["analysis"]["publication_significance"] = ["给出可检验的资源界，并联系量子计算的可行性。"]
        build_site(self.state, self.output, "https://user.github.io/project")
        data = json.loads((self.output / "papers.json").read_text())
        public = data["papers"][0]
        self.assertEqual(public["sources"], ["nature_physics"])
        self.assertEqual(data["profile_title"], "Quantum Paper Digest")
        self.assertEqual(data["source_feeds"]["nature_physics"], "feeds/sources/nature_physics.xml")
        self.assertEqual(len(data["source_labels"]), 9)
        for source, relative in data["source_feeds"].items():
            self.assertTrue((self.output / relative).is_file(), source)
        item = ET.parse(self.output / "feed.xml").find("channel/item")
        self.assertEqual(item.findtext("guid"), "urn:doi:10.1038/s41567-026-00001-2")
        self.assertIn("Nature Physics", [c.text for c in item.findall("category")])
        self.assertIn("Journal publication", item.findtext("description"))
        self.assertIn("DOI:10.1038/s41567-026-00001-2", item.findtext("description"))
        self.assertNotIn("arXiv:", item.findtext("description"))
        article = (self.output / public["article_path"]).read_text()
        self.assertIn("为何适合该期刊（分析判断）", article)
        self.assertIn("不代表编辑或审稿人的实际决定", article)
        self.assertIn("Nature Physics ↗", article)
        self.assertNotIn("arXiv ↗", article)
        self.assertNotIn("First submitted", article)
        self.assertIn('id="source-filter"', (self.output / "index.html").read_text())
        self.assertIn("paperSources(paper).includes(state.source)", (self.output / "app.js").read_text())
        self.assertEqual(len(ET.parse(self.output / data["source_feeds"]["nature_physics"]).findall("channel/item")), 1)
        self.assertEqual(ET.parse(self.output / data["source_feeds"]["arxiv"]).findall("channel/item"), [])

    def test_journal_match_preserves_arxiv_guid_and_independent_provenance(self):
        self.paper["sources"] = ["arxiv", "prx_quantum"]
        self.paper["publication"] = {"journal_id": "prx_quantum", "journal": "Untrusted journal name",
                                     "doi": "10.1103/example", "published": "2026-10-14T00:00:00Z",
                                     "date_basis": "publisher_rss", "url": "https://journals.aps.org/prxquantum/abstract/10.1103/example"}
        self.paper["analysis"]["publication_significance"] = ["<img src=x onerror=alert(1)> 理论界与物理问题的联系。"]
        self.paper["publication_review"] = {"evidence_level": "abstract", "source_url": "https://journals.aps.org/prxquantum/abstract/10.1103/example",
                                             "analyzed_at": "2026-10-14T01:00:00Z", "model": "test", "doi": "10.1103/example", "private": "DO-NOT-PUBLISH"}
        build_site(self.state, self.output, "https://user.github.io/project")
        public = json.loads((self.output / "papers.json").read_text())["papers"][0]
        self.assertEqual(public["publication"]["journal"], "PRX Quantum")
        self.assertNotIn("private", public["publication_review"])
        item = ET.parse(self.output / "feed.xml").find("channel/item")
        self.assertEqual(item.findtext("guid"), "urn:arxiv:2209.09267")
        self.assertIn("First submitted", item.findtext("description"))
        self.assertIn("Journal publication", item.findtext("description"))
        article = (self.output / "articles/2209.09267.html").read_text()
        self.assertIn("arXiv ↗", article)
        self.assertIn("PRX Quantum ↗", article)
        self.assertIn("本节依据论文摘要", article)
        self.assertIn("2026-10-14T01:00:00Z", article)
        self.assertNotIn("<img src=x", article)
        self.assertIn("&lt;img src=x", article)
        self.assertNotIn("DO-NOT-PUBLISH", article)
        self.paper["publication"]["url"] = "javascript:alert(1)"
        self.paper["publication_review"]["source_url"] = "javascript:alert(1)"
        build_site(self.state, self.output, "https://user.github.io/project")
        self.assertNotIn("javascript:", (self.output / "articles/2209.09267.html").read_text())

    def test_journal_first_matching_preprint_keeps_doi_and_separate_arxiv_link(self):
        self.paper.update({"id": "doi:10.1126/example", "sources": ["science", "arxiv"], "arxiv_id": "2209.09267", "arxiv_version": 3,
                           "arxiv_url": "https://arxiv.org/abs/2209.09267v3", "url": "https://www.science.org/doi/10.1126/example",
                           "publication": {"journal_id": "science", "doi": "10.1126/example", "url": "https://www.science.org/doi/10.1126/example",
                                           "published": "2026-10-14T00:00:00Z"}})
        build_site(self.state, self.output, "https://user.github.io/project")
        public = json.loads((self.output / "papers.json").read_text())["papers"][0]
        article = (self.output / public["article_path"]).read_text()
        self.assertIn("DOI:10.1126/example", article)
        self.assertNotIn("arXiv:doi:", article)
        self.assertIn('href="https://arxiv.org/abs/2209.09267v3"', article)
        self.assertIn("Science ↗", article)
        self.assertEqual(ET.parse(self.output / "feed.xml").findtext("channel/item/guid"), "urn:doi:10.1126/example")

    def test_publisher_feed_excerpt_is_not_labelled_as_original_abstract(self):
        self.paper.update({"id": "doi:10.1103/example", "sources": ["prx"], "abstract_basis": "publisher_feed_excerpt",
                           "abstract_source_url": "https://feeds.aps.org/rss/recent/prx.xml",
                           "publication": {"journal_id": "prx", "doi": "10.1103/example", "published": "2026-10-14",
                                           "url": "https://journals.aps.org/prx/abstract/10.1103/example"}})
        build_site(self.state, self.output, "https://user.github.io/project")
        public = json.loads((self.output / "papers.json").read_text())["papers"][0]
        self.assertEqual(public["abstract_basis"], "publisher_feed_excerpt")
        self.assertEqual(public["abstract_source_url"], "https://feeds.aps.org/rss/recent/prx.xml")
        article = (self.output / public["article_path"]).read_text()
        self.assertIn("<summary>Publisher feed excerpt</summary>", article)
        self.assertNotIn("<summary>Original abstract</summary>", article)
        self.assertIn("not the full original abstract", article)
        description = ET.parse(self.output / "feed.xml").findtext("channel/item/description")
        self.assertIn("Publisher feed excerpt", description)
        self.assertIn("not the full original abstract", description)
        script = (self.output / "app.js").read_text()
        self.assertIn('paper.abstract_basis === "publisher_feed_excerpt"', script)
        self.paper["abstract_basis"] = "publisher_abstract"
        self.paper["abstract_source_url"] = "javascript:alert(1)"
        build_site(self.state, self.output, "https://user.github.io/project")
        public = json.loads((self.output / "papers.json").read_text())["papers"][0]
        self.assertEqual(public["abstract_source_url"], "")
        self.assertIn("<summary>Original abstract</summary>", (self.output / public["article_path"]).read_text())
        self.assertNotIn("Publisher feed excerpt", ET.parse(self.output / "feed.xml").findtext("channel/item/description"))

    def test_reject_nonabsolute_or_insecure_site_urls(self):
        for url in ("relative/path", "javascript:alert(1)", "http://example.com", "https://user:password@example.com", "https://example.com/?key=secret", "https://example.com/#x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                build_site(self.state, self.output, url)

    def test_collection_and_analysis_timestamps_are_distinct(self):
        self.state.update({"last_collection_run": "2026-10-09T01:02:00Z",
                           "last_analysis_run": "2026-10-08T02:00:00Z",
                           "last_run": {"fetched": 50, "keyword_candidates": 12, "analyzed": 0, "selected": 0, "pending": 12}})
        candidate = copy.deepcopy(self.paper)
        candidate["id"] = "2610.09999"
        candidate.pop("analysis")
        self.state["pending"] = [candidate]
        build_site(self.state, self.output, "https://user.github.io/project")
        data = json.loads((self.output / "papers.json").read_text())
        self.assertEqual(data["last_collection_run"], "2026-10-09T01:02:00Z")
        self.assertEqual(data["last_analysis_run"], "2026-10-08T02:00:00Z")
        self.assertEqual(data["last_run"]["analyzed"], 0)
        self.assertEqual(data["last_run"]["pending"], 12)
        self.assertEqual(len(data["papers"]), 1)
        self.assertNotIn("pending", data)
        self.assertNotIn("2610.09999", (self.output / "papers.json").read_text())
        channel = ET.parse(self.output / "feed.xml").find("channel")
        self.assertEqual(channel.findtext("lastBuildDate"), "Thu, 08 Oct 2026 02:00:00 GMT")

    def test_collection_only_does_not_claim_an_ai_update(self):
        self.state.update({"last_collection_run": "2026-10-09T01:02:00Z", "last_analysis_run": None})
        # Defensive guard even if an unanalyzed candidate is accidentally put
        # in the selected-paper list by an external state editor.
        candidate = copy.deepcopy(self.paper)
        candidate["id"] = "2610.09999"
        candidate["analysis"] = {"model": "configured-model", "status": "pending"}
        self.state["papers"].append(candidate)
        build_site(self.state, self.output, "https://user.github.io/project")
        data = json.loads((self.output / "papers.json").read_text())
        self.assertIsNone(data["last_analysis_run"])
        self.assertEqual(len(data["papers"]), 1)
        self.assertNotIn("configured-model", (self.output / "papers.json").read_text())
        self.assertIsNone(ET.parse(self.output / "feed.xml").find("channel/lastBuildDate"))

    def test_legacy_run_timestamp_falls_back_for_both_phases(self):
        build_site(self.state, self.output, "https://user.github.io/project")
        data = json.loads((self.output / "papers.json").read_text())
        self.assertEqual(data["last_collection_run"], self.state["last_successful_run"])
        self.assertEqual(data["last_analysis_run"], self.state["last_successful_run"])


if __name__ == "__main__":
    unittest.main()

