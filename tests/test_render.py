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
        self.state = {"papers": [self.paper], "is_demo": False, "profile_title": "Guoding Liu · 量子信息理论",
                      "topic_labels": {"logical_qec": "逻辑 QEC"}, "last_successful_run": "2026-10-09T01:02:00Z",
                      "last_run": {"fetched": 100, "selected": 1}}

    def tearDown(self):
        self.directory.cleanup()

    def test_portable_site_and_subpath_feed(self):
        build_site(self.state, self.output, "https://user.github.io/quantum-arxiv-digest")
        for filename in ("index.html", "snapshot.html", "style.css", "app.js", "papers.json", "feed.xml", "feeds/logical_qec.xml"):
            self.assertTrue((self.output / filename).is_file(), filename)
        root = ET.parse(self.output / "feed.xml").getroot()
        channel = root.find("channel")
        self.assertEqual(channel.find("{http://www.w3.org/2005/Atom}link").get("href"), "https://user.github.io/quantum-arxiv-digest/feed.xml")
        item = channel.find("item")
        self.assertEqual(item.findtext("guid"), "urn:arxiv:2209.09267")
        self.assertEqual(item.findtext("pubDate"), "Fri, 09 Oct 2026 01:00:00 GMT")
        self.assertIn("2022-09-19", item.findtext("description"))
        self.assertEqual(item.findtext("category"), "逻辑 QEC")
        self.assertIn("必要假设", item.findtext("description"))
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
        self.assertIn("&lt;img", index)
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
        self.assertFalse(json.loads((self.output / "papers.json").read_text())["is_demo"])

    def test_demo_is_labelled_in_rss_and_data(self):
        self.state["is_demo"] = True
        self.paper["date_basis"] = "rss_announcement"
        build_site(self.state, self.output, "http://localhost:8000")
        channel = ET.parse(self.output / "feed.xml").find("channel")
        self.assertIn("阅读预览", channel.findtext("title"))
        self.assertIn("不是每日自动筛选结果", channel.findtext("item/description"))
        self.assertIn("arXiv 公告日期", channel.findtext("item/description"))

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
