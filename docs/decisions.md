# FactoryOps — Architectural Decision Log

This document records key architectural, design, and integration decisions made during the implementation of FactoryOps.

---

## ADR-001: Technology Stack Baseline
- **Status:** Accepted
- **Context:** FactoryOps requires a high-performance, modular backend capable of deterministic simulation, cross-domain querying, probabilistic forecasting, and grounded LLM explanations.
- **Decision:** Use Python 3.13 with FastAPI for the API layer and background processing, and PostgreSQL 17 for persistent application state and event logs.
- **Rationale:** FastAPI offers native Pydantic validation, async/sync flexibility, and rapid execution. Python provides rich numerical and data structures for discrete event simulation and graph traversal.

---

## ADR-002: Infrastructure Minimalism & Runtime Safety
- **Status:** Accepted
- **Context:** Hackathon constraints and operational reliability require low complexity and high reproducibility.
- **Decision:** Strictly avoid Kubernetes, Apache Kafka, Redis clusters, or external graph databases in v1. All dependency graphs are modeled and traversed directly in Python; local state is persisted in PostgreSQL.
- **Rationale:** Eliminates orchestration overhead, failure modes, and resource competition with the local Zetaris deployment.

---

## ADR-003: Dedicated Application Database on Port 5433
- **Status:** Accepted
- **Context:** The local development environment hosts an existing Zetaris PostgreSQL instance. FactoryOps requires its own relational persistence without mutating or relying on Zetaris internals.
- **Decision:** Provision a dedicated PostgreSQL 17 Docker container (`factoryops-db`) mapping container port 5432 to host port 5433 (`127.0.0.1:5433:5432`), using named volume `factoryops_pgdata` and distinct passwords for superuser `postgres` and non-superuser application user `factoryops_app`.
- **Rationale:** Ensures clean separation of concerns, guarantees loopback-only private binding, and prevents any disruption to Zetaris.

---

## ADR-004: Sponsor Technology Roles & Integration Boundaries
- **Status:** Accepted
- **Context:** Sponsor tools must be central and substantive, not superficial or mocked in the demonstration.
- **Decision:**
  - **Zetaris:** The mandatory operational integration and query layer across factory domains.
  - **Meterless World Model:** Responsible for canonical entity identity resolution, alias tracking, provenance, and idempotent ingestion.
  - **NVIDIA Nemotron:** Used strictly on the backend to synthesize structured evidence packets and explain recovery trade-offs without performing arithmetic or generating ungrounded facts.
  - **Cursor:** Development-time authoring and review tool; never treated as a runtime dependency.
- **Rationale:** Enforces clear architectural separation of duties and guarantees honesty in sponsor integration claims.

---

## ADR-005: Deterministic Business Calculations & Isolated Recovery
- **Status:** Accepted
- **Context:** Factory operational decisions carry real costs, order deadlines, and resource constraints.
- **Decision:** Calculations, impact tracing, hard-constraint checks, and recovery evaluations are executed deterministically in Python. Recovery candidates are evaluated against isolated state copies compared to a no-action baseline. Authoritative state is mutated only after explicit human approval.
- **Rationale:** Prevents AI hallucination from compromising operational facts and guarantees human-in-the-loop safety.

---

## ADR-006: Zetaris HTTP REST API Adapter over Host JDBC Bridge
- **Status:** Accepted
- **Context:** Zetaris provides a JDBC endpoint on port 10000 and an HTTP REST API on port 8888 (with UI proxy on port 3000). The Windows host machine does not have a Java runtime environment (`java` is not installed), making JDBC from Python via `JayDeBeApi` or `JPype` reliant on installing a JRE/JDK, setting `JAVA_HOME`, and native C-binding configuration.
- **Decision:** Integrate FactoryOps with Zetaris primarily via its authenticated HTTP REST API (`/api/v1.0/query/sql/start`), wrapping it in an async Python client. Retain JDBC configuration parameters and the driver JAR for future containerized or production deployments where a JVM is packaged.
- **Rationale:** The REST API executes native Spark SQL against the live engine, returns structured JSON records, requires zero host JVM dependencies, and provides complete query capabilities for operational data integration.

---

## ADR-007: Relational Schema Separation for Operational Sources vs Application Projections
- **Status:** Accepted
- **Context:** Factory operational records originate from fragmented sources (ERP, MES, SCADA, WMS) and must be queried through Zetaris without direct application shortcuts. Meanwhile, FactoryOps owns simulation scenarios, current-state projections, and append-only event envelopes.
- **Decision:** Separate relational tables into two distinct PostgreSQL schemas within the `factoryops` database:
  1. `factory_source`: Hosts raw operational silo tables (`source_suppliers`, `source_inventory`, `source_machine_telemetry`, `source_batches`, `source_orders`) serving as the ground-truth operational store federated by Zetaris.
  2. `public`: Hosts application-owned domain models (`suppliers`, `inventory_lots`, `machine_assets`, `production_batches`, `customer_orders`, `simulation_scenarios`, `operational_events`) managed via Alembic migrations.
- **Rationale:** Guarantees strict boundary compliance, ensures Zetaris cannot be bypassed by raw SQL shortcuts, and prevents operational source pollution of application state.

---

## ADR-008: Transactional Savepoint Test Isolation
- **Status:** Accepted
- **Context:** Integration tests write records to verify constraints, triggers, and foreign keys. Leaving test artifacts in shared development databases risks polluting baseline demonstrations and skewing row counts.
- **Decision:** Standardize test fixtures to use nested transactions (`join_transaction_mode="create_savepoint"`). Tests and FastAPI `TestClient` dependency overrides run within an active savepoint; session commits commit the savepoint, and fixture teardown executes `transaction.rollback()`.
- **Rationale:** Guarantees zero persistent rows on disk after test suite completion, prevents test-order interdependencies, and ensures repeatable validation without destructive table truncation.

---

## ADR-009: Copy-on-Write In-Memory Scenario Snapshots with Explicit Logical Timelines
- **Status:** Accepted
- **Context:** Discrete simulation requires evaluating "what-if" disruptions and multi-step scenarios without corrupting baseline operational records or interfering with parallel scenarios.
- **Decision:** Each `SimulationScenario` owns an isolated JSONB state snapshot (`parameters["snapshot"]`) initialized from baseline operational data. Discrete simulation steps advance simulated time in fixed intervals (e.g. 15 minutes), mutate only the scenario snapshot in memory, and append immutable `OperationalEvent` records to PostgreSQL referencing the scenario ID.
- **Rationale:** Guarantees absolute scenario isolation, prevents baseline state corruption, ensures deterministic reproducibility given a random seed, and provides an auditable, append-only chronological event timeline.
