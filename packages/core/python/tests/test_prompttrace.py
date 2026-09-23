import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from prompttrace import (
    JSONLSink,
    KeyNameRedactor,
    MemorySink,
    RegexRedactor,
    trace,
)


class TraceCaptureTest(unittest.TestCase):
    def setUp(self):
        self.sink = MemorySink()

    def last(self):
        return dict(self.sink.records[-1])

    def test_sync_success(self):
        @trace(sink=self.sink, operation="math.add")
        def add(a, b):
            return a + b

        self.assertEqual(add(2, 3), 5)
        record = self.last()
        self.assertEqual(record["schemaVersion"], "1.0")
        self.assertEqual(record["operation"], "math.add")
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["artifacts"]["output"], 5)
        self.assertIn("input", record["artifacts"])
        self.assertGreaterEqual(record["durationMs"], 0)
        self.assertEqual(record["sdk"]["language"], "python")
        self.assertIn("id", record)
        self.assertNotIn("error", record)

    def test_async_success(self):
        @trace(sink=self.sink)
        async def fetch(query):
            await asyncio.sleep(0)
            return f"answer:{query}"

        result = asyncio.run(fetch("q"))
        self.assertEqual(result, "answer:q")
        record = self.last()
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["artifacts"]["output"], "answer:q")

    def test_sync_exception_is_recorded_and_rerais_original(self):
        @trace(sink=self.sink)
        def boom():
            raise ValueError("bad input")

        with self.assertRaises(ValueError) as ctx:
            boom()
        self.assertEqual(str(ctx.exception), "bad input")
        record = self.last()
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error"]["message"], "bad input")
        self.assertEqual(record["error"]["type"], "ValueError")
        self.assertIn("stack", record["error"])

    def test_async_exception(self):
        @trace(sink=self.sink)
        async def boom():
            raise RuntimeError("async fail")

        with self.assertRaises(RuntimeError):
            asyncio.run(boom())
        record = self.last()
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error"]["message"], "async fail")

    def test_static_keys_and_resolver(self):
        @trace(sink=self.sink, keys={"experiment": "exp-1"})
        def f(x):
            return x

        f(1)
        self.assertEqual(self.last()["keys"], {"experiment": "exp-1"})

        @trace(sink=self.sink, keys=lambda args, kwargs: {"case": kwargs["case_id"]})
        def g(x, *, case_id):
            return x

        g(2, case_id="c-7")
        self.assertEqual(self.last()["keys"], {"case": "c-7"})

    def test_artifacts_and_metrics_resolvers(self):
        @trace(
            sink=self.sink,
            artifacts=lambda ctx: {"prompt": ctx.kwargs.get("prompt")},
            metrics=lambda ctx: {"tokens.total": 42 if ctx.error is None else 0},
        )
        def run(query, *, prompt):
            return len(query)

        run("hello", prompt="system prompt")
        record = self.last()
        self.assertEqual(record["artifacts"]["prompt"], "system prompt")
        self.assertEqual(record["metrics"], {"tokens.total": 42})

    def test_capture_input_output_off(self):
        @trace(sink=self.sink, capture_input=False, capture_output=False)
        def f(x):
            return x * 2

        f(10)
        record = self.last()
        self.assertNotIn("input", record["artifacts"])
        self.assertNotIn("output", record["artifacts"])

    def test_key_name_redaction(self):
        @trace(sink=self.sink, redactors=[KeyNameRedactor()])
        def login(payload):
            return payload

        login({"api_key": "secret-value", "user": "alice", "nested": {"token": "abc"}})
        artifacts = self.last()["artifacts"]
        self.assertEqual(artifacts["output"]["api_key"], "[REDACTED]")
        self.assertEqual(artifacts["output"]["nested"]["token"], "[REDACTED]")
        self.assertEqual(artifacts["output"]["user"], "alice")

    def test_prompt_capture_resolver_and_static(self):
        @trace(
            sink=self.sink,
            prompt=lambda args, kwargs: {"system": kwargs.get("system"), "user": args[0]},
        )
        def run(query, *, system):
            return query

        run("hello", system="be concise")
        self.assertEqual(
            self.last()["artifacts"]["prompt"], {"system": "be concise", "user": "hello"}
        )

        @trace(sink=self.sink, prompt={"system": "sys", "user": "u"})
        def f(x):
            return x

        f(1)
        self.assertEqual(self.last()["artifacts"]["prompt"], {"system": "sys", "user": "u"})

    def test_metric_keys_are_not_redacted(self):
        @trace(
            sink=self.sink,
            redactors=[KeyNameRedactor()],
            metrics=lambda ctx: {"tokens.total": 10},
        )
        def f(x):
            return {"api_key": "secret", "tokens.total": 5}

        f(1)
        record = self.last()
        self.assertEqual(record["metrics"], {"tokens.total": 10})
        output = record["artifacts"]["output"]
        self.assertEqual(output["api_key"], "[REDACTED]")
        self.assertEqual(output["tokens.total"], 5)

    def test_regex_redaction(self):
        @trace(sink=self.sink, redactors=[RegexRedactor(r"\b[A-Z]{6,}\b")])
        def f(payload):
            return payload

        f({"text": "the SECRETKEY is here"})
        self.assertEqual(
            self.last()["artifacts"]["output"]["text"], "the [REDACTED] is here"
        )

    def test_circular_reference_is_safe(self):
        node = {"name": "node"}
        node["self"] = node

        @trace(sink=self.sink)
        def f(obj):
            return obj

        f(node)
        self.assertEqual(
            self.last()["artifacts"]["output"]["self"], "<circular>"
        )

    def test_unserializable_object_falls_back(self):
        class Handle:
            pass

        @trace(sink=self.sink)
        def f(obj):
            return obj

        f(Handle())
        output = self.last()["artifacts"]["output"]
        self.assertIsInstance(output, str)

    def test_payload_limit_truncates_and_marks(self):
        @trace(sink=self.sink, max_payload_bytes=400)
        def f(payload):
            return payload

        f({"big": "x" * 5000})
        record = self.last()
        self.assertTrue(record["artifacts"].get("_prompttrace", {}).get("truncated"))
        self.assertLessEqual(len(json.dumps(record)), 4000)

    def test_best_effort_on_sink_failure(self):
        class BrokenSink:
            def append(self, record):
                raise RuntimeError("disk full")

        @trace(sink=BrokenSink())
        def f(x):
            return x + 1

        self.assertEqual(f(1), 2)

    def test_strict_mode_raises_on_sink_failure(self):
        class BrokenSink:
            def append(self, record):
                raise RuntimeError("disk full")

        @trace(sink=BrokenSink(), strict=True)
        def f(x):
            return x + 1

        with self.assertRaises(RuntimeError):
            f(1)

    def test_jsonl_sink_writes_valid_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "traces.jsonl")
            with JSONLSink(path) as sink:

                @trace(sink=sink)
                def f(x):
                    return x

                f(1)
                f(2)

            lines = path and [json.loads(l) for l in Path(path).read_text().splitlines()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["status"], "ok")
            self.assertEqual(lines[1]["artifacts"]["output"], 2)

    def test_nested_parent_id_via_context(self):
        parent_sink = MemorySink()

        @trace(sink=parent_sink, operation="parent")
        def parent(fn, **kw):
            return fn(**kw)

        @trace(sink=self.sink, operation="child")
        def child(x):
            return x

        parent(lambda: child(3))
        parent_record = parent_sink.records[0]
        child_record = self.sink.records[0]
        self.assertEqual(parent_record["operation"], "parent")
        self.assertEqual(child_record["operation"], "child")


if __name__ == "__main__":
    unittest.main()