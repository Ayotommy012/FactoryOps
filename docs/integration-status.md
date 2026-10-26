# FactoryOps — Integration Status

This document tracks the integration verification status of sponsor technologies and core infrastructure. It distinguishes verified real implementations from planned or mocked components.

---

## Component Integration Matrix

| Component | Target Role | Verified Connection | Current Status | Notes |
|---|---|---|---|---|
| **PostgreSQL 17** | Application persistence & event store | **Real** (Port 5433) | `Operational` | Phase 2 migrations applied (`public` & `factory_source` schemas). 15/15 tests passing with transactional savepoint isolation (0 row drift). |
| **FastAPI Backend** | Core API & orchestration engine | **Real** (Port 8000) | `Operational` | `GET /health`, `GET /health/db`, and `/simulations` endpoints operational. |
| **Zetaris** | Operational data integration & querying | **Real** (Ports 8888 / 3000) | `Operational` | Authenticated Spark SQL queries verified via REST API (`SELECT 1`, `SELECT current_database()`). See `docs/zetaris-integration.md`. |
| **Meterless World Model** | Identity, provenance & idempotency | **Verified Spec & Ref** | `Spec Audited` | Reference inspected at `factoryops-world-model-demo\reference`. Apache-2.0. Clean contract identified (stable IDs, banding, provenance, idempotency). |
| **NVIDIA Nemotron** | Evidence explanation & trade-off synthesis | **Real API Verified** | `Operational` | Model `nvidia/nemotron-3.5-lightning-30b-a3b` verified live on `https://integrate.api.nvidia.com/v1`. Validated JSON & 100% evidence grounding verified. Deterministic fallback defined. |
| **Simulator (Phase 3 Slice)** | Discrete-event virtual factory simulator | **Internal Service & API** | `Operational` | 15-min stepping, roaster anomaly trigger at Step 3, isolated scenario snapshots, explicit timeline ordering. |
| **Cursor** | Dev/test authoring & code review | N/A (Dev Tool) | `Active / Compatible` | Repository instructions and tests aligned with AGENTS.md conventions. |

---

## Detailed Integration Logs

### 1. PostgreSQL Application Database (Phase 2 Migrations Complete)
- **Host / Port:** `127.0.0.1:5433`
- **Database:** `factoryops`
- **Application User:** `factoryops_app` (Non-superuser)
- **Status:** Verified via both host CLI and backend SQLAlchemy 2.x / Psycopg 3 engine.
- **Migration Version:** `96ee2c2f5765` (`create_core_domain_and_source_tables`) applied via Alembic.
- **Schema Separation (ADR-007):**
  - Schema `factory_source`: operational source tables (`source_suppliers`, `source_inventory`, `source_machine_telemetry`, `source_batches`, `source_orders`).
  - Schema `public`: application domain projections (`suppliers`, `inventory_lots`, `machine_assets`, `production_batches`, `customer_orders`, `simulation_scenarios`, `operational_events`).
- **Verification Suite:** `tests/test_domain_models.py` passing 6/6 tests covering foreign keys, check constraints, unique constraints, and idempotency keys.

### 2. Zetaris Platform (Phase 1A Spike Completed)
- **Containers:** `zetaris-driver-1`, `zetaris-api-1`, `zetaris-ui-1`, `zetaris-postgres-1` (All healthy/running)
- **Verified Interface:** REST API on `http://127.0.0.1:8888` (direct) & `http://127.0.0.1:3000/api/proxy`
- **Authentication:** Bearer token authentication against `/api/auth/login` verified.
- **SQL Execution:** Endpoint `/api/v1.0/query/sql/start` executed real Spark SQL queries (`SELECT 1 AS probe, 7 AS seven`, `SELECT current_database() AS db, current_date() AS today`).
- **JDBC Assessment:** Port 10000 open with driver jar staged at `jdbc/ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar`. However, host lacks a Java runtime (`java: command not found`). The REST API provides a clean zero-JVM interface without host dependencies.
- **Full Report:** Documented in [`docs/zetaris-integration.md`](file:///C:/Users/USER/Documents/factoryops/docs/zetaris-integration.md).

### 3. Meterless World Model (Phase 1B Investigation Completed)
- **Reference Location:** `C:\Users\USER\Downloads\factoryops-world-model-demo\reference`
- **License:** Apache License 2.0 (permissive, zero external runtime dependencies in `@meterless/world-model-reference`).
- **Verified Behavior & Interfaces:**
  - Content-addressed stable IDs: `type:system:externalId` and `ent_${sha256(type:normalizeName(name)).slice(0,16)}`.
  - Resolution banding: `>=0.85` auto-merge, `[0.82, 0.85)` review queue, `<0.82` new entity.
  - Mandatory provenance: `{ kind, at, by?, url?, runId? }` on every mutation; observation timestamps drive record clocks for idempotency.
  - Conflict policies: `"most-recent-wins"`, `"highest-confidence-wins"`, `"source-priority"`.
  - Bitemporal edge supersession and point-in-time snapshot reconstruction (`foldUntil`).
- **Integration Boundary:**
  - Used for canonical identity, alias resolution, provenance auditing, and idempotent event ingestion.
  - Meterless does **NOT** perform causal inference or forecasting (these belong to Python deterministic graph traversal and simulation).
- **Proposed Architecture:** In-process Python engine/adapter implementing identical Apache-2.0 contract and math, or a versioned JSON contract via file/CLI. Avoids external networked microservices.

### 4. NVIDIA Nemotron (Phase 1C Spike Completed)
- **API Base URL:** `https://integrate.api.nvidia.com/v1`
- **Active Model ID:** `nvidia/nemotron-3.5-lightning-30b-a3b` (verified via `GET /v1/models` and live `/chat/completions`)
- **Key Characteristics Discovered:**
  - `nemotron-3.5-lightning-30b-a3b` is a reasoning model that emits both `reasoning_content` and `content`.
  - Reasoning tokens count toward the completion budget. `max_tokens` must be set sufficiently high (e.g. 2500) to allow reasoning completion before final JSON emission.
  - Latency is ~45-50 seconds for full chain-of-thought and structured JSON generation.
- **Verified Behavior:**
  - Tested live against a synthetic cocoa factory disruption evidence packet (`ROAST-01` bearing temperature, vibration, batch delay, plan trade-offs).
  - Model returned valid, schema-compliant JSON with zero ungrounded facts.
  - **Grounding verification:** 100% of cited evidence IDs (`EV-ROAST-TEMP`, `EV-ROAST-VIB`, `EV-BATCH-DELAY`) mapped to input evidence with zero hallucinated IDs or numbers.
- **Deterministic Non-LLM Fallback:**
  - Implemented and verified for API timeouts, rate limits, network failures, or malformed responses.
  - Fallback strictly maps verified facts and sorts plans deterministically by deadline and cost without inventing numbers or confidence scores.
- **Security:** API key maintained backend-only in `.env` (excluded by `.gitignore`). Never logged or exposed.
