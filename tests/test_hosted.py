import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qradar.analysis import AnalysisError
from qradar.pipeline import (StateError, atomic_json, collect_update, empty_state,
                            import_analyses, import_analysis_update, make_update, read_json)
from test_pipeline import NOW, PROFILE, paper


def item(paper_id="2610.00001", score=90):
    return {"id": paper_id, "version": 1, "analysis": {
        "summary": "测试阅读摘要。", "problem": "估计量子操作的质量。", "setting": "固定访问与噪声模型。",
        "main_results": ["摘要中明确声称的结果。"], "techniques": [], "assumptions": ["明确的假设。"],
        "limitations": [], "relevance": ["与目标问题有关。"], "follow_up": [], "relevance_score": score,
        "evidence_level": "abstract", "source_url": f"https://arxiv.org/abs/{paper_id}",
        "model": "ChatGPT hosted test fixture", "analyzed_at": "2026-10-09T00:01:00Z", "status": "analyzed"}}


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.profile = copy.deepcopy(PROFILE)
        self.state = empty_state(self.profile)

    def collected(self):
        with patch("qradar.pipeline.fetch_recent", return_value=[paper("2610.00001"), paper("2610.00002")]), \
             patch("qradar.pipeline.analyze_paper") as ai:
            result = make_update(self.state, self.profile, now=NOW, analyze_candidates=False)
            ai.assert_not_called()
            return result

    def test_collection_is_no_key_and_never_publishes_pending_as_analysis(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
             patch("qradar.pipeline.fetch_recent", return_value=[paper("2610.00001")]), \
             patch("qradar.pipeline.analyze_paper") as ai:
            state = Path(directory) / "state.json"
            profile = Path(directory) / "profile.json"
            site = Path(directory) / "site"
            atomic_json(profile, self.profile)
            result = collect_update(profile, state, site, "https://example.org/radar")
            self.assertEqual(len(result["pending"]), 1)
            self.assertEqual(result["papers"], [])
            self.assertEqual(read_json(site / "papers.json")["papers"], [])
            self.assertNotIn("last_analysis_run", result)
            self.assertIn("last_collection_run", result)
            ai.assert_not_called()

    def test_external_analysis_preserves_collection_and_updates_public_archive(self):
        state = self.collected()
        result = import_analyses(state, self.profile, {"analyses": [item()]}, now=NOW)
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual([p["id"] for p in result["pending"]], ["2610.00002"])
        self.assertEqual(result["last_collection_run"], state["last_collection_run"])
        self.assertIn("last_analysis_run", result)
        self.assertIn("本条分析仅依据", result["papers"][0]["analysis"]["limitations"][-1])
        self.assertEqual(state["papers"], [])

    def test_unrelated_external_analysis_rejected_without_fabricating_paper(self):
        state = self.collected()
        for entry in (item("2610.99999"), {**item(), "version": 2}):
            with self.assertRaises(StateError):
                import_analyses(state, self.profile, {"analyses": [entry]}, now=NOW)

    def test_duplicate_or_invalid_late_item_makes_batch_transactional(self):
        state = self.collected()
        original = copy.deepcopy(state)
        with self.assertRaises(StateError):
            import_analyses(state, self.profile, {"analyses": [item(), item()]}, now=NOW)
        bad = item("2610.00002")
        bad["analysis"]["main_results"] = "not a list"
        with self.assertRaises(AnalysisError):
            import_analyses(state, self.profile, {"analyses": [item(), bad]}, now=NOW)
        self.assertEqual(state, original)

    def test_no_key_import_can_reject_low_relevance(self):
        state = self.collected()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("qradar.pipeline.analyze_paper") as ai:
            result = import_analyses(state, self.profile, {"analyses": [item(score=40)]}, now=NOW)
            ai.assert_not_called()
        self.assertEqual(result["papers"], [])
        self.assertEqual(result["screened"]["2610.00001"]["status"], "ai_rejected")
        self.assertEqual(len(result["pending"]), 1)

    def test_import_requires_honest_provenance_and_correct_source(self):
        state = self.collected()
        for key, value in (("status", "preview"), ("source_url", "https://arxiv.org/abs/2610.99999"),
                           ("source_url", "https://example.org/2610.00001"), ("model", ""),
                           ("analyzed_at", "2026-10-09T00:00:00"), ("evidence_level", "full_text")):
            entry = item()
            entry["analysis"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(AnalysisError):
                import_analyses(state, self.profile, {"analyses": [entry]}, now=NOW)

    def test_failed_render_preserves_import_state_file(self):
        with tempfile.TemporaryDirectory() as directory, patch("qradar.render.build_site", side_effect=OSError("failed")):
            directory = Path(directory)
            state = directory / "state.json"
            profile = directory / "profile.json"
            batch = directory / "batch.json"
            atomic_json(state, self.collected())
            atomic_json(profile, self.profile)
            atomic_json(batch, {"analyses": [item()]})
            before = state.read_bytes()
            with self.assertRaises(OSError):
                import_analysis_update(batch, profile, state, directory / "site", "https://example.org/radar")
            self.assertEqual(before, state.read_bytes())


if __name__ == "__main__":
    unittest.main()
