# FactoryOps — Implementation Plan

**Team:** Forge Intelligence  
**Approach:** Small vertical slices, integration-first, measurable acceptance checks  
**Important:** Do not start by generating all business logic. First inspect the repository and validate the sponsor integration contracts.

## Phase 0 — Repository and safety baseline

### Tasks
- Inspect the actual FactoryOps root, current Git status, existing files, Docker Compose configuration, `.gitignore`, `.env.example`, and Python version/virtual environment.
- Read `prd.md`, `Trd.md`, `agents.md`, `AGENTS.md`, `appflow.md`, and this plan.
- Preserve existing user changes and the separate Zetaris deployment.
- Confirm `.env` is ignored and secrets are not tracked.
- Create/verify a minimal health endpoint and PostgreSQL health check only if absent.
- Add a `docs/decisions.md` and `docs/integration-status.md` if absent.

### Exit criteria
- Existing project state is documented.
- API and database health checks pass.
- No secrets exposed and no destructive Docker commands run.

## Phase 1 — Sponsor integration spikes (highest priority)

### 1A. Zetaris (blocker)
- Inspect the running local Zetaris instance, logs, available sources, docs, supported interfaces, and authentication.
- Confirm how to expose the factory simulator's source datasets to Zetaris.
- Execute real queries for each operational domain and at least one cross-domain relationship.
- Document exact working configuration without credentials.
- Implement the minimal real adapter behind a `ZetarisOperationalDataPort`.
- Add contract tests for rows, errors, timeouts, missing tables, and stale data.

**Exit criteria:** The demo can query factory-specific operational data through Zetaris. A mock-only connection does not pass.

### 1B. Meterless World Model
- Read the reference repository's `AGENTS.md`, usage guide, README, package scripts, and license.
- Re-run the reference tests and idempotency check.
- Select the supported identity/provenance/idempotency operations that fit FactoryOps.
- Build the smallest working adapter with versioned data contracts.
- Add a factory entity fixture, stable-ID test, provenance test, duplicate/replay test, and attribution/license notes.

**Exit criteria:** A real adapter invocation preserves source identity/provenance and repeat ingestion is idempotent.

### 1C. NVIDIA Nemotron
- Verify the API base URL and exact model ID from sponsor-provided/official information.
- Add backend environment settings and a minimal request.
- Add schema validation, timeout handling, safe error messages, and deterministic fallback.
- Add unit tests with mocked HTTP responses and one controlled live smoke test when credentials are configured.

**Exit criteria:** A verified API call produces schema-valid evidence-grounded explanation, and failure does not break deterministic investigation.

### 1D. Cursor development workflow
- Keep this plan, `AGENTS.md`, and test commands clear for Cursor.
- When credits/access permit, use Cursor for test authoring, test review, and targeted implementation support.
- If Cursor remains unavailable, use the available authorized coding environment; do not block the project or claim Cursor was used when it was not.
- Record meaningful development/test workflow evidence in README.

**Exit criteria:** Repository is agent-ready; development tooling is honestly documented, without introducing a runtime dependency.

## Phase 2 — Domain model and database

### Tasks
- Define core entities and state transitions.
- Separate `factory_source` operational source tables from `factoryops` application-owned tables, or document an alternative if Zetaris requires separate source connections.
- Add migrations for current-state projections, append-only event history, source provenance, canonical entity mapping, investigations, impact findings, forecast runs, recovery plans, simulations, approvals, and audit events.
- Add idempotency keys and appropriate uniqueness constraints.
- Use decimal types for costs/quantities where required and timezone-aware recorded timestamps.
- Seed a small deterministic factory and validate relational constraints.

### Exit criteria
- Migrations work from an empty database.
- Seed data is repeatable.
- Invariants and unique keys have tests.
- Data ownership is clear.

## Phase 3 — Simulator vertical slice

### Tasks
- Implement discrete simulation steps and event processing.
- Implement suppliers/deliveries, inventory, machines, production batches, and orders.
- Add configurable seed and versioned scenario configuration.
- Implement the six disruption families.
- Record events append-only and update current state transactionally.
- Expose simulator control through API endpoints.

### Exit criteria
- One supplier delay can be replayed reproducibly.
- Inventory, batch, machine, and order state remain valid.
- Combined disruption scenario works.
- Tests cover invariants and reproducibility.

## Phase 4 — Zetaris ingestion + Meterless identity

### Tasks
- Implement typed domain normalization from actual Zetaris query results.
- Preserve source dataset/table/record key, retrieval time, schema version, and watermark.
- Resolve canonical entity identity and aliases through the Meterless-backed adapter.
- Persist idempotent application projections and append-only ingestion events.
- Expose integration health and data freshness.

### Exit criteria
- Every required domain is queried through Zetaris.
- Same records ingested twice produce no unintended changes.
- Entity provenance can be traced from finding back to source record.
- Failure/staleness is visible.

## Phase 5 — Investigation engine

### Tasks
- Define explicit dependency relationships: supplier → delivery → inventory/material availability → batch/line/machine → finished goods → customer order.
- Implement bounded graph traversal in Python.
- Implement deterministic calculations for inventory requirements, capacity, batch timing, and order deadline risk.
- Add immediate high-priority triggers and periodic review triggers.
- Persist evidence-backed findings and certainty labels.

### Exit criteria
- Labelled scenarios match expected impact paths.
- Each finding has source evidence and calculation metadata.
- Trigger processing is idempotent.
- Periodic review detects a gradual/interacting risk.

## Phase 6 — Forecasting

### Tasks
- Define uncertain variables and configured distributions.
- Persist distribution/config version, seed, forecast horizon, and observed outcomes.
- Run repeated/held-out simulations.
- Measure calibration and error; compare to a simple baseline.
- Display estimates as uncalibrated until evidence justifies stronger claims.

### Exit criteria
- Results are reproducible.
- Metrics are computed from simulation outcomes, not hardcoded.
- Documentation clearly describes calibration limitations.

## Phase 7 — Recovery simulation and ranking

### Tasks
- Define candidate action types and their operational preconditions.
- Implement hard-constraint validation.
- Snapshot relevant state and run no-action baseline.
- Simulate each feasible plan in an isolated scenario.
- Calculate outcomes and deltas.
- Implement configurable multi-objective ranking.
- Return the top three feasible plans and the reason for ranking.

### Exit criteria
- Candidate scenarios never mutate authoritative state.
- Same initial state/config yields fair comparisons.
- Infeasible plans are rejected with explicit reasons.
- Recovery outcomes can be compared with no action.

## Phase 8 — Nemotron explanation

### Tasks
- Create a versioned evidence packet.
- Call the verified Nemotron API from the backend.
- Require structured output that references supplied evidence IDs.
- Validate claims and numeric fields; label forecasts, assumptions, and unknowns.
- Add deterministic fallback.
- Log model/config version and latency without secrets.

### Exit criteria
- The model explains verified findings and plan trade-offs.
- Missing evidence is disclosed.
- Invalid output is rejected/falls back.
- The application remains functional when the model is unavailable.

## Phase 9 — Human approval and audit

### Tasks
- Add approve, choose alternative, and reject-all flows.
- Revalidate plan/state version, data freshness, resources, and hard constraints at approval time.
- Apply only permitted simulator actions transactionally.
- Record decisions and results as append-only events.
- Verify rejected/stale plans do not mutate state.

### Exit criteria
- Approval safety tests pass.
- The audit trail links trigger → evidence → plan → decision → outcome.

## Phase 10 — Demo interface, evaluation, and polish

### Tasks
- Provide a minimal dashboard or clear API views for system status, incident, impact chain, forecasts, plan comparison, approval, and audit.
- Prepare at least three reproducible demo scenarios, including a combined disruption.
- Run a baseline and collect investigation, recovery, forecasting, and latency metrics.
- Add setup instructions, architecture diagram, known limitations, and sponsor attribution/use in README and `metadata.json` if the hackathon requires it.
- Capture genuine evidence of working Zetaris queries, Meterless idempotency, Nemotron explanation, and development/testing workflow.
- Run clean setup and full automated test suite.

### Exit criteria
- Fresh setup instructions work.
- One end-to-end demo is reproducible.
- Sponsor tool claims are backed by actual execution.
- No secrets are committed.
- Results and limitations are presented honestly.

## Phase 11 — Hybrid Simulation Timing & Recovery Lifecycle

### Tasks
- Automatic progression driver (`SimulationAutoDriver`): Advances eligible simulation scenarios by one discrete step (15 simulated minutes) every 5.0 seconds of real time.
- Single-worker leader election: Enforce single-worker execution across application restarts and multiple uvicorn worker processes via PostgreSQL session-scoped advisory lock (`SIMULATION_DRIVER_LOCK_ID = 84729103`).
- Deterministic simulation engine integrity: Preserve discrete event ordering, reproducibility with fixed seed, and zero row drift.
- Manual progression controls: Coordinate manual `Step +1` and `Step +3` actions alongside background auto-ticks with row-level locks (`SELECT FOR UPDATE`).
- Human approval guardrails: Automatically pause progression when an anomaly is detected and an incident is awaiting an operator recovery decision (`is_awaiting_recovery_decision()`).
- Complete recovery maintenance lifecycle: Fix state machine defect by allowing `step_scenario` during `recovering` status. Compute plan duration in discrete steps (e.g. 0.75h = 3 steps). Progress through maintenance while tracking throttled throughput, and automatically transition `scenario.status` back to `running` with restored nominal machine throughput upon reaching `completed_step`.
- UI operational controls: Display live progression badge (`🟢 Auto (1 step / 5s)`, `⏸️ Auto Paused`, `⚠️ Awaiting Decision`, `🔧 Recovering (X steps left)`), Pause/Resume toggle button, and synchronize frontend polling with backend timing state.

### Exit criteria
- Automatic driver ticks eligible scenarios at 5-second real-time intervals.
- Progression halts when a disruption awaits an operator decision.
- Approved recovery maintenance steps progress cleanly and complete back to `running`.
- Manual and automatic steps do not race or corrupt PostgreSQL state.
- Automated tests verify all 8 hybrid timing scenarios.

## Phase 8 — Integrated Supply Chain, Disruption Simulator, Dynamic Production Line & Cocoa Visual Identity

### Focus
Implement focused supply-chain disruption simulation (Option A), 6-event disruption simulator catalogue with Random mode, dynamic physical derivation of all 7 production stages with upstream/downstream blocking, and an authentic Cocoa-Industrial SCADA editorial visual redesign.

### Tasks
- **Supply-Chain Disruption & Lookahead Forecasting (`app/services/production_line.py` & `app/services/recovery.py`):**
  - Implement working inventory in Silo 1 (3,600 kg) and inbound ocean freight shipment tracking (`SHP-2026-COCOA-01`, 15,000 kg, PO-2026-88).
  - Model administrative customs clearance hold (+6.0h / +24 simulation steps) and supplier delivery shortfall disruptions.
  - Implement inventory lookahead forecasting math: $T_{stockout} = Q_{silo} / R_{burn}$, calculating exact steps/time until silo runs dry (12 steps / 3.0h under nominal 1,200 kg/h roasting feed).
  - Compute production starvation window (20 steps / 5.0h) where roaster and cleaning lines are starved before delayed shipment arrives at Step 32.
  - Trace downstream customer order exposure: `customer_order:erp:ORD-9402` (Artisan Chocolatier BV) breaching SLA with $350/day penalty.
  - Implement 3 ranked supply-chain recovery plans with human-in-the-loop authorization:
    1. *Spot Market Buffer Procurement* (5,000 kg lot, $2,850 cost, +2.5h safety buffer, eliminates stockout).
    2. *Recipe Line Throttle* (throttle feed to 180 kg/h / 45 kg per step, $450 cost, stretches inventory across 20 hours).
    3. *Customs Expedite & Priority Clearance* (authorized bonded broker clearance, $1,200 cost, advances ETA by 16 steps / 4.0h).
- **Disruption Simulator Catalogue & Random Mode (`app/services/disruptions.py`):**
  - Six industrial disruption definitions across Supply Chain, Machinery & Equipment, and Inventory & Quality.
  - Dynamic prerequisite validation (e.g. roaster cannot overheat if offline or recovering; raw lot cannot be quarantined twice).
  - Random mode selection: Uniform probability across all eligible disruptions with automated parameter boundary generation.
  - Consequence propagation to machines, batches, inventory, stages, and orders with append-only `OperationalEvent` logging and World Model fact assertion.
- **Dynamic 7-Stage Production Line (`app/services/production_line.py`):**
  - Authoritative physical derivation for all 7 stages: `CLEAN-01`, `ROAST-01`, `WINNOW-01`, `MILL-01`, `PRESS-01`, `PULV-01`, `PACK-01`.
  - State machine covering `Running`, `At Risk`, `Blocked`, `Offline`, `Recovering`, `Queued`, `Completed`.
  - Physical cascading dependency: Silo empty or quarantined propagates blocking to Stage 1 and Stage 2, starving downstream stages.
- **Cocoa-Industrial Editorial Visual Redesign:**
  - Purpose-built SCADA editorial design: Charcoal ink (`#252820`), Warm ivory canvas (`#F3EFE5`), Cocoa brown (`#6B4937`), Muted sage (`#7D8B72`), Burnt orange (`#C66B3D`), Muted steel (`#777D80`), Deep brick red (`#A7473D`).
  - Monospace tabular SCADA data typography, fine dividers, crisp borders, zero generic AI glowing gradients.
  - Interactive Stage Detail Inspector: Click any stage to inspect live SCADA telemetry (throughput kg/h, health %, bearing temperature °C, vibration RMS, inputs, outputs, operational reasons, upstream/downstream links).
  - 25-Step depletion trajectory table and stockout risk gauges in React Operator Console.
- **API Endpoints (`app/api/simulations.py`):**
  - `GET /simulations/{id}/disruptions/catalogue`
  - `POST /simulations/{id}/disruptions/trigger` (supports named disruptions & `"random"`)
  - `GET /simulations/{id}/production-line`
  - `GET /simulations/{id}/supply-chain`
- **Verification & Automated Tests (`tests/test_disruptions_and_supply_chain.py`):**
  - 12 comprehensive unit and integration tests covering all requirements.
  - Full test suite passes: 88 passed, 0 failed.

### Exit criteria
- All 6 disruptions in catalogue triggerable with parameter validation.
- Random mode selects uniformly from eligible events and applies consequences.
- Inventory depletion and starvation gap calculated deterministically.
- All 7 stages reflect real-time physical states and cascading blocking.
- Production build succeeds (`vite build`) and serves at `/console`.
- Full pytest test suite passes (88/88 tests).

## Chunk 6 — Run Button Modal, 7-Stage Sequential Cycle & Black-Green Terminal UI

### Implemented Features
- **Centered Modal Dialog for Disruption Selection (`frontend/src/components/RunModal.tsx`):**
  - Replaced inline select/pop-under with elevated centered modal dialog (`z-index: 1000`, backdrop blur, semi-transparent overlay).
  - Wired directly to the primary **Run** button in the SCADA header bar.
  - Dynamically fetches catalogue from `GET /simulations/{id}/disruptions/catalogue` with live eligibility checks.
  - Prominent "🎲 Random Scenario Injection" option card for one-click stress-testing.
  - Inline error banners with retry capability, loading spinners, and submit locks to prevent duplicate submissions.
  - Closes on Escape key press, backdrop click, or explicit close button.
  - Immediately refreshes simulation and operational state across the app upon success.
- **Continuous 7-Stage Sequential Manufacturing Lifecycle:**
  - Implemented sequential manufacturing lifecycle in backend `app/services/simulation.py` and `app/services/production_line.py`:
    - Cycle start (Step 0): Stage 1 = `Running`, Stages 2–7 = `Pending`.
    - Active stage finishes upon reaching its duration steps $\rightarrow$ marks stage `Completed`, next stage becomes `Running`, subsequent stages remain `Pending`.
    - Cycle completion: When Stage 7 finishes, `completed_cycles` increments, `cycle_number` increments, and a new manufacturing cycle begins with Stage 1 `Running` and Stages 2–7 `Pending`.
    - Driven by actual simulation clock and discrete steps, preserving full determinism.
    - Anomaly-aware: Roaster overheat or bean quarantine inhibits progression and marks stages `At Risk` or `Blocked`.
    - Automated unit test: `test_sequential_continuous_production_cycle_progression` in `tests/test_disruptions_and_supply_chain.py`.
- **Restrained Black & Terminal Green SCADA Aesthetic:**
  - Overhauled palette in `frontend/src/index.css`:
    - Near-black canvas (`#0A0D0A`), dark charcoal surfaces (`#141A14`), subtle green borders (`#243524`).
    - Terminal green primary accents (`#22C55E`, `#4ADE80`, `#15803D`).
    - Crisp off-white typography (`#F1F5F1`), muted grey secondary (`#8E9E8E`).
    - Monospace tabular font for telemetry, metrics, timestamps, and stage cards.
    - Zero illegible neon glows or decorative gradients.
- **Full Responsive Audit:**
  - Header with flex-wrap and responsive controls.
  - Process line grid (`repeat(7, 1fr)` on desktop, `repeat(4, 1fr)` on tablet, responsive stacked on mobile).
  - Responsive modals with `max-width: 780px`, `width: 94%`, `max-height: 90vh`, and vertical scroll.
- **Verification:**
  - Full backend test suite passing: 89 passed, 0 failed.
  - Production frontend build succeeding: `npm run build --prefix frontend`.

## Priority if time is constrained

1. Real Zetaris query path.
2. Meterless-backed identity/provenance/idempotent ingestion.
3. Deterministic end-to-end incident investigation.
4. Isolated recovery simulation and human approval.
5. Nemotron grounded explanation.
6. Repeatable metrics and demo evidence.
7. UI polish.

Cursor is a development-time sponsor tool; its current availability must not block progress. Do not cut sponsor runtime integration evidence in favor of decorative UI.

## Final release checklist

- [ ] PRD/TRD/agent/app flow are reflected in code.
- [ ] Zetaris integration uses a real configured factory source.
- [ ] Meterless reference is correctly attributed and its functionality is tested.
- [ ] Nemotron model ID/API route is verified and secrets are backend-only.
- [ ] Event history is append-only and replay is idempotent.
- [ ] Seeded simulations are reproducible.
- [ ] Investigations cite evidence and calculations.
- [ ] Forecast quality is measured and limitations are clear.
- [ ] Recovery scenarios are isolated and compared with baseline.
- [ ] Human approval revalidates current state.
- [ ] Automated tests pass.
- [ ] README and required `metadata.json` document actual sponsor use.
- [ ] `.env` and credentials are not committed.
