from __future__ import annotations

import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from qradar.local import MAX_BODY_BYTES, LocalServer, analysis_request
from qradar.pipeline import atomic_json, empty_state, read_json
from qradar.sources import SourceError
from test_hosted import item
from test_pipeline import PROFILE, paper


class LocalTests(unittest.TestCase):
    """Exercise the actual loopback handler; all arXiv calls use metadata fixtures."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.profile = copy.deepcopy(PROFILE)
        self.profile["screening"]["max_analyses_per_run"] = 2
        self.profile_path = self.directory / "profile.json"
        self.state_path = self.directory / "state.json"
        self.output = self.directory / "site"
        atomic_json(self.profile_path, self.profile)
        atomic_json(self.state_path, empty_state(self.profile))
        self.server = LocalServer(self.profile_path, self.state_path, self.output, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, method, path, value=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        send_headers = {}
        body = None
        if method == "POST":
            send_headers = {"Origin": self.server.site_url, "Content-Type": "application/json"}
            body = json.dumps(value if value is not None else {}).encode("utf-8")
        if headers:
            for key, value in headers.items():
                if value is None:
                    send_headers.pop(key, None)
                else:
                    send_headers[key] = value
        try:
            connection.request(method, path, body=body, headers=send_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def collect(self, count=1):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
             patch("qradar.pipeline.fetch_recent", return_value=[paper(f"2610.{i:05d}") for i in range(1, count + 1)]), \
             patch("qradar.pipeline.analyze_paper") as ai:
            status, _, body = self.request("POST", "/api/collect")
            self.assertEqual(status, 200, body)
            ai.assert_not_called()
            return json.loads(body)

    def test_boot_builds_loopback_rss_and_status_without_api(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertTrue(value["local"])
        self.assertFalse(value["model_available"])
        self.assertEqual(value["pending"], 0)
        self.assertIsNone(value["last_analysis_run"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, headers, body = self.request("GET", "/feed.xml")
        self.assertEqual(status, 200)
        self.assertIn("application/rss+xml", headers["Content-Type"])
        channel = ET.fromstring(body).find("channel")
        self.assertEqual(channel.findtext("link"), self.server.site_url + "/")
        self.assertEqual(channel.findall("item"), [])
        self.assertEqual(self.request("GET", "/")[0], 200)

    def test_collect_exports_real_queue_without_fabricated_analysis(self):
        value = self.collect(count=2)
        self.assertEqual(value["pending"], 2)
        self.assertEqual(value["published"], 0)
        self.assertEqual(value["last_run"]["analyzed"], 0)
        _, headers, body = self.request("GET", "/api/analysis-request")
        self.assertIn("attachment", headers["Content-Disposition"])
        text = body.decode("utf-8")
        self.assertIn("2610.00001", text)
        self.assertIn("2610.00002", text)
        self.assertIn("A rigorous quantum protocol.", text)
        self.assertIn("核查了证明", text)
        self.assertIn("本批 2 篇", text)
        self.assertNotIn("OPENAI_API_KEY", text)
        self.assertEqual(read_json(self.output / "papers.json")["papers"], [])

    def test_import_publishes_validated_analysis_and_desktop_feed(self):
        self.collect(count=2)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("qradar.pipeline.analyze_paper") as ai:
            status, _, body = self.request("POST", "/api/import-analysis", {"analyses": [item()]})
            ai.assert_not_called()
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["last_analysis_batch"], {"analyzed": 1, "selected": 1, "pending": 1})
        _, _, body = self.request("GET", "/feed.xml")
        entries = ET.fromstring(body).findall("channel/item")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].findtext("guid"), "urn:arxiv:2610.00001")
        self.assertEqual(entries[0].findtext("link"), self.server.site_url + "/articles/2610.00001.html")
        self.assertNotIn("本条分析仅依据", entries[0].findtext("description"))
        status, headers, body = self.request("GET", "/articles/2610.00001.html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("本条分析仅依据", body.decode("utf-8"))
        self.assertIn("主要贡献与结果", body.decode("utf-8"))
        _, _, body = self.request("GET", "/api/status")
        value = json.loads(body)
        self.assertIsNotNone(value["last_collection_run"])
        self.assertIsNotNone(value["last_analysis_run"])

    def test_bad_import_and_arxiv_failure_leave_archive_intact(self):
        self.collect(count=2)
        before = self.state_path.read_bytes()
        invalid = {**item("2610.00002"), "version": 2}
        status, _, body = self.request("POST", "/api/import-analysis", {"analyses": [item(), invalid]})
        self.assertEqual(status, 400, body)
        self.assertEqual(self.state_path.read_bytes(), before)
        with patch("qradar.pipeline.fetch_recent", side_effect=SourceError("arXiv unavailable")):
            status, _, body = self.request("POST", "/api/collect")
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"], "arXiv unavailable")
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_cross_origin_or_missing_origin_cannot_modify_state(self):
        before = self.state_path.read_bytes()
        with patch("qradar.local.collect_update") as update:
            for origin in ("https://attacker.example", "null", None, "http://localhost:" + str(self.server.server_port)):
                with self.subTest(origin=origin):
                    status, _, body = self.request("POST", "/api/collect", headers={"Origin": origin})
                    self.assertEqual(status, 403, body)
            update.assert_not_called()
        self.assertEqual(before, self.state_path.read_bytes())
        self.assertEqual(self.request("OPTIONS", "/api/collect")[0], 405)

    def test_dns_rebinding_host_and_path_escape_are_rejected(self):
        for host in ("attacker.example", "localhost:" + str(self.server.server_port), "127.0.0.1"):
            self.assertEqual(self.request("GET", "/api/status", headers={"Host": host})[0], 403)
        (self.directory / "secret.txt").write_text("secret", encoding="utf-8")
        (self.output / "private.txt").write_text("private", encoding="utf-8")
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/config/profile.json", "/private.txt", "/%00", "/articles/../index.html", "/articles/%2e%2e/index.html", "/articles/private.html"):
            self.assertEqual(self.request("GET", path)[0], 404, path)
        (self.output / "feeds" / "secret.xml").symlink_to(self.directory / "secret.txt")
        self.assertEqual(self.request("GET", "/feeds/secret.xml")[0], 404)
        (self.output / "articles" / "2610.12345.html").symlink_to(self.directory / "secret.txt")
        self.assertEqual(self.request("GET", "/articles/2610.12345.html")[0], 404)

    def test_oversize_or_wrong_content_type_rejected_before_mutation(self):
        with patch("qradar.local.collect_update") as update:
            status, _, body = self.request("POST", "/api/collect", headers={"Content-Length": str(MAX_BODY_BYTES + 1)})
            self.assertEqual(status, 413, body)
            self.assertEqual(self.request("POST", "/api/collect", headers={"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(self.request("POST", "/api/collect", {"state_path": "/elsewhere"})[0], 400)
            update.assert_not_called()

    def test_export_caps_queue_and_keeps_pending_order(self):
        state = empty_state(self.profile)
        state["pending"] = [{**paper(f"2610.{i:05d}"), "first_seen_at": "2026-10-08T00:00:00Z", "keyword_score": i}
                            for i in range(1, 35)]
        self.profile["screening"]["max_analyses_per_run"] = 100
        text = analysis_request(self.profile, state)
        self.assertIn("当前队列 34 篇；本批 30 篇", text)
        self.assertIn("2610.00034", text)
        self.assertNotIn("2610.00001", text)
        self.assertEqual(len(state["pending"]), 34)

    def test_status_preserves_analysis_only_clock_and_export_honors_reading_scope(self):
        state = empty_state(self.profile)
        state["last_successful_run"] = "2026-10-08T00:00:00Z"
        state["last_analysis_run"] = "2026-10-09T01:00:00Z"
        atomic_json(self.state_path, state)
        _, _, body = self.request("GET", "/api/status")
        self.assertEqual(json.loads(body)["last_analysis_run"], "2026-10-09T01:00:00Z")
        state["pending"] = [paper("2610.00001")]
        text = analysis_request(self.profile, state)
        self.assertIn("只有元数据和摘要，没有正文附件", text)
        self.assertIn("关闭了正文预取", text)
        self.assertIn("不要用后续替换版本代替", text)
        self.profile["screening"]["prefetch_fulltext"] = True
        self.profile["ai"]["max_source_chars"] = 12345
        text = analysis_request(self.profile, state)
        self.assertIn("最多使用 12345 个字符的正文节选", text)


if __name__ == "__main__":
    unittest.main()
