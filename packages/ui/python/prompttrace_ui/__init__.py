from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask, abort, render_template, request

from prompttrace_importer import DuckDBAnalysisStore, TraceQuery

DEFAULT_DB = ".prompttrace/analysis.duckdb"


def _query(db_path: str, *, keys: dict | None, status: str | None, text: str | None, limit: int) -> list[dict[str, Any]]:
    store = DuckDBAnalysisStore(db_path)
    try:
        page = store.query(
            TraceQuery(
                keys=keys or None,
                status=status or None,
                text=text or None,
                limit=limit,
            )
        )
        return page.items
    finally:
        store.close()


def _percentile(sorted_values: list[float], percentile: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(len(sorted_values) * percentile))
    return sorted_values[index]


def _preview(value: Any) -> str:
    try:
        if isinstance(value, str):
            text = value
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        elif value is None:
            return ""
        else:
            text = str(value)
    except Exception:  # noqa: BLE001
        return "<unprintable>"
    text = " ".join(text.split())
    return text[:300] + ("…" if len(text) > 300 else "")


def _variant_compare(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Compare all variants in a result set.

    Returns (comparison rows, case-paired cells, variant->letter map).
    """
    variants: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    case_cells: dict[str, dict[str, dict[str, Any]]] = {}
    for item in items:
        keys = item.get("keys", {})
        variant = str(keys.get("variant", "(none)"))
        case = str(keys.get("case", "_all"))
        if variant not in variants:
            variants[variant] = {"runs": 0, "ok": 0, "scores": [], "durations": [], "tokens": 0.0, "cost": 0.0}
            order.append(variant)
        v = variants[variant]
        v["runs"] += 1
        v["ok"] += 1 if item["status"] == "ok" else 0
        metrics = item.get("metrics", {})
        if metrics.get("score") is not None:
            v["scores"].append(float(metrics["score"]))
        v["durations"].append(item["durationMs"])
        v["tokens"] += metrics.get("tokens.total", 0) or 0
        v["cost"] += metrics.get("cost.usd", 0) or 0
        case_cells.setdefault(case, {}).setdefault(variant, []).append(
            {
                "id": item["id"],
                "status": item["status"],
                "output": item.get("artifacts", {}).get("output"),
                "prompt": item.get("artifacts", {}).get("prompt"),
                "error": (
                    item.get("error", {}).get("message")
                    if item["status"] == "error"
                    else None
                ),
            }
        )

    rows = []
    for variant in order:
        v = variants[variant]
        durations = sorted(v["durations"])
        rows.append(
            {
                "variant": variant,
                "runs": v["runs"],
                "success": f"{v['ok'] / v['runs']:.0%}",
                "avg_score": (sum(v["scores"]) / len(v["scores"])) if v["scores"] else None,
                "p50_ms": _percentile(durations, 0.50),
                "p95_ms": _percentile(durations, 0.95),
                "tokens": round(v["tokens"]),
                "cost": round(v["cost"], 6),
            }
        )
    rows.sort(key=lambda row: row["runs"], reverse=True)
    baseline = rows[0] if rows else None
    for row in rows:
        row["is_baseline"] = baseline is not None and row["variant"] == baseline["variant"]
        if baseline and row["avg_score"] is not None and baseline["avg_score"] is not None:
            row["score_diff"] = round(row["avg_score"] - baseline["avg_score"], 3)
        else:
            row["score_diff"] = None
        if baseline and row["p50_ms"] is not None and baseline["p50_ms"] is not None:
            row["p50_diff"] = round(row["p50_ms"] - baseline["p50_ms"])
        else:
            row["p50_diff"] = None

    letters = {variant: chr(ord("A") + index) for index, variant in enumerate(order)}
    cases: list[dict[str, Any]] = []
    for case, by_variant in sorted(case_cells.items(), key=lambda kv: -max(len(v) for v in kv[1].values())):
        columns = []
        for variant in order:
            traces = by_variant.get(variant)
            if not traces:
                continue
            trace = traces[0]
            columns.append(
                {
                    "letter": letters[variant],
                    "variant": variant,
                    "id": trace["id"],
                    "status": trace["status"],
                    "output": _preview(trace["output"]),
                    "prompt": _preview(trace["prompt"]),
                    "error": trace["error"],
                }
            )
        if len(columns) < 2:
            continue
        cases.append({"case": case, "columns": columns})
    return rows, cases, letters


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(ANALYSIS_DB=os_env_db())
    if test_config:
        app.config.update(test_config)

    def db_path() -> str:
        return app.config["ANALYSIS_DB"]

    @app.get("/")
    def index():
        experiment = request.args.get("experiment") or ""
        variant = request.args.get("variant") or ""
        case = request.args.get("case") or ""
        status = request.args.get("status") or ""
        text = request.args.get("q") or ""
        keys = {}
        if experiment:
            keys["experiment"] = experiment
        if variant:
            keys["variant"] = variant
        if case:
            keys["case"] = case
        try:
            items = _query(
                db_path(),
                keys=keys,
                status=status or None,
                text=text or None,
                limit=int(request.args.get("limit", 200)),
            )
        except Exception as exc:  # noqa: BLE001 - surface a readable error
            items = []
            error = str(exc)
        else:
            error = None
        compare_rows, compare_cases, _letters = _variant_compare(items)
        return render_template(
            "index.html",
            items=items,
            compare_rows=compare_rows,
            compare_cases=compare_cases,
            filters={
                "experiment": experiment,
                "variant": variant,
                "case": case,
                "status": status,
                "q": text,
            },
            error=error,
            db=db_path(),
        )

    @app.get("/traces/<trace_id>")
    def detail(trace_id: str):
        store = DuckDBAnalysisStore(db_path())
        try:
            record = store.get(trace_id)
        finally:
            store.close()
        if record is None:
            abort(404)
        return render_template("detail.html", record=record, raw=json.dumps(record, ensure_ascii=False, indent=2))

    @app.errorhandler(404)
    def not_found(_error):
        return "Trace not found.\n", 404, {"Content-Type": "text/plain; charset=utf-8"}

    return app


def os_env_db() -> str:
    import os

    return os.environ.get("PROMPTTRACE_DB", DEFAULT_DB)