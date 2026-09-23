import argparse
import sys

from . import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(prog="prompttrace-ui", description="PromptTrace analysis UI")
    parser.add_argument("--db", default=".prompttrace/analysis.duckdb", help="analysis DuckDB path")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5173, help="bind port (default 5173)")
    args = parser.parse_args(argv)
    app = create_app({"ANALYSIS_DB": args.db})
    print(f"PromptTrace UI -> http://{args.host}:{args.port}  (db: {args.db})")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main(sys.argv[1:])