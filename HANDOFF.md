# PromptTrace handoff

Updated: 2026-09-23 (Asia/Tokyo)

## Goal

Complete PromptTrace as a local-first parallel prompt workbench, add a
library-style in-app Quickstart, and use the visual language of
https://www.omniroute.online/ without copying its branding or content.

## Completed

- Unlimited A, B, C, ... prompt variants in the workbench.
- Concurrent execution through a configurable OpenAI-compatible Chat
  Completions endpoint.
- Server-side provider credentials via `PROMPTTRACE_API_URL`,
  `PROMPTTRACE_API_KEY`, and `PROMPTTRACE_MODEL`.
- Immutable SQLite traces for successful and failed variants.
- Unlimited side-by-side historical comparison with horizontal scrolling.
- Trace detail and JSON export.
- `/quickstart` guide covering configuration, experimentation, and review.
- OmniRoute-inspired theme: warm off-white canvas, editorial serif display
  type, black/green workbench, electric-lime accents, pills, thin rules, and
  generous whitespace.
- Default development port changed to 5050 because macOS Control Center owns
  port 5000 on this machine.
- Design and README updated to match the implemented product.

## Verification

`uv run --with 'Flask>=3.0,<4' python -m unittest -v`

- 10 tests passed.
- Tests cover persistence, validation, detail, unlimited comparison, export,
  mocked concurrent provider success/partial failure, Quickstart, and column
  labels beyond Z.
- `python3 -m py_compile app.py test_app.py` passed.
- `git diff --check` passed.

## Current state

- The development server is stopped.
- `instance/prompttrace.sqlite3` is ignored and contains four earlier demo
  traces on this machine.
- No live provider credentials were supplied, so provider HTTP execution is
  covered with an injected mock, not a real paid request.
- The repository has no commits; all project files are currently untracked.
- Do not use Superset. Use built-in agent tools and local commands only.

## Remaining task

1. Start the app on port 5050.
2. Perform a browser-level visual and interaction smoke test of `/`,
   `/quickstart`, an existing detail page, and a four-run comparison.
3. Verify add/remove variant behavior and responsive layout.
4. Fix only concrete defects found; do not add speculative features.
5. Rerun the complete test suite and report exact evidence.

## Key files

- `app.py` — application, persistence, provider execution, routes.
- `templates/index.html` — workbench and history.
- `templates/quickstart.html` — user-facing Quickstart.
- `templates/compare.html` and `templates/detail.html` — trace review.
- `static/style.css` — visual system and responsive layout.
- `test_app.py` — automated checks.
- `PromptTrace_DESIGN.md` — product contract.
