# FactoryOps — Application Flow

**Purpose:** Define the main runtime flows and safety boundaries for the Forge Intelligence FactoryOps demo.

## 1. System overview

FactoryOps investigates operational disruption across a virtual cocoa-processing factory. Zetaris connects fragmented operational data. Meterless World Model functionality reconciles entity identities and supports provenance/idempotency. Deterministic Python logic calculates impacts and evaluates recovery. NVIDIA Nemotron explains structured findings. A human makes the final recovery decision.

## 2. Startup and readiness

1. Start the local PostgreSQL container without deleting existing volumes.
2. Start/verify the local Zetaris instance independently.
3. Verify required environment configuration is present without printing secrets.
4. Run database migration/status checks.
5. Check PostgreSQL connectivity.
6. Check Zetaris connectivity and execute a minimal real factory-source query.
7. Check Meterless adapter health/version and run a small identity/provenance test.
8. Verify Nemotron configuration; do not call a paid model unnecessarily as part of every health check.
9. Report each dependency separately as healthy, degraded, or unavailable.
10. Do not report end-to-end ready if the required Zetaris factory source is not actually queryable.

## 3. Simulation setup

1. User creates a simulation run with a factory configuration, scenario profile, and random seed.
2. System validates configuration and persists a run ID, seed, schema/config version, and initial-state snapshot.
3. Simulator initializes suppliers, deliveries, inventory lots/balances, machines, production lines, bills of materials, production batches, and customer orders.
4. Source-domain data is exposed through the factory source datasets registered with Zetaris.
5. The system verifies that expected source domains can be queried through Zetaris before the run is marked integration-ready.

## 4. Hybrid simulation clock & automatic progression

The FactoryOps simulation uses a **hybrid timing model**:
- **Discrete time base:** Authoritative factory physics and discrete events advance in uniform simulation steps (15 simulated minutes per step).
- **Automatic progression:** A background service (`SimulationAutoDriver`) advances eligible scenarios by 1 step every **5.0 seconds of real time**.
- **Single-worker leader election:** Across multi-worker or restart environments, single-driver progression is enforced via PostgreSQL session-scoped advisory locking (`pg_try_advisory_lock(84729103)`).
- **Manual operator controls:** Operators can manually trigger `Step +1` or `Step +3` at any time. PostgreSQL row-level locks serialize manual and automatic steps without state corruption.
- **Human approval safety pause:** When a factory disruption is detected and candidate recovery plans are awaiting operator review (`is_awaiting_recovery_decision()`), automatic progression pauses immediately. The system never self-approves recovery plans.
- **Operator pause/resume toggle:** Operators can explicitly pause or resume automatic progression via `POST /simulations/{id}/pause` and `POST /simulations/{id}/resume`.
- **Full recovery lifecycle:** Upon operator approval of a recovery plan, the scenario enters `recovering` status with computed maintenance duration steps (e.g., Plan A thermal flush: 0.75h = 3 steps). Automatic and manual stepping progress through maintenance with throttled throughput, decrementing remaining steps. Upon completing the final maintenance step (`completed_step`), the scenario automatically transitions back to `running`, restoring nominal machine throughput (1,200 kg/h) and recording a `recovery.completed` event.

Each discrete step follows a stable order, configurable but versioned:

1. Advance simulation clock (by `step_size_minutes`, default 15m).
2. Process active recovery maintenance progress (decrement remaining steps, restore throughput upon completion).
3. Dequeue and process events due at this time.
4. Apply deterministic state transition rules to the authoritative simulator state.
5. Sample configured stochastic event attributes using the run seed.
6. Enforce invariants and record resulting operational events.
7. Publish/update source-domain records available through Zetaris.
8. Process immediate high-priority triggers and evaluate disruptions.
9. At configured intervals, execute a periodic risk review.
10. Persist step summary, event IDs, and state version.

The same seed, configuration, and initial state should produce the same simulated outcomes unless a documented nondeterministic external dependency is involved.

## 5. Ingestion, identity, and provenance

1. The FactoryOps ingestion adapter queries operational records through Zetaris.
2. Each record is normalized into a typed domain envelope while preserving source dataset/table, source record ID, retrieval timestamp, schema version, and watermark where available.
3. Meterless-backed logic resolves stable canonical IDs, source aliases, and duplicates according to the supported reference implementation.
4. An idempotency key prevents the same source record/version from causing repeated state changes.
5. The application writes/updates current-state projections transactionally and appends ingestion/event history.
6. Conflicts, stale records, schema mismatch, or ambiguous entity matches are surfaced rather than silently discarded.
7. A repeated ingest of the same unchanged records should yield no new semantic changes.

## 6. Investigation triggers

### Immediate trigger
- Receive a configured high-priority event, such as a critical machine failure or a supplier delay that crosses a defined order-risk threshold.
- Deduplicate the trigger by event/run/trigger key.
- Capture current state version and source watermark.
- Start an investigation.

### Periodic trigger
- At a configurable simulation interval, evaluate gradual risks and interactions across current factory state.
- Identify risk candidates using deterministic thresholds and/or evaluated forecasting output.
- Create investigations only for material risks; deduplicate repeated reviews.

A trigger is a request to investigate, not proof of cause.

## 7. Investigation flow

1. Create investigation record and record trigger, time, source state version, and data freshness.
2. Query relevant domains through Zetaris.
3. Resolve entity IDs and source aliases using the Meterless-backed layer.
4. Build a bounded dependency graph from explicit relationships and domain rules.
5. Traverse dependencies from the initiating event/entity.
6. Calculate affected inventory, machines, batches, and orders using deterministic rules.
7. Store each impact finding with evidence references, rule/calculation metadata, and a certainty label:
   - `observed`: directly supported by a source record;
   - `derived`: calculated by a deterministic rule;
   - `forecast`: uncertain future estimate;
   - `assumption`: explicit scenario assumption;
   - `unknown`: required information is missing or conflicting.
8. Run configured forecast calculations and attach model/config version.
9. Persist investigation result and timing metrics.
10. Construct a structured evidence packet for Nemotron.

## 8. Nemotron explanation flow

1. Backend constructs an allowlisted JSON payload with verified facts, evidence IDs, derived values, forecast estimates, assumptions, hard constraints, plan outcomes, and unknowns.
2. Backend sends the payload to the verified NVIDIA Nemotron API/model.
3. Response is parsed and validated against a schema.
4. Every material claim must reference supplied evidence or be labelled as estimate/assumption/unknown.
5. Reject or repair invalid output with bounded validation; never let the model alter calculated values.
6. Persist model ID, request/config version, response status, latency, and validated response (not secrets).
7. If API fails, return a deterministic template explanation with the same evidence links and disclose that AI explanation is unavailable.

## 9. Recovery candidate flow

1. Generate candidate actions from explicit rules, such as alternate supplier, reorder/expedite delivery, reroute a batch to another feasible line, reschedule a batch, or prioritize a critical order. Only implement actions for which operational constraints can be represented accurately.
2. Validate hard constraints; record infeasible candidates and the exact constraint.
3. Create an isolated snapshot of the relevant starting state.
4. Run a no-action baseline.
5. Run each feasible candidate in a separate scenario with comparable starting conditions.
6. Advance the scenario clock and calculate outcomes.
7. Compute differences against baseline for on-time orders, delay, downtime, capacity, waste, and estimated cost where modeled.
8. Rank feasible candidates using versioned configurable weights.
9. Return the top three (or fewer if fewer are feasible) with evidence, assumptions, and trade-offs.
10. Ask Nemotron to explain the structured comparison without generating new scores or values.

## 10. Human decision and commit

1. Human reviews the preferred plan and alternatives.
2. Human chooses: approve preferred plan, approve another plan, or reject all.
3. On approval, system checks the referenced plan version, source state version, data freshness, hard constraints, and resource availability.
4. If stale or infeasible, do not apply it; mark it stale and request re-simulation/review.
5. If valid, apply only the permitted action to the authoritative simulator state inside a transaction.
6. Append an approval decision and action-result event.
7. Verify postconditions and update projections.
8. If rejected, record rejection without mutating factory state.
9. Continue simulation and measure actual outcome against the prediction.

## 11. Disruption simulator, Run modal & continuous 7-stage sequential cycle flow

1. **Centered Run Modal & Disruption Selector Dialog:**
   - When an operator clicks the primary **Run** button in the SCADA header bar, a centered modal dialog overlay opens above all panels and navigation (`z-index: 1000`, backdrop blur, dark semi-transparent overlay).
   - Fetches and renders the dynamic backend disruption catalogue (`GET /simulations/{id}/disruptions/catalogue`) with live eligibility matrices across physical equipment, supply chain, and raw material quality domains.
   - Prominently showcases a **🎲 Random Scenario Injection** option card for single-click stochastic stress-testing.
   - Prevents duplicate clicks during API submission (`submitting` state locks actions).
   - Displays inline error banners with retry buttons on network failure.
   - Closes smoothly on backdrop click, close button, or Escape key. On success, closes and immediately triggers state refreshes across operations, recovery, and timeline tabs.

2. **Supply Chain Stockout Forecasting:**
   - Evaluates Silo 1 working inventory against roaster burn rate: $T_{stockout} = Q_{usable} / R_{burn}$ (3,600 kg / 300 kg/step = 12 steps / 3.0h under nominal operations).
   - Compares $T_{stockout}$ with inbound shipment arrival step (`SHP-2026-COCOA-01`).
   - If customs delay (+6.0h / +24 steps) delays delivery to Step 32, computes the 20-step (5.0h) production starvation gap and downstream customer order exposure (`customer_order:erp:ORD-9402` SLA breach, $350/day penalty).
   - Generates 3 ranked recovery options (Spot Market Buffer Procurement, Recipe Line Throttle, Customs Broker Expedite) for human authorization.

3. **Continuous 7-Stage Sequential Production Lifecycle:**
   - Models the authoritative manufacturing lifecycle sequentially across the 7 stages:
     1. Cleaning & Destoning (`CLEAN-01`, 1 step)
     2. Drum Roasting (`ROAST-01`, 2 steps)
     3. Winnowing (`WINNOW-01`, 1 step)
     4. Liquor Grinding (`MILL-01`, 1 step)
     5. Hydraulic Pressing (`PRESS-01`, 1 step)
     6. Cake Pulverizing (`PULV-01`, 1 step)
     7. Packing & Fulfillment (`PACK-01`, 1 step)
   - **Cycle Start (Step 0):** Stage 1 = `Running`, Stages 2–7 = `Pending`.
   - **Sequential Progression:** As simulation steps advance, the active stage accumulates step progress. Upon reaching its duration, it transitions to `Completed`, the next stage transitions to `Running`, and subsequent stages remain `Pending`.
   - **Continuous Looping:** When Stage 7 finishes, `completed_cycles` increments, `cycle_number` increments, and a new manufacturing cycle begins with Stage 1 `Running` and Stages 2–7 `Pending`.
   - **Disruption Awareness:** Active disruptions (e.g. roaster thermal runaway at step 3, or silo quarantine lock) inhibit stage progression, marking the stage `At Risk` or `Blocked` until authorized recovery maintenance restores nominal operations.
   - **Restrained Black & Green SCADA Terminal Aesthetic:** Dark charcoal panels (`#141A14`), deep canvas (`#0A0D0A`), crisp off-white text (`#F1F5F1`), terminal phosphor green accents (`#22C55E` / `#4ADE80`), monospace tabular telemetry typography, and fully responsive multi-breakpoint layout.

## 12. Failure flows

- **Zetaris unavailable:** show integration as degraded/unavailable; do not silently bypass it in the hackathon demo. Test fixtures may be used only in an explicitly labelled test mode.
- **Source stale/missing:** mark affected findings uncertain; do not present them as confirmed.
- **Meterless adapter unavailable:** fail the required integrated demo or enter an explicitly labelled degraded mode that does not claim sponsor functionality.
- **Nemotron unavailable:** show deterministic explanation fallback; investigation and calculations remain usable.
- **Database unavailable:** stop state-changing work and return a clear dependency error.
- **Malformed model output:** reject it and use deterministic fallback.
- **Stale recovery plan:** do not commit; revalidate/re-simulate.
- **Invariant violation:** fail the simulation step, record diagnostics, and do not silently continue with corrupted state.

## 12. User-facing screens or API views

A minimal demo may use API responses or a simple UI. Required views:
1. Readiness/dependency status.
2. Factory overview and active simulation.
3. Incident/investigation summary.
4. Impact chain with evidence and certainty labels.
5. Forecast assumptions and evaluation status.
6. Recovery-plan comparison and preferred plan.
7. Approval/reject action and result.
8. Audit/replay view showing seed, source provenance, state version, and events.

## 13. Flow-level acceptance tests

- Same seed + same initial state + same configuration yields reproducible simulation output.
- Re-ingesting unchanged source records produces no semantic changes.
- An immediate critical event starts one investigation, not duplicates.
- Periodic review catches a configured gradual risk not caught by an immediate event.
- Every derived impact includes evidence and calculation metadata.
- Scenario simulation does not mutate authoritative state.
- Approval of a stale plan is rejected.
- Rejecting all plans changes no operational state.
- Nemotron output that invents a number or cites an absent evidence ID fails validation.
- Zetaris outage is visible and does not silently become a direct database read.
