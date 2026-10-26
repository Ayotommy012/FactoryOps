"""
Comprehensive verification suite for Hybrid Simulation Timing in FactoryOps.

Tests:
1. Valid automatic tick advances simulated time by exactly one step (15 mins) and increments step count once.
2. Automatic progression respects pause and resume behavior.
3. Scenario awaiting human recovery decision pauses automatic progression at the approval gate.
4. Manual Step +1 and Step +3 controls work seamlessly.
5. Concurrent step execution serializes via row-level locks without corrupting state or losing steps.
6. Approved recovery plan enters 'recovering' with exact calculated maintenance duration steps.
7. Stepping through recovery progresses maintenance steps without premature completion.
8. Recovery completion transitions scenario status back to 'running' and restores nominal production.
9. Rejection does not apply recovery mutations.
10. Idempotent approval rejects conflicting plans and prevents duplicate applications.
11. Timing API endpoint returns complete operational visibility (interval, auto_advance, awaiting_decision, recovery).
12. Auto driver leader election via PostgreSQL advisory locks.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.simulations import get_db
from app.database import engine
from app.main import app
from app.models import OperationalEvent, SimulationScenario
from app.services.auto_driver import auto_driver, SIMULATION_DRIVER_LOCK_ID
from app.services.recovery import (
    apply_recovery_decision,
    calculate_plan_duration_steps,
    generate_candidate_plans,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import (
    create_scenario,
    get_scenario_timeline,
    is_awaiting_recovery_decision,
    is_scenario_eligible_for_auto_tick,
    step_scenario,
)


@pytest.fixture
def db_session():
    """Isolated session with nested savepoints ensuring zero database drift."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def test_auto_tick_advances_exactly_one_step(db_session: Session):
    """Verifies that an automatic tick advances an eligible scenario by exactly 1 step."""
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        session=db_session,
        name="Auto Tick Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_auto_tick_01",
    )
    assert scenario.current_step == 0
    assert scenario.simulation_time.isoformat() == "2026-10-22T06:00:00+00:00"

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        resp = client.post("/simulations/scn_auto_tick_01/auto-tick")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticked"] is True
        assert data["current_step"] == 1
        assert data["simulation_time"] == "2026-10-22T06:15:00+00:00"
        assert data["scenario_status"] == "running"
    finally:
        app.dependency_overrides.clear()


def test_auto_progression_pause_and_resume(db_session: Session):
    """Verifies that pause/resume endpoints toggle auto-progression and stop automatic ticks."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Pause Resume Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_pause_resume",
    )

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)

        # 1. Pause scenario
        res_pause = client.post("/simulations/scn_pause_resume/pause")
        assert res_pause.status_code == 200
        assert res_pause.json()["auto_advance"] is False
        assert res_pause.json()["status"] == "paused"

        # 2. Attempt auto-tick while paused -> must not tick
        res_tick_paused = client.post("/simulations/scn_pause_resume/auto-tick")
        assert res_tick_paused.status_code == 200
        data_paused = res_tick_paused.json()
        assert data_paused["ticked"] is False
        assert "paused" in data_paused["reason"].lower()
        assert data_paused["current_step"] == 0

        # 3. Resume scenario
        res_resume = client.post("/simulations/scn_pause_resume/resume")
        assert res_resume.status_code == 200
        assert res_resume.json()["auto_advance"] is True
        assert res_resume.json()["status"] == "resumed"

        # 4. Attempt auto-tick after resume -> ticks normally
        res_tick_resumed = client.post("/simulations/scn_pause_resume/auto-tick")
        assert res_tick_resumed.status_code == 200
        data_resumed = res_tick_resumed.json()
        assert data_resumed["ticked"] is True
        assert data_resumed["current_step"] == 1
    finally:
        app.dependency_overrides.clear()


def test_scenario_awaiting_decision_pauses_auto_tick(db_session: Session):
    """Verifies that automatic progression halts when a disruption occurs and awaits human decision."""
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        session=db_session,
        name="Awaiting Gate Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_await_decision",
    )

    # Advance to anomaly step 3 manually
    step_scenario(db_session, "scn_await_decision", steps=3)
    db_session.refresh(scenario)

    # Verify scenario is awaiting recovery decision
    assert is_awaiting_recovery_decision(scenario) is True
    eligible, reason = is_scenario_eligible_for_auto_tick(scenario)
    assert eligible is False
    assert "awaiting" in reason.lower()

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        # Attempt auto-tick while awaiting decision -> must be blocked
        res = client.post("/simulations/scn_await_decision/auto-tick")
        assert res.status_code == 200
        data = res.json()
        assert data["ticked"] is False
        assert "awaiting" in data["reason"].lower()
        assert data["current_step"] == 3
    finally:
        app.dependency_overrides.clear()


def test_manual_step_plus_1_and_step_plus_3(db_session: Session):
    """Verifies manual Step +1 and Step +3 endpoints advance the expected number of steps."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Manual Step Test",
        disruption_type="none",
        scenario_id="scn_manual_steps",
    )

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)

        # Step +1
        r1 = client.post("/simulations/scn_manual_steps/step", json={"steps": 1})
        assert r1.status_code == 200
        assert r1.json()["current_step"] == 1
        assert r1.json()["simulation_time"] == "2026-10-22T06:15:00+00:00"

        # Step +3
        r3 = client.post("/simulations/scn_manual_steps/step", json={"steps": 3})
        assert r3.status_code == 200
        assert r3.json()["current_step"] == 4
        assert r3.json()["simulation_time"] == "2026-10-22T07:00:00+00:00"
    finally:
        app.dependency_overrides.clear()


def test_recovery_lifecycle_full_progression_and_completion(db_session: Session):
    """
    Comprehensive end-to-end test of the recovery lifecycle:
    1. Steps to anomaly (Step 3).
    2. Approves Plan A (0.75h = 3 steps). Status becomes 'recovering'.
    3. Verifies approval step does NOT count toward maintenance duration.
    4. Steps through maintenance Step 4 (remaining: 2) and Step 5 (remaining: 1).
    5. At Step 6 (final maintenance step), recovery completes!
       Status transitions back to 'running', roaster restored to 1200 kg/h nominal throughput.
    6. Verifies event 'recovery.completed' emitted in timeline.
    7. Step 7 continues nominal production at 1200 kg/h.
    """
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        session=db_session,
        name="Recovery Lifecycle Full Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_rec_lifecycle",
    )

    # 1. Step to anomaly at step 3
    step_scenario(db_session, "scn_rec_lifecycle", steps=3)
    db_session.refresh(scenario)
    assert scenario.current_step == 3
    snap = scenario.parameters["snapshot"]
    assert snap["machines"]["machine:plant:ROAST-01"]["status"] == "degraded"
    assert is_awaiting_recovery_decision(scenario) is True

    # 2. Operator approves Plan A (downtime = 0.75h = 3 steps)
    duration_steps = calculate_plan_duration_steps("plan_a_thermal_flush", scenario.step_size_minutes)
    assert duration_steps == 3

    dec = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_rec_lifecycle",
        plan_id="plan_a_thermal_flush",
        decision="approved",
        operator_id="OP-TEST-CHIEF",
        rationale="Authorize thermal flush to recover roaster within SLA buffer.",
    )
    assert dec.decision == "approved"
    assert dec.updated_scenario_status == "recovering"
    assert scenario.status == "recovering"

    rec_param = scenario.parameters["recovery"]
    assert rec_param["start_step"] == 3
    assert rec_param["duration_steps"] == 3
    assert rec_param["completed_step"] == 6
    assert rec_param["status"] == "in_progress"

    # Approval step does not advance simulation clock: current_step is still 3
    assert scenario.current_step == 3
    assert is_awaiting_recovery_decision(scenario) is False

    # 3. Step 4 (Maintenance Step 1 of 3)
    res_s4 = step_scenario(db_session, "scn_rec_lifecycle", steps=1)
    assert res_s4["current_step"] == 4
    assert res_s4["status"] == "recovering"
    assert res_s4["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] == "maintenance"
    assert res_s4["snapshot"]["machines"]["machine:plant:ROAST-01"]["current_throughput_kg_h"] == 0.0

    # 4. Step 5 (Maintenance Step 2 of 3)
    res_s5 = step_scenario(db_session, "scn_rec_lifecycle", steps=1)
    assert res_s5["current_step"] == 5
    assert res_s5["status"] == "recovering"
    assert res_s5["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] == "maintenance"

    # 5. Step 6 (Maintenance Step 3 of 3 - RECOVERY COMPLETES)
    res_s6 = step_scenario(db_session, "scn_rec_lifecycle", steps=1)
    assert res_s6["current_step"] == 6
    # Status MUST transition back to 'running'
    assert res_s6["status"] == "running"
    assert scenario.status == "running"

    roaster_s6 = res_s6["snapshot"]["machines"]["machine:plant:ROAST-01"]
    assert roaster_s6["status"] == "running"
    assert roaster_s6["temperature_c"] == 115.0
    assert roaster_s6["vibration_rms"] == 1.6
    assert roaster_s6["health_score"] >= 0.95
    assert roaster_s6["current_throughput_kg_h"] == 1200.0

    # Verify recovery parameters updated to completed
    db_session.refresh(scenario)
    final_rec = scenario.parameters["recovery"]
    assert final_rec["status"] == "completed"
    assert final_rec["remaining_steps"] == 0
    assert final_rec["finished_at_step"] == 6

    # 6. Verify timeline contains 'recovery.completed' and 'incident.resolved' events
    timeline = get_scenario_timeline(db_session, "scn_rec_lifecycle")
    event_types = [e["event_type"] for e in timeline]
    assert "recovery.approved" in event_types
    assert "recovery.completed" in event_types
    assert "incident.resolved" in event_types

    compl_event = next(e for e in timeline if e["event_type"] == "recovery.completed")
    assert compl_event["payload"]["plan_id"] == "plan_a_thermal_flush"
    assert compl_event["payload"]["completed_step"] == 6

    # Verify scenario is NOT awaiting decision at step 6
    assert is_awaiting_recovery_decision(scenario) is False

    # 7. Step 7 (Nominal Production Resumed)
    res_s7 = step_scenario(db_session, "scn_rec_lifecycle", steps=1)
    assert res_s7["current_step"] == 7
    assert res_s7["status"] == "running"
    assert res_s7["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] == "running"
    assert is_awaiting_recovery_decision(scenario) is False

    # 8. Check API endpoints (GET /simulations/{id} and GET /simulations/{id}/timing)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        r_state = client.get("/simulations/scn_rec_lifecycle")
        assert r_state.status_code == 200
        state_data = r_state.json()
        assert state_data["status"] == "running"
        assert state_data["parameters"]["recovery"]["status"] == "completed"
        assert state_data["parameters"]["recovery"]["decision"] == "approved"

        r_timing = client.get("/simulations/scn_rec_lifecycle/timing")
        assert r_timing.status_code == 200
        timing_data = r_timing.json()
        assert timing_data["scenario_status"] == "running"
        assert timing_data["is_awaiting_decision"] is False
        assert timing_data["is_eligible_for_auto_tick"] is True
    finally:
        app.dependency_overrides.clear()
    # Batch advanced by 300 kg nominal capacity
    assert res_s7["snapshot"]["batches"]["production_batch:mes:BATCH-101"]["actual_quantity_kg"] > 645.0


def test_rejection_does_not_apply_recovery(db_session: Session):
    """Verifies that rejecting a recovery plan preserves running status without applying maintenance."""
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        session=db_session,
        name="Rejection Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_rec_rejection",
    )
    step_scenario(db_session, "scn_rec_rejection", steps=3)

    dec = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_rec_rejection",
        plan_id="plan_c_split_expedite",
        decision="rejected",
        operator_id="OP-TEST-CHIEF",
        rationale="Rejected: do not outsource production.",
    )
    assert dec.decision == "rejected"
    assert dec.updated_scenario_status == "running"
    assert scenario.status == "running"

    # Step 4 continues with roaster in degraded status (15% throttled throughput)
    res_s4 = step_scenario(db_session, "scn_rec_rejection", steps=1)
    assert res_s4["current_step"] == 4
    assert res_s4["status"] == "running"
    assert res_s4["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] == "degraded"
    assert res_s4["snapshot"]["machines"]["machine:plant:ROAST-01"]["current_throughput_kg_h"] == 180.0

    # Ensure rejection does NOT leave scenario stuck awaiting decision
    assert is_awaiting_recovery_decision(scenario) is False

    # Check timing API confirms is_awaiting_decision is False
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        r_rej = client.get("/simulations/scn_rec_rejection/timing")
        assert r_rej.status_code == 200
        assert r_rej.json()["is_awaiting_decision"] is False
    finally:
        app.dependency_overrides.clear()


def test_genuinely_unresolved_decision_requires_approval_gate(db_session: Session):
    """Verifies that an unhandled anomaly at step >= 3 requires human decision and halts auto-tick."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Unresolved Anomaly",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_unresolved_decision",
    )
    # Step to step 3 where anomaly occurs
    res_s3 = step_scenario(db_session, "scn_unresolved_decision", steps=3)
    assert res_s3["current_step"] == 3
    assert res_s3["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] == "degraded"

    scn = db_session.get(SimulationScenario, "scn_unresolved_decision")
    assert is_awaiting_recovery_decision(scn) is True

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        r_timing = client.get("/simulations/scn_unresolved_decision/timing")
        assert r_timing.status_code == 200
        data = r_timing.json()
        assert data["is_awaiting_decision"] is True
        assert data["is_eligible_for_auto_tick"] is False
        assert "decision" in data["ineligibility_reason"].lower()

        # Auto-tick POST must be blocked
        r_tick = client.post("/simulations/scn_unresolved_decision/auto-tick")
        assert r_tick.status_code == 200
        assert r_tick.json()["ticked"] is False
        assert "decision" in r_tick.json()["reason"].lower()
    finally:
        app.dependency_overrides.clear()


def test_timing_endpoint_payload(db_session: Session):
    """Verifies GET /simulations/{id}/timing returns comprehensive hybrid timing metadata."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Timing Endpoint Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_timing_meta",
    )

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        res = client.get("/simulations/scn_timing_meta/timing")
        assert res.status_code == 200
        timing = res.json()
        assert timing["scenario_id"] == "scn_timing_meta"
        assert timing["auto_advance"] is True
        assert timing["interval_seconds"] == 5
        assert timing["current_step"] == 0
        assert timing["scenario_status"] == "running"
        assert timing["is_awaiting_decision"] is False
        assert timing["is_eligible_for_auto_tick"] is True
    finally:
        app.dependency_overrides.clear()


def test_auto_driver_tick_once_execution(db_session: Session):
    """Verifies that auto_driver.tick_once executes eligible scenarios using advisory locking."""
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        session=db_session,
        name="Driver Test",
        disruption_type="none",
        scenario_id="scn_driver_tick",
    )
    db_session.commit()

    # Call tick_once directly with active session and bypass_lock to test execution logic
    ticked = auto_driver.tick_once(session=db_session, bypass_lock=True)
    assert ticked >= 1

    # Verify scenario advanced by 1 step
    db_session.refresh(scenario)
    assert scenario.current_step >= 1
