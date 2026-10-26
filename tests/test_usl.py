"""Comprehensive verification suite for Unified Semantic Layer (USL) & Zetaris Federation.

Tests:
1. NASA IMS Bearing dataset loader, unit normalization, ISO 10816 velocity RMS, and raw accelerometer g-force.
2. NASA IMS timeline progression: validates run-to-failure degradation from nominal to critical failure.
3. USL mapping manifesto: verifies canonical mappings for SCADA telemetry, MES batches, ERP orders, and operator decisions.
4. Canonical entity normalization: transforms raw source records into typed canonical models with provenance.
5. Zetaris JDBC driver verification: checks JAR presence on disk, driver class, and host JVM boundary documentation.
6. Zetaris REST query and PostgreSQL fallback execution: serves real operational rows with full provenance tracking.
7. World Model integration: populates canonical entities and asserts facts tagged with empirical provenance.
8. Provenance tracing: separates empirical measured facts from discrete simulation projections and operator decisions.
9. Recovery impact analysis provenance: verifies disruption evidence carries genuine NASA benchmark attribution.
10. FastAPI USL endpoints: GET /mappings, GET /nasa-bearing, GET /timeline, GET /status, POST /federated-query, GET /provenance.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.simulations import get_db
from app.database import engine
from app.main import app
from app.services.recovery import detect_and_trace_impact
from app.services.seed import seed_baseline_factory
from app.services.simulation import create_scenario, step_scenario
from app.usl import (
    CanonicalCustomerOrder,
    CanonicalMachineCondition,
    CanonicalProductionBatch,
    NASABearingDataLoader,
    SemanticRecordKind,
    UnifiedSemanticLayerService,
    ZetarisFederationClient,
)
from app.world_model import Provenance, WorldModel


@pytest.fixture
def db_session():
    """Isolated session with nested savepoints ensuring zero row drift."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def test_nasa_ims_data_loader():
    """Verifies parsing of authentic NASA / UC IMS Bearing Dataset run-to-failure vibration data."""
    loader = NASABearingDataLoader()
    records = loader.load_records()
    assert len(records) >= 6

    # Verify first nominal record
    rec_nominal = records[0]
    assert rec_nominal.entity_id == "machine:plant:ROAST-01"
    assert rec_nominal.source_bearing_id == "bearing_1"
    assert rec_nominal.record_kind == SemanticRecordKind.MEASURED_FACT
    assert rec_nominal.provenance.source_system == "nasa_ims_benchmarks"
    assert "NASA" in rec_nominal.provenance.source_dataset
    assert rec_nominal.vibration_rms_mms > 0.0
    assert rec_nominal.temperature_c > 0.0

    # Verify degraded anomaly record
    rec_degraded = loader.get_record_by_condition("degraded")
    assert rec_degraded is not None
    assert rec_degraded.vibration_rms_mms >= 4.0
    assert rec_degraded.temperature_c >= 140.0
    assert rec_degraded.operational_status == "degraded"


def test_nasa_ims_timeline_progression():
    """Validates the chronological degradation progression with delta ratios against baseline."""
    loader = NASABearingDataLoader()
    timeline = loader.get_timeline()
    assert len(timeline) >= 6

    first = timeline[0]
    last = timeline[-1]
    assert first["operational_status"] == "nominal"
    assert first["vibration_ratio_vs_baseline"] == 1.0
    assert "raw_ch1_g" in first
    assert "raw_ch2_g" in first

    # Last record should show severe degradation (> 4x baseline)
    assert last["vibration_ratio_vs_baseline"] >= 4.0
    assert last["operational_status"] in ("degraded", "critical_failure")


def test_usl_mapping_manifesto():
    """Validates the USL mapping manifesto across SCADA, MES, ERP, and Operator governance domains."""
    service = UnifiedSemanticLayerService()
    mappings = service.get_mapping_manifesto()
    assert len(mappings) >= 5

    kinds = {m.record_kind for m in mappings}
    assert SemanticRecordKind.MEASURED_FACT in kinds
    assert SemanticRecordKind.SIMULATED_SCENARIO in kinds
    assert SemanticRecordKind.OPERATOR_DECISION in kinds

    for m in mappings:
        assert m.source_entity
        assert m.canonical_entity
        assert len(m.key_mapping) > 0
        assert m.provenance_rule
        assert m.timestamp_semantics


def test_canonical_batch_and_order_mapping():
    """Tests semantic mapping of raw operational source records into canonical USL entities."""
    service = UnifiedSemanticLayerService()

    # Raw MES batch
    raw_batch = {
        "batch_id": "BATCH-101",
        "recipe_code": "dark_70_standard",
        "target_quantity_kg": 2500.0,
        "actual_quantity_kg": 645.0,
        "stage": "IN_PROGRESS",
        "assigned_machine_code": "ROAST-01",
    }
    canon_batch = service.map_operational_batch(raw_batch, scenario_id="scn_test")
    assert canon_batch.batch_id == "production_batch:mes:BATCH-101"
    assert canon_batch.planned_quantity_kg == 2500.0
    assert canon_batch.remaining_quantity_kg == 1855.0
    assert canon_batch.record_kind == SemanticRecordKind.SIMULATED_SCENARIO
    assert canon_batch.provenance.source_system == "factory_source.source_batches"

    # Raw ERP order
    raw_order = {
        "order_id": "ORD-9402",
        "customer_name": "Artisan Chocolatier BV",
        "target_product": "Dark Chocolate 70%",
        "quantity_kg": 2500.0,
        "promised_delivery": "2026-10-22T14:00:00+00:00",
        "status": "confirmed",
    }
    canon_order = service.map_operational_order(raw_order, scenario_id="scn_test")
    assert canon_order.order_id == "customer_order:erp:ORD-9402"
    assert canon_order.customer_name == "Artisan Chocolatier BV"
    assert canon_order.record_kind == SemanticRecordKind.SIMULATED_SCENARIO
    assert canon_order.provenance.source_system == "factory_source.source_orders"


def test_zetaris_jdbc_status_and_driver_jar():
    """Verifies that the Zetaris Lightning JDBC driver JAR exists on disk and reports environment status."""
    client = ZetarisFederationClient()
    jdbc_status = client.check_jdbc_status()

    assert jdbc_status["jar_present"] is True
    assert jdbc_status["driver_jar_exists"] is True
    assert jdbc_status["driver_jar_filename"] == "ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar"
    assert jdbc_status["driver_version"] == "2.4.3.1-7eff043"
    assert jdbc_status["jar_size_bytes"] > 10_000_000
    assert jdbc_status["driver_class"] == "com.zetaris.lightning.jdbc.LightningDriver"
    assert "boundary_notes" in jdbc_status
    assert "next_operator_action" in jdbc_status
    assert jdbc_status["factory_schema_registered"] is False


def test_zetaris_jdbc_missing_jar_and_unreachable_host():
    """Verifies that driver presence on disk is distinguished from live connection."""
    # 1. Missing JAR client
    bad_jar_client = ZetarisFederationClient(driver_jar="jdbc/non_existent_driver.jar")
    status_bad_jar = bad_jar_client.check_jdbc_status()
    assert status_bad_jar["driver_jar_exists"] is False
    assert status_bad_jar["jar_present"] is False
    assert status_bad_jar["connection_status"] == "driver_jar_missing"
    assert status_bad_jar["live_session_verified"] is False

    # 2. Unreachable port client
    unreachable_client = ZetarisFederationClient(jdbc_url="jdbc:zetaris:lightning@localhost:59999")
    status_unreachable = unreachable_client.check_jdbc_status()
    assert status_unreachable["jdbc_port_open"] is False
    assert status_unreachable["connection_status"] == "host_unreachable"
    assert status_unreachable["live_session_verified"] is False
    assert "password" not in status_unreachable["next_operator_action"].lower()
    assert "secret" not in status_unreachable["next_operator_action"].lower()


def test_zetaris_federation_fallback_boundary():
    """Verifies Zetaris query execution with transparent fallback to PostgreSQL factory_source."""
    client = ZetarisFederationClient(base_url="http://127.0.0.1:8888")
    res = client.query_federated("SELECT * FROM factory_source.source_batches LIMIT 2", allow_fallback=True)

    assert res.success is True
    assert res.source_layer in ("zetaris_federation", "direct_postgres_fallback")
    assert res.provenance.source_system in ("zetaris_ndp_federation", "factoryops_postgresql_fallback")
    assert len(res.boundary_notes) > 0

    if res.source_layer == "direct_postgres_fallback":
        assert len(res.records) >= 1
        assert "batch_id" in res.records[0] or "recipe_code" in res.records[0]


def test_populate_world_model_from_usl():
    """Verifies injecting genuine NASA IMS telemetry into World Model with verified provenance."""
    service = UnifiedSemanticLayerService()
    world = WorldModel(namespace="test_usl_scn")

    res = service.populate_world_model_from_usl(world, "test_usl_scn", condition="degraded")
    assert res["status"] == "degraded"
    assert res["vibration_rms"] >= 4.0
    assert res["record_kind"] == "measured_fact"

    # Verify World Model facts
    facts = [f for f in world.state_shape()["facts"] if f["about"] == "machine:plant:ROAST-01"]
    assert len(facts) >= 3
    fact_map = {f["predicate"]: f["value"] for f in facts}
    assert fact_map["status"] == "degraded"
    assert fact_map["vibration_rms"] >= 4.0


def test_usl_scenario_provenance_tracing():
    """Validates categorization of World Model facts into measured facts, simulation facts, and operator decisions."""
    service = UnifiedSemanticLayerService()
    world = WorldModel(namespace="test_trace_scn")

    # 1. Assert measured fact
    world.assert_fact(
        about="machine:plant:ROAST-01",
        predicate="vibration_rms",
        value=4.21,
        source=Provenance(kind="telemetry", at="2026-10-22T06:45:00Z", by="nasa_ims_benchmarks", run_id="test_trace_scn"),
    )

    # 2. Assert simulated scenario fact
    world.assert_fact(
        about="production_batch:mes:BATCH-101",
        predicate="status",
        value="delayed",
        source=Provenance(kind="simulator", at="2026-10-22T06:45:00Z", by="simulator:step", run_id="test_trace_scn"),
    )

    # 3. Assert operator governance decision
    world.assert_fact(
        about="decision:plan_a",
        predicate="approved_plan",
        value="plan_a_thermal_flush",
        source=Provenance(kind="approval", at="2026-10-22T07:00:00Z", by="operator:chief", run_id="test_trace_scn"),
    )

    trace = service.trace_scenario_provenance(world)
    assert trace["total_facts_asserted"] == 3
    assert trace["measured_facts_count"] >= 1
    assert trace["simulated_scenario_count"] >= 1
    assert trace["governance_decisions_count"] >= 1


def test_recovery_impact_analysis_carries_usl_provenance(db_session: Session):
    """Verifies that recovery disruption evidence carries NASA benchmark attribution and semantic kind."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="USL Provenance Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_usl_prov",
    )
    step_scenario(db_session, "scn_usl_prov", steps=3)

    impact = detect_and_trace_impact(db_session, "scn_usl_prov")
    assert len(impact.evidence) >= 1

    ev = impact.evidence[0]
    assert ev.record_kind == "measured_fact"
    assert "NASA" in ev.provenance_source
    assert "physical_observations" in impact.provenance_summary
    assert "semantic_layer" in impact.provenance_summary


def test_usl_fastapi_endpoints(db_session: Session):
    """Verifies all FastAPI routes exposed by the Unified Semantic Layer router."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="API Test Scenario",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_usl_api",
    )
    step_scenario(db_session, "scn_usl_api", steps=3)

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)

    try:
        # 1. GET /usl/mappings
        r_map = client.get("/usl/mappings")
        assert r_map.status_code == 200
        assert r_map.json()["mapping_count"] >= 5

        # 2. GET /usl/nasa-bearing
        r_bearing = client.get("/usl/nasa-bearing")
        assert r_bearing.status_code == 200
        assert r_bearing.json()["record_count"] >= 6

        # 3. GET /usl/nasa-bearing/timeline
        r_time = client.get("/usl/nasa-bearing/timeline")
        assert r_time.status_code == 200
        assert r_time.json()["progression_count"] >= 6

        # 4. GET /usl/nasa-bearing/degraded
        r_deg = client.get("/usl/nasa-bearing/degraded")
        assert r_deg.status_code == 200
        assert r_deg.json()["record"]["vibration_rms_mms"] >= 4.0

        # 5. GET /usl/zetaris/status
        r_status = client.get("/usl/zetaris/status")
        assert r_status.status_code == 200
        assert r_status.json()["jdbc_environment"]["jar_present"] is True

        # 6. GET /usl/zetaris-query
        r_query = client.get("/usl/zetaris-query?sql=SELECT+1+as+probe")
        assert r_query.status_code == 200
        assert r_query.json()["success"] is True

        # 7. POST /usl/federated-query
        r_post_query = client.post(
            "/usl/federated-query",
            json={"sql": "SELECT * FROM factory_source.source_batches LIMIT 1", "allow_fallback": True},
        )
        assert r_post_query.status_code == 200
        assert r_post_query.json()["success"] is True

        # 8. GET /usl/provenance/{scenario_id}
        r_prov = client.get("/usl/provenance/scn_usl_api")
        assert r_prov.status_code == 200
        assert "provenance_trace" in r_prov.json()
    finally:
        app.dependency_overrides.clear()


def test_zetaris_jdbc_query_mocked_success():
    """Unit test: verifies JDBC query execution, cursor mapping, and source attribution with mocked jaydebeapi."""
    mock_cursor = MagicMock()
    mock_cursor.description = [("LocationID", None), ("borough", None), ("zone", None)]
    mock_cursor.fetchall.return_value = [
        [1, "EWR", "Newark Airport"],
        [2, "Queens", "Jamaica Bay"],
    ]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("jaydebeapi.connect", return_value=mock_conn):
        client = ZetarisFederationClient()
        res = client.query_federated("SELECT LocationID, borough, zone FROM NY_TAXI.taxi_zones LIMIT 2", channel="jdbc")

        assert res.success is True
        assert res.source_layer == "zetaris_federation"
        assert res.total_count == 2
        assert res.records[0]["borough"] == "EWR"
        assert res.provenance.source_system == "zetaris_ndp_federation"
        assert res.provenance.source_dataset == "zetaris_lightning_jdbc"
        mock_cursor.close.assert_called_once()
        mock_conn.close.assert_called_once()


def test_zetaris_jdbc_query_mocked_failure_with_fallback(db_session: Session):
    """Unit test: verifies that a JDBC error transparently falls back to PostgreSQL when allow_fallback=True."""
    seed_baseline_factory(db_session)

    with patch("jaydebeapi.connect", side_effect=RuntimeError("Connection refused to port 10000")):
        client = ZetarisFederationClient()
        res = client.query_federated("SELECT * FROM factory_source.source_batches LIMIT 2", allow_fallback=True, channel="jdbc")

        assert res.success is True
        assert res.source_layer == "direct_postgres_fallback"
        assert res.provenance.source_system == "factoryops_postgresql_fallback"
        assert "Connection refused" in res.boundary_notes
        assert len(res.records) >= 1


def test_zetaris_jdbc_query_mocked_failure_no_fallback():
    """Unit test: verifies that when allow_fallback=False, JDBC failure returns success=False without fallback."""
    with patch("jaydebeapi.connect", side_effect=RuntimeError("Simulated JDBC network failure")):
        client = ZetarisFederationClient()
        res = client.query_federated("SELECT 1", allow_fallback=False, channel="jdbc")

        assert res.success is False
        assert res.source_layer == "zetaris_federation"
        assert res.total_count == 0
        assert "Simulated JDBC network failure" in res.boundary_notes


def test_zetaris_live_jdbc_smoke_query():
    """Integration test: runs a real query against live Zetaris JDBC on port 10000 if reachable."""
    client = ZetarisFederationClient()
    status = client.check_jdbc_status()

    if not status["jdbc_port_open"]:
        pytest.skip("Live Zetaris JDBC port 10000 is not reachable; skipping live smoke test.")

    res = client.query_federated("SELECT 1 as ping", allow_fallback=False, channel="jdbc")
    assert res.success is True
    assert res.source_layer == "zetaris_federation"
    assert res.total_count >= 1
    assert res.records[0].get("ping") == 1 or res.records[0].get("1") == 1
    assert res.provenance.source_dataset == "zetaris_lightning_jdbc"

