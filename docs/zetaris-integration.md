# FactoryOps — Zetaris Integration Spike Report

**Date:** 2026-10-22  
**Status:** Spike Complete — Authenticated Query Verified  
**Author:** Forge Intelligence Engineering  

---

## 1. Executive Summary

A connectivity and query spike was conducted against the local Zetaris deployment running in Docker Compose (`zetaris-driver-1`, `zetaris-api-1`, `zetaris-ui-1`, `zetaris-postgres-1`).

### Key Findings
1. **Authenticated Query Succeeded:** Successfully authenticated and executed Spark SQL queries against the live Zetaris query engine, returning structured record results.
2. **Zero-JVM HTTP Interface Available:** Zetaris provides a first-class HTTP REST API on port `8888` (and via proxy on port `3000`) that executes arbitrary Spark SQL queries without requiring Java, JVM bridges, or native C-compilers on the host.
3. **Host Java Limitation:** The Windows host environment has **no Java runtime installed** (`java: command not found`). Attempting to use the JDBC driver (`jdbc/ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar`) via `JayDeBeApi` or `JPype` requires installing a 64-bit JRE/JDK on Windows.
4. **Recommended Integration Path:** Use a lightweight asynchronous Python REST client (`ZetarisRestClient`) leveraging standard HTTP libraries (`httpx`). This eliminates the heavyweight JVM dependency while achieving full query functionality.

---

## 2. Infrastructure Architecture & Topology

The running Zetaris stack consists of four Docker containers:

| Container Name | Role | Container Port(s) | Host Port(s) | Service Description |
|---|---|---|---|---|
| `zetaris-driver-1` | Spark Driver & Hive Thrift Server | 10000, 4040, 8080 | `10000` (JDBC), `4040` (Spark UI), `32771` | Spark 3.x cluster driver running `org.apache.spark.sql.hive.thriftserver.LightningHiveServer2`. |
| `zetaris-api-1` | Zetaris Core API | 8888, 8889 | `8888` (Public REST), `8889` (Admin) | Scala `http4s` REST API coordinating query submission and catalog services. |
| `zetaris-ui-1` | Zetaris Console | 3000 | `3000` (Web UI & Proxy) | Web frontend and reverse proxy to the core API. |
| `zetaris-postgres-1` | Zetaris Metadata Store | 5432 | `32770` (Dynamic) | Internal PostgreSQL storing accounts, workspaces, and catalog metadata. |

---

## 3. Verified Client Interfaces

### Interface A: HTTP REST API (Verified & Recommended)

The Zetaris stack exposes a RESTful query endpoint on both `http://127.0.0.1:8888` (direct) and `http://127.0.0.1:3000/api/proxy` (UI proxy).

#### Authentication Protocol
- **Endpoint:** `POST /api/auth/login` (or `/api/v1.0/auth/login`)
- **Headers:** `Content-Type: application/json`
- **Payload:**
  ```json
  {
    "email": "<ZETARIS_USER>",
    "password": "<ZETARIS_PASSWORD>"
  }
  ```
- **Response Structure (200 OK):**
  ```json
  {
    "idToken": "<jwt_or_bearer_token>",
    "tokenType": "Bearer",
    "expiresIn": 86400,
    "user": {
      "id": 1,
      "email": "<sanitized>",
      "organizations": [
        {
          "id": 1,
          "name": "Default",
          "default": true
        }
      ]
    }
  }
  ```

#### Query Execution Protocol
- **Endpoint:** `POST /api/v1.0/query/sql/start`
- **Required Headers:**
  - `Authorization: Bearer <idToken>`
  - `X-Org-ID: 1`
  - `X-Request-ID: <uuidv4>`
  - `Content-Type: application/json`
- **Payload:**
  ```json
  {
    "select": "SELECT 1 AS probe, 7 AS seven",
    "pageLimit": 100
  }
  ```
- **Validation Rule:** The endpoint enforces that the input SQL statement is a `SELECT` statement. Metadata commands such as `SHOW DATABASES` return HTTP 400 (`Bad Select SQL at 'select'`). Catalog and metadata inspection must be queried through SQL `information_schema` views or dedicated management REST endpoints.

#### Verified Live Query Results

##### Test 1: Arithmetic & Column Projection Probe
- **Query:** `SELECT 1 AS probe, 7 AS seven`
- **HTTP Status:** `200 OK`
- **Response Payload:**
  ```json
  {
    "queryId": "<uuid>",
    "executionStatus": "SUCCEEDED",
    "records": [
      {
        "probe": "1",
        "seven": "7"
      }
    ],
    "count": 1
  }
  ```

##### Test 2: Spark SQL Context & Date Function Verification
- **Query:** `SELECT current_database() AS db, current_date() AS today`
- **HTTP Status:** `200 OK`
- **Response Payload:**
  ```json
  {
    "queryId": "<uuid>",
    "executionStatus": "SUCCEEDED",
    "records": [
      {
        "db": "default",
        "today": "2026-10-22"
      }
    ],
    "count": 1
  }
  ```
- **Execution Latency:** ~1.8 seconds round-trip.

---

### Interface B: JDBC via Hive Thrift Server (Port 10000)

The Zetaris driver container runs `LightningHiveServer2` on port `10000`.

- **JDBC URL:** `jdbc:zetaris:lightning@localhost:10000`
- **Driver Class:** `com.zetaris.lightning.jdbc.LightningDriver`
- **Driver Jar:** Located at `jdbc/ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar` (size: 61,712,414 bytes, verified SHA-256 integrity).
- **Prerequisites Analysis:**
  - Running JDBC from Python requires either `JayDeBeApi` or `JPype1`.
  - Both libraries require a native Java Virtual Machine runtime (`jvm.dll` on Windows).
  - Checking the Windows development environment:
    ```powershell
    Get-Command java
    # Result: java: The term 'java' is not recognized as a name of a cmdlet...
    ```
  - **Verdict:** Using JDBC on this host requires installing Java 8/11/17 on Windows, setting `JAVA_HOME`, and compiling/linking `JPype1`. Because the REST API provides direct Spark SQL execution over standard HTTP, introducing Java dependencies onto the host is unnecessary complexity for the hackathon MVP.

---

## 4. Configuration Specification (Sanitized)

To support the REST client without breaking future JDBC capability, `.env` and `app/core/config.py` can be organized as follows:

```dotenv
# Zetaris Integration
ZETARIS_API_BASE_URL=http://127.0.0.1:8888
ZETARIS_UI_BASE_URL=http://127.0.0.1:3000
ZETARIS_USER=<configured_user>
ZETARIS_PASSWORD=<configured_password>
ZETARIS_ORG_ID=1
ZETARIS_QUERY_TIMEOUT_SECONDS=30

# Optional JDBC legacy parameters (preserved for future or containerized use)
ZETARIS_JDBC_URL=jdbc:zetaris:lightning@localhost:10000
ZETARIS_JDBC_DRIVER_CLASS=com.zetaris.lightning.jdbc.LightningDriver
ZETARIS_JDBC_DRIVER_JAR=jdbc/ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar
```

---

## 5. Catalog Status & Unresolved Blockers

### Current Catalog Inventory
- Default catalogs: `default`
- Preloaded sample datasets: `NY_TAXI`, `TPCDS_DB` (pointing to AWS S3 demo buckets).
- External registered datasources: **0** (`SHOW DATASOURCES` returns empty).

### Next Blockers & Required Decisions
1. **Factory Operational Data Ingestion Strategy:**
   - Zetaris is designed to federate and query multiple operational silos.
   - For FactoryOps (virtual cocoa-processing plant: silo storage, roasting, winnowing, grinding, conching, press):
     - **Option 1 (PostgreSQL Federation):** Register our dedicated PostgreSQL database (`factoryops-db` on port 5432 internal) as a JDBC datasource within Zetaris. Zetaris then queries telemetry and batch runs via federated views.
     - **Option 2 (File-based Staging):** Mount a CSV/Parquet volume into the `zetaris-driver-1` container or place files in a directory accessible to Zetaris, registering them as external tables.
     - **Option 3 (Direct Operational Store + Zetaris Analytical Federation):** Ingest raw operational telemetry into PostgreSQL tables, and use Zetaris for cross-silo aggregation and anomaly queries.
2. **REST Client Adapter Implementation (Phase 2):**
   - Implement an async `ZetarisClient` in `app/integrations/zetaris.py` with token caching, automatic re-authentication upon 401, timeout enforcement, and schema mapping to Pydantic models.
