from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

import duckdb

from .validation import validate_record

SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id              TEXT PRIMARY KEY,
    schema_version  TEXT NOT NULL,
    parent_id       TEXT,
    operation       TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT NOT NULL,
    duration_ms     REAL NOT NULL,
    status          TEXT NOT NULL,
    record_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_keys (
    trace_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    value_type      TEXT NOT NULL,
    value_text      TEXT,
    value_number    REAL,
    value_boolean   BOOLEAN,
    PRIMARY KEY (trace_id, key),
    FOREIGN KEY (trace_id) REFERENCES traces(id)
);

CREATE TABLE IF NOT EXISTS trace_metrics (
    trace_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    value_number    REAL NOT NULL,
    PRIMARY KEY (trace_id, key),
    FOREIGN KEY (trace_id) REFERENCES traces(id)
);
"""

SCHEMA_VERSION = 1


@dataclass
class ImportResult:
    total: int = 0
    imported: int = 0
    skipped: int = 0
    invalid: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


@dataclass
class TraceQuery:
    keys: dict[str, str | int | float | bool | list[str | int | float | bool]] | None = None
    status: str | None = None
    operation: str | list[str] | None = None
    started_after: str | None = None
    started_before: str | None = None
    metric_ranges: dict[str, dict[str, float]] | None = None
    text: str | None = None
    order_by: str = "startedAt"
    order: str = "desc"
    limit: int = 100
    cursor: str | None = None


@dataclass
class TracePage:
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class AnalysisStore(Protocol):
    def insert_batch(self, records: Sequence[dict[str, Any]]) -> ImportResult: ...
    def get(self, trace_id: str) -> dict[str, Any] | None: ...
    def query(self, query: TraceQuery) -> TracePage: ...


def _migrate(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_MIGRATIONS)
    conn.execute(SCHEMA)
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    applied = row[0] or 0
    if applied < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            [SCHEMA_VERSION, _utc_now()],
        )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class DuckDBAnalysisStore:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(path)
        _migrate(self._conn)

    def close(self) -> None:
        self._conn.close()

    def insert_batch(self, records: Sequence[dict[str, Any]]) -> ImportResult:
        result = ImportResult(total=len(records))
        self._conn.execute("BEGIN")
        try:
            for record in records:
                errors = validate_record(record)
                if errors:
                    result.invalid += 1
                    result.errors.append(
                        f"record {record.get('id', '?')}: " + "; ".join(errors)
                    )
                    continue
                trace_id = record["id"]
                exists = self._conn.execute(
                    "SELECT 1 FROM traces WHERE id = ?", [trace_id]
                ).fetchone()
                if exists:
                    result.skipped += 1
                    continue
                self._conn.execute(
                    """
                    INSERT INTO traces (
                        id, schema_version, parent_id, operation, started_at,
                        ended_at, duration_ms, status, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        trace_id,
                        record["schemaVersion"],
                        record.get("parentId"),
                        record["operation"],
                        record["startedAt"],
                        record["endedAt"],
                        float(record["durationMs"]),
                        record["status"],
                        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    ],
                )
                self._insert_keys(trace_id, record["keys"])
                self._insert_metrics(trace_id, record["metrics"])
                result.imported += 1
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return result

    def _insert_keys(self, trace_id: str, keys: dict[str, Any]) -> None:
        rows = []
        for key, value in keys.items():
            if isinstance(value, bool):
                rows.append([trace_id, key, "boolean", None, None, value])
            elif isinstance(value, (int, float)):
                rows.append([trace_id, key, "number", None, float(value), None])
            else:
                rows.append([trace_id, key, "string", str(value), None, None])
        if rows:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO trace_keys
                    (trace_id, key, value_type, value_text, value_number, value_boolean)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _insert_metrics(self, trace_id: str, metrics: dict[str, float]) -> None:
        if not metrics:
            return
        self._conn.executemany(
            "INSERT OR IGNORE INTO trace_metrics (trace_id, key, value_number) VALUES (?, ?, ?)",
            [[trace_id, key, float(value)] for key, value in metrics.items()],
        )

    def get(self, trace_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT record_json FROM traces WHERE id = ?", [trace_id]
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def query(self, query: TraceQuery) -> TracePage:
        where: list[str] = []
        params: list[Any] = []

        if query.status is not None:
            where.append("t.status = ?")
            params.append(query.status)
        if query.operation is not None:
            operations = (
                [query.operation]
                if isinstance(query.operation, str)
                else query.operation
            )
            placeholders = ", ".join("?" for _ in operations)
            where.append(f"t.operation IN ({placeholders})")
            params.extend(operations)
        if query.started_after is not None:
            where.append("t.started_at >= ?")
            params.append(query.started_after)
        if query.started_before is not None:
            where.append("t.started_at <= ?")
            params.append(query.started_before)
        if query.keys:
            for key, value in query.keys.items():
                values = [value] if not isinstance(value, list) else value
                placeholders = ", ".join("?" for _ in values)
                where.append(
                    f"""EXISTS (
                        SELECT 1 FROM trace_keys tk
                        WHERE tk.trace_id = t.id AND tk.key = ?
                          AND (
                            tk.value_text IN ({placeholders})
                            OR tk.value_number IN ({placeholders})
                            OR tk.value_boolean IN ({placeholders})
                          )
                    )"""
                )
                params.append(key)
                params.extend(values)
                params.extend(float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else -1e308 for v in values)
                params.extend(bool(v) if isinstance(v, bool) else False for v in values)
        if query.metric_ranges:
            for key, bounds in query.metric_ranges.items():
                conditions = ["tm.key = ?"]
                params.append(key)
                if bounds.get("gte") is not None:
                    conditions.append("tm.value_number >= ?")
                    params.append(bounds["gte"])
                if bounds.get("lte") is not None:
                    conditions.append("tm.value_number <= ?")
                    params.append(bounds["lte"])
                where.append(
                    f"EXISTS (SELECT 1 FROM trace_metrics tm "
                    f"WHERE tm.trace_id = t.id AND {' AND '.join(conditions)})"
                )
        if query.text:
            escaped = query.text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                "(t.operation LIKE ? ESCAPE '\\' OR t.record_json LIKE ? ESCAPE '\\')"
            )
            params.append(f"%{escaped}%")
            params.append(f"%{escaped}%")

        order_column = "started_at" if query.order_by == "startedAt" else "duration_ms"
        direction = "ASC" if query.order.lower() == "asc" else "DESC"
        limit = max(1, min(int(query.limit), 1000))

        if query.cursor:
            started_at, trace_id = query.cursor.rsplit(":", 1)
            if direction == "DESC":
                where.append("(t.started_at < ? OR (t.started_at = ? AND t.id < ?))")
            else:
                where.append("(t.started_at > ? OR (t.started_at = ? AND t.id > ?))")
            params.extend([started_at, started_at, trace_id])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self._conn.execute(
            f"""
            SELECT t.record_json
            FROM traces t
            {where_sql}
            ORDER BY t.{order_column} {direction}, t.id {direction}
            LIMIT ?
            """,
            [*params, limit + 1],
        ).fetchall()

        items = [json.loads(row[0]) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            next_cursor = f"{last['startedAt']}:{last['id']}"
        return TracePage(items=items, next_cursor=next_cursor)