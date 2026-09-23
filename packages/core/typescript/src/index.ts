import { randomUUID } from "node:crypto"
import { appendFileSync, mkdirSync } from "node:fs"
import { dirname } from "node:path"

export const SDK_VERSION = "0.1.0"

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }
export type SearchValue = string | number | boolean
export type JsonObject = { [key: string]: JsonValue }

export interface TraceError {
  type?: string
  message: string
  stack?: string
  data?: JsonValue
}

export interface TraceRecord {
  schemaVersion: "1.0"
  id: string
  parentId?: string
  operation: string
  startedAt: string
  endedAt: string
  durationMs: number
  status: "ok" | "error"
  keys: Record<string, SearchValue>
  artifacts: Record<string, JsonValue>
  metrics: Record<string, number>
  error?: TraceError
  sdk: { language: "python" | "typescript"; version: string }
}

export interface SyncTraceSink {
  append(record: TraceRecord): void
}

export interface AsyncTraceSink {
  append(record: TraceRecord): Promise<void>
}

export type TraceSink = SyncTraceSink | AsyncTraceSink

export interface BeforeContext<Args extends unknown[]> {
  args: Args
}

export interface TraceContext<Args extends unknown[], Result> {
  args: Args
  result?: Result
  error?: unknown
  startedAt: string
  endedAt: string
  durationMs: number
}

export type SearchKeys = Record<string, SearchValue>
export type KeyResolver<Args extends unknown[]> = (ctx: BeforeContext<Args>) => SearchKeys

export interface TraceOptions<Args extends unknown[], Result> {
  sink: TraceSink
  operation?: string
  keys?: SearchKeys | KeyResolver<Args>
  artifacts?: (ctx: TraceContext<Args, Result>) => JsonObject
  metrics?: (ctx: TraceContext<Args, Result>) => Record<string, number>
  captureInput?: boolean
  captureOutput?: boolean
  captureStack?: boolean
  redactors?: Redactor[]
  maxPayloadBytes?: number
  strict?: boolean
}

export interface Redactor {
  redact(value: JsonValue): JsonValue
}

const SENSITIVE_KEY_NAMES = [
  "authorization",
  "api_key",
  "apikey",
  "token",
  "password",
  "passwd",
  "secret",
  "cookie",
  "access_key",
  "private_key",
]

export class KeyNameRedactor implements Redactor {
  private readonly re: RegExp
  readonly replacement: string

  constructor(names: string[] = SENSITIVE_KEY_NAMES, replacement = "[REDACTED]") {
    this.re = new RegExp(`(?:${names.map(escapeRegex).join("|")})(?!\\w)`, "i")
    this.replacement = replacement
  }

  redact(value: JsonValue): JsonValue {
    if (Array.isArray(value)) return value.map((v) => this.redact(v))
    if (value !== null && typeof value === "object") {
      const out: Record<string, JsonValue> = {}
      for (const [key, item] of Object.entries(value)) {
        out[key] = this.re.test(key) ? this.replacement : this.redact(item)
      }
      return out
    }
    return value
  }
}

export class RegexRedactor implements Redactor {
  private readonly re: RegExp
  readonly replacement: string

  constructor(pattern: string, replacement = "[REDACTED]") {
    this.re = new RegExp(pattern)
    this.replacement = replacement
  }

  redact(value: JsonValue): JsonValue {
    if (Array.isArray(value)) return value.map((v) => this.redact(v))
    if (value !== null && typeof value === "object") {
      const out: Record<string, JsonValue> = {}
      for (const [key, item] of Object.entries(value)) out[key] = this.redact(item)
      return out
    }
    if (typeof value === "string") return value.replace(this.re, this.replacement)
    return value
  }
}

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

export class MemorySink implements SyncTraceSink {
  readonly records: TraceRecord[] = []

  append(record: TraceRecord): void {
    this.records.push(record)
  }
}

export class JSONLSink implements SyncTraceSink {
  readonly path: string

  constructor(path: string) {
    this.path = path
    mkdirSync(dirname(path), { recursive: true })
  }

  append(record: TraceRecord): void {
    appendFileSync(this.path, JSON.stringify(record) + "\n", "utf8")
  }
}

function toJsonable(value: unknown, seen: WeakSet<object> = new WeakSet()): JsonValue {
  if (value === null || value === undefined) return null
  const t = typeof value
  if (t === "string" || t === "boolean") return value as JsonValue
  if (t === "number") {
    return Number.isFinite(value as number) ? (value as number) : String(value)
  }
  if (t === "bigint") return String(value)
  if (t === "function") return "[function]"
  if (value instanceof Date) return value.toISOString()
  if (value instanceof Map) {
    if (seen.has(value)) return "<circular>"
    seen.add(value)
    const out: Record<string, JsonValue> = {}
    for (const [k, v] of value.entries()) out[String(k)] = toJsonable(v, seen)
    seen.delete(value)
    return out
  }
  if (value instanceof Set) {
    if (seen.has(value)) return "<circular>"
    seen.add(value)
    const out = [...value].map((v) => toJsonable(v, seen))
    seen.delete(value)
    return out
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) return "<circular>"
    seen.add(value)
    const out = value.map((v) => toJsonable(v, seen))
    seen.delete(value)
    return out
  }
  if (t === "object") {
    const obj = value as Record<string, unknown>
    if (seen.has(value as object)) return "<circular>"
    seen.add(value as object)
    const out: Record<string, JsonValue> = {}
    for (const key of Object.keys(obj)) out[key] = toJsonable(obj[key], seen)
    seen.delete(value as object)
    return out
  }
  return String(value)
}

function utcNow(): string {
  return new Date().toISOString()
}

function resolveKeys<Args extends unknown[]>(
  keys: SearchKeys | KeyResolver<Args> | undefined,
  args: Args,
): SearchKeys {
  if (keys === undefined) return {}
  if (typeof keys === "function") return keys({ args })
  return keys
}

function captureError(error: unknown, withStack: boolean): TraceError {
  const record: TraceError = {
    message: error instanceof Error ? error.message : String(error),
  }
  if (error instanceof Error) {
    record.type = error.name
    if (withStack && error.stack) record.stack = error.stack
  }
  return record
}

function fitPayload(record: TraceRecord, maxBytes: number): TraceRecord {
  if (JSON.stringify(record).length <= maxBytes) return record
  const artifacts = record.artifacts
  const fitted: Record<string, JsonValue> = {}
  for (const [key, value] of Object.entries(artifacts)) {
    if (typeof value === "string" && value.length > 256) fitted[key] = value.slice(0, 256) + "..."
    else fitted[key] = value
  }
  fitted._prompttrace = { truncated: true }
  record.artifacts = fitted
  if (JSON.stringify(record).length <= maxBytes) return record
  record.artifacts = { _prompttrace: { truncated: true } }
  return record
}

function makeRecord<Args extends unknown[], Result>(
  options: Required<Pick<TraceOptions<Args, Result>, "captureInput" | "captureOutput" | "captureStack">> &
    TraceOptions<Args, Result>,
  operation: string,
  args: Args,
  startedAt: string,
  endedAt: string,
  durationMs: number,
  result: Result | undefined,
  error: unknown | undefined,
): TraceRecord {
  const keys = resolveKeys(options.keys, args)
  const artifacts: Record<string, JsonValue> = {}
  if (options.captureInput) artifacts.input = toJsonable({ args })
  if (options.captureOutput && error === undefined) artifacts.output = toJsonable(result)
  if (options.artifacts) {
    const extra = options.artifacts({
      args,
      result,
      error,
      startedAt,
      endedAt,
      durationMs,
    })
    if (extra !== null && typeof extra === "object") Object.assign(artifacts, extra)
  }
  let metrics: Record<string, number> = {}
  if (options.metrics) {
    const resolved = options.metrics({
      args,
      result,
      error,
      startedAt,
      endedAt,
      durationMs,
    })
    if (resolved !== null && typeof resolved === "object") metrics = resolved
  }

  const record: TraceRecord = {
    schemaVersion: "1.0",
    id: randomUUID(),
    operation,
    startedAt,
    endedAt,
    durationMs: Math.round(durationMs * 1000) / 1000,
    status: error === undefined ? "ok" : "error",
    keys,
    artifacts,
    metrics,
    sdk: { language: "typescript", version: SDK_VERSION },
  }
  if (error !== undefined) record.error = captureError(error, options.captureStack)
  return record
}

function applyRedactors(record: TraceRecord, redactors: Redactor[] | undefined): TraceRecord {
  if (!redactors || redactors.length === 0) return record
  let value: JsonValue = record as unknown as JsonValue
  for (const redactor of redactors) value = redactor.redact(value)
  const redacted = value as unknown as TraceRecord
  redacted.metrics = record.metrics
  return redacted
}

function reportFailure(error: unknown): void {
  console.warn("prompttrace failed to record trace:", error)
}

function isAsync(fn: unknown): boolean {
  return typeof fn === "function" && fn.constructor.name === "AsyncFunction"
}

export function traced<Args extends unknown[], Result>(
  options: TraceOptions<Args, Result>,
  fn: (...args: Args) => Result,
): (...args: Args) => Result

export function traced<Args extends unknown[], Result>(
  options: TraceOptions<Args, Result>,
  fn: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result>

export function traced<Args extends unknown[], Result>(
  options: TraceOptions<Args, Result>,
  fn: (...args: Args) => Result | Promise<Result>,
): (...args: Args) => Result | Promise<Result> {
  const sink = options.sink
  const asyncSink = isAsync(sink.append)
  const operation = options.operation ?? (fn.name || "anonymous")
  const maxBytes = options.maxPayloadBytes ?? 1_000_000
  const strict = options.strict ?? false
  const captureStack = options.captureStack ?? true

  if (!isAsync(fn) && asyncSink) {
    throw new TypeError(
      "Cannot wrap a synchronous function with an asynchronous sink: the return value would become a Promise.",
    )
  }

  const build = (
    args: Args,
    startedAt: string,
    started: number,
    result: Result | undefined,
    error: unknown | undefined,
  ): TraceRecord => {
    const endedAt = utcNow()
    const durationMs = performance.now() - started
    const record = makeRecord(
      {
        ...options,
        captureInput: options.captureInput ?? true,
        captureOutput: options.captureOutput ?? true,
        captureStack,
      },
      operation,
      args,
      startedAt,
      endedAt,
      durationMs,
      result,
      error,
    )
    return fitPayload(applyRedactors(record, options.redactors), maxBytes)
  }

  const emit = async (record: TraceRecord): Promise<void> => {
    if (asyncSink) await sink.append(record)
    else (sink as SyncTraceSink).append(record)
  }

  if (isAsync(fn)) {
    return async (...args: Args): Promise<Result> => {
      const startedAt = utcNow()
      const started = performance.now()
      let result: Result | undefined
      let error: unknown
      try {
        result = (await fn(...args)) as Result
      } catch (err) {
        error = err
      }
      const record = build(args, startedAt, started, result, error)
      try {
        await emit(record)
      } catch (err) {
        if (strict) throw err
        reportFailure(err)
      }
      if (error !== undefined) throw error
      return result as Result
    }
  }

  return (...args: Args): Result => {
    const startedAt = utcNow()
    const started = performance.now()
    let result: Result | undefined
    let error: unknown
    try {
      result = fn(...args) as Result
    } catch (err) {
      error = err
    }
    try {
      const record = build(args, startedAt, started, result, error)
      if (asyncSink) {
        void emit(record).catch((err) => {
          if (strict) throw err
          reportFailure(err)
        })
      } else {
        ;(sink as SyncTraceSink).append(record)
      }
    } catch (err) {
      if (strict) throw err
      reportFailure(err)
    }
    if (error !== undefined) throw error
    return result as Result
  }
}

export { toJsonable }