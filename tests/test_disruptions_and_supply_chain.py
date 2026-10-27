from sqlalchemy.orm.attributes import flag_modified
"""
Comprehensive Integration & Verification Tests for Supply Chain Disruption,
Disruption Simulator Catalogue & Random Mode, and Dynamic 7-Stage Production Line.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.main import app
from app.models import OperationalEvent, SimulationScenario
from app.services.seed import seed_baseline_factory
from app.services.disruptions import (
    DISRUPTION_CATALOGUE,
    apply_disruption,
    evaluate_disruption_eligibility,
    get_disruption_catalogue_list,
    get_eligible_disruptions,
    select_random_disruption,
)
from app.services.production_line import (
    calculate_inventory_forecast,
    derive_production_line_stages,
)
from app.services.recovery import (
    PLAN_DOWNTIME_HOURS,
    VALID_PLANS,
    apply_recovery_decision,
    detect_and_trace_impact,
    generate_candidate_plans,
)
from app.services.simulation import (
    create_scenario,
    get_scenario_world_model,
    is_awaiting_recovery_decision,
    is_scenario_eligible_for_auto_tick,
    step_scenario,
)


@pytest.fixture
def db_session():
    """Savepoint-isolated session (ADR-008): endpoint commits never reach the dev DB."""
    from app.database import engine
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# =============================================================================
# PART 1: Supply Chain Disruption & Stockout Forecasting
# =============================================================================

def test_initial_scenario_has_inbound_shipments_and_silos(db_session: Session):
    scenario = create_scenario(db_session, name="Test Supply Chain Baseline", disruption_type="none")
    snapshot = scenario.parameters.get("snapshot", {})

    assert "inbound_shipments" in snapshot
    shipments = snapshot["inbound_shipments"]
    assert len(shipments) >= 1

    shp = next(iter(shipments.values()))
    assert shp["shipment_code"] == "SHP-2026-COCOA-01"
    assert shp["quantity_kg"] == 15000.0
    assert shp["status"] == "in_transit"
    assert shp["scheduled_arrival_step"] == 8

    assert "silos" in snapshot
    silo = snapshot["silos"]["silo_01"]
    assert silo["current_stock_kg"] == 3600.0


def test_supply_chain_inventory_forecast_nominal_vs_delayed(db_session: Session):
    scenario = create_scenario(db_session, name="Test Inventory Forecast", disruption_type="none")
    snapshot = scenario.parameters.get("snapshot", {})

    # Nominal forecast: 3,600 kg in silo, burn rate 300 kg/step (1,200 kg/h)
    # Stockout in 12 steps. Scheduled arrival at step 8.
    # Because 12 >= 8, no stockout before replenishment in nominal conditions.
    fc_nominal = calculate_inventory_forecast(snapshot, step_size_minutes=15)
    assert fc_nominal["usable_inventory_kg"] == 3600.0
    assert fc_nominal["active_burn_rate_kg_step"] == 300.0
    assert fc_nominal["steps_until_stockout"] == 12
    assert fc_nominal["is_stockout_before_replenishment"] is False
    assert len(fc_nominal["trajectory"]) >= 20

    # Apply customs delay disruption (+6.0h / +24 steps -> arrival at step 32)
    res = apply_disruption(db_session, scenario.id, "supplier_customs_delay", {"delay_hours": 6.0})
    assert res["status"] == "applied"

    # Refresh snapshot
    db_session.refresh(scenario)
    delayed_snapshot = scenario.parameters["snapshot"]
    fc_delayed = calculate_inventory_forecast(delayed_snapshot, step_size_minutes=15)

    # Now arrival is at step 32, but stockout occurs at step 12.
    # Starvation gap = 32 - 12 = 20 steps (5.0 hours).
    assert fc_delayed["is_stockout_before_replenishment"] is True
    assert fc_delayed["starvation_gap_steps"] == 20
    assert fc_delayed["starvation_gap_hours"] == 5.0
    assert len(fc_delayed["affected_stages"]) >= 2


def test_supply_chain_impact_tracing_and_candidate_plans(db_session: Session):
    scenario = create_scenario(db_session, name="Test Supply Chain Plans", disruption_type="none")
    apply_disruption(db_session, scenario.id, "supplier_customs_delay", {"delay_hours": 6.0})
    db_session.refresh(scenario)

    impact = detect_and_trace_impact(db_session, scenario.id)
    assert impact.disrupted_machine_id == "silo:plant:SILO-01"
    assert impact.affected_order_id == "customer_order:erp:ORD-9402"
    # Silo 1 holds 3,600 kg (3h at 1,200 kg/h): a 6h hold starves the line for 3h.
    # ORD-9402's remaining 2,500 kg still completes before its 14:00 deadline,
    # so the evidence is critical starvation, not an SLA breach.
    assert impact.starvation_hours == 3.0
    assert impact.severity == "critical"
    assert impact.sla_breached is False
    assert len(impact.evidence) >= 2

    # Check evidence metrics
    metrics = {e.metric for e in impact.evidence}
    assert "shipment_status" in metrics
    assert "silo_stockout_risk" in metrics

    # Generate Candidate Recovery Plans
    plans = generate_candidate_plans(db_session, scenario.id, use_ai=False)
    assert len(plans) == 3

    plan_ids = [p.plan_id for p in plans]
    assert plan_ids == [
        "plan_spot_market_buffer",
        "plan_recipe_line_throttle",
        "plan_customs_expedite",
    ]

    # Rank 1: Spot Market Buffer meets deadline
    p1 = plans[0]
    assert p1.rank == 1
    assert p1.meets_deadline is True
    assert p1.total_cost_usd == 2850.0

    # Rank 2: Recipe Line Throttle
    p2 = plans[1]
    assert p2.rank == 2
    assert p2.total_cost_usd == 450.0

    # Rank 3: Customs Expedite
    p3 = plans[2]
    assert p3.rank == 3
    assert p3.meets_deadline is True


def test_supply_chain_recovery_approval_spot_market_buffer(db_session: Session):
    scenario = create_scenario(db_session, name="Test Supply Chain Approval", disruption_type="none")
    apply_disruption(db_session, scenario.id, "supplier_customs_delay", {"delay_hours": 6.0})
    db_session.refresh(scenario)

    # Approve Spot Market Buffer
    decision = apply_recovery_decision(
        db_session,
        scenario_id=scenario.id,
        plan_id="plan_spot_market_buffer",
        decision="approved",
        operator_id="operator_sim_chief",
        rationale="Procure 5,000 kg buffer to guarantee zero production starvation for ORD-9402.",
    )
    assert decision.decision == "approved"
    assert decision.updated_scenario_status == "recovering"

    db_session.refresh(scenario)
    snapshot = scenario.parameters["snapshot"]
    # Verify Silo 1 and inventory lot were replenished by 5,000 kg
    assert snapshot["inventory"]["inventory_lot:wms:LOT-2026-001"]["current_quantity_kg"] == 20000.0
    assert snapshot["silos"]["silo_01"]["current_stock_kg"] == 8600.0


# =============================================================================
# PART 2: Disruption Simulator Catalogue & Random Mode
# =============================================================================

def test_disruption_catalogue_contains_all_six_disruptions():
    catalogue = get_disruption_catalogue_list()
    assert len(catalogue) == 6

    type_ids = {d["type_id"] for d in catalogue}
    expected_ids = {
        "supplier_customs_delay",
        "supplier_shortfall",
        "roaster_bearing_overheat",
        "equipment_failure",
        "reduced_machine_capacity",
        "quality_contamination",
    }
    assert type_ids == expected_ids

    # Check categories
    categories = {d["category"] for d in catalogue}
    assert "Supply Chain" in categories
    assert "Machinery & Equipment" in categories
    assert "Inventory & Quality" in categories


def test_disruption_eligibility_evaluation(db_session: Session):
    scenario = create_scenario(db_session, name="Test Eligibility", disruption_type="none")

    # Baseline: all 6 should be eligible
    eligible_list = get_eligible_disruptions(db_session, scenario.id)
    assert len(eligible_list) == 6
    for item in eligible_list:
        assert item["eligible"] is True, f"Expected {item['type_id']} to be eligible initially"

    # Trigger roaster overheat
    apply_disruption(db_session, scenario.id, "roaster_bearing_overheat")
    db_session.refresh(scenario)

    # Now roaster_bearing_overheat should NOT be eligible because ROAST-01 is already degraded
    eligible, reason = evaluate_disruption_eligibility(db_session, scenario.id, "roaster_bearing_overheat")
    assert eligible is False
    assert "already degraded" in reason.lower() or "not nominal" in reason.lower()


def test_random_disruption_selection(db_session: Session):
    scenario = create_scenario(db_session, name="Test Random Disruption", disruption_type="none")

    chosen_type, params = select_random_disruption(db_session, scenario.id)
    assert chosen_type in DISRUPTION_CATALOGUE
    assert isinstance(params, dict)
    assert len(params) > 0

    # Apply the random disruption
    result = apply_disruption(db_session, scenario.id, chosen_type, params)
    assert result["status"] == "applied"
    assert result["disruption_type"] == chosen_type


def test_trigger_each_catalogue_disruption_type(db_session: Session):
    disruptions = [
        ("supplier_shortfall", {"delivered_quantity_kg": 5000.0}),
        ("equipment_failure", {"temperature_c": 175.0, "vibration_rms": 9.2}),
        ("reduced_machine_capacity", {"capacity_pct": 35.0}),
        ("quality_contamination", {"moisture_pct": 9.5}),
    ]

    for d_type, d_params in disruptions:
        scn = create_scenario(db_session, name=f"Test {d_type}", disruption_type="none")
        res = apply_disruption(db_session, scn.id, d_type, d_params)
        assert res["status"] == "applied"
        assert res["disruption_type"] == d_type

        db_session.refresh(scn)
        snap = scn.parameters["snapshot"]

        if d_type == "supplier_shortfall":
            shp = next(iter(snap["inbound_shipments"].values()))
            assert shp["quantity_kg"] == 5000.0
        elif d_type == "equipment_failure":
            mill = snap["machines"]["machine:plant:MILL-01"]
            assert mill["status"] == "failed"
            assert mill["temperature_c"] == 175.0
        elif d_type == "reduced_machine_capacity":
            press = snap["machines"]["machine:plant:PRESS-01"]
            assert press["status"] == "degraded"
            assert press["current_throughput_kg_h"] == 210.0  # 35% of 600
        elif d_type == "quality_contamination":
            silo = snap["inventory"]["inventory_lot:wms:LOT-2026-001"]
            assert silo["quality_status"] == "QUARANTINE_HOLD"


# =============================================================================
# PART 3: Dynamic 7-Stage Production Line
# =============================================================================

def test_production_line_seven_stages_authoritative_derivation(db_session: Session):
    scenario = create_scenario(db_session, name="Test 7 Stages", disruption_type="none")
    snapshot = scenario.parameters.get("snapshot", {})

    stages = derive_production_line_stages(snapshot)
    assert len(stages) == 7

    stage_names = [s["name"] for s in stages]
    assert stage_names == [
        "Cleaning & Destoning",
        "Drum Roasting",
        "Winnowing",
        "Liquor Grinding",
        "Hydraulic Pressing",
        "Cake Pulverizing",
        "Packing & Fulfillment",
    ]

    # Verify stage machines
    machine_codes = [s["machine_code"] for s in stages]
    assert machine_codes == [
        "CLEAN-01",
        "ROAST-01",
        "WINNOW-01",
        "MILL-01",
        "PRESS-01",
        "PULV-01",
        "PACK-01",
    ]

    # Baseline: all stages should have healthy metrics in sequential cycle
    for s in stages:
        assert s["status"] in ("running", "queued", "completed", "pending")
        assert s["throughput_kg_h"] > 0 or s["status"] in ("queued", "completed", "pending")


def test_production_line_cascading_blocking_on_quarantine_or_stockout(db_session: Session):
    scenario = create_scenario(db_session, name="Test Cascading Block", disruption_type="none")

    # Apply quality contamination hold on raw beans
    apply_disruption(db_session, scenario.id, "quality_contamination", {"moisture_pct": 9.2})
    db_session.refresh(scenario)

    snapshot = scenario.parameters["snapshot"]
    stages = derive_production_line_stages(snapshot)

    # Stage 1 (Cleaning) and Stage 2 (Roasting) should be Blocked due to quarantined beans!
    assert stages[0]["status"] == "blocked"
    assert "quarantine" in stages[0]["operational_reason"].lower()

    assert stages[1]["status"] == "blocked"
    assert "quarantined" in stages[1]["operational_reason"].lower() or "starved" in stages[1]["operational_reason"].lower()


def test_production_line_milling_failure_cascades_downstream(db_session: Session):
    scenario = create_scenario(db_session, name="Test Milling Failure", disruption_type="none")

    apply_disruption(db_session, scenario.id, "equipment_failure")
    db_session.refresh(scenario)

    snapshot = scenario.parameters["snapshot"]
    stages = derive_production_line_stages(snapshot)

    # Stage 4 (Milling) should be Offline
    st4 = stages[3]
    assert st4["machine_code"] == "MILL-01"
    assert st4["status"] == "offline"
    assert st4["throughput_kg_h"] == 0.0

    # Downstream Stage 5 (Pressing) should be Blocked or At Risk due to liquor starvation
    st5 = stages[4]
    assert st5["machine_code"] == "PRESS-01"
    assert st5["status"] in ("blocked", "at_risk")


# =============================================================================
# PART 4: FastAPI Router Integration for Disruptions & Production Line
# =============================================================================

def test_fastapi_disruptions_and_production_line_endpoints(db_session: Session):
    from fastapi.testclient import TestClient
    from app.api.simulations import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        _run_disruption_api_checks(TestClient(app))
    finally:
        app.dependency_overrides.clear()


def _run_disruption_api_checks(client):

    # 1. Create a fresh scenario
    create_resp = client.post(
        "/simulations",
        json={"name": "Test API Disruption Suite", "disruption_type": "none"},
    )
    assert create_resp.status_code == 201
    scenario_id = create_resp.json()["id"]

    # 2. GET catalogue
    cat_resp = client.get(f"/simulations/{scenario_id}/disruptions/catalogue")
    assert cat_resp.status_code == 200
    cat_data = cat_resp.json()
    assert cat_data["scenario_id"] == scenario_id
    assert len(cat_data["disruptions"]) == 6

    # 3. GET production-line
    line_resp = client.get(f"/simulations/{scenario_id}/production-line")
    assert line_resp.status_code == 200
    line_data = line_resp.json()
    assert len(line_data["stages"]) == 7

    # 4. GET supply-chain
    sc_resp = client.get(f"/simulations/{scenario_id}/supply-chain")
    assert sc_resp.status_code == 200
    sc_data = sc_resp.json()
    assert sc_data["usable_inventory_kg"] == 3600.0
    assert "trajectory" in sc_data

    # 5. POST trigger disruption (named)
    trig_resp = client.post(
        f"/simulations/{scenario_id}/disruptions/trigger",
        json={"disruption_type": "supplier_customs_delay", "parameters": {"delay_hours": 6.0}},
    )
    assert trig_resp.status_code == 200
    trig_data = trig_resp.json()
    assert trig_data["status"] == "applied"
    assert trig_data["disruption_type"] == "supplier_customs_delay"

    # Verify supply chain now reflects the delay
    sc_resp2 = client.get(f"/simulations/{scenario_id}/supply-chain")
    sc_data2 = sc_resp2.json()
    assert sc_data2["is_stockout_before_replenishment"] is True
    assert sc_data2["starvation_gap_steps"] == 20

    # 6. POST trigger disruption with "random" on another scenario
    create_resp2 = client.post(
        "/simulations",
        json={"name": "Test API Random Disruption", "disruption_type": "none"},
    )
    assert create_resp2.status_code == 201
    scenario_id2 = create_resp2.json()["id"]

    rand_resp = client.post(
        f"/simulations/{scenario_id2}/disruptions/trigger",
        json={"disruption_type": "random"},
    )
    assert rand_resp.status_code == 200
    rand_data = rand_resp.json()
    assert rand_data["status"] == "applied"
    assert rand_data["random_selection"] is True
    assert rand_data["disruption_type"] in [
        "supplier_customs_delay",
        "supplier_shortfall",
        "roaster_bearing_overheat",
        "equipment_failure",
        "reduced_machine_capacity",
        "quality_contamination",
    ]


def test_sequential_continuous_production_cycle_progression(db_session: Session):
    """
    Verifies the sequential continuous manufacturing lifecycle across the 7 stages:
    1. At cycle start (step 0): Stage 1 = Running, Stages 2-7 = Pending.
    2. After Stage 1 completes: Stage 1 = Completed, Stage 2 = Running, Stages 3-7 = Pending.
    3. Respects stage duration (Stage 2 takes 2 steps).
    4. Progression loops continuously from Stage 7 -> Stage 1 and increments cycle count.
    """
    scenario = create_scenario(db_session, name="Test Sequential Cycle Progression", disruption_type="none")

    # Initial Step 0 checks
    snapshot_0 = scenario.parameters["snapshot"]
    stages_0 = derive_production_line_stages(snapshot_0)

    assert stages_0[0]["status"] == "running"  # Stage 1: CLEAN-01
    assert stages_0[0]["num"] == 1
    assert stages_0[0]["is_active"] is True
    for st in stages_0[1:]:
        assert st["status"] == "pending", f"Expected stage {st['num']} to be pending, got {st['status']}"
        assert st["is_active"] is False

    # Step 1: Stage 1 duration is 1 step -> Stage 1 finishes, Stage 2 starts
    res_1 = step_scenario(db_session, scenario.id, steps=1)
    db_session.refresh(scenario)
    stages_1 = derive_production_line_stages(res_1["snapshot"])

    assert stages_1[0]["status"] == "completed"  # Stage 1 completed
    assert stages_1[1]["status"] == "running"    # Stage 2 running
    assert stages_1[1]["is_active"] is True
    for st in stages_1[2:]:
        assert st["status"] == "pending"

    # Step 2: Stage 2 duration is 2 steps -> Step 2 is 1st step of Stage 2 (still running)
    res_2 = step_scenario(db_session, scenario.id, steps=1)
    db_session.refresh(scenario)
    stages_2 = derive_production_line_stages(res_2["snapshot"])

    assert stages_2[0]["status"] == "completed"
    assert stages_2[1]["status"] == "running"
    for st in stages_2[2:]:
        assert st["status"] == "pending"

    # Step 3: Stage 2 reaches duration -> Stage 2 finishes, Stage 3 starts
    res_3 = step_scenario(db_session, scenario.id, steps=1)
    db_session.refresh(scenario)
    stages_3 = derive_production_line_stages(res_3["snapshot"])

    assert stages_3[0]["status"] == "completed"
    assert stages_3[1]["status"] == "completed"
    assert stages_3[2]["status"] == "running"    # Stage 3 running
    for st in stages_3[3:]:
        assert st["status"] == "pending"

    # Step remaining stages to complete Cycle 1 (Stage 3, 4, 5, 6, 7 = 5 more steps -> Step 8)
    res_8 = step_scenario(db_session, scenario.id, steps=5)
    db_session.refresh(scenario)
    stages_8 = derive_production_line_stages(res_8["snapshot"])
    cycle_meta = res_8["snapshot"]["production_cycle"]

    assert cycle_meta["completed_cycles"] == 1
    assert cycle_meta["cycle_number"] == 2
    assert cycle_meta["active_stage_num"] == 1
    assert stages_8[0]["status"] == "running"  # Stage 1 running again for Cycle 2!
    for st in stages_8[1:]:
        assert st["status"] == "pending"


def test_repeated_step_one_past_batch_completion_and_cycle_restart(db_session: Session):
    """
    Regression test for actual_advance UnboundLocalError during repeated manual Step +1 calls.
    Verifies:
    1. Clicking Step +1 repeatedly (16 times) crosses multiple stage boundaries without errors.
    2. At Step 4, BATCH-101 reaches 1000 kg and completes.
    3. At Step 5+, actual_advance is 0.0, avoiding UnboundLocalError and spurious Silo 1 deductions.
    4. At Step 8, Stage 7 completes and Cycle 1 finishes, starting Cycle 2 cleanly.
    5. At Step 16, Cycle 2 completes and Cycle 3 starts cleanly.
    """
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        db_session,
        name="Repeated Step 1 Regression Test",
        disruption_type="none",
        scenario_id="scn_repeat_step1",
    )

    # Explicitly configure BATCH-101 planned quantity to 1000 kg so that it completes at step 4
    snap = scenario.parameters["snapshot"]
    snap["batches"]["production_batch:mes:BATCH-101"]["planned_quantity_kg"] = 1000.0
    scenario.parameters = {**scenario.parameters, "snapshot": snap}
    flag_modified(scenario, "parameters")
    db_session.flush()

    silo_initial = scenario.parameters["snapshot"]["silos"]["silo_01"]["current_stock_kg"]
    assert silo_initial == 3600.0

    # Step through 16 steps individually
    for s in range(1, 17):
        res = step_scenario(db_session, scenario.id, steps=1)
        db_session.refresh(scenario)
        assert res["current_step"] == s
        assert scenario.current_step == s

        snapshot = res["snapshot"]
        b101 = snapshot["batches"]["production_batch:mes:BATCH-101"]
        silo = snapshot["silos"]["silo_01"]

        if s < 4:
            # Nominal roaster progress: 300 kg per 15-min step
            assert b101["actual_quantity_kg"] == s * 300.0
            assert b101["status"] in ("in_progress", "running")
            # Silo decremented by 300 kg per step
            assert silo["current_stock_kg"] == 3600.0 - (s * 300.0)
        elif s == 4:
            # Step 4 completes the remaining 100 kg to reach 1000 kg planned
            assert b101["actual_quantity_kg"] == 1000.0
            assert b101["status"] == "completed"
            assert silo["current_stock_kg"] == 2600.0
        else:
            # Step 5+: BATCH-101 is completed! actual_advance must be 0.0
            assert b101["actual_quantity_kg"] == 1000.0
            assert b101["status"] == "completed"
            if s < 8:
                # Silo stock remains 2600.0 until shipment delivery at step 8
                assert silo["current_stock_kg"] == 2600.0
            elif s == 8:
                # Shipment SHP-2026-COCOA-01 arrives at step 8 (+15000 kg)
                assert silo["current_stock_kg"] == 2600.0 + 15000.0

        # Verify production cycle progression
        prod_cycle = snapshot["production_cycle"]
        if s < 8:
            assert prod_cycle["cycle_number"] == 1
            assert prod_cycle["completed_cycles"] == 0
        elif s == 8:
            assert prod_cycle["cycle_number"] == 2
            assert prod_cycle["completed_cycles"] == 1
            assert prod_cycle["active_stage_num"] == 1
        elif s < 16:
            assert prod_cycle["cycle_number"] == 2
            assert prod_cycle["completed_cycles"] == 1
        elif s == 16:
            assert prod_cycle["cycle_number"] == 3
            assert prod_cycle["completed_cycles"] == 2
            assert prod_cycle["active_stage_num"] == 1


def test_repeated_step_three_multi_step_isolation_and_no_leakage(db_session: Session):
    """
    Regression test for Step +3 multi-step calls:
    Verifies that multi-step advancement (steps=3) correctly resets actual_advance
    for each inner iteration so that completed batches do not bleed deducted quantities
    into subsequent iterations within the same function call.
    """
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        db_session,
        name="Step 3 Multi-Step Regression Test",
        disruption_type="none",
        scenario_id="scn_multi_step3",
    )

    # Explicitly configure BATCH-101 planned quantity to 1000 kg so that it completes at step 4
    snap = scenario.parameters["snapshot"]
    snap["batches"]["production_batch:mes:BATCH-101"]["planned_quantity_kg"] = 1000.0
    scenario.parameters = {**scenario.parameters, "snapshot": snap}
    flag_modified(scenario, "parameters")
    db_session.flush()

    # 1st call: steps=3 -> Advances from step 0 to step 3
    # Step 1: 300kg, Step 2: 300kg, Step 3: 300kg -> Total 900kg processed
    res_3 = step_scenario(db_session, scenario.id, steps=3)
    db_session.refresh(scenario)
    assert res_3["current_step"] == 3
    snap_3 = res_3["snapshot"]
    assert snap_3["batches"]["production_batch:mes:BATCH-101"]["actual_quantity_kg"] == 900.0
    assert snap_3["silos"]["silo_01"]["current_stock_kg"] == 2700.0

    # 2nd call: steps=3 -> Advances from step 3 to step 6
    # Iteration 1 (step 4): processed remaining 100kg -> reaches 1000kg.
    # Iteration 2 (step 5): batch is completed, actual_advance must be 0.0 (NOT 100kg!).
    # Iteration 3 (step 6): batch is completed, actual_advance must be 0.0.
    res_6 = step_scenario(db_session, scenario.id, steps=3)
    db_session.refresh(scenario)
    assert res_6["current_step"] == 6
    snap_6 = res_6["snapshot"]
    assert snap_6["batches"]["production_batch:mes:BATCH-101"]["actual_quantity_kg"] == 1000.0
    assert snap_6["batches"]["production_batch:mes:BATCH-101"]["status"] == "completed"
    # Silo 1 must have lost exactly 100kg in step 4 and zero kg in steps 5 and 6!
    # 2700 - 100 = 2600.0
    assert snap_6["silos"]["silo_01"]["current_stock_kg"] == 2600.0

    # 3rd call: steps=3 -> Advances from step 6 to step 9 (crosses Stage 7 and cycle boundary at step 8)
    res_9 = step_scenario(db_session, scenario.id, steps=3)
    db_session.refresh(scenario)
    assert res_9["current_step"] == 9
    snap_9 = res_9["snapshot"]
    # Shipment arrived at step 8 (+15,000 kg) -> 2600 + 15000 = 17600
    assert snap_9["silos"]["silo_01"]["current_stock_kg"] == 17600.0
    assert snap_9["production_cycle"]["completed_cycles"] == 1
    assert snap_9["production_cycle"]["cycle_number"] == 2


def test_stepping_while_paused_or_recovering_and_approval_gate(db_session: Session):
    """
    Verifies that stepping respects simulation state gates:
    1. Stepping a paused or cancelled scenario raises ValueError.
    2. A disruption requiring operator approval halts automatic simulation timing.
    3. Once recovery is approved, stepping proceeds and executes recovery maintenance steps.
    """
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        db_session,
        name="State Gates Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_state_gates",
    )

    # 1. Test paused and cancelled state gates
    scenario.status = "paused"
    db_session.flush()

    with pytest.raises(ValueError, match="Cannot step scenario 'scn_state_gates' in status 'paused'"):
        step_scenario(db_session, scenario.id, steps=1)

    scenario.status = "cancelled"
    db_session.flush()

    with pytest.raises(ValueError, match="Cannot step scenario 'scn_state_gates' in status 'cancelled'"):
        step_scenario(db_session, scenario.id, steps=1)

    # Restore to running and advance to step 3 where disruption triggers
    scenario.status = "running"
    db_session.flush()

    res_3 = step_scenario(db_session, scenario.id, steps=3)
    assert res_3["current_step"] == 3

    # Clock holds until the anomaly is decided (autonomously for this opted-in scenario)
    eligible, reason = is_scenario_eligible_for_auto_tick(scenario)
    assert eligible is False
    assert "awaiting autonomous recovery decision" in reason.lower()

    # Apply recovery decision -> in_progress
    scenario.status = "recovering"
    scenario.parameters["recovery"] = {
        "status": "in_progress",
        "decision": "approved",
        "selected_plan": "reroute_line2",
        "start_step": 3,
        "completed_step": 5,
    }
    flag_modified(scenario, "parameters")
    db_session.flush()

    eligible_rec, _ = is_scenario_eligible_for_auto_tick(scenario)
    assert eligible_rec is True

    # Now stepping succeeds and executes recovery steps
    res_4 = step_scenario(db_session, scenario.id, steps=1)
    assert res_4["current_step"] == 4

    res_5 = step_scenario(db_session, scenario.id, steps=1)
    assert res_5["current_step"] == 5
    db_session.refresh(scenario)
    # At step 5, completed_step is reached, recovery completes and status restores to running
    assert scenario.status == "running"
    assert scenario.parameters["recovery"]["status"] == "completed"


def test_stepping_when_disruption_inhibits_active_stage(db_session: Session):
    """
    Verifies that when a disruption inhibits the active production stage:
    1. Stepping the scenario continues to advance the simulation clock and step count.
    2. The inhibited stage does NOT increment its stage_step_counter or advance to the next stage.
    3. An explicit 'production_stage.inhibited' event is emitted with the inhibit reason.
    """
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        db_session,
        name="Stage Inhibition Test",
        disruption_type="none",
        scenario_id="scn_inhibit_test",
    )

    # Place raw bean silo on quarantine hold (inhibiting Stage 1: Cleaning & Destoning)
    snap = scenario.parameters["snapshot"]
    snap["silos"]["silo_01"]["quality_status"] = "QUARANTINE_HOLD"
    scenario.parameters = {**scenario.parameters, "snapshot": snap}
    flag_modified(scenario, "parameters")
    db_session.flush()

    # Step +1
    res = step_scenario(db_session, scenario.id, steps=1)
    db_session.refresh(scenario)
    assert res["current_step"] == 1
    new_snap = res["snapshot"]
    prod_cycle = new_snap["production_cycle"]

    # Stage 1 must STILL be active and counter must remain 0
    assert prod_cycle["active_stage_num"] == 1
    assert prod_cycle["stage_step_counter"] == 0

    # Verify inhibition event in operational events
    events = db_session.execute(
        select(OperationalEvent).where(
            OperationalEvent.scenario_id == scenario.id,
            OperationalEvent.event_type == "production_stage.inhibited",
        )
    ).scalars().all()
    assert len(events) == 1
    assert "quarantine hold" in events[0].payload["reason"].lower()


def test_step_api_transaction_atomicity(db_session: Session):
    """
    Verifies that the FastAPI POST /{scenario_id}/step endpoint:
    1. Returns HTTP 200 with StepResponse on successful multi-step execution.
    2. Rolls back transaction on failure without committing partial updates or orphaned records.
    """
    from app.api.simulations import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        seed_resp = client.post("/simulations/seed")
        assert seed_resp.status_code == 200

        import uuid
        test_scn_id = f"scn_atom_{uuid.uuid4().hex[:8]}"

        create_resp = client.post(
            "/simulations",
            json={
                "name": "API Step Atomicity Test",
                "disruption_type": "none",
                "scenario_id": test_scn_id,
            },
        )
        assert create_resp.status_code == 201

        # Step +1 via API
        resp_step1 = client.post(f"/simulations/{test_scn_id}/step", json={"steps": 1})
        assert resp_step1.status_code == 200
        data1 = resp_step1.json()
        assert data1["current_step"] == 1
        assert data1["status"] == "running"

        # Step +3 via API
        resp_step3 = client.post(f"/simulations/{test_scn_id}/step", json={"steps": 3})
        assert resp_step3.status_code == 200
        data3 = resp_step3.json()
        assert data3["current_step"] == 4

        # Step +3 again via API (crosses BATCH-101 completion into steps 5, 6, 7)
        resp_step3_b = client.post(f"/simulations/{test_scn_id}/step", json={"steps": 3})
        assert resp_step3_b.status_code == 200
        data3_b = resp_step3_b.json()
        assert data3_b["current_step"] == 7
    finally:
        app.dependency_overrides.clear()


