from __future__ import annotations

from typing import Any

REQUIRED = [
    "schemaVersion",
    "id",
    "operation",
    "startedAt",
    "endedAt",
    "durationMs",
    "status",
    "keys",
    "artifacts",
    "metrics",
    "sdk",
]


def validate_record(record: Any) -> list[str]:
    """Return a list of validation errors for a TraceRecord. Empty means valid."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record must be an object"]
    for field in REQUIRED:
        if field not in record:
            errors.append(f"missing required field: {field}")
    if errors:
        return errors
    if record["schemaVersion"] != "1.0":
        errors.append("schemaVersion must be '1.0'")
    for field in ("id", "operation"):
        if not isinstance(record[field], str) or not record[field].strip():
            errors.append(f"{field} must be a non-empty string")
    for field in ("startedAt", "endedAt"):
        if not isinstance(record[field], str):
            errors.append(f"{field} must be a string")
    if not isinstance(record["durationMs"], (int, float)) or record["durationMs"] < 0:
        errors.append("durationMs must be a non-negative number")
    if record["status"] not in ("ok", "error"):
        errors.append("status must be 'ok' or 'error'")
    if not isinstance(record["keys"], dict):
        errors.append("keys must be an object")
    else:
        for key, value in record["keys"].items():
            if not isinstance(value, (str, int, float, bool)):
                errors.append(f"key {key!r} must be a scalar value")
    if not isinstance(record["artifacts"], dict):
        errors.append("artifacts must be an object")
    if not isinstance(record["metrics"], dict):
        errors.append("metrics must be an object")
    else:
        for key, value in record["metrics"].items():
            if not isinstance(value, (int, float)):
                errors.append(f"metric {key!r} must be a number")
    if not isinstance(record["sdk"], dict) or not isinstance(
        record["sdk"].get("language"), str
    ) or not isinstance(record["sdk"].get("version"), str):
        errors.append("sdk must contain language and version strings")
    error = record.get("error")
    if error is not None:
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            errors.append("error must be an object with a message string")
    return errors