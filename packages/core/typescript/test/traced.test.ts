import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import {
  JSONLSink,
  KeyNameRedactor,
  MemorySink,
  RegexRedactor,
  traced,
} from "../src/index.ts"

test("sync success", () => {
  const sink = new MemorySink()
  const add = traced({ sink, operation: "math.add" }, (a: number, b: number) => a + b)
  assert.equal(add(2, 3), 5)
  const record = sink.records[0]
  assert.equal(record.schemaVersion, "1.0")
  assert.equal(record.operation, "math.add")
  assert.equal(record.status, "ok")
  assert.equal(record.artifacts.output, 5)
  assert.equal(record.sdk.language, "typescript")
  assert.ok(record.id)
  assert.ok(!("error" in record))
})

test("async success", async () => {
  const sink = new MemorySink()
  const fetch = traced({ sink }, async (query: string) => `answer:${query}`)
  assert.equal(await fetch("q"), "answer:q")
  assert.equal(sink.records[0].status, "ok")
  assert.equal(sink.records[0].artifacts.output, "answer:q")
})

test("sync exception recorded and original rethrown", () => {
  const sink = new MemorySink()
  const boom = traced({ sink }, () => {
    throw new TypeError("bad input")
  })
  assert.throws(() => boom(), (err: unknown) => err instanceof TypeError && err.message === "bad input")
  const record = sink.records[0]
  assert.equal(record.status, "error")
  assert.equal(record.error?.message, "bad input")
  assert.equal(record.error?.type, "TypeError")
  assert.ok(record.error?.stack)
})

test("async exception recorded and original rethrown", async () => {
  const sink = new MemorySink()
  const boom = traced({ sink }, async () => {
    throw new RangeError("async fail")
  })
  await assert.rejects(() => boom(), (err: unknown) => err instanceof RangeError)
  assert.equal(sink.records[0].status, "error")
  assert.equal(sink.records[0].error?.message, "async fail")
})

test("static and runtime keys", () => {
  const sink = new MemorySink()
  const f1 = traced({ sink, keys: { experiment: "exp-1" } }, (x: number) => x)
  f1(1)
  assert.deepEqual(sink.records[0].keys, { experiment: "exp-1" })

  const f2 = traced(
    { sink, keys: ({ args }) => ({ case: String(args[0]) }) },
    (x: number) => x,
  )
  f2(7)
  assert.deepEqual(sink.records[1].keys, { case: "7" })
})

test("artifacts and metrics resolvers", () => {
  const sink = new MemorySink()
  const run = traced(
    {
      sink,
      artifacts: ({ args }) => ({ prompt: String(args[1]) }),
      metrics: ({ result }) => ({ tokens: (result as number) ?? 0 }),
    },
    (query: string, prompt: string) => query.length,
  )
  run("hello", "system")
  const record = sink.records[0]
  assert.equal(record.artifacts.prompt, "system")
  assert.equal(record.metrics.tokens, 5)
})

test("capture input/output off", () => {
  const sink = new MemorySink()
  const f = traced({ sink, captureInput: false, captureOutput: false }, (x: number) => x * 2)
  f(10)
  assert.ok(!("input" in sink.records[0].artifacts))
  assert.ok(!("output" in sink.records[0].artifacts))
})

test("key name redaction", () => {
  const sink = new MemorySink()
  const login = traced({ sink, redactors: [new KeyNameRedactor()] }, (payload: unknown) => payload)
  login({ api_key: "secret", user: "alice", nested: { token: "abc" } })
  const output = sink.records[0].artifacts.output as Record<string, unknown>
  assert.equal(output.api_key, "[REDACTED]")
  assert.equal((output.nested as Record<string, unknown>).token, "[REDACTED]")
  assert.equal(output.user, "alice")
})

test("metric keys are not redacted", () => {
  const sink = new MemorySink()
  const f = traced(
    { sink, redactors: [new KeyNameRedactor()], metrics: () => ({ "tokens.total": 10 }) },
    (_x: number) => ({ api_key: "secret", "tokens.total": 5 }),
  )
  f(1)
  const record = sink.records[0]
  assert.deepEqual(record.metrics, { "tokens.total": 10 })
  const output = record.artifacts.output as Record<string, unknown>
  assert.equal(output.api_key, "[REDACTED]")
  assert.equal(output["tokens.total"], 5)
})

test("regex redaction", () => {
  const sink = new MemorySink()
  const f = traced({ sink, redactors: [new RegexRedactor("[A-Z]{6,}")] }, (x: unknown) => x)
  f({ text: "the SECRETKEY is here" })
  assert.equal((sink.records[0].artifacts.output as { text: string }).text, "the [REDACTED] is here")
})

test("circular reference is safe", () => {
  const sink = new MemorySink()
  const f = traced({ sink }, (x: unknown) => x)
  const node: Record<string, unknown> = { name: "node" }
  node.self = node
  f(node)
  assert.equal((sink.records[0].artifacts.output as Record<string, unknown>).self, "<circular>")
})

test("payload limit truncates", () => {
  const sink = new MemorySink()
  const f = traced({ sink, maxPayloadBytes: 400 }, (x: unknown) => x)
  f({ big: "x".repeat(5000) })
  const record = sink.records[0]
  assert.equal((record.artifacts._prompttrace as Record<string, unknown>).truncated, true)
  assert.ok(JSON.stringify(record).length < 4000)
})

test("best effort on sink failure", () => {
  const failingSink = { append: () => { throw new Error("disk full") } }
  const f = traced({ sink: failingSink }, (x: number) => x + 1)
  assert.equal(f(1), 2)
})

test("strict mode rethrows sink failure", () => {
  const failingSink = { append: () => { throw new Error("disk full") } }
  const f = traced({ sink: failingSink, strict: true }, (x: number) => x + 1)
  assert.throws(() => f(1), /disk full/)
})

test("rejects sync fn with async sink", () => {
  const asyncSink = { async append(_r: unknown) {} }
  assert.throws(() => traced({ sink: asyncSink }, (x: number) => x), /Promise/)
})

test("async fn with sync sink works", async () => {
  const sink = new MemorySink()
  const f = traced({ sink }, async (x: number) => x + 1)
  assert.equal(await f(1), 2)
  assert.equal(sink.records[0].status, "ok")
})

test("JSONL sink writes valid lines", async () => {
  const dir = mkdtempSync(join(tmpdir(), "prompttrace-"))
  try {
    const path = join(dir, "traces.jsonl")
    const sink = new JSONLSink(path)
    const f = traced({ sink }, (x: number) => x)
    f(1)
    f(2)
    const lines = readFileSync(path, "utf8").trim().split("\n").map((l) => JSON.parse(l))
    assert.equal(lines.length, 2)
    assert.equal(lines[0].status, "ok")
    assert.equal(lines[1].artifacts.output, 2)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})