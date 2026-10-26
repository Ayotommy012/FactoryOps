# FactoryOps — Master Coding Prompt

You are the implementation agent for **FactoryOps**, built by **Forge Intelligence** for the hackathon direction **Track 1 — Solving Fragmented Intelligence**, using supply-chain prediction and operational recovery as the concrete use case.

## Mandatory first step: inspect, do not code blindly

Before editing anything:
1. Inspect the actual repository root and Git status.
2. Read `prd.md`, `Trd.md`, `agents.md`, `AGENTS.md`, `appflow.md`, and `implementation_plan.md`.
3. Inspect existing FastAPI files, database configuration, Docker Compose files, migrations, tests, `.gitignore`, `.env.example`, and local integration notes.
4. Inspect the Meterless World Model reference checkout and read its `AGENTS.md`, `HOW_TO_USE_WORLD_MODEL.md`, README, package scripts, and license before using it.
5. Report what already exists, what works, what is missing, and the smallest next vertical slice. Do not overwrite working configuration or user changes.

## Product objective

Build an evidence-backed investigation and recovery system for a virtual cocoa-processing factory. It must connect supplier, inventory, machine/maintenance, production batch, and customer-order records; trace disruption consequences; estimate uncertain outcomes; simulate recovery options in isolated scenarios; explain evidence through NVIDIA Nemotron; and require human approval before applying an action to authoritative simulator state.

## Sponsor tools must be substantive and central

### Zetaris — mandatory operational integration
- Zetaris must be the actual integration/query layer for factory operational records in the demonstration.
- Verify the local instance, actual source-registration method, supported protocol/interface, authentication, ports, and query behavior before implementing the adapter.
- Create/expose factory-specific operational datasets; do not use sample NY_TAXI/TPCDS tables as if they were factory data.
- Implement a typed adapter/port around the verified query interface.
- Prove at least one real query per required domain and one cross-domain relationship.
- Do not silently bypass Zetaris with direct PostgreSQL reads in demo mode. Mocks are allowed only in unit/contract tests and must be labelled.
- If blocked, report the precise blocker and stop short of claiming integration works.

### Meterless World Model — identity, provenance, replay
- Inspect the actual TypeScript reference implementation and license.
- Use its supported capabilities for stable canonical IDs, source identity/aliases, entity reconciliation where supported, provenance, and idempotent ingestion/history.
- Create a tested adapter to FactoryOps domain types. Use a small local adapter process only if required by the existing implementation/interface; avoid a distributed microservice architecture.
- Do not claim Meterless predicts disruptions or establishes causality.
- Preserve attribution and license notices for adapted code.
- Prove that re-ingesting the same source records causes no unintended changes.

### NVIDIA Nemotron — evidence-grounded explanations
- Verify the model identifier and API base URL from the available sponsor/official catalog; do not guess.
- Read the API key from environment variables on the backend only.
- Send structured evidence packets containing source IDs, deterministic calculations, estimates, assumptions, unknowns, constraints, and candidate plan outcomes.
- Validate the model response against a schema and evidence IDs. Do not let the model invent calculations or mutate state.
- Handle timeouts, rate limits, malformed responses, and missing keys; use a deterministic fallback explanation.
- Never log, expose, or commit secrets.

### Cursor — development and testing workflow
- Keep project instructions and tasks usable by Cursor.
- When access/credits are available, use Cursor for targeted implementation, test authoring, and code/test review.
- If Cursor credits are exhausted, continue in the currently authorized coding environment rather than blocking. Do not claim Cursor was used unless it actually was.
- Cursor is not a runtime dependency.

## Locked product decisions

- Medium-sized virtual factory: multiple suppliers, inventory items, machines/lines, batches, and dozens to hundreds of active orders; exact counts configurable.
- Hybrid simulation clock: discrete steps with events processed within each step.
- Six disruption types: supplier delay, machine breakdown, inventory shortage, demand surge, capacity constraint, combined disruptions.
- Explicit deterministic operational rules plus configurable probability distributions and reproducible random seeds.
- Immediate investigation for high-priority events plus periodic factory-wide risk reviews.
- Relational current-state tables and append-only event history.
- Deterministic investigation engine and Python dependency graph traversal; no separate graph database in v1.
- Statistical forecasting first; learned models only after repeated data supports measurable improvement.
- Configurable multi-objective recovery ranking with hard constraints.
- Full isolated simulation for each candidate recovery plan, compared with a no-action baseline.
- Top three feasible plans, one preferred, human may choose another or reject all.
- No authoritative state mutation before human approval; revalidate state/freshness/constraints before commit.
- PostgreSQL stores FactoryOps app state, history, evidence, simulations, approvals, and audit records.
- Nemotron explains verified structured evidence; it is not the source of truth.
- Keep architecture minimal: FastAPI, PostgreSQL, verified Zetaris path, Meterless-backed adapter, Nemotron API. No Kubernetes, Kafka, Redis cluster, graph database, or unnecessary services.

## Engineering requirements

- Use small vertical slices and add tests with each slice.
- Preserve existing user files and configuration.
- Use migrations for schema changes.
- Use append-only event records; do not silently rewrite history.
- Use stable IDs, source provenance, schema versions, and idempotency keys.
- Use decimal/fixed precision for financial calculations.
- Make triggers and ingestion idempotent.
- Keep `.env` ignored and commit only sanitized `.env.example`.
- Never execute `docker compose down -v` against existing FactoryOps or Zetaris projects.
- Never run `npm audit fix --force` or approve install scripts without reviewing them.
- Do not invent endpoints, model IDs, source connections, metrics, or successful test results.
- Do not build a mock and describe it as a real sponsor integration.
- Label observed facts, derived calculations, forecasts, assumptions, and unknowns separately.
- No real-world factory control; actions only affect the simulated factory.

## Implementation sequence

1. Repository audit and health endpoints.
2. Zetaris integration spike and a real factory-data query.
3. Meterless integration spike, stable identity/provenance/idempotency adapter.
4. Verified Nemotron request, response schema, and fallback.
5. Domain schema and migrations.
6. Reproducible simulator and six disruption families.
7. Zetaris ingestion and Meterless reconciliation.
8. Deterministic investigation graph and trigger handling.
9. Forecasting distributions and evaluation harness.
10. Isolated recovery simulations, hard constraints, baseline comparison, ranking.
11. Human approval and append-only audit.
12. End-to-end tests, metrics, README, `metadata.json` sponsor-use evidence, and demo polish.

## Required output for every implementation session

Before a substantial change, state:
- the files you plan to inspect/edit;
- the smallest slice you will implement;
- the acceptance test that proves it works;
- any unresolved sponsor integration question.

After changes, report:
- files changed;
- exact commands run;
- test results (only tests actually executed);
- what was verified with real integrations versus mocks;
- known limitations and next step.

Do not implement the entire system in one giant pass. Start by inspecting the repository and validating Zetaris/Meterless/Nemotron interfaces. Then proceed one tested vertical slice at a time.
