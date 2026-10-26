# FactoryOps — Agent Instructions

These instructions apply to AI coding agents working in this repository. Read this file and the project documents before making changes.

## 1. Read first

1. `prd.md`
2. `Trd.md`
3. `appflow.md`
4. `implementation_plan.md`
5. This file (`agents.md`; also see `AGENTS.md` for tools that expect that conventional filename).
6. Any existing root README, project configuration, migrations, tests, `.env.example`, and relevant local integration documentation.
7. For Meterless work, inspect the downloaded reference's `AGENTS.md`, `HOW_TO_USE_WORLD_MODEL.md`, `README.md`, package scripts, and license before adapting code.

## 2. Product invariants

- FactoryOps is an evidence-backed investigation and recovery system for a simulated cocoa-processing factory.
- Zetaris is a central, mandatory operational data access/integration path in the demonstration.
- Meterless World Model functionality must materially support canonical identity/reconciliation, provenance, and idempotent ingestion/history patterns.
- NVIDIA Nemotron must materially support evidence-grounded explanation and trade-off synthesis.
- Cursor is a development-time sponsor tool, not a runtime dependency. The repository should be Cursor-ready even when implementation is performed in Antigravity.
- Deterministic calculations establish operational facts. Nemotron explains structured evidence; it does not create source facts, causal links, or authoritative numeric calculations.
- Recovery candidates run in isolated simulation copies. No authoritative state changes before human approval.
- Use event-triggered high-priority investigations plus periodic reviews.
- Use append-only event history and explicit provenance.
- No claim of calibrated forecasting until repeated/held-out evaluation supports it.

## 3. Work in small vertical slices

- Inspect before editing. Do not assume the repository is empty.
- Report current state and the next smallest step before substantial changes.
- Implement one testable slice at a time.
- Prefer explicit interfaces, typed schemas, small modules, and deterministic functions.
- Keep a concise `docs/decisions.md` for material architecture decisions if one does not exist.
- After each slice, run relevant tests/lint/type checks and report exact commands and results.
- Do not claim tests passed unless you actually ran them.
- Do not generate large amounts of untested code in one pass.

## 4. Mandatory integration honesty

### Zetaris
- Verify the actual running instance, supported source registration, interface, authentication, and query behavior.
- Do not treat sample NY_TAXI/TPCDS data as factory data.
- Do not silently read directly from source PostgreSQL as a fallback in the hackathon demo.
- Test at least one real query through Zetaris before claiming integration works.
- If blocked, preserve the adapter boundary and report the exact blocker. A mock is allowed for unit tests only and must be labelled.

### Meterless World Model
- Inspect the real reference code and license.
- Reuse/adapt only the parts whose interfaces and license permit it.
- Preserve attribution and license notices.
- Test stable identity, provenance retention, duplicate handling, and idempotent replay.
- Do not claim it predicts disruptions or proves causality unless the actual code demonstrably does that.

### NVIDIA Nemotron
- Verify model ID/base URL from the sponsor-provided API catalog or official docs.
- Backend only; secrets in environment variables.
- Validate output schema and evidence references.
- Provide deterministic fallback behavior for API failures.
- Do not invent model IDs or include API keys in code, browser bundles, screenshots, or logs.

### Cursor
- Keep repository instructions, tasks, and tests clear for Cursor.
- Use Cursor for implementation/test authoring/review when access is available. If credits are exhausted, continue using the available authorized coding environment and record that Cursor is a development workflow tool, not a runtime dependency.
- Never claim a specific tool was used unless it was actually used.

## 5. Secrets and local environment

- Never ask the user to paste secrets into chat.
- Never print `.env` contents or include credentials in logs, screenshots, commits, docs, or final responses.
- Keep `.env` ignored by Git; commit only sanitized `.env.example`.
- Use URL-encoded database credentials in connection strings.
- Do not change global system configuration unless necessary and explicitly discussed.
- Do not disrupt the separate Zetaris deployment or its named volumes.
- Never run `docker compose down -v` on existing projects.
- Do not run `npm audit fix --force`; inspect advisories and scripts before any change.

## 6. Data correctness and safety

- Use UTC timestamps for recorded time and explicit simulation time for simulated events.
- Use Decimal/fixed precision for costs and quantities where appropriate; avoid floating-point money arithmetic.
- Make ingestion and trigger processing idempotent.
- Preserve source IDs, source table/dataset, retrieval time, and schema version.
- Distinguish `observed`, `derived`, `forecast`, `assumption`, and `unknown`.
- Never silently overwrite append-only history.
- Use transactions for approval and authoritative state transitions.
- Reject or re-simulate a plan if the source state has changed since evaluation.
- Enforce hard constraints before weighted ranking.
- Ensure all candidate plans use comparable baseline conditions.

## 7. Test requirements

At minimum, add tests for:
- inventory/material balance and non-negative inventory;
- machine capacity and batch state transitions;
- order deadline/late-order calculations;
- dependency traversal and expected impact paths;
- fixed random-seed reproducibility;
- same-source-record idempotency;
- stable canonical entity identity;
- evidence/provenance retention;
- recovery hard constraints and weighted ranking;
- isolated scenario does not mutate authoritative state;
- approval revalidation against stale state;
- Nemotron malformed/unavailable output fallback;
- Zetaris adapter failures and missing/stale data;
- complete end-to-end scenario replay.

Use mocks for failure cases, but also document at least one actual Zetaris query and a controlled live Nemotron smoke test when sponsor credentials are available.

## 8. Git and file safety

- Do not delete existing files, rewrite project history, reset branches, or overwrite user changes without permission.
- Keep changes focused and inspect `git status` before and after work.
- Do not add generated data, local databases, `.env`, tokens, or the entire external reference checkout to the project repository.
- Add license/attribution when vendoring adapted Meterless code.
- Use migration files for schema changes; do not rely only on manual database edits.
- Keep documentation synchronized with the behavior actually implemented.

## 9. Definition of done for each task

A task is complete only when:
1. the change is implemented;
2. relevant tests pass and commands/results are reported;
3. configuration/documentation is updated;
4. no secret or unsafe destructive action was introduced;
5. actual integrations and mocks are clearly distinguished;
6. known limitations are stated.
