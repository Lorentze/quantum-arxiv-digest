from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from qradar.analysis import AnalysisError, ConfigurationError
from qradar.pipeline import (StateError, atomic_json, empty_state, load_state,
                            lookback_for, make_update, run_update, validate_profile)
from qradar.screening import screen_paper
from qradar.sources import SourceError


PROFILE = {
    "title": "Test", "source": {"categories": ["quant-ph"], "lookback_days": 7,
    "max_results": 100, "max_catchup_days": 30},
    "screening": {"min_keyword_score": 3, "min_ai_score": 70,
                  "max_analyses_per_run": 1, "prefetch_fulltext": False},
    "ai": {}, "topics": [{"id": "benchmarking", "weight": 4,
                           "keywords": ["SPAM", "randomized benchmarking"]}]
}
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def paper(paper_id: str, title="SPAM robust randomized benchmarking") -> dict:
    return {"id": paper_id, "version": 1, "title": title, "abstract": "A rigorous quantum protocol.",
            "url": f"https://arxiv.org/abs/{paper_id}", "published": "2026-10-08T00:00:00Z",
            "updated": "2026-10-08T00:00:00Z", "authors": ["Researcher"], "categories": ["quant-ph"]}


def analysis(score=90) -> dict:
    return {"status": "analyzed", "relevance_score": score, "summary": "摘要", "evidence_level": "abstract"}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.profile = copy.deepcopy(PROFILE)
        self.state = empty_state(self.profile)

    def update(self, fetched, scores=None):
        with patch("qradar.pipeline.fetch_recent", return_value=fetched), \
             patch("qradar.pipeline.analyze_paper", side_effect=scores or [analysis()]) as ai:
            result = make_update(self.state, self.profile, now=NOW)
            return result, ai

    def test_cap_preserves_backlog_even_after_original_fetch_window(self):
        result, ai = self.update([paper("2610.00001"), paper("2610.00002")])
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual([x["id"] for x in result["pending"]], ["2610.00002"])
        self.state = result
        result, ai = self.update([])
        self.assertEqual(len(result["papers"]), 2)
        self.assertEqual(result["pending"], [])
        self.assertEqual(ai.call_args[0][0]["id"], "2610.00002")

    def test_daily_overlap_and_crosslist_analyzed_once(self):
        self.state, _ = self.update([paper("2610.00001")])
        result, ai = self.update([paper("2610.00001"), paper("2610.00001")])
        self.assertEqual(len(result["papers"]), 1)
        ai.assert_not_called()

    def test_ai_reject_does_not_reconsume_budget(self):
        self.state, _ = self.update([paper("2610.00001")], [analysis(40)])
        self.assertEqual(self.state["papers"], [])
        self.assertEqual(self.state["screened"]["2610.00001"]["status"], "ai_rejected")
        result, ai = self.update([paper("2610.00001")])
        ai.assert_not_called()

    def test_late_analysis_failure_leaves_original_state_unchanged(self):
        original = copy.deepcopy(self.state)
        self.profile["screening"]["max_analyses_per_run"] = 2
        with patch("qradar.pipeline.fetch_recent", return_value=[paper("2610.00001"), paper("2610.00002")]), \
             patch("qradar.pipeline.analyze_paper", side_effect=[analysis(), AnalysisError("failed")]):
            with self.assertRaises(AnalysisError):
                make_update(self.state, self.profile, now=NOW)
        self.assertEqual(self.state, original)

    def test_source_failure_is_not_empty_success(self):
        with patch("qradar.pipeline.fetch_recent", side_effect=SourceError("unavailable")):
            with self.assertRaises(SourceError):
                make_update(self.state, self.profile, now=NOW)
        self.assertIsNone(self.state["last_successful_run"])

    def test_valid_empty_fetch_is_successful_and_makes_no_ai_call(self):
        result, ai = self.update([])
        self.assertEqual(result["last_successful_run"], "2026-10-09T00:00:00Z")
        self.assertEqual(result["last_run"]["fetched"], 0)
        ai.assert_not_called()

    def test_missing_key_fails_before_network_or_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
             patch("qradar.pipeline.fetch_recent") as fetch:
            state_file = Path(directory) / "state.json"
            atomic_json(state_file, self.state)
            before = state_file.read_bytes()
            with self.assertRaises(ConfigurationError):
                run_update("missing-profile", state_file, Path(directory) / "site", "https://example.com/radar")
            self.assertEqual(before, state_file.read_bytes())
            fetch.assert_not_called()

    def test_render_failure_prevents_commit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), \
             patch("qradar.pipeline.fetch_recent", return_value=[]), \
             patch("qradar.render.build_site", side_effect=OSError("render failed")):
            state_file = Path(directory) / "state.json"
            profile_file = Path(directory) / "profile.json"
            atomic_json(state_file, self.state)
            atomic_json(profile_file, self.profile)
            before = state_file.read_bytes()
            with self.assertRaises(OSError):
                run_update(profile_file, state_file, Path(directory) / "site", "https://example.com/radar")
            self.assertEqual(state_file.read_bytes(), before)

    def test_auto_catchup_and_explicit_gap(self):
        self.state["last_successful_run"] = (NOW - timedelta(days=14)).isoformat()
        self.assertEqual(lookback_for(self.state, self.profile, NOW), 16)
        self.state["last_successful_run"] = (NOW - timedelta(days=40)).isoformat()
        with self.assertRaises(SourceError):
            lookback_for(self.state, self.profile, NOW)
        self.assertEqual(lookback_for(self.state, self.profile, NOW, 45), 45)

    def test_invalid_state_and_demo_not_used_in_production(self):
        with tempfile.TemporaryDirectory() as directory:
            name = Path(directory) / "state.json"
            for change in ({"is_demo": True}, {"papers": [paper("2610.00001"), paper("2610.00001")]}, {"schema_version": 7}):
                state = copy.deepcopy(self.state)
                state.update(change)
                atomic_json(name, state)
                with self.assertRaises(StateError):
                    load_state(name, self.profile)

    def test_invalid_profile_budget(self):
        for value in (0, -1, True, 2.5):
            self.profile["screening"]["max_analyses_per_run"] = value
            with self.assertRaises(ConfigurationError):
                validate_profile(self.profile)


class ScreeningTests(unittest.TestCase):
    def test_word_boundaries_and_case(self):
        self.assertEqual(screen_paper(paper("2610.00001", "spam filtering in classical email"), PROFILE)["keyword_score"], 4)
        self.assertEqual(screen_paper(paper("2610.00001", "spamming"), PROFILE)["keyword_score"], 0)
        # An exclusion is decided by AI, not a hard keyword gate. A paper on
        # SPAM plus quantum effects must not be deleted by a guessed heuristic.

    def test_caps_repeated_topic_synonyms(self):
        item = screen_paper(paper("2610.00001"), PROFILE)
        self.assertEqual(item["topics"], ["benchmarking"])
        self.assertEqual(item["keyword_score"], 5)


if __name__ == "__main__":
    unittest.main()
