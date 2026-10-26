# FactoryOps — Technical Requirements Document (TRD)

**Version:** 1.0  
**Team:** Forge Intelligence  
**Status:** Proposed implementation baseline. Confirm exact Zetaris and Meterless integration contracts against the checked-out software before coding.

## 1. Technical principles

1. Deterministic business calculations; LLM for explanation, not arithmetic or source-of-truth decisions.
2. Zetaris is the required operational-data access path in the demonstration.
3. PostgreSQL is the authoritative store for FactoryOps application state, event history, investigations, simulation runs, and approvals.
4. Meterless World Model owns the entity-reconciliation/provenance/idempotency capability through a tested adapter.
5. Simulations are isolated and reproducible.
6. Human approval is a hard boundary before an action changes authoritative state.
7. Keep the architecture small: modular FastAPI application plus only the minimal auxiliary process needed for Meterless/JDBC compatibility.

## 2. Proposed runtime topology

- **FastAPI application (Python):** API, orchestration, simulator, investigation engine, forecasting, recovery evaluator, approval workflow.
- **PostgreSQL 17:** FactoryOps database, schemas separated by ownership.
- **Zetaris local instance:** federation/integration/query layer over the configured operational data source(s).
- **Meterless World Model adapter:** use the TypeScript reference implementation from the downloaded repo after reading its `AGENTS.md`, usage docs, package metadata, and license. Preferred deployment order: import/use it as a local TypeScript module where practical; otherwise a small local adapter process with a versioned JSON contract. Do not create a networked microservice fleet.
- **NVIDIA Nemotron API:** outbound explanation requests from the backend only.
- **Web UI:** only if time permits after API vertical slice; no secrets in browser code.
- **Cursor:** development-time code/test assistant, not a runtime dependency.

### Logical data flow

`Simulator → operational source tables/events → Zetaris → FactoryOps ingestion/reconciliation → PostgreSQL application projection + event history → deterministic investigation → probabilistic forecast → isolated recovery simulations → Nemotron explanation → human decision → approved state transition`

The operational source data path and the application persistence path must be distinguished in code and docs. A same-PostgreSQL demo may host multiple source-domain schemas, but Zetaris must actually query the configured factory source; a direct SQL shortcut does not count as the Zetaris integration.

## 3. Required early integration spikes

### 3.1 Zetaris spike (blocker before building business logic)
1. Inspect the local Zetaris deployment's actual health, configured connections, supported interfaces, and logs.
2. Read the official/local documentation bundled with the running version.
3. Verify how to register or expose factory-specific tables and how to query them using a supported interface.
4. Test one query per domain: suppliers/deliveries, inventory, machines/maintenance, production batches, orders.
5. Test a cross-domain join or relationship query.
6. Capture the exact working URL/port, authentication method, driver/protocol, SQL dialect limitations, and error handling in `docs/zetaris-integration.md`; never write credentials there.
7. Implement a `ZetarisOperationalDataPort` interface and a real adapter for the verified method.
8. If the source cannot be registered/queryable yet, stop and report the blocker; do not silently replace it with direct PostgreSQL reads or a fake connector.

Existing context suggests a JDBC driver/class and localhost port may be available, but all endpoint, port, credentials, and protocol details must be verified against the actual local instance. Do not assume the sample NY_TAXI or TPCDS datasets are factory operational data.

### 3.2 Meterless spike
1. Inspect the local `factoryops-world-model-demo/reference` contents and read `AGENTS.md`, `HOW_TO_USE_WORLD_MODEL.md`, `README.md`, package scripts, and `LICENSE`.
2. Re-run the existing tests and idempotency check.
3. Identify the public functions/types for stable IDs, entity resolution, provenance, upsert/snapshot/history, and idempotent ingest.
4. Implement an adapter contract and a tiny round-trip fixture with a factory entity and source record.
5. Preserve the upstream license/attribution for copied or adapted files.
6. Do not assume the TypeScript reference is a production Python runtime or that it supplies causality or forecasting.

### 3.3 NVIDIA Nemotron spike
1. Verify the model identifier and API base URL from the provided NVIDIA API catalog/official documentation available to the team.
2. Read credentials from environment variables only.
3. Implement a small backend-only request with timeout, retry policy for transient errors, and redacted logs.
4. Validate response structure and test missing key, timeout, rate-limit, malformed response, and fallback behavior.
5. Do not guess the model ID, expose the key in the UI, or hardcode it.

## 4. Stack and dependency policy

- Python 3.14 is currently installed, but compatibility must be verified for FastAPI, SQLAlchemy/psycopg, testing, and any JDBC bridge. Use a project virtual environment.
- FastAPI with Pydantic request/response schemas.
- PostgreSQL 17; SQLAlchemy 2.x and Alembic if compatible with the selected Python version.
- `psycopg` v3 for direct application persistence.
- HTTP client only if the verified Meterless adapter exposes HTTP; otherwise use the chosen local integration contract.
- Use the least-complex supported Zetaris driver/protocol. Do not add a JDBC bridge library until a real connectivity spike proves it is needed and supported.
- `pytest` for unit/integration tests; `httpx` for API tests when compatible.
- `ruff` for lint/format if available.
- `python-dotenv` or framework settings for local `.env` loading.
- TypeScript/Node dependencies for Meterless must be pinned and isolated; do not run broad/force audit fixes or approve package scripts without inspection.
- No Redis, Kafka, Kubernetes, graph database, or extra orchestration stack in v1.

## 5. Configuration and secrets

Environment variables (names are proposed; adapt to actual API contract):

```dotenv
DATABASE_URL=postgresql+psycopg://...
ZETARIS_ENABLED=true
ZETARIS_BASE_URL=
ZETARIS_JDBC_URL=
ZETARIS_DRIVER_CLASS=
ZETARIS_DRIVER_JAR=
ZETARIS_USERNAME=
ZETARIS_PASSWORD=
NVIDIA_API_KEY=
NVIDIA_API_BASE_URL=
NVIDIA_MODEL_ID=
METERLESS_ADAPTER_MODE=local
METERLESS_ADAPTER_URL=
SIMULATION_SEED=42
SIMULATION_STEP_MINUTES=15
INVESTIGATION_REVIEW_INTERVAL_STEPS=4
```

Only configure the fields required by the verified integration. Do not assume both JDBC and HTTP fields are needed. Do not commit real values. URL-encode credentials in database URLs. Keep `.env` ignored; commit `.env.example` with placeholders. Redact all secrets from exceptions and logs.

## 6. Database boundaries and core entities

Use explicit schema ownership. Example logical schemas:

- `factory_source`: simulated source-domain records consumed through Zetaris (if same local PostgreSQL hosts the simulator source).
- `factoryops`: application-owned projections, event history, investigations, forecasts, recovery plans, approvals, and audit events.

If Zetaris requires separate physical source connections, adapt the source layout without changing the application port. Do not duplicate source data into application tables without provenance and a defined projection/upsert rule.

### Domain entities
- `Supplier`
- `Delivery`
- `InventoryItem`
- `InventoryLot` / `InventoryBalance`
- `Machine`
- `ProductionLine`
- `ProductionBatch`
- `BillOfMaterials` / material requirement
- `CustomerOrder`
- `OrderLine`
- `DisruptionScenario`
- `OperationalEvent`
- `CanonicalEntity` / alias / source identity (Meterless-backed)
- `Investigation`
- `ImpactFinding`
- `ForecastRun`
- `RecoveryCandidate`
- `RecoverySimulation`
- `ApprovalDecision`
- `AuditEvent`

### Required event envelope fields
- `event_id`
- `event_type`
- `schema_version`
- `simulation_time`
- `recorded_at`
- `source_system`
- `source_dataset` / `source_table` where available
- `source_record_id`
- `entity_refs`
- `idempotency_key`
- `payload`
- `correlation_id` / `scenario_id`

Use append-only event records. Current-state projections may be updated transactionally as a result of events, but event history should not be silently rewritten.

## 7. Zetaris data contract

Define an application port independent of the concrete connector:

- `health()`
- `list_sources()` or the equivalent supported discovery method
- `query_suppliers(...)`
- `query_deliveries(...)`
- `query_inventory(...)`
- `query_machines_and_maintenance(...)`
- `query_production_batches(...)`
- `query_orders(...)`
- `query_related_entities(...)`

Exact method signatures should follow the codebase conventions. Implement only the calls supported by the actual local Zetaris interface. Each returned row should carry source identity/provenance and retrieval time. Queries must be bounded and parameterized where the protocol supports it. Handle timeout, unavailable source, invalid credentials, schema mismatch, and stale data explicitly.

## 8. Meterless contract

Create a `WorldModelPort` with the minimum capabilities supported by the inspected reference implementation:

- canonical/stable entity ID generation;
- source identity and alias registration;
- duplicate/near-duplicate resolution when supported by the reference;
- provenance-preserving idempotent ingestion/upsert;
- snapshot/current view or history retrieval where supported.

Use a versioned request/response schema for any process boundary. Keep deterministic local fixtures. The adapter must report its implementation/version and must not claim an operation is supported if the underlying reference does not expose it.

## 9. Investigation algorithm

1. Receive a high-priority event or periodic-review trigger.
2. Create an idempotent investigation record and capture data watermark/state version.
3. Retrieve related facts via Zetaris and normalize/reconcile entity identities through Meterless.
4. Build a bounded dependency subgraph from explicit relationships and domain rules.
5. Traverse dependencies and calculate impact using deterministic rules.
6. Attach evidence references and calculation metadata to each finding.
7. Label each finding as `observed`, `derived`, `forecast`, `assumption`, or `unknown`.
8. Persist the investigation and enqueue/execute forecasts as configured.
9. Generate an explanation from the structured evidence package via Nemotron; validate and store the response with model/config metadata.

The graph is a derived investigation structure. The event sequence alone does not prove causality.

## 10. Simulation and forecast requirements

- Discrete simulation steps with event processing inside each step.
- Configurable random seed; persist seed and scenario configuration.
- Six disruption families: supplier delay, machine breakdown, inventory shortage, demand surge, capacity constraint, combined disruption.
- Separate authoritative state from scenario snapshots.
- Use a scenario version/hash to ensure fair baseline-versus-candidate comparison.
- Statistical distributions are explicit and versioned.
- Store forecast target, horizon, probability/estimate, distribution parameters, model/config version, and observed result.
- Evaluate calibration on held-out/repeated scenarios; mark unvalidated probabilities as scenario estimates.

## 11. Recovery and approval safety

- Hard constraints are checked before scoring.
- Score only feasible plans using configurable weights.
- Simulate each candidate in an isolated copy.
- Compare candidate outcomes to the same no-action baseline and same starting state/seed where applicable.
- Return top three feasible plans or fewer if fewer exist.
- Approval references a specific plan ID and scenario/state version.
- On approval, revalidate current state, constraints, and resource availability in a transaction.
- Reject stale/infeasible approvals and require re-simulation or human review.
- Record approval/rejection, actor, timestamps, state versions, and resulting events.
- No external real-world write operations in v1.

## 12. Nemotron contract

Input JSON should contain:
- scenario and investigation identifiers;
- short task instruction;
- verified facts and evidence IDs;
- deterministic calculations and units;
- forecast outputs clearly marked as estimates;
- candidate recovery results and ranking rationale;
- hard constraints and unresolved unknowns;
- response schema/version.

Output JSON should contain:
- concise incident summary;
- verified impact chain with evidence IDs;
- separated facts/estimates/assumptions/unknowns;
- plan comparison explanation;
- limitations and missing data;
- no new numeric values unless explicitly present in evidence.

Parse/validate output; if invalid or unavailable, display a deterministic template summary. Never treat model-generated values as authoritative state changes.

## 13. API surface (initial proposal)

- `GET /health`
- `GET /health/db`
- `GET /health/zetaris`
- `GET /health/meterless`
- `GET /health/nemotron` (configuration/availability check; avoid costly model call unless explicitly requested)
- `POST /simulations`
- `POST /simulations/{id}/step`
- `GET /simulations/{id}`
- `POST /simulations/{id}/disruptions`
- `POST /investigations` or event-triggered internal creation
- `GET /investigations/{id}`
- `GET /investigations/{id}/impacts`
- `GET /investigations/{id}/recovery-plans`
- `POST /recovery-plans/{id}/simulate`
- `POST /recovery-plans/{id}/decision`
- `GET /scenarios/{id}/audit`

Treat names as an initial contract; inspect the existing app before creating duplicates or incompatible endpoints.

## 14. Testing strategy

- Unit: inventory arithmetic, capacity rules, order deadlines, graph traversal, hard constraints, scoring, distributions, seed reproducibility.
- Contract: Zetaris adapter query mapping and schema/error handling; Meterless adapter identity/provenance/idempotency; Nemotron output schema.
- Integration: actual configured Zetaris query path; PostgreSQL migrations/transactions; Meterless-backed ingestion round trip; Nemotron request with mocked transport for tests and one controlled live smoke test when credentials exist.
- Safety: simulation cannot mutate authoritative state; approval revalidates stale state; rejected plan causes no state mutation.
- End-to-end: supplier delay + machine constraint + order deadlines, repeated with same seed.
- Replay: second ingestion of same records reports no unintended changes.
- Performance: capture latency and dataset sizes; set thresholds after baseline.
- Security: secret redaction, no keys in frontend/logs, no destructive volume commands.

## 15. Operational safety and non-negotiable rules

- Do not run `docker compose down -v` against existing FactoryOps or Zetaris projects.
- Do not alter, remove, or reset the separate Zetaris containers/volumes.
- Do not run `npm audit fix --force` or enable blocked install scripts without reviewing the exact reason and package.
- Do not invent URLs, ports, API paths, model IDs, or driver support.
- Do not claim real integration based only on a mocked adapter.
- Do not build factory business logic until integration spikes and repository inspection are documented.
