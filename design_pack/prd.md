# FactoryOps — Product Requirements Document (PRD)

**Version:** 1.0  
**Team:** Forge Intelligence  
**Hackathon direction:** Track 1 — Solving Fragmented Intelligence  
**Product use case:** Supply-chain prediction and operational recovery for a virtual cocoa-processing factory  
**Status:** Design baseline; integration assumptions must be validated against the actual local Zetaris instance.

## 1. Product summary

FactoryOps is an evidence-backed operational investigation and recovery agent for a virtual cocoa-processing factory. It connects fragmented supplier, inventory, machine, production, and customer-order information; traces how disruptions propagate; forecasts uncertain outcomes; simulates recovery alternatives; and presents recommendations for human approval.

The product must demonstrate more than an AI-generated explanation. Its conclusions must be traceable to source records and deterministic operational rules, its forecasts must be evaluated against repeatable simulated outcomes, and its recovery plans must be compared against a no-action baseline.

## 2. Problem

Factory operations data is fragmented across domains. A late supplier delivery may affect ingredient availability, constrain a production batch, delay a finished product, and put several customer orders at risk. When the records are viewed separately, the operator must manually reconstruct the dependency chain and estimate the consequences of each possible intervention.

FactoryOps makes those relationships queryable and investigates the operational consequences with evidence.

## 3. Goals

1. Demonstrate Zetaris as a central integration layer for fragmented operational data.
2. Resolve cross-domain records to canonical operational entities with provenance and idempotent ingestion using Meterless World Model concepts/reference implementation.
3. Detect urgent events and perform periodic reviews for emerging risks.
4. Trace impacts deterministically across suppliers, inventory, machines, production batches, and customer orders.
5. Estimate uncertain outcomes using configurable statistical distributions and evaluate those estimates against simulation results.
6. Simulate recovery alternatives in isolated scenarios without mutating the live state.
7. Use NVIDIA Nemotron to explain structured findings and trade-offs without inventing facts or calculations.
8. Present the top three feasible recovery plans, identify the highest-ranked plan under configurable priorities, and let a human choose another or reject all.
9. Demonstrate repeatable tests, provenance, and measurable end-to-end performance.

## 4. Non-goals

- Connecting to a real production factory or executing real-world actions.
- Autonomous execution of recovery actions without approval.
- A general-purpose digital twin covering workforce, energy, quality, and every plant subsystem.
- A separate graph database, Kafka, Kubernetes, microservice fleet, or distributed deployment unless evidence later justifies it.
- Treating an LLM response as authoritative operational truth.
- Claiming calibrated probabilities before evaluation demonstrates calibration.
- Claiming Zetaris, Meterless, or Nemotron functionality that has not been exercised in the running demo.
- Requiring a paid subscription or billing-linked API outside the provided sponsor access.

## 5. Users and primary jobs

### Primary user: Factory operations planner
- Understand what happened and which operational entities are affected.
- See evidence and the chain of dependencies behind an impact finding.
- Compare recovery options by service, time, operational, and financial trade-offs.
- Approve a feasible action, choose an alternative, or reject every option.

### Secondary user: Hackathon judge
- Verify that sponsor technologies have substantive roles in the product.
- Reproduce a scenario and inspect the evidence trail.
- Compare outcomes against a no-action baseline.
- See honest limitations, metrics, and failure behavior.

## 6. Core product workflow

1. The simulator advances hybrid discrete simulation time and emits operational events.
2. Source-domain records are exposed through a Zetaris-connected operational data path.
3. The ingestion/reconciliation layer reads data through Zetaris, preserves source provenance, and uses Meterless World Model patterns for stable entity identity, alias/duplicate resolution, and idempotent updates.
4. High-priority events trigger immediate investigations; scheduled simulation intervals trigger periodic reviews.
5. The investigation engine queries the relevant operational facts, traverses explicit dependencies, and calculates affected entities and constraints.
6. The forecasting component estimates uncertain outcomes from configured distributions and retained simulation evidence.
7. Recovery candidates are checked against hard constraints, then evaluated in isolated simulation copies.
8. A configurable multi-objective ranker selects a preferred feasible plan and returns the top three.
9. NVIDIA Nemotron receives a bounded, structured evidence package and explains the finding and alternatives. Deterministic values are calculated outside the model.
10. The operator approves a plan, selects another, or rejects all. Approval triggers freshness and feasibility revalidation before the permitted action is applied to the simulator's authoritative state.
11. The system records the decision, applied action, and subsequent outcome as append-only events.

## 7. Functional requirements

### FR-01 — Virtual factory simulator
- Model suppliers/deliveries, inventory items/lots, machines/production lines, production batches, and customer orders.
- Support supplier delays, machine breakdowns, inventory shortages, demand surges, capacity constraints, and combined disruptions.
- Use explicit operational rules plus configurable probability distributions for selected event attributes.
- Support a configurable random seed and deterministic replay for reproducible runs.
- Advance time in discrete steps and process events within each step.
- Enforce inventory non-negativity, capacity, material requirements, batch states, and order-state invariants.

### FR-02 — Zetaris integration
- Zetaris is the mandatory integration path for operational reads in the hackathon demonstration.
- Configure and verify a factory operational source in the actual Zetaris instance; do not assume the example NY_TAXI/TPCDS datasets represent factory data.
- Provide a typed adapter boundary for querying the supported Zetaris interface.
- Record source dataset/table, source record key, retrieval time, and source version/watermark where available.
- Expose integration health and data freshness.
- In the absence of a verified connection, fail visibly or run only an explicitly labelled test-fixture mode. Never silently bypass Zetaris in demo mode.
- A technical spike must confirm the actual connection/query protocol and source-registration process before the main integration is built.

### FR-03 — Meterless World Model
- Use the downloaded Meterless World Model reference implementation and its documented conventions where technically appropriate.
- Make it responsible for canonical entity identity, alias/near-duplicate resolution where supported, provenance, idempotent ingestion, and stable entity reconciliation.
- Preserve required source attribution and the applicable license for any copied/adapted code.
- Add an explicit adapter between FactoryOps domain entities and the Meterless representation.
- Do not treat Meterless as the forecast model or claim that it proves causal relationships.
- Test that repeated ingestion of identical records causes no duplicate state changes.

### FR-04 — Current state and event history
- Store authoritative application state and append-only events in PostgreSQL.
- Corrections are represented as new events; do not silently rewrite history.
- Record event type, entity references, simulation time, recorded time, source provenance, schema version, and idempotency key as applicable.
- Separate simulation source data from FactoryOps application records by schema/table boundary and document ownership.

### FR-05 — Investigation triggers
- Immediately investigate configured high-priority events.
- Run periodic factory-wide risk reviews at configurable simulation intervals.
- Deduplicate repeated triggers and make investigation creation idempotent.
- A trigger initiates investigation; it does not itself prove causality.

### FR-06 — Deterministic investigation
- Represent operational dependencies explicitly and traverse them in Python initially.
- Produce an impact graph/path and a list of affected entities.
- Calculate impacts from source-backed quantities, timestamps, capacity, material requirements, and order constraints.
- Attach evidence references and the rule/calculation used to every material conclusion.
- Distinguish confirmed facts, derived results, assumptions, and uncertain forecasts.

### FR-07 — Forecasting
- Begin with transparent statistical distributions and configurable parameters.
- Track predicted outcomes and observed simulation outcomes.
- Evaluate calibration and error on held-out/repeated scenarios.
- Do not display a probability as calibrated until the evaluation supports that claim.
- Introduce learned models only when sufficient simulation evidence exists and they measurably outperform the baseline.

### FR-08 — Recovery generation and evaluation
- Generate candidate actions supported by explicit domain rules.
- Enforce hard constraints before ranking.
- Clone the relevant state into an isolated scenario; apply candidate actions only to that clone.
- Simulate each candidate and compare against the no-action baseline.
- Rank using configurable weights for customer commitments, recovery time, operational efficiency, and estimated financial impact.
- Return the top three feasible plans when at least three exist; otherwise return all feasible plans.
- Explain infeasible plans and constraint violations rather than hiding them.

### FR-09 — Human approval
- Present the preferred plan and alternatives with projected outcomes, cost assumptions, risks, and evidence.
- Allow approve, choose an alternative, or reject all.
- Revalidate against the latest state before applying an approved action.
- Record actor/decision, plan version, state version, timestamp, and result.
- Do not apply a simulated plan to authoritative state before approval.

### FR-10 — NVIDIA Nemotron explanation
- Use NVIDIA Nemotron through the provided/verified API access for explanation and synthesis.
- Supply a structured evidence packet, relevant source citations/IDs, deterministic calculations, assumptions, and constraints.
- Require output to distinguish facts from estimates and unknowns.
- Validate structured output against a schema; handle timeout, rate limit, malformed output, and unavailable API.
- The application must retain a deterministic non-LLM fallback summary so core investigations remain usable if Nemotron is unavailable.
- Never send API keys to the browser or store them in source control.

### FR-11 — Auditability and observability
- Log scenario ID, simulation seed, integration source, data freshness, investigation ID, evidence IDs, forecast model/config version, candidate plan IDs, approval outcome, and timings.
- Make each demonstration scenario reproducible from a seed and configuration.
- Provide health checks for API, PostgreSQL, Zetaris, Meterless adapter, and Nemotron configuration/availability.

## 8. Sponsor-tool integration matrix

| Sponsor tool | Required role | Demonstrable evidence |
|---|---|---|
| Zetaris | Central operational data integration and cross-domain querying | Factory-specific source/query setup; working queries joining or relating operational domains; provenance/freshness shown |
| Meterless World Model | Entity identity/reconciliation, provenance, idempotent ingestion/history patterns | Adapter/module in repository; stable IDs; duplicate/replay test; attribution to upstream project and license |
| NVIDIA Nemotron | Evidence-grounded operational explanation and recovery-plan trade-off synthesis | Live API call using sponsor-provided access; validated structured output; grounded answer and error/fallback test |
| Cursor | AI-assisted implementation and test-development workflow when access/credits are available | Project rules in `AGENTS.md`; meaningful code/test changes; test commands/results and development notes in README; do not claim Cursor is a runtime dependency |

Cursor is a development tool, not a deployed runtime service. If Cursor credits are unavailable, use Antigravity for the build and preserve Cursor-ready repository instructions; use Cursor for test-writing/review when access returns. This does not reduce the required runtime roles of Zetaris, Meterless, or Nemotron.

## 9. Success metrics and acceptance criteria

Targets should be set after a baseline run; do not invent a benchmark before measuring.

- Integration: all required operational domains can be queried through the verified Zetaris path, with source provenance and freshness recorded.
- Identity/replay: repeated identical ingestion yields no duplicate entity or state change.
- Investigation: on a labelled scenario suite, report affected-entity precision/recall or exact expected impact-path match rate.
- Recovery: compare each plan with no action on on-time orders, production delay, cost, and constraint violations.
- Forecasting: report calibration and proper error metrics over repeated/held-out runs.
- Reliability: report successful scenario completion rate and median/p95 duration for investigation and recovery evaluation.
- Grounding: every material claim in a Nemotron explanation maps to evidence or is labelled as an assumption/unknown.
- Approval safety: no candidate plan changes authoritative state before approval; stale plans are revalidated or rejected.

## 10. Risks and mitigations

- **Zetaris interface/source configuration uncertain:** perform an early integration spike; document actual working commands, source configuration, and query method. No mock connection presented as real.
- **Meterless implementation is TypeScript while core is Python:** isolate behind a typed adapter; reuse the reference module or a thin local service only after inspecting its interface, AGENTS.md, and license. Avoid a distributed architecture unless needed.
- **LLM hallucination:** calculate facts deterministically, constrain input/output, validate output, and preserve evidence IDs.
- **Uncalibrated probabilities:** label as scenario estimates until empirically evaluated.
- **Simulation complexity:** limit first release to six disruption types and core operational entities.
- **Secret leakage:** use `.env`, ignore it in Git, commit only a sanitized `.env.example`, and redact secrets from logs.
- **Resource pressure:** keep services local and minimal; do not disrupt the separate Zetaris containers or volumes.
- **Scope creep:** no real factory control, separate graph database, or unnecessary infrastructure.

## 11. Demo acceptance scenario

A reproducible scenario delays a cocoa supplier delivery while a machine has constrained capacity and a set of customer orders approach their due dates. FactoryOps must:
1. retrieve cross-domain records through Zetaris;
2. reconcile entities and preserve source provenance through the Meterless-backed layer;
3. trace which inventory, batches, machines, and orders are affected;
4. calculate the impact using deterministic rules;
5. estimate uncertain outcomes with labelled assumptions;
6. simulate at least two feasible recovery alternatives in isolation;
7. compare alternatives against no action;
8. use Nemotron to explain evidence and trade-offs;
9. present a ranked shortlist for human decision;
10. demonstrate that no live state changed before approval and that replay is idempotent.

## 12. Definition of done

The vertical slice is done when a fresh local setup can run the documented scenario, use the real configured Zetaris and Nemotron paths, invoke the Meterless-backed identity/provenance/replay functionality, show evidence-backed findings and recovery alternatives, pass automated tests, and explain any degraded mode honestly.
