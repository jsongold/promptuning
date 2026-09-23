"""PromptTrace Python SDK example: a small simulated agent recorded with @trace.

Run:

    PYTHONPATH=packages/core/python python3 examples/python-agent/agent.py
    prompttrace import --source .prompttrace/logs --db .prompttrace/analysis.duckdb
    prompttrace query --db .prompttrace/analysis.duckdb --experiment exp-005
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "core" / "python"))

from prompttrace import JSONLSink, KeyNameRedactor, trace

sink = JSONLSink(".prompttrace/logs/python-agent.jsonl")


def fake_llm(prompt: str) -> str:
    return f"Simulated answer to: {prompt[:40]}"


@trace(
    sink=sink,
    operation="agent.run",
    keys=lambda args, kwargs: {
        "experiment": kwargs["experiment"],
        "variant": kwargs["variant"],
        "case": kwargs["case"],
    },
    artifacts=lambda ctx: {"prompt": {"system": ctx.kwargs.get("system_prompt")}},
    metrics=lambda ctx: {"tokens.total": len(ctx.args[0]), "score": 0.95},
    redactors=[KeyNameRedactor()],
)
def run_agent(
    query: str,
    *,
    experiment: str,
    variant: str,
    case: str,
    system_prompt: str = "",
    api_key: str = "",
) -> str:
    return fake_llm(f"{system_prompt}\n{query}")


@trace(
    sink=sink,
    operation="agent.run.async",
    keys={"experiment": "exp-005", "variant": "async"},
    metrics=lambda ctx: {"tokens.total": 8},
)
async def run_agent_async(query: str) -> str:
    await asyncio.sleep(0)
    return fake_llm(query)


def main() -> None:
    run_agent(
        "What is prompt tracing?",
        experiment="exp-005",
        variant="v1",
        case="case-1",
        api_key="sk-super-secret",
        system_prompt="Be concise.",
    )
    run_agent(
        "Compare v1 against v2.",
        experiment="exp-005",
        variant="v2",
        case="case-1",
        system_prompt="Be concise.",
    )
    asyncio.run(run_agent_async("Async trace example."))
    print("Traces written to .prompttrace/logs/python-agent.jsonl")


if __name__ == "__main__":
    main()