from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from .analysis_store import DuckDBAnalysisStore, TraceQuery

DEFAULT_DB = ".prompttrace/analysis.duckdb"


def _iter_jsonl_files(source: str) -> Iterable[Path]:
    path = Path(source)
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        raise SystemExit(f"source does not exist: {source}")
    yield from sorted(path.rglob("*.jsonl"))


def _read_records(source: str) -> tuple[list[dict], int]:
    records: list[dict] = []
    malformed = 0
    for file in _iter_jsonl_files(source):
        for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            records.append(record)
    return records, malformed


def cmd_import(args: argparse.Namespace) -> None:
    records, malformed = _read_records(args.source)
    store = DuckDBAnalysisStore(args.db)
    try:
        result = store.insert_batch(records)
    finally:
        store.close()
    print(f"source files read: {args.source}")
    print(f"total records:     {result.total}")
    print(f"imported:          {result.imported}")
    print(f"skipped (dup id):  {result.skipped}")
    print(f"invalid:           {result.invalid}")
    print(f"malformed lines:   {malformed}")
    for error in result.errors[:20]:
        print(f"  - {error}")
    if result.invalid or malformed:
        raise SystemExit(1)
    if result.imported == 0 and result.total:
        print("nothing new to import (all records already present)")


def cmd_query(args: argparse.Namespace) -> None:
    keys: dict = {}
    if args.experiment:
        keys["experiment"] = args.experiment
    if args.variant:
        keys["variant"] = args.variant
    if args.case:
        keys["case"] = args.case
    store = DuckDBAnalysisStore(args.db)
    try:
        page = store.query(
            TraceQuery(
                keys=keys or None,
                status=args.status,
                operation=args.operation,
                order_by=args.order_by,
                order=args.order,
                limit=args.limit,
            )
        )
    finally:
        store.close()
    if args.json:
        print(json.dumps(page.items, ensure_ascii=False, indent=2))
    else:
        for item in page.items:
            keys_summary = ",".join(f"{k}={v}" for k, v in item.get("keys", {}).items())
            print(
                f"{item['id'][:8]}  {item['startedAt']}  {item['status']:<5}  "
                f"{item['operation']:<24}  {item['durationMs']:>8.1f}ms  "
                f"[{keys_summary}]"
            )
        print(f"\n{len(page.items)} trace(s)")
    if page.next_cursor:
        print(f"\ncursor: {page.next_cursor}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompttrace", description="PromptTrace CLI")
    parser.add_argument("--db", default=DEFAULT_DB, help="analysis DuckDB path")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("import", "load"):
        p = sub.add_parser(name, help="read traces from a JSONL sink into the analysis DB")
        p.add_argument("--source", required=True, help="JSONL sink file or directory")
        p.add_argument("--db", default=DEFAULT_DB, help="analysis DuckDB path")
        p.set_defaults(func=cmd_import)

    q = sub.add_parser("query", help="query traces from the analysis DB")
    q.add_argument("--db", default=DEFAULT_DB, help="analysis DuckDB path")
    q.add_argument("--experiment")
    q.add_argument("--variant")
    q.add_argument("--case")
    q.add_argument("--status", choices=["ok", "error"])
    q.add_argument("--operation")
    q.add_argument("--order-by", choices=["startedAt", "durationMs"], default="startedAt")
    q.add_argument("--order", choices=["asc", "desc"], default="desc")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_query)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])