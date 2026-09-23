import json
import tempfile
import unittest
from pathlib import Path

from prompttrace_importer import DuckDBAnalysisStore
from prompttrace_ui import create_app


def make_record(trace_id, operation, status="ok", variant="v1", score=0.9):
    return {
        "schemaVersion": "1.0",
        "id": trace_id,
        "operation": operation,
        "startedAt": "2026-09-22T03:10:01.120Z",
        "endedAt": "2026-09-22T03:10:02.120Z",
        "durationMs": 1000,
        "status": status,
        "keys": {"experiment": "exp-1", "variant": variant, "case": "case-1"},
        "artifacts": {"input": {"query": "hello"}, "output": "world"},
        "metrics": {"score": score, "tokens.total": 120},
        "sdk": {"language": "python", "version": "0.1.0"},
    }


class UiTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tempdir.name) / "analysis.duckdb")
        store = DuckDBAnalysisStore(self.db)
        store.insert_batch(
            [
                make_record("id-1", "agent.run", variant="v1"),
                make_record("id-2", "agent.run", variant="v2", status="error", score=0.1),
            ]
        )
        store.close()
        self.app = create_app({"TESTING": True, "ANALYSIS_DB": self.db})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_index_lists_traces_and_summary(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("agent.run", body)
        self.assertIn("Variant comparison", body)
        self.assertIn("Side by side", body)
        self.assertIn("case-1", body)
        self.assertIn("id-1", body)
        self.assertIn("id-2", body)

    def test_index_side_by_side_pairs_cases(self):
        response = self.client.get("/")
        body = response.get_data(as_text=True)
        self.assertIn('class="compare-column ok"', body)
        self.assertIn('class="compare-column error"', body)

    def test_index_filters_by_variant(self):
        response = self.client.get("/?variant=v2")
        body = response.get_data(as_text=True)
        self.assertIn("id-2", body)
        self.assertNotIn("id-1", body)

    def test_index_filters_by_status(self):
        response = self.client.get("/?status=error")
        body = response.get_data(as_text=True)
        self.assertIn("id-2", body)
        self.assertNotIn("id-1", body)

    def test_detail_shows_record_and_raw(self):
        response = self.client.get("/traces/id-1")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("agent.run", body)
        self.assertIn("Raw record", body)
        self.assertIn("world", body)

    def test_unknown_trace_is_404(self):
        self.assertEqual(self.client.get("/traces/nope").status_code, 404)

    def test_empty_store_renders(self):
        empty_db = str(Path(self.tempdir.name) / "empty.duckdb")
        DuckDBAnalysisStore(empty_db).close()
        app = create_app({"TESTING": True, "ANALYSIS_DB": empty_db})
        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("No traces yet", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()