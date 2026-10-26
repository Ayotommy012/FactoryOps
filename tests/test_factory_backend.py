"""
Comprehensive Chunk 1 Factory Operations Backend Verification Suite.

Tests:
1. Multi-stage factory operations domain across all 7 production stages and 8 machines.
2. Strict material and capacity conservation (no negative inventory, throughput bound by rated capacity).
3. All 4 scenario disruption types (healthy_baseline, roaster_bearing_overheat, equipment_failure, interacting_constraint).
4. Incident propagation, dependency tracing, deterministic candidate recovery, and human approval workflow.
5. REST API endpoints: GET /factory-state, GET /incidents, GET /recovery-options.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.simulations import get_db
from app.database import engine
from app.main import app
from app.models import (
    CustomerOrder,
    InventoryLot,
    MachineAsset,
    OperationalEvent,
    ProductionBatch,
    SimulationScenario,
)
from app.services.recovery import (
    apply_recovery_decision,
    detect_and_trace_impact,
    generate_candidate_plans,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import (
    create_scenario,
    get_scenario_timeline,
    get_scenario_world_model,
    step_scenario,
)


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


def test_multi_stage_factory_layout_and_assets(db_session: Session):
    """Verifies that all 7 production stages, 8 machines, 4 lots, 5 batches, and 3 orders exist in baseline domain."""
    seed_baseline_factory(db_session)

    # 1. 8 Machines across 7 stages
    machines = db_session.query(MachineAsset).all()
    machine_codes = {m.code for m in machines}
    expected_machines = {
        "CLEAN-01",
        "ROAST-01",
        "ROAST-02",
        "WINNOW-01",
        "MILL-01",
        "PRESS-01",
        "PULV-01",
        "PACK-01",
    }
    assert expected_machines.issubset(machine_codes), f"Missing machines: {expected_machines - machine_codes}"

    # Verify stages
    stage_set = {m.station for m in machines}
    expected_stages = {"cleaning", "roasting", "winnowing", "milling", "pressing", "pulverizing", "packaging"}
    assert expected_stages.issubset(stage_set), f"Missing stages: {expected_stages - stage_set}"

    # 2. Multi-stage Inventory Lots
    lots = db_session.query(InventoryLot).all()
    lot_codes = {l.lot_code for l in lots}
    expected_lots = {"LOT-2026-001", "LOT-NIBS-01", "LOT-LIQUOR-01", "LOT-POWDER-01"}
    assert expected_lots.issubset(lot_codes)

    # 3. Production Batches
    batches = db_session.query(ProductionBatch).all()
    batch_codes = {b.batch_code for b in batches}
    expected_batches = {"BATCH-101", "BATCH-102", "BATCH-103", "BATCH-104", "BATCH-105"}
    assert expected_batches.issubset(batch_codes)

    # 4. Customer Orders
    orders = db_session.query(CustomerOrder).all()
    order_codes = {o.order_code for o in orders}
    expected_orders = {"ORD-9402", "ORD-9405", "ORD-9408"}
    assert expected_orders.issubset(order_codes)


def test_strict_material_and_capacity_conservation(db_session: Session):
    """Verifies that batch advance is strictly bounded by machine capacity and input lot quantity.

    No phantom material is created, and inventory never drops below zero.
    """
    seed_baseline_factory(db_session)

    scenario = create_scenario(
        session=db_session,
        name="Material Conservation Test",
        disruption_type="healthy_baseline",
        scenario_id="scn_mat_conserv",
    )

    # Advance 4 discrete steps (60 minutes = 1 full hour)
    result = step_scenario(db_session, scenario.id, steps=4)
    snapshot = result["snapshot"]

    batch_roast = snapshot["batches"]["production_batch:mes:BATCH-101"]
    lot_raw = snapshot["inventory"]["inventory_lot:wms:LOT-2026-001"]

    # Rated capacity of ROAST-01 is 1200 kg/h -> exactly 1200 kg in 4 steps of 15 min
    assert batch_roast["actual_quantity_kg"] == 1200.0
    assert lot_raw["current_quantity_kg"] == 15000.0 - 1200.0
    assert lot_raw["current_quantity_kg"] >= 0.0

    # Advance 5 more steps (total 9 steps = 2.25 hours)
    result_9 = step_scenario(db_session, scenario.id, steps=5)
    snapshot_9 = result_9["snapshot"]
    batch_roast_9 = snapshot_9["batches"]["production_batch:mes:BATCH-101"]
    lot_raw_9 = snapshot_9["inventory"]["inventory_lot:wms:LOT-2026-001"]

    # Planned is 2500 kg: after 9 steps (nominal 2700 kg capacity), it must cap at planned (2500 kg)
    assert batch_roast_9["actual_quantity_kg"] == 2500.0
    assert batch_roast_9["status"] == "completed"
    assert lot_raw_9["current_quantity_kg"] == 15000.0 - 2500.0
    assert lot_raw_9["current_quantity_kg"] >= 0.0


def test_equipment_failure_simulation_and_recovery(db_session: Session):
    """Verifies equipment_failure disruption triggers at Step 3 on MILL-01, traces downstream,

    and resolves through human-authorized motor module replacement.
    """
    seed_baseline_factory(db_session)

    scenario = create_scenario(
        session=db_session,
        name="Mill Failure Scenario",
        disruption_type="equipment_failure",
        scenario_id="scn_mill_fail_01",
    )

    # Step 1 & 2: Nominal
    step_scenario(db_session, scenario.id, steps=2)
    snap2 = scenario.parameters["snapshot"]
    assert snap2["machines"]["machine:plant:MILL-01"]["status"] in ("running", "idle")

    # Step 3: Failure occurs on MILL-01
    step_scenario(db_session, scenario.id, steps=1)
    snap3 = scenario.parameters["snapshot"]
    mill = snap3["machines"]["machine:plant:MILL-01"]
    batch_mill = snap3["batches"]["production_batch:mes:BATCH-103"]
    order_swiss = snap3["orders"]["customer_order:erp:ORD-9405"]

    assert mill["status"] == "failed"
    assert mill["temperature_c"] == 168.0
    assert mill["vibration_rms"] == 8.6
    assert batch_mill["status"] == "delayed"
    assert order_swiss["status"] == "at_risk"

    # Verify Impact Analysis
    impact = detect_and_trace_impact(db_session, scenario.id)
    assert impact.disrupted_machine_id == "machine:plant:MILL-01"
    assert impact.affected_batch_id == "production_batch:mes:BATCH-103"
    assert impact.affected_order_id == "customer_order:erp:ORD-9405"
    assert impact.sla_breached is True
    assert len(impact.evidence) >= 2

    # Verify Candidate Plans for MILL-01
    plans = generate_candidate_plans(db_session, scenario.id, use_nemotron=False)
    assert len(plans) == 3
    plan_a = plans[0]
    assert plan_a.plan_id == "plan_a_motor_swap"
    assert plan_a.estimated_downtime_hours == 2.0
    assert plan_a.meets_deadline is True
    assert plan_a.total_cost_usd == 750.0

    # Human Approval Workflow: Approve Plan A
    decision = apply_recovery_decision(
        session=db_session,
        scenario_id=scenario.id,
        plan_id="plan_a_motor_swap",
        decision="approved",
        operator_id="OP-TECH-44",
        rationale="Replace seized motor using in-stock spare to restore grinding.",
    )
    assert decision.decision == "approved"
    assert decision.updated_scenario_status == "recovering"

    # Verify scenario state updated
    snap_recovered = scenario.parameters["snapshot"]
    assert snap_recovered["machines"]["machine:plant:MILL-01"]["status"] == "maintenance"
    assert snap_recovered["machines"]["machine:plant:MILL-01"]["temperature_c"] == 68.0
    assert snap_recovered["batches"]["production_batch:mes:BATCH-103"]["status"] == "in_progress"


def test_interacting_constraint_simulation(db_session: Session):
    """Verifies compound multi-stage disruption (thermal degradation + raw shortage + winnowing backpressure)."""
    seed_baseline_factory(db_session)

    scenario = create_scenario(
        session=db_session,
        name="Compound Constraint Scenario",
        disruption_type="interacting_constraint",
        scenario_id="scn_interact_01",
    )

    # Step to Step 3
    step_scenario(db_session, scenario.id, steps=3)

    timeline = get_scenario_timeline(db_session, scenario.id)
    event_types = {e["event_type"] for e in timeline}

    # Verify compound events across multiple stages
    assert "machine.overheated" in event_types
    assert "machine.vibration_high" in event_types
    assert "inventory.shortage" in event_types
    assert "machine.bottleneck" in event_types
    assert "batch.delayed" in event_types
    assert "order.at_risk" in event_types


def test_factory_backend_api_endpoints(db_session: Session):
    """Verifies GET /factory-state, GET /incidents, and GET /recovery-options API endpoints."""
    from app.api.simulations import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)

        # 1. Initialize scenario
        create_resp = client.post(
            "/simulations",
            json={
                "name": "API Full Test Scenario",
                "disruption_type": "roaster_bearing_overheat",
                "random_seed": 42,
                "step_size_minutes": 15,
                "scenario_id": "scn_api_chunk1",
            },
        )
        assert create_resp.status_code == 201

        # 2. Advance 3 steps
        client.post("/simulations/scn_api_chunk1/step", json={"steps": 3})

        # 3. GET /factory-state
        fs_resp = client.get("/simulations/scn_api_chunk1/factory-state")
        assert fs_resp.status_code == 200
        fs_data = fs_resp.json()
        assert fs_data["scenario_id"] == "scn_api_chunk1"
        assert len(fs_data["stages"]) == 7
        assert "machine:plant:ROAST-01" in fs_data["machines"]
        assert "inventory_lot:wms:LOT-2026-001" in fs_data["inventory"]
        assert "production_batch:mes:BATCH-101" in fs_data["batches"]
        assert "customer_order:erp:ORD-9402" in fs_data["orders"]

        # 4. GET /incidents
        inc_resp = client.get("/simulations/scn_api_chunk1/incidents")
        assert inc_resp.status_code == 200
        inc_data = inc_resp.json()
        assert inc_data["has_active_incidents"] is True
        assert inc_data["disrupted_machine_id"] == "machine:plant:ROAST-01"
        assert inc_data["affected_batch_id"] == "production_batch:mes:BATCH-101"
        assert len(inc_data["evidence"]) >= 1

        # 5. GET /recovery-options
        rec_resp = client.get("/simulations/scn_api_chunk1/recovery-options")
        assert rec_resp.status_code == 200
        rec_data = rec_resp.json()
        assert rec_data["candidate_count"] == 3
        assert rec_data["plans"][0]["plan_id"] == "plan_a_thermal_flush"
    finally:
        app.dependency_overrides.clear()
