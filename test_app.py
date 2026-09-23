import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import column_label, create_app


class PromptTraceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "prompttrace.db")
        self.app = create_app({"TESTING": True, "DATABASE": self.database})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def completed_run(self, name="Completed run"):
        return {
            "name": name,
            "system_prompt": "Answer exactly.\nKeep formatting.",
            "user_prompt": "Say <hello> & goodbye.",
            "model": "local-model-v1",
            "status": "completed",
            "parameters": '{"temperature": 0.2}',
            "response": "<hello>\nGoodbye.",
            "error": "",
            "duration_ms": "125",
            "prompt_tokens": "12",
            "completion_tokens": "4",
        }

    def failed_run(self, name="Failed run"):
        data = self.completed_run(name)
        data.update(status="failed", response="", error="provider timed out")
        return data

    def create_run(self, data):
        response = self.client.post("/runs", data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        return response

    def exported_runs(self, client=None):
        response = (client or self.client).get("/export.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        return response.get_json()

    def test_empty_database(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.exported_runs(), [])

    def test_completed_and_failed_runs_are_preserved(self):
        self.create_run(self.completed_run())
        self.create_run(self.failed_run())

        runs = self.exported_runs()
        self.assertEqual([run["name"] for run in runs], ["Failed run", "Completed run"])
        completed = runs[1]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["system_prompt"], "Answer exactly.\nKeep formatting.")
        self.assertEqual(completed["user_prompt"], "Say <hello> & goodbye.")
        self.assertEqual(completed["response"], "<hello>\nGoodbye.")
        self.assertEqual(completed["parameters"], {"temperature": 0.2})
        self.assertEqual(completed["duration_ms"], 125)
        self.assertEqual(completed["prompt_tokens"], 12)
        self.assertEqual(completed["completion_tokens"], 4)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["error"], "provider timed out")

    def test_invalid_input_does_not_insert(self):
        invalid = self.completed_run()
        invalid["name"] = ""
        response = self.client.post("/runs", data=invalid)

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"name", response.data.lower())
        with sqlite3.connect(self.database) as connection:
            count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_history_persists_across_app_and_client_recreation(self):
        self.create_run(self.completed_run("Persistent run"))

        recreated_app = create_app({"TESTING": True, "DATABASE": self.database})
        recreated_client = recreated_app.test_client()
        response = recreated_client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Persistent run", response.data)
        self.assertEqual(self.exported_runs(recreated_client)[0]["name"], "Persistent run")

    def test_run_detail(self):
        location = self.create_run(self.completed_run("Detail run")).headers["Location"]
        run_id = self.exported_runs()[0]["id"]

        response = self.client.get(f"/runs/{run_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Detail run", response.data)
        self.assertIn(b"Say &lt;hello&gt; &amp; goodbye.", response.data)
        self.assertTrue(location.endswith(f"/runs/{run_id}"))

    def test_compare_accepts_any_number_of_distinct_runs(self):
        self.create_run(self.completed_run("First run"))
        self.create_run(self.failed_run("Second run"))
        self.create_run(self.completed_run("Third run"))
        self.create_run(self.completed_run("Fourth run"))
        first, second, third, fourth = [
            run["id"] for run in reversed(self.exported_runs())
        ]

        response = self.client.get(f"/compare?run={first}&run={second}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"First run", response.data)
        self.assertIn(b"Second run", response.data)

        response = self.client.get(
            f"/compare?run={first}&run={second}&run={third}&run={fourth}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Third run", response.data)
        self.assertIn(b"Fourth run", response.data)

        for query in (
            "",
            f"run={first}",
            f"run={first}&run={first}",
        ):
            with self.subTest(query=query):
                invalid = self.client.get(f"/compare?{query}")
                self.assertEqual(invalid.status_code, 400)
                self.assertIn(b"at least two", invalid.data.lower())

    def test_unknown_run_is_404(self):
        self.assertEqual(self.client.get("/runs/999999").status_code, 404)

    def test_json_export_contains_all_snapshots_newest_first(self):
        self.create_run(self.completed_run("Older"))
        self.create_run(self.failed_run("Newer"))

        response = self.client.get("/export.json")
        exported = json.loads(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([run["name"] for run in exported], ["Newer", "Older"])
        self.assertEqual(exported[0]["error"], "provider timed out")
        self.assertEqual(exported[1]["response"], "<hello>\nGoodbye.")

    def test_parallel_experiment_records_success_and_failure(self):
        calls = []

        def runner(system_prompt, prompt, model, temperature):
            calls.append((system_prompt, prompt, model, temperature))
            if prompt == "Fail this one":
                raise RuntimeError("mock provider failure")
            return {
                "response": f"Response to {prompt}",
                "duration_ms": 10,
                "prompt_tokens": 5,
                "completion_tokens": 7,
            }

        app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database,
                "API_URL": "https://mock.invalid/v1/chat/completions",
                "DEFAULT_MODEL": "demo-model",
                "RUNNER": runner,
            }
        )
        client = app.test_client()
        response = client.post(
            "/experiments",
            data={
                "system_prompt": "Be useful.",
                "prompt": ["First variant", "Fail this one", "Third variant"],
                "model": ["model-a", "model-b", "model-c"],
                "temperature": ["0.2", "0.5", "0.8"],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/compare?run=", response.headers["Location"])
        comparison = client.get(response.headers["Location"])
        self.assertEqual(comparison.status_code, 200)
        self.assertIn(b"Variant A", comparison.data)
        self.assertIn(b"Variant C", comparison.data)

        runs = self.exported_runs(client)
        self.assertEqual(len(runs), 3)
        self.assertEqual([run["name"] for run in runs], ["Variant C", "Variant B", "Variant A"])
        self.assertEqual(runs[1]["status"], "failed")
        self.assertEqual(runs[1]["error"], "mock provider failure")
        self.assertEqual(runs[2]["parameters"], {"temperature": 0.2})
        self.assertEqual(len(calls), 3)

    def test_parallel_experiment_validation_and_quickstart(self):
        response = self.client.post(
            "/experiments",
            data={"prompt": ["Only one"], "model": ["model"], "temperature": ["0.2"]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"at least two", response.data.lower())
        self.assertEqual(self.exported_runs(), [])

        quickstart = self.client.get("/quickstart")
        self.assertEqual(quickstart.status_code, 200)
        self.assertIn(b"PROMPTTRACE_API_URL", quickstart.data)
        self.assertEqual(column_label(1), "A")
        self.assertEqual(column_label(26), "Z")
        self.assertEqual(column_label(27), "AA")


if __name__ == "__main__":
    unittest.main()
