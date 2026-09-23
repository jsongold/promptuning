/**
 * PromptTrace TypeScript SDK example: a small simulated agent recorded with traced().
 *
 * Run:
 *   node examples/typescript-agent/agent.ts
 *   prompttrace import --source .prompttrace/logs --db .prompttrace/analysis.duckdb
 *   prompttrace query --db .prompttrace/analysis.duckdb --experiment exp-005
 */

import { JSONLSink, KeyNameRedactor, traced } from "../../packages/core/typescript/src/index.ts"

const sink = new JSONLSink(".prompttrace/logs/typescript-agent.jsonl")

const fakeLlm = (prompt: string) => `Simulated answer to: ${prompt.slice(0, 40)}`

interface RunOptions {
  experiment: string
  variant: string
  case: string
  apiKey?: string
}

const runAgent = traced(
  {
    sink,
    operation: "agent.run",
    keys: ({ args }) => ({
      experiment: args[2].experiment,
      variant: args[2].variant,
      case: args[2].case,
    }),
    prompt: ({ args }) => ({ system: args[1], user: args[0] }),
    metrics: ({ args }) => ({ "tokens.total": args[0].length, score: 0.95 }),
    redactors: [new KeyNameRedactor()],
  },
  (query: string, systemPrompt: string, options: RunOptions): string =>
    fakeLlm(`${systemPrompt}\n${query}`),
)

const runAgentAsync = traced(
  {
    sink,
    operation: "agent.run.async",
    keys: { experiment: "exp-005", variant: "async" },
    metrics: () => ({ "tokens.total": 8 }),
  },
  async (query: string): Promise<string> => fakeLlm(query),
)

async function main(): Promise<void> {
  runAgent("What is prompt tracing?", "Be concise.", {
    experiment: "exp-005",
    variant: "v1",
    case: "case-1",
    apiKey: "sk-super-secret",
  })
  runAgent("Compare v1 against v2.", "Be concise.", {
    experiment: "exp-005",
    variant: "v2",
    case: "case-1",
  })
  await runAgentAsync("Async trace example.")
  console.log("Traces written to .prompttrace/logs/typescript-agent.jsonl")
}

void main()