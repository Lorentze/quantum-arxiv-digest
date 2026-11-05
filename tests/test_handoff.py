import copy
import hashlib
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from qradar.hosted import analysis_health, collection_request_due, export_hosted_queue, prepare_inbox
from qradar.pipeline import StateError, atomic_json, empty_state, import_analyses
from qradar.analysis import AnalysisError
from test_hosted import item
from test_pipeline import NOW, PROFILE, paper


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.profile = copy.deepcopy(PROFILE)
        self.profile["screening"]["max_analyses_per_run"] = 6
        self.state = empty_state(self.profile)
        self.state["pending"] = [paper("2610.00001"), paper("2610.00002")]
        self.state["last_collection_run"] = "2026-10-09T00:00:00Z"
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inbox, self.receipts = self.root / "inbox", self.root / "receipts"
        self.inbox.mkdir()
        self.receipts.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_large_state_exports_exact_queue_in_small_reconstructible_chunks(self):
        self.state["pending"] = []
        for index in range(150):
            value = paper(f"2610.{index:05d}")
            value["abstract"] = "Quantum circuit results. " * 500
            value["extra_metadata"] = {"arbitrary": [index, "保留原始元数据"]}
            self.state["pending"].append(value)
        self.assertGreater(len(json.dumps(self.state).encode()), 1024 * 1024)
        before = copy.deepcopy(self.state)
        manifest = export_hosted_queue(self.state, self.profile, self.root / "queue", now=NOW)
        reconstructed = []
        for chunk in manifest["chunks"]:
            path = self.root / "queue" / chunk["path"]
            self.assertEqual(path.stat().st_size, chunk["bytes"])
            self.assertLessEqual(chunk["bytes"], 100 * 1024)
            data = json.loads(path.read_text())
            self.assertEqual(len(data["candidates"]), chunk["count"])
            reconstructed.extend(x["paper"] for x in data["candidates"])
        self.assertEqual({p["id"]: p for p in reconstructed}, {p["id"]: p for p in before["pending"]})
        self.assertEqual(self.state, before)

    def test_recommended_batch_is_recent_with_journal_reservation(self):
        self.state["pending"] = []
        for index in range(10):
            value = paper(f"2610.{index:05d}")
            value["published"] = f"2026-10-{index + 10:02d}T00:00:00Z"
            value["keyword_score"] = index
            self.state["pending"].append(value)
        for value in self.state["pending"][:3]:
            value["publication"] = {"doi": "10.1103/" + value["id"], "published": value["published"]}
        manifest = export_hosted_queue(self.state, self.profile, self.root / "queue", now=NOW)
        ids = [x["id"] for x in manifest["recommended_batch"]]
        self.assertEqual(ids, ["2610.00002", "2610.00001", "2610.00009", "2610.00008", "2610.00007", "2610.00006"])
        for record in manifest["recommended_batch"]:
            chunk = json.loads((self.root / "queue" / record["chunk"]).read_text())
            self.assertIn(record["id"], [x["paper"]["id"] for x in chunk["candidates"]])

    def test_date_only_journal_publications_sort_before_older_timestamps(self):
        self.state["pending"][0]["publication"] = {"doi": "10.1103/new", "published": "2026-10-08"}
        self.state["pending"][1]["publication"] = {"doi": "10.1103/old", "published": "2026-10-07T12:00:00Z"}
        manifest = export_hosted_queue(self.state, self.profile, self.root / "queue", now=NOW)
        self.assertEqual([p["id"] for p in manifest["recommended_batch"]], ["2610.00001", "2610.00002"])

    def test_health_separates_fresh_collection_from_stale_analysis(self):
        self.state["last_analysis_run"] = "2026-10-06T00:00:00Z"
        status = analysis_health(self.state, now=NOW)
        self.assertEqual(status["status"], "stale")
        self.assertTrue(status["stale"])
        self.assertEqual(status["age_hours"], 72)
        self.state["last_analysis_run"] = "2026-10-08T00:00:00Z"
        self.assertFalse(analysis_health(self.state, now=NOW)["stale"])
        self.state["pending"] = []
        self.state["last_analysis_run"] = None
        self.assertFalse(analysis_health(self.state, now=NOW)["stale"])

    def test_current_beijing_day_request_triggers_only_when_unattempted(self):
        requests = self.root / "requests"
        atomic_json(requests / "2026-10-09.json", {"date": "2026-10-09", "source": "all"})
        self.state["last_collection_run"] = "2026-10-08T00:00:00Z"
        self.assertTrue(collection_request_due(self.state, requests, now=NOW))
        # UTC previous evening is already today's date in Beijing.
        self.state["last_collection_run"] = "2026-10-08T17:00:00Z"
        self.assertFalse(collection_request_due(self.state, requests, now=NOW))

    def test_old_request_is_ignored_and_failed_attempt_prevents_retry(self):
        requests = self.root / "requests"
        atomic_json(requests / "2026-10-08.json", {"date": "2026-10-08", "source": "all"})
        self.assertFalse(collection_request_due(self.state, requests, now=NOW))
        atomic_json(requests / "2026-10-09.json", {"date": "2026-10-09", "source": "all"})
        self.state["last_collection_run"] = "2026-10-08T00:00:00Z"
        self.state["arxiv_source_status"] = {"status": "failed", "checked_at": "2026-10-09T00:00:00Z"}
        self.assertFalse(collection_request_due(self.state, requests, now=NOW))

    def test_invalid_current_request_is_rejected_even_if_collection_is_fresh(self):
        requests = self.root / "requests"
        path = requests / "2026-10-09.json"
        invalid = [{"date": "2026-10-09", "source": "arxiv"},
                   {"date": "2026-10-08", "source": "all"},
                   {"date": "2026-10-09", "source": "all", "force": True},
                   {"date": 20261009, "source": "all"}]
        for payload in invalid:
            atomic_json(path, payload)
            with self.subTest(payload=payload), self.assertRaises(StateError):
                collection_request_due(self.state, requests, now=NOW)
        path.write_text("not JSON")
        with self.assertRaises(StateError):
            collection_request_due(self.state, requests, now=NOW)

    def test_inbox_preserves_archive_collection_unrelated_fields_and_guid_identity(self):
        original = import_analyses(self.state, self.profile, {"analyses": [item()]}, now=NOW)
        original["custom_metadata"] = {"must": "survive"}
        original["papers"][0]["selected_at"] = "2026-10-01T00:00:00Z"
        original["papers"][0]["custom_guid"] = "original-guid"
        original["pending"].append(paper("2610.00003"))
        saved_paper = copy.deepcopy(original["papers"][0])
        atomic_json(self.inbox / "new.json", {"analyses": [item("2610.00002")]})
        result, receipts = prepare_inbox(original, self.profile, self.inbox, self.receipts, now=NOW + timedelta(hours=1))
        self.assertEqual(result["last_collection_run"], original["last_collection_run"])
        self.assertEqual(result["custom_metadata"], original["custom_metadata"])
        self.assertEqual(next(p for p in result["papers"] if p["id"] == saved_paper["id"]), saved_paper)
        self.assertEqual([p["id"] for p in result["pending"]], ["2610.00003"])
        self.assertEqual(receipts["new.json"]["analyzed"], 1)
        self.assertEqual(receipts["new.json"]["selected"], 1)
        self.assertEqual(receipts["new.json"]["rejected"], 0)
        self.assertFalse((self.receipts / "new.json").exists())
        self.assertEqual(len(original["papers"]), 1)

    def test_receipt_replay_is_idempotent_and_changed_payload_is_rejected(self):
        path = self.inbox / "run.json"
        atomic_json(path, {"analyses": [item()]})
        result, receipts = prepare_inbox(self.state, self.profile, self.inbox, self.receipts, now=NOW)
        for name, value in receipts.items():
            atomic_json(self.receipts / name, value)
        replayed, new = prepare_inbox(result, self.profile, self.inbox, self.receipts, now=NOW + timedelta(hours=1))
        self.assertEqual(replayed, result)
        self.assertEqual(new, {})
        path.write_text(path.read_text() + "\n")
        with self.assertRaises(StateError):
            prepare_inbox(result, self.profile, self.inbox, self.receipts, now=NOW)

    def test_malformed_later_batch_does_not_mutate_any_state_or_receipts(self):
        before = copy.deepcopy(self.state)
        atomic_json(self.inbox / "a-valid.json", {"analyses": [item()]})
        bad = item("2610.00002")
        bad["analysis"]["summary"] = ""
        atomic_json(self.inbox / "b-invalid.json", {"analyses": [bad]})
        with self.assertRaises(AnalysisError):
            prepare_inbox(self.state, self.profile, self.inbox, self.receipts, now=NOW)
        self.assertEqual(self.state, before)
        self.assertEqual(list(self.receipts.iterdir()), [])

    def test_existing_same_version_is_validated_then_skipped(self):
        result = import_analyses(self.state, self.profile, {"analyses": [item()]}, now=NOW)
        atomic_json(self.inbox / "duplicate.json", {"analyses": [item()]})
        updated, receipts = prepare_inbox(result, self.profile, self.inbox, self.receipts, now=NOW)
        self.assertEqual(updated, result)
        self.assertEqual(receipts["duplicate.json"]["skipped_already_analyzed"], ["2610.00001"])
        bad = item()
        bad["analysis"]["summary"] = ""
        atomic_json(self.inbox / "duplicate.json", {"analyses": [bad]})
        with self.assertRaises(AnalysisError):
            prepare_inbox(result, self.profile, self.inbox, self.receipts, now=NOW)

    def test_unknown_ids_and_bad_reviews_are_not_silently_dropped(self):
        atomic_json(self.inbox / "unknown.json", {"analyses": [item("2610.99999")]})
        with self.assertRaises(StateError):
            prepare_inbox(self.state, self.profile, self.inbox, self.receipts, now=NOW)
        (self.inbox / "unknown.json").unlink()
        atomic_json(self.inbox / "review.json", {"publication_reviews": [{"id": "unknown"}]})
        with self.assertRaises(AnalysisError):
            prepare_inbox(self.state, self.profile, self.inbox, self.receipts, now=NOW)

    def test_queue_retains_exact_publication_review_and_saved_paper(self):
        result = import_analyses(self.state, self.profile, {"analyses": [item()]}, now=NOW)
        result["papers"][0]["publication"] = {"doi": "10.1103/test", "published": "2026-10-08"}
        review = {"id": "2610.00001", "doi": "10.1103/test", "first_seen_at": "2026-10-09T00:00:00Z"}
        result["publication_reviews_pending"] = [review]
        manifest = export_hosted_queue(result, self.profile, self.root / "queue", now=NOW)
        records = []
        for entry in manifest["chunks"]:
            records += json.loads((self.root / "queue" / entry["path"]).read_text())["candidates"]
        record = next(x for x in records if x["kind"] == "publication_review")
        self.assertEqual(record["review"], review)
        self.assertEqual(record["paper"], result["papers"][0])


if __name__ == "__main__":
    unittest.main()
