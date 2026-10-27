# FactoryOps Backend
FactoryOps — Manufacturing Disruption Investigation & Recovery

FactoryOps is an AI-assisted operational intelligence platform for investigating manufacturing disruptions, tracing their downstream consequences, and evaluating recovery plans before operators act.

A disruption in one part of a factory can affect machine availability, production batches, inventory, supplier dependencies, customer orders, delivery commitments, and operational costs. FactoryOps connects these dependencies so operators can investigate the impact, compare feasible recovery options, and verify outcomes.

What FactoryOps Does

* Investigates disruptions: Trace dependencies across equipment, production, inventory, suppliers, and customer orders.
* Simulates consequences: Advance a discrete-step operational simulation and inspect projected delays and downstream effects.
* Evaluates recovery plans: Compare candidate plans using deterministic feasibility checks, cost considerations, and AI-assisted explanations where available.
* Keeps operators in control: Support human approval, rejection, and audit recording, with execution governed by explicit authorization and feasibility checks.
* Maintains operational traceability: Inspect chronological events, state changes, and source provenance.

Technology Stack

* Backend: Python, FastAPI, Uvicorn
* Frontend: React 19, TypeScript, Vite
* Database: PostgreSQL
* Data integration: Zetaris Networked Data Platform (NDP), where configured and available
* AI assistance: Meterless runtime and NVIDIA-related model capabilities where configured and verified

FactoryOps distinguishes live integrations from deterministic fallbacks. A fallback response should not be interpreted as evidence that an external sponsor service was successfully invoked.

Running Locally

Follow the prerequisites, environment setup, PostgreSQL startup, backend startup, and frontend instructions below. The application can be evaluated locally without a public deployment URL.



Review note: Sponsor integration status, test results, and demo behavior should be verified against the current code and runtime environment rather than inferred from the interface alone.
FastAPI foundation for FactoryOps operational intelligence and investigation platform.

---

## Prerequisites

- **Python**: 3.13 or 3.14
- **Docker & Docker Compose**: For running the local PostgreSQL database service

---

## 1. Environment & Setup

Ensure the root `.env` file exists with your local configuration (copy from `.env.example` if setting up fresh):

```bash
# Windows (PowerShell)
Copy-Item .env.example .env
```

### Create and Activate Virtual Environment

```bash
# Create local virtual environment
py -3.13 -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Start PostgreSQL Database

Start the dedicated local PostgreSQL 17 container:

```bash
docker compose up -d factoryops-db
```

Verify that the database container is healthy:

```bash
docker compose ps
```

---

## 3. Start the API

Run the development server using Uvicorn:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## 4. Test Endpoints

### Application Health
```bash
# PowerShell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get

# curl
curl -X GET "http://127.0.0.1:8000/health"
```
**Expected Response (200 OK):**
```json
{
  "status": "healthy",
  "app": "FactoryOps",
  "environment": "development"
}
```

### Database Connectivity Health
```bash
# PowerShell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/db" -Method Get

# curl
curl -X GET "http://127.0.0.1:8000/health/db"
```
**Expected Response (200 OK):**
```json
{
  "status": "healthy",
  "database": "connected"
}
```

*Note: If the database is unreachable, `/health/db` returns `503 Service Unavailable` with a sanitized message, preventing credential exposure.*

### Interactive API Documentation
Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) in your browser.

---

## 5. React Operator Console (Chunk 3)

The primary operator interface is a modern React 19 + TypeScript + Vite single-page application located in `frontend/`, designed as both an operator console for manufacturing simulation and a demonstrable full-stack portfolio application.

### Key Capabilities

1. **Operations Overview & 7-Stage Process Line**:
   - Visual process flow: Cleaning, Roasting, Winnowing, Grinding, Pressing, Pulverizing, Packing.
   - Real-time SCADA telemetry: Bearing temperature, vibration RMS, and throughput across all machine assets.
   - Active MES production batches (e.g., BATCH-101 Dark Chocolate 70%) and ERP customer order SLA tracking (ORD-9402 Artisan Chocolatier).
   - WMS inventory tracking across silos and storage lots.

2. **Disruptions & Recovery Intelligence**:
   - Discrete-event simulation controller (`Step +1`, `Step to Anomaly +3`, custom steps).
   - Real-time anomaly detection banner with SCADA threshold violation alerts and NASA IMS benchmark correlation.
   - Downstream consequence tracing: Unintervened completion projections, delay hours, and contractual penalty calculations.
   - Ranked candidate recovery plans (Plan A, B, C) with deterministic feasibility/cost breakdowns and AI operational explanations (Meterless Runtime / NVIDIA Nemotron / deterministic fallback).
   - Authoritative **Human-in-the-Loop** operator approval/rejection modal with audit recording.

3. **Meterless World Model & Canonical Graph**:
   - Hierarchical entity graph: Plant $\to$ Equipment Fleet $\to$ Batches $\to$ Customer Orders.
   - Canonical state inspection (`wm.state_shape()`).
   - Bitemporal verified facts and append-only state mutation audit trail.

4. **Unified Semantic Layer (USL) & Zetaris Federation**:
   - Zetaris Networked Data Platform (NDP) connectivity diagnostics and host JDBC driver verification.
   - Interactive Federated SQL Query Runner with live execution channel selection (`auto`, `jdbc`, `rest`).
   - Explicit **Source Provenance** banner distinguishing `zetaris_federation` from transparent `direct_postgres_fallback`.
   - Semantic Mapping Manifesto table aligning heterogeneous operational systems.
   - NASA IMS Bearing Run-to-Failure degradation benchmark data viewer (ISO 10816 velocity RMS).

5. **Verified Event Audit Timeline**:
   - Append-only chronological timeline of operational events with event type filtering, search, and deep JSON payload inspection.

### Running the Console

#### Option A: Served Directly via FastAPI (Production/Static Mode)
The frontend build is compiled directly into `app/static/dist/`:
```bash
# Build frontend (compiles to app/static/dist)
cd frontend
npm run build
cd ..

# Start FastAPI backend (serves console at root and /console)
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open **[http://127.0.0.1:8000/console](http://127.0.0.1:8000/console)** or **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)** in your browser.

#### Option B: Vite Dev Server with Live HMR (Developer Mode)
```bash
# Terminal 1: Backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: Frontend
cd frontend
npm run dev
```
Open **[http://localhost:5173/](http://localhost:5173/)** in your browser. API requests are automatically proxied to `http://127.0.0.1:8000`.

