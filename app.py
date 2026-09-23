import concurrent.futures
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    user_prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    parameters TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    response TEXT,
    error TEXT,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens INTEGER CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=str(Path(app.instance_path) / "prompttrace.sqlite3"),
        MAX_CONTENT_LENGTH=1024 * 1024,
        API_URL=os.environ.get("PROMPTTRACE_API_URL", ""),
        API_KEY=os.environ.get("PROMPTTRACE_API_KEY", ""),
        DEFAULT_MODEL=os.environ.get("PROMPTTRACE_MODEL", ""),
        RUNNER=None,
    )
    if test_config:
        app.config.update(test_config)

    database = app.config["DATABASE"]
    if database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)

    def get_db():
        if "db" not in request.environ:
            db = sqlite3.connect(app.config["DATABASE"])
            db.row_factory = sqlite3.Row
            request.environ["db"] = db
        return request.environ["db"]

    @app.teardown_request
    def close_db(_error=None):
        db = request.environ.pop("db", None)
        if db is not None:
            db.close()

    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)

    def all_runs():
        return get_db().execute(
            "SELECT * FROM runs ORDER BY created_at DESC, id DESC"
        ).fetchall()

    def find_run(run_id):
        return get_db().execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()

    def render_index(*, form=None, errors=None, error=None, status=200):
        return (
            render_template(
                "index.html",
                runs=all_runs(),
                form=form or {},
                errors=errors or {},
                error=error,
                provider_ready=bool(app.config["API_URL"]),
                default_model=app.config["DEFAULT_MODEL"],
            ),
            status,
        )

    def insert_run(values):
        cursor = get_db().execute(
            """
            INSERT INTO runs (
                name, system_prompt, user_prompt, model, parameters, status,
                response, error, duration_ms, prompt_tokens, completion_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["name"],
                values["system_prompt"],
                values["user_prompt"],
                values["model"],
                values["parameters"],
                values["status"],
                values["response"],
                values["error"],
                values["duration_ms"],
                values["prompt_tokens"],
                values["completion_tokens"],
            ),
        )
        return cursor.lastrowid

    def execute_provider(system_prompt, prompt, model, temperature):
        if not app.config["API_URL"]:
            raise RuntimeError(
                "No provider configured. Set PROMPTTRACE_API_URL first."
            )
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if app.config["API_KEY"]:
            headers["Authorization"] = f"Bearer {app.config['API_KEY']}"
        provider_request = urllib.request.Request(
            app.config["API_URL"], data=payload, headers=headers, method="POST"
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(provider_request, timeout=120) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Provider returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach provider: {exc.reason}") from exc

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Provider response did not contain message content.") from exc
        usage = result.get("usage") or {}
        return {
            "response": content or "",
            "duration_ms": round((time.monotonic() - started) * 1000),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        }

    @app.get("/")
    def index():
        return render_index()

    @app.post("/runs")
    def create_run():
        form = request.form.to_dict()
        values, errors = validate_run(form)
        if errors:
            return render_index(form=form, errors=errors, status=400)

        run_id = insert_run(values)
        get_db().commit()
        return redirect(url_for("run_detail", run_id=run_id))

    @app.post("/experiments")
    def run_experiment():
        system_prompt = request.form.get("system_prompt", "")
        prompts = request.form.getlist("prompt")
        models = request.form.getlist("model")
        temperatures = request.form.getlist("temperature")
        if len(prompts) < 2:
            return render_index(
                error="Add at least two prompt variants to run in parallel.", status=400
            )
        if not (len(prompts) == len(models) == len(temperatures)):
            return render_index(error="Variant fields are incomplete.", status=400)

        variants = []
        for position, (prompt, model, raw_temperature) in enumerate(
            zip(prompts, models, temperatures), start=1
        ):
            if not prompt.strip() or not model.strip():
                return render_index(
                    error=f"Variant {position} needs both a prompt and model.",
                    status=400,
                )
            try:
                temperature = float(raw_temperature)
                if not 0 <= temperature <= 2:
                    raise ValueError
            except ValueError:
                return render_index(
                    error=f"Variant {position} temperature must be between 0 and 2.",
                    status=400,
                )
            variants.append((prompt, model, temperature))

        runner = app.config.get("RUNNER") or execute_provider

        def execute(variant):
            prompt, model, temperature = variant
            started = time.monotonic()
            try:
                outcome = runner(system_prompt, prompt, model, temperature)
                return {
                    "status": "completed",
                    "response": outcome.get("response", ""),
                    "error": "",
                    "duration_ms": outcome.get(
                        "duration_ms", round((time.monotonic() - started) * 1000)
                    ),
                    "prompt_tokens": outcome.get("prompt_tokens"),
                    "completion_tokens": outcome.get("completion_tokens"),
                }
            except Exception as exc:
                return {
                    "status": "failed",
                    "response": "",
                    "error": str(exc),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "prompt_tokens": None,
                    "completion_tokens": None,
                }

        with concurrent.futures.ThreadPoolExecutor() as executor:
            outcomes = list(executor.map(execute, variants))

        run_ids = []
        for position, ((prompt, model, temperature), outcome) in enumerate(
            zip(variants, outcomes), start=1
        ):
            values = {
                "name": f"Variant {column_label(position)}",
                "system_prompt": system_prompt,
                "user_prompt": prompt,
                "model": model,
                "parameters": json.dumps(
                    {"temperature": temperature}, separators=(",", ":")
                ),
                **outcome,
            }
            run_ids.append(insert_run(values))
        get_db().commit()
        return redirect(url_for("compare", run=run_ids))

    @app.get("/quickstart")
    def quickstart():
        return render_template(
            "quickstart.html",
            provider_ready=bool(app.config["API_URL"]),
            default_model=app.config["DEFAULT_MODEL"],
        )

    @app.get("/runs/<int:run_id>")
    def run_detail(run_id):
        run = find_run(run_id)
        if run is None:
            abort(404)
        return render_template("detail.html", run=run)

    @app.get("/compare")
    def compare():
        selected = request.args.getlist("run")
        if len(selected) < 2 or len(set(selected)) != len(selected):
            return render_index(
                error="Select at least two different runs to compare.", status=400
            )
        try:
            run_ids = [int(value) for value in selected]
        except ValueError:
            return render_index(error="Run selections must be valid IDs.", status=400)

        runs = [find_run(run_id) for run_id in run_ids]
        if any(run is None for run in runs):
            abort(404)
        return render_template("compare.html", runs=runs)

    @app.get("/export.json")
    def export_runs():
        exported = []
        for row in all_runs():
            run = dict(row)
            run["parameters"] = json.loads(run["parameters"])
            exported.append(run)
        return jsonify(exported)

    @app.errorhandler(404)
    def not_found(_error):
        return "Page or run not found.\n", 404, {
            "Content-Type": "text/plain; charset=utf-8"
        }

    @app.errorhandler(413)
    def request_too_large(_error):
        return "Request exceeds the 1 MiB limit.\n", 413, {
            "Content-Type": "text/plain; charset=utf-8"
        }

    return app


def column_label(position):
    label = ""
    while position:
        position, remainder = divmod(position - 1, 26)
        label = chr(65 + remainder) + label
    return label


def validate_run(form):
    errors = {}
    values = {
        "name": form.get("name", ""),
        "system_prompt": form.get("system_prompt", ""),
        "user_prompt": form.get("user_prompt", ""),
        "model": form.get("model", ""),
        "status": form.get("status", ""),
        "response": form.get("response", ""),
        "error": form.get("error", ""),
    }

    for field, label in (
        ("name", "Name"),
        ("user_prompt", "User prompt"),
        ("model", "Model"),
    ):
        if not values[field].strip():
            errors[field] = f"{label} is required."

    if values["status"] not in {"completed", "failed"}:
        errors["status"] = "Status must be completed or failed."
    elif values["status"] == "completed" and not values["response"].strip():
        errors["response"] = "A completed run requires a response."
    elif values["status"] == "failed" and not values["error"].strip():
        errors["error"] = "A failed run requires an error message."

    raw_parameters = form.get("parameters", "").strip() or "{}"
    try:
        parameters = json.loads(raw_parameters)
        if not isinstance(parameters, dict):
            raise ValueError
        values["parameters"] = json.dumps(
            parameters, ensure_ascii=False, separators=(",", ":")
        )
    except (json.JSONDecodeError, ValueError):
        errors["parameters"] = "Parameters must be a JSON object."
        values["parameters"] = "{}"

    numeric_fields = {
        "duration_ms": form.get("duration_ms", ""),
        "prompt_tokens": form.get("prompt_tokens", form.get("input_tokens", "")),
        "completion_tokens": form.get(
            "completion_tokens", form.get("output_tokens", "")
        ),
    }
    for field, raw_value in numeric_fields.items():
        if raw_value is None or not str(raw_value).strip():
            values[field] = None
            continue
        try:
            value = int(raw_value)
            if value < 0:
                raise ValueError
            values[field] = value
        except (TypeError, ValueError):
            errors[field] = "Enter a non-negative whole number."
            values[field] = None

    return values, errors


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", "5050")))
