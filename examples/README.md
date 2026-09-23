# Examples

Two self-contained agents that record traces with the PromptTrace SDKs:

- `python-agent/` — Python, uses the `@trace` decorator (sync + async).
- `typescript-agent/` — TypeScript, uses the `traced()` wrapper (sync + async).

Each writes a JSONL sink under `.prompttrace/logs/` and needs no provider, DB,
or network to run.

## Run

Python:

```sh
PYTHONPATH=packages/core/python python3 examples/python-agent/agent.py
```

TypeScript (Node >= 23.6, type stripping):

```sh
node examples/typescript-agent/agent.mts
```

## Import and query

Both sinks share one analysis DB (see the importer CLI). Run from the repo
root so `.prompttrace/logs/` lands where the commands expect:

```sh
poetry run prompttrace import --source .prompttrace/logs/ --db .prompttrace/analysis.duckdb
poetry run prompttrace query --db .prompttrace/analysis.duckdb --experiment exp-005
```

Each run records keys (`experiment`, `variant`, `case`), artifacts (input,
output, prompt), metrics (`tokens.total`, `score`), and redacts sensitive
values such as `api_key`.