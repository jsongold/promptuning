import unittest

from prompttrace_importer import DuckDBAnalysisStore, TraceQuery


def make_record(trace_id, operation="op.add", status="ok", **overrides):
    record = {
        "schemaVersion": "1.0",
        "id": trace_id,
        "operation": operation,
        "startedAt": "2026-09-22T03:10:01.120Z",
        "endedAt": "2026-09-22T03:10:02.120Z",
        "durationMs": 1000,
        "status": status,
        "keys": {"experiment": "exp-1", "variant": "v1", "case": "case-1"},
        "artifacts": {"input": {"query": "hello"}, "output": "world"},
        "metrics": {"score": 0.92, "tokens.total": 120},
        "sdk": {"language": "python", "version": "0.1.0"},
    }
    record.update(overrides)
    return record


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.store = DuckDBAnalysisStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_insert_and_get(self):
        record = make_record("id-1")
        result = self.store.insert_batch([record])
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.total, 1)
        fetched = self.store.get("id-1")
        self.assertEqual(fetched["operation"], "op.add")
        self.assertEqual(fetched["artifacts"]["output"], "world")

    def test_reimport_is_idempotent(self):
        record = make_record("id-1")
        self.store.insert_batch([record])
        result = self.store.insert_batch([record, make_record("id-2")])
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(self.store.query(TraceQuery()).items.__len__(), 2)

    def test_invalid_records_are_rejected(self):
        bad = make_record("bad-1")
        bad["status"] = "weird"
        result = self.store.insert_batch([bad])
        self.assertEqual(result.invalid, 1)
        self.assertEqual(result.imported, 0)
        self.assertIsNone(self.store.get("bad-1"))

    def test_keys_and_metrics_columns(self):
        self.store.insert_batch([make_record("id-1")])
        conn = self.store._conn
        key = conn.execute(
            "SELECT key, value_type, value_text FROM trace_keys WHERE trace_id='id-1' ORDER BY key"
        ).fetchall()
        self.assertIn(("variant", "string", "v1"), key)
        metric = conn.execute(
            "SELECT key, value_number FROM trace_metrics WHERE trace_id='id-1'"
        ).fetchall()
        self.assertIn(("tokens.total", 120.0), metric)

    def test_query_by_keys_status_operation(self):
        self.store.insert_batch(
            [
                make_record("id-1", operation="op.add", status="ok"),
                make_record("id-2", operation="op.add", status="error", keys={"experiment": "exp-1", "variant": "v2", "case": "case-1"}),
                make_record("id-3", operation="op.sub", status="ok", keys={"experiment": "exp-2", "variant": "v1", "case": "case-1"}),
            ]
        )
        ok = self.store.query(TraceQuery(status="ok"))
        self.assertEqual([i["id"] for i in ok.items], ["id-3", "id-1"])

        by_key = self.store.query(TraceQuery(keys={"variant": "v2"}))
        self.assertEqual([i["id"] for i in by_key.items], ["id-2"])

        by_op = self.store.query(TraceQuery(operation=["op.add"]))
        self.assertEqual(len(by_op.items), 2)

        exp1 = self.store.query(TraceQuery(keys={"experiment": "exp-1", "variant": "v2"}))
        self.assertEqual([i["id"] for i in exp1.items], ["id-2"])

    def test_query_metric_range_and_text(self):
        self.store.insert_batch(
            [
                make_record("id-1", metrics={"score": 0.5, "tokens.total": 100}),
                make_record("id-2", metrics={"score": 0.9, "tokens.total": 300}),
            ]
        )
        high = self.store.query(TraceQuery(metric_ranges={"score": {"gte": 0.8}}))
        self.assertEqual([i["id"] for i in high.items], ["id-2"])

        text = self.store.query(TraceQuery(text="world"))
        self.assertEqual([i["id"] for i in text.items], ["id-2", "id-1"])

    def test_order_and_limit(self):
        records = [make_record(f"id-{i}", startedAt=f"2026-09-22T03:10:0{i}.000Z") for i in range(3)]
        self.store.insert_batch(records)
        asc = self.store.query(TraceQuery(order_by="startedAt", order="asc", limit=2))
        self.assertEqual([i["id"] for i in asc.items], ["id-0", "id-1"])
        self.assertIsNotNone(asc.next_cursor)
        next_page = self.store.query(TraceQuery(order_by="startedAt", order="asc", limit=2, cursor=asc.next_cursor))
        self.assertEqual([i["id"] for i in next_page.items], ["id-2"])

    def test_invalid_status_query_value(self):
        self.store.insert_batch([make_record("id-1")])
        page = self.store.query(TraceQuery(status="error"))
        self.assertEqual(page.items, [])


if __name__ == "__main__":
    unittest.main()