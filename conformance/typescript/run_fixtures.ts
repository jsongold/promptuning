import { readFileSync, mkdirSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { KeyNameRedactor, MemorySink, traced } from "../../packages/core/typescript/src/index.ts"

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, "..")

const add = (a: number, b: number) => a + b
const boom = () => {
  throw new TypeError("bad input")
}
const identity = (value: unknown) => value
const double = (x: number) => x * 2

const FUNCTIONS: Record<string, (...args: any[]) => unknown> = {
  add,
  boom,
  identity,
  double,
}

interface Scenario {
  name: string
  operation: string
  fn: string
  args: unknown[]
  expectedOutput?: unknown
  expectedError?: { type: string; message: string }
  keys?: Record<string, string | number | boolean>
  runtimeKeys?: Record<string, string | number | boolean>
  artifacts?: Record<string, unknown>
  metrics?: Record<string, number>
  redactSensitive?: boolean
  captureInput?: boolean
  captureOutput?: boolean
}

function normalize(record: any, scenario: Scenario): Record<string, unknown> {
  const out: Record<string, unknown> = {
    scenario: scenario.name,
    operation: record.operation,
    status: record.status,
    keys: record.keys,
    artifacts: record.artifacts,
    metrics: record.metrics,
    sdkVersion: record.sdk.version,
  }
  if (record.error) {
    out.error = { type: record.error.type ?? null, message: record.error.message }
  }
  return out
}

function run(scenario: Scenario): Record<string, unknown> {
  const sink = new MemorySink()
  const fn = FUNCTIONS[scenario.fn]
  const options: any = {
    sink,
    operation: scenario.operation,
    captureInput: scenario.captureInput ?? true,
    captureOutput: scenario.captureOutput ?? true,
  }
  if (scenario.redactSensitive) options.redactors = [new KeyNameRedactor()]
  if (scenario.keys) options.keys = scenario.keys
  if (scenario.runtimeKeys) {
    const expected = scenario.runtimeKeys
    options.keys = () => ({ ...expected })
  }
  if (scenario.artifacts) {
    const fixed = scenario.artifacts
    options.artifacts = () => ({ ...fixed })
  }
  if (scenario.metrics) {
    const fixed = scenario.metrics
    options.metrics = () => ({ ...fixed })
  }

  const wrapped = traced(options, fn as any)
  const args = scenario.args as never[]
  try {
    const result = wrapped(...args)
    if ("expectedOutput" in scenario) {
      if (result instanceof Promise) throw new Error("unexpected async")
      const expected = JSON.stringify(scenario.expectedOutput)
      if (JSON.stringify(result) !== expected) throw new Error(`output mismatch: ${result}`)
    }
  } catch (err) {
    const expected = scenario.expectedError
    if (expected) {
      if (!(err instanceof Error)) throw err
      if (err.name !== expected.type || err.message !== expected.message) throw err
    } else {
      throw err
    }
  }
  return normalize(sink.records[0], scenario)
}

function main() {
  const scenarios = JSON.parse(
    readFileSync(join(root, "fixtures", "scenarios.json"), "utf8"),
  ).scenarios as Scenario[]
  const records = scenarios.map(run)
  const outDir = join(root, "expected")
  mkdirSync(outDir, { recursive: true })
  for (const record of records) {
    writeFileSync(join(outDir, `${record.scenario}.json`), JSON.stringify(record))
  }
  process.stdout.write(JSON.stringify(records, null, 2) + "\n")
}

main()