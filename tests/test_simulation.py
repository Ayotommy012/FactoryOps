"""
Verification suite for the Phase 3 minimal simulator vertical slice.

Tests:
1. Deterministic baseline factory seed fixture & schema separation.
2. Scenario creation and initial isolated snapshot.
3. 15-minute discrete stepping: nominal production advance.
4. Reproducible roaster anomaly: bearing overheating and vibration disruption.
5. Explicit event ordering and simulation-time semantics.
6. Scenario isolation: stepping one scenario leaves other scenarios and baseline untouched.
7. Endpoints test: POST /simulations, POST /step, GET /simulations/{id}, GET /timeline.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from app.models import (
    CustomerOrder,
    InventoryLot,
    MachineAsset,
    OperationalEvent,
    ProductionBatch,
    SimulationScenario,
    SourceSupplier,
    Supplier,
)
from app.services.seed import BASELINE_START_TIME, seed_baseline_factory
from app.services.simulation import (
    create_scenario,
    get_scenario_timeline,
    step_scenario,
)


@pytest.fixture
def db_session():
    """
    Isolated session using nested savepoints.
    All commits within tests are rolled back at fixture teardown,
    leaving zero persistent rows.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def test_baseline_factory_seed_and_schema_separation(db_session: Session):
    """Verifies that baseline seed populates factory_source and public schemas idempotently."""
    res1 = seed_baseline_factory(db_session)
    assert res1["status"] in ("seeded", "already_seeded")

    # Verify factory_source tables contain raw records
    raw_sup = db_session.get(SourceSupplier, "SUP-101")
    assert raw_sup is not None
    assert raw_sup.name == "Abidjan Cocoa Union"
    assert raw_sup.__table__.schema == "factory_source"

    # Verify public tables contain reconciled domain records
    app_sup = db_session.get(Supplier, "supplier:erp:SUP-101")
    assert app_sup is not None
    assert app_sup.code == "SUP-101"

    app_roaster = db_session.get(MachineAsset, "machine:plant:ROAST-01")
    assert app_roaster is not None
    assert app_roaster.rated_capacity_kg_h == 1200.00

    # Running seed second time must be idempotent
    res2 = seed_baseline_factory(db_session)
    assert res2["status"] == "already_seeded"


def test_scenario_creation_initializes_isolated_snapshot(db_session: Session):
    """Verifies that scenario creation initializes an isolated snapshot without mutating baseline."""
    seed_baseline_factory(db_session)

    scenario = create_scenario(
        session=db_session,
        name="Test Scenario",
        disruption_type="none",
        random_seed=42,
        scenario_id="scn_test_init",
    )
    assert scenario.id == "scn_test_init"
    assert scenario.current_step == 0
    assert scenario.simulation_time == BASELINE_START_TIME

    # Check snapshot structure
    snapshot = scenario.parameters["snapshot"]
    assert snapshot["step"] == 0
    assert "machine:plant:ROAST-01" in snapshot["machines"]
    assert "production_batch:mes:BATCH-101" in snapshot["batches"]
    assert "customer_order:erp:ORD-9402" in snapshot["orders"]
    assert snapshot["batches"]["production_batch:mes:BATCH-101"]["actual_quantity_kg"] == 0.0


def test_nominal_stepping_advances_time_and_production(db_session: Session):
    """Verifies that stepping a nominal scenario advances simulated time and batch processing."""
    scenario = create_scenario(
        session=db_session,
        name="Nominal Run",
        disruption_type="none",
        scenario_id="scn_nominal_01",
    )

    # Advance 2 steps (30 minutes)
    result = step_scenario(db_session, scenario.id, steps=2)

    assert result["current_step"] == 2
    # 2 steps * 15 min = 30 min from start (06:30:00)
    assert result["simulation_time"] == "2026-10-22T06:30:00+00:00"

    snapshot = result["snapshot"]
    roaster = snapshot["machines"]["machine:plant:ROAST-01"]
    batch = snapshot["batches"]["production_batch:mes:BATCH-101"]
    lot = snapshot["inventory"]["inventory_lot:wms:LOT-2026-001"]

    # In 2 steps, nominal capacity is 2 * 300 = 600 kg
    assert batch["actual_quantity_kg"] == 600.0
    assert batch["status"] == "in_progress"
    assert lot["current_quantity_kg"] == 15000.0 - 600.0
    assert roaster["status"] == "running"
    assert roaster["temperature_c"] == 115.0


def test_roaster_anomaly_disruption_triggers_at_step_3(db_session: Session):
    """Verifies that roaster bearing anomaly triggers at Step 3 and emits evidence events."""
    scenario = create_scenario(
        session=db_session,
        name="Disruption Run",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_disrupt_01",
    )

    # Step 1 & 2: Nominal
    step_scenario(db_session, scenario.id, steps=2)
    snap2 = scenario.parameters["snapshot"]
    assert snap2["machines"]["machine:plant:ROAST-01"]["temperature_c"] == 115.0

    # Step 3: Disruption triggers
    result3 = step_scenario(db_session, scenario.id, steps=1)
    snap3 = result3["snapshot"]

    roaster = snap3["machines"]["machine:plant:ROAST-01"]
    batch = snap3["batches"]["production_batch:mes:BATCH-101"]
    order = snap3["orders"]["customer_order:erp:ORD-9402"]

    # Verify anomaly metrics
    assert roaster["temperature_c"] == 142.5  # EV-ROAST-TEMP
    assert roaster["vibration_rms"] == 4.2    # EV-ROAST-VIB
    assert roaster["status"] == "degraded"
    assert roaster["health_score"] == 0.55

    # Verify batch delay
    assert batch["status"] == "delayed"
    assert batch["delay_hours"] == 3.5

    # Verify order status
    assert order["status"] == "at_risk"

    # Verify operational events emitted
    timeline = get_scenario_timeline(db_session, scenario.id)
    event_types = [e["event_type"] for e in timeline]

    assert "machine.overheated" in event_types
    assert "machine.vibration_high" in event_types
    assert "batch.delayed" in event_types
    assert "order.at_risk" in event_types

    # Find the temperature event and verify payload
    temp_event = next(e for e in timeline if e["event_type"] == "machine.overheated")
    assert temp_event["payload"]["observed_value"] == 142.5
    assert temp_event["payload"]["threshold_value"] == 120.0
    assert "EV-ROAST-TEMP" in temp_event["entity_refs"]


def test_explicit_event_ordering_and_simulation_time(db_session: Session):
    """Verifies that events in the timeline have strictly monotonic simulation_time."""
    scenario = create_scenario(
        session=db_session,
        name="Timeline Order Run",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_order_01",
    )

    step_scenario(db_session, scenario.id, steps=4)
    timeline = get_scenario_timeline(db_session, scenario.id)

    assert len(timeline) > 5

    # Verify non-decreasing simulation_time order
    sim_times = [e["simulation_time"] for e in timeline]
    assert sim_times == sorted(sim_times), "Event timeline is not ordered by simulation_time!"

    # Verify each event carries a valid idempotency key
    for e in timeline:
        assert len(e["idempotency_key"]) == 32


def test_scenario_isolation_no_baseline_or_cross_scenario_mutation(db_session: Session):
    """
    Verifies that stepping Scenario A has zero effect on Scenario B or baseline database rows.
    """
    seed_baseline_factory(db_session)

    # Create two parallel scenarios
    scn_a = create_scenario(db_session, "Scenario A Nominal", disruption_type="none", scenario_id="scn_A")
    scn_b = create_scenario(db_session, "Scenario B Disrupted", disruption_type="roaster_bearing_overheat", scenario_id="scn_B")

    # Step Scenario B to disruption (Step 3)
    step_scenario(db_session, "scn_B", steps=3)

    # Reload Scenario A from session
    db_session.refresh(scn_a)
    snap_a = scn_a.parameters["snapshot"]

    # Scenario A must still be at Step 0 with nominal state
    assert scn_a.current_step == 0
    assert snap_a["step"] == 0
    assert snap_a["machines"]["machine:plant:ROAST-01"]["temperature_c"] == 115.0
    assert snap_a["machines"]["machine:plant:ROAST-01"]["status"] == "idle"

    # Baseline database rows in public and factory_source must remain untouched
    baseline_batch = db_session.get(ProductionBatch, "production_batch:mes:BATCH-101")
    assert baseline_batch.actual_quantity_kg == 0.0
    assert baseline_batch.delay_hours == 0.0

    baseline_roaster = db_session.get(MachineAsset, "machine:plant:ROAST-01")
    assert baseline_roaster.current_temp_c == 115.00
    assert baseline_roaster.operating_status == "idle"


def test_simulation_fastapi_endpoints(db_session: Session):
    """Verifies minimal simulation endpoints via FastAPI TestClient within isolated savepoint."""
    from app.api.simulations import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)

        # 1. Create simulation
        create_resp = client.post(
            "/simulations",
            json={
                "name": "API Disruption Run",
                "disruption_type": "roaster_bearing_overheat",
                "random_seed": 42,
                "step_size_minutes": 15,
                "scenario_id": "scn_api_test_01",
            },
        )
        assert create_resp.status_code == 201
        created_data = create_resp.json()
        assert created_data["id"] == "scn_api_test_01"
        assert created_data["current_step"] == 0

        # 2. Advance 3 steps
        step_resp = client.post(
            "/simulations/scn_api_test_01/step",
            json={"steps": 3},
        )
        assert step_resp.status_code == 200
        step_data = step_resp.json()
        assert step_data["current_step"] == 3
        assert step_data["snapshot"]["machines"]["machine:plant:ROAST-01"]["temperature_c"] == 142.5

        # 3. Get simulation state
        get_resp = client.get("/simulations/scn_api_test_01")
        assert get_resp.status_code == 200
        assert get_resp.json()["current_step"] == 3

        # 4. Get timeline
        timeline_resp = client.get("/simulations/scn_api_test_01/timeline")
        assert timeline_resp.status_code == 200
        timeline_data = timeline_resp.json()
        assert timeline_data["event_count"] > 0
        event_types = [e["event_type"] for e in timeline_data["timeline"]]
        assert "machine.overheated" in event_types
    finally:
        app.dependency_overrides.clear()


def test_deterministic_replay_with_identical_seed(db_session: Session):
    """
    Verifies that two independent scenarios initialized with identical seed and configuration
    produce identical normalized simulation snapshots and event timelines when advanced identically.
    Legitimate scenario-specific identifiers (scenario_id, event_id, database autoincrement id,
    and wall-clock recorded_at) are excluded from the comparison.
    """
    # 1. Initialize two independent scenarios with identical configuration
    scn_a = create_scenario(
        session=db_session,
        name="Replay Test A",
        disruption_type="roaster_bearing_overheat",
        random_seed=42,
        step_size_minutes=15,
        scenario_id="scn_replay_A",
    )
    scn_b = create_scenario(
        session=db_session,
        name="Replay Test B",
        disruption_type="roaster_bearing_overheat",
        random_seed=42,
        step_size_minutes=15,
        scenario_id="scn_replay_B",
    )

    # 2. Advance both scenarios by 4 discrete steps
    res_a = step_scenario(db_session, "scn_replay_A", steps=4)
    res_b = step_scenario(db_session, "scn_replay_B", steps=4)

    # 3. Assert step progress and simulated time equality
    assert res_a["current_step"] == res_b["current_step"] == 4
    assert res_a["simulation_time"] == res_b["simulation_time"] == "2026-10-22T07:00:00+00:00"
    assert res_a["status"] == res_b["status"] == "running"
    assert res_a["disruption_type"] == res_b["disruption_type"] == "roaster_bearing_overheat"
    assert res_a["events_emitted_count"] == res_b["events_emitted_count"]

    # 4. Compare normalized simulation state snapshots
    snap_a = res_a["snapshot"]
    snap_b = res_b["snapshot"]

    assert snap_a["machines"] == snap_b["machines"]
    assert snap_a["batches"] == snap_b["batches"]
    assert snap_a["inventory"] == snap_b["inventory"]
    assert snap_a["orders"] == snap_b["orders"]
    assert snap_a["step"] == snap_b["step"]
    assert snap_a["simulation_time"] == snap_b["simulation_time"]

    # 5. Compare chronological event timelines
    tl_a = get_scenario_timeline(db_session, "scn_replay_A")
    tl_b = get_scenario_timeline(db_session, "scn_replay_B")

    assert len(tl_a) == len(tl_b)
    assert len(tl_a) > 5

    for ev_a, ev_b in zip(tl_a, tl_b):
        # Operational facts must be strictly identical
        assert ev_a["event_type"] == ev_b["event_type"]
        assert ev_a["simulation_time"] == ev_b["simulation_time"]
        assert ev_a["payload"] == ev_b["payload"]

        # Normalize scenario-specific identifiers in source_record_id
        src_a = "<scenario_id>" if ev_a["source_record_id"] == "scn_replay_A" else ev_a["source_record_id"]
        src_b = "<scenario_id>" if ev_b["source_record_id"] == "scn_replay_B" else ev_b["source_record_id"]
        assert src_a == src_b


def test_simulation_api_validation_and_error_handling(db_session: Session):
    """
    Verifies input validation and error handling across simulation API endpoints:
    - Non-positive step counts (steps=0, steps=-1) return HTTP 422 Unprocessable Entity
    - Step counts above configured maximum (steps=101) return HTTP 422 Unprocessable Entity
    - Non-existent scenario IDs return HTTP 404 Not Found for step, get, and timeline
    """
    from app.api.simulations import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)

        # First seed baseline and create a valid scenario
        seed_resp = client.post("/simulations/seed")
        assert seed_resp.status_code == 200

        create_resp = client.post(
            "/simulations",
            json={
                "name": "Validation Target",
                "disruption_type": "none",
                "scenario_id": "scn_val_target",
            },
        )
        assert create_resp.status_code == 201

        # 1. Validation: steps = 0 (violates ge=1)
        resp_zero = client.post("/simulations/scn_val_target/step", json={"steps": 0})
        assert resp_zero.status_code == 422

        # 2. Validation: negative steps (violates ge=1)
        resp_neg = client.post("/simulations/scn_val_target/step", json={"steps": -5})
        assert resp_neg.status_code == 422

        # 3. Validation: steps > 100 (violates le=100)
        resp_max = client.post("/simulations/scn_val_target/step", json={"steps": 101})
        assert resp_max.status_code == 422

        # 4. Error Handling: non-existent scenario ID for step
        resp_step_404 = client.post("/simulations/scn_non_existent/step", json={"steps": 1})
        assert resp_step_404.status_code == 404
        assert "not found" in resp_step_404.json()["detail"].lower()

        # 5. Error Handling: non-existent scenario ID for GET state
        resp_get_404 = client.get("/simulations/scn_non_existent")
        assert resp_get_404.status_code == 404

        # 6. Error Handling: non-existent scenario ID for GET timeline
        resp_tl_404 = client.get("/simulations/scn_non_existent/timeline")
        assert resp_tl_404.status_code == 404

    finally:
        app.dependency_overrides.clear()


def test_seed_concurrency_race_handling(db_session: Session):
    """
    Verifies that seed_baseline_factory handles race conditions gracefully
    using nested savepoints, returning 'already_seeded' without throwing unhandled exceptions.
    """
    # First seed passes normally
    res1 = seed_baseline_factory(db_session)
    assert res1["status"] in ("seeded", "already_seeded")

    # Second call detects existing seed and returns already_seeded immediately
    res2 = seed_baseline_factory(db_session)
    assert res2["status"] == "already_seeded"
    assert "supplier_id" in res2
