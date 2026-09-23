# PromptTrace

PromptTrace is a local-first workbench for running unlimited prompt variants in
parallel, comparing their results side by side, and retaining immutable traces.

## Run locally

```sh
poetry install
export PROMPTTRACE_API_URL="https://api.example.com/v1/chat/completions"
export PROMPTTRACE_API_KEY="your-api-key"
export PROMPTTRACE_MODEL="your-model"
poetry run python app.py
```

Open <http://127.0.0.1:5050>. The in-app Quickstart is available at
<http://127.0.0.1:5050/quickstart>. Data is stored in
`instance/prompttrace.sqlite3` by default.

`PROMPTTRACE_API_KEY` may be omitted for a local endpoint that does not require
authentication. Provider credentials are read server-side and never stored in
traces or sent to the browser.

## Test

```sh
python -m unittest -v
```

The provider must implement the OpenAI-compatible Chat Completions response
shape. See `PromptTrace_DESIGN.md` for the complete product contract.

## Trace your own code with the SDKs

The core of PromptTrace is a V0.1 Capture SDK: decorate any function and each
call is recorded as a schema-compatible `TraceRecord` (see
`schemas/trace-record.schema.json` and `PromptTrace_DESIGN.md` §7). Records are
written to a sink — JSONL, in-memory, or custom — so recording never requires a
running server.

Python (`packages/core/python`, stdlib only):

```python
from prompttrace import JSONLSink, KeyNameRedactor, trace

sink = JSONLSink(".prompttrace/logs/trace.jsonl")

@trace(
    sink=sink,
    operation="research_agent.run",
    keys={"experiment": "exp-003", "variant": "planner-v4"},
    redactors=[KeyNameRedactor()],
)
def run_agent(query: str) -> str:
    return agent.run(query)
```

`keys`, `artifacts`, and `metrics` also accept callables, e.g.
`keys=lambda args, kwargs: {"case": kwargs["case_id"]}`. Sync and `async def`
functions are both supported, and a failed trace never changes the original
return value or exception unless `strict=True`.

TypeScript (`packages/core/typescript`, zero runtime dependencies):

```ts
import { JSONLSink, KeyNameRedactor, traced } from "@prompttrace/core"

const sink = new JSONLSink(".prompttrace/logs/trace.jsonl")

const runAgent = traced(
  {
    sink,
    operation: "research_agent.run",
    keys: { experiment: "exp-003", variant: "planner-v4" },
    redactors: [new KeyNameRedactor()],
  },
  async (query: string) => agent.run(query),
)
```

Resolvers receive `{ args, result, error }`, e.g.
`keys: ({ args }) => ({ case: String(args[0]) })`. Per the design contract, a
synchronous function may not be combined with an asynchronous sink.

Common schema, sinks, redaction, and payload limits are shared across both
languages and verified by cross-language conformance tests.

## Import traces into the analysis DB

SDKs only write to a sink; analysis happens in a single local DuckDB
(`packages/importer/python`, per `PromptTrace_DESIGN.md` §14.4). The `prompttrace`
CLI is installed with the project, so from the repo root run:

```sh
poetry run prompttrace import --source .prompttrace/logs/ --db .prompttrace/analysis.duckdb
poetry run prompttrace load --source .prompttrace/logs/ --db .prompttrace/analysis.duckdb
```

`load` is an alias of `import`. Import is idempotent: records are validated
against the schema, deduplicated by `id`, and inserted in a single transaction.
Re-running the same command imports nothing new.

Query the DB with:

```sh
poetry run prompttrace query --db .prompttrace/analysis.duckdb --experiment exp-003
poetry run prompttrace query --db .prompttrace/analysis.duckdb --status error --variant v4 --json
```

`--experiment`, `--variant`, `--case`, `--status`, `--operation`, `--order-by`,
`--order`, and `--limit` filter the result; `--json` prints full records.

## View traces in the browser

The local analysis UI reads the same DuckDB file and runs in one command
(`packages/ui/python`):

```sh
poetry run prompttrace-ui --db .prompttrace/analysis.duckdb
# open http://127.0.0.1:5173
```

The UI is read-only: a variant comparison table (runs, success rate, avg score,
p50/p95 latency, tokens, cost, Δ vs the baseline variant), a case-paired
side-by-side view of each variant's prompt/output, a filterable trace list
(experiment / variant / case / status / text), and a per-trace detail page with
keys, metrics, artifacts, error, and raw JSON.

### SDK tests

```sh
PYTHONPATH=packages/core/python python3 -m unittest discover -s packages/core/python/tests -v
cd packages/importer/python && poetry run python -m unittest discover -s tests -v
cd packages/ui/python && poetry run python -m unittest discover -s tests -v
cd packages/core/typescript && npm test && npx tsc --noEmit
node conformance/compare.mjs
```

## How tracing works (Flask workbench)

The `app.py` workbench below is a separate prompt-run UI. The SDK/analysis
pipeline above is the tracing path described in `PromptTrace_DESIGN.md`.

Python side (`app.py`):

- Every run is stored as an immutable snapshot in the `runs` table
  (`app.py:13`), so the archive is the full version history.
- `POST /experiments` (`app.py:172`) validates the variants, runs each one
  concurrently in a thread pool, and records every outcome as its own run
  (`app.py:232`).
- Provider calls happen in `execute_provider` (`app.py:113`): system/user
  prompts and the model are sent to the configured endpoint; the response,
  error, duration, and token usage are captured.
- Both successes and failures are stored (`status` is `completed` or `failed`).
  The exact inputs are never rewritten, which is what makes a run reproducible.
- Trace access routes: `GET /runs/<id>` (`app.py:261`) for one snapshot,
  `GET /compare` (`app.py:268`) for side-by-side review, and
  `GET /export.json` (`app.py:285`) for the full archive newest first.

Frontend side (`templates/index.html`):

- The workbench script (`templates/index.html:106`) manages variant columns:
  "Add variant" clones the template, remove buttons are disabled below two
  variants, and labels renumber A, B, C, and beyond.
- Submitting the form disables the run button and posts to `/experiments`
  (`templates/index.html:134`); the server redirects to a new comparison.
- The history script (`templates/index.html:141`) keeps the "Compare selected"
  button disabled until at least two runs are checked.

Server request logs are printed to the console where `poetry run python app.py`
is running (Flask development server, stderr). Traces themselves live in
`instance/prompttrace.sqlite3` and are exposed through the routes above.
