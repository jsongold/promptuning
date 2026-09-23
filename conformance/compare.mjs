import { spawnSync } from "node:child_process"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))

function run(command, args) {
  const res = spawnSync(command, args, {
    cwd: join(here, ".."),
    encoding: "utf8",
  })
  if (res.status !== 0) {
    process.stderr.write(res.stderr ?? res.stdout ?? "command failed")
    process.exit(1)
  }
  return JSON.parse(res.stdout)
}

const python = run("python3", ["conformance/python/run_fixtures.py"])
const typescript = run("node", ["conformance/typescript/run_fixtures.ts"])

const pyByScenario = new Map(python.map((r) => [r.scenario, r]))
const tsByScenario = new Map(typescript.map((r) => [r.scenario, r]))

let failures = 0
for (const [name, expected] of pyByScenario) {
  const actual = tsByScenario.get(name)
  if (!actual) {
    console.error(`✗ ${name}: missing in TypeScript output`)
    failures++
    continue
  }
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    console.log(`✓ ${name}`)
  } else {
    failures++
    console.error(`✗ ${name}: mismatch`)
    console.error("  python: " + JSON.stringify(expected))
    console.error("  ts:     " + JSON.stringify(actual))
  }
}

console.log(`\n${pyByScenario.size} scenario(s); ${failures} mismatch(es)`)
process.exit(failures === 0 ? 0 : 1)