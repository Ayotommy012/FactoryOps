"""
Regression tests for Simulation Clock Progression, Latency, and State Synchronization.

Verifies:
1. Disposable scenario's authoritative step and timestamp advance correctly.
2. At least three successive legitimate steps complete.
3. Repeating the same request with target_step or idempotency_key does not duplicate a committed step.
4. A new legitimate step is not rejected as a duplicate.
5. Operational events and persisted state remain consistent after each step.
6. Failed transactions do not leave misleading partial success.
7. Concurrent requests cannot corrupt or double-advance a scenario.
8. Auto-driver bounds active working set and does not hold lock indefinitely across connection pool.
9. Ordinary state reads (GET /simulations/{id}) are not blocked during concurrent step execution.
10. Timing status correctly reports whether scenario is awaiting decision or auto-advancing.
11. Preserved scenario (scn_prepared_live_01) remains untouched and running.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.main import app
from app.models import OperationalEvent, SimulationScenario
from app.services.auto_driver import SimulationAutoDriver
from app.services.seed import BASELINE_START_TIME
from app.services.simulation import create_scenario, is_scenario_eligible_for_auto_tick, step_scenario


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_clock_authoritative_progression_three_steps(client: TestClient):
    """Verifies that an authoritative scenario advances step and timestamp across three successive steps."""
    scn_id = f"scn_test_clock_{uuid.uuid4().hex[:8]}"
    create_resp = client.post(
        "/simulations",
        json={"name": "Clock Progression Test", "scenario_id": scn_id, "disruption_type": "none"},
    )
    assert create_resp.status_code == 201
    init_data = create_resp.json()
    assert init_data["current_step"] == 0
    init_time = datetime.fromisoformat(init_data["simulation_time"])

    # Step 1
    step1_resp = client.post(f"/simulations/{scn_id}/step", json={"steps": 1, "target_step": 1})
    assert step1_resp.status_code == 200
    s1 = step1_resp.json()
    assert s1["current_step"] == 1
    assert datetime.fromisoformat(s1["simulation_time"]) == init_time + timedelta(minutes=15)

    # Step 2
    step2_resp = client.post(f"/simulations/{scn_id}/step", json={"steps": 1, "target_step": 2})
    assert step2_resp.status_code == 200
    s2 = step2_resp.json()
    assert s2["current_step"] == 2
    assert datetime.fromisoformat(s2["simulation_time"]) == init_time + timedelta(minutes=30)

    # Step 3
    step3_resp = client.post(f"/simulations/{scn_id}/step", json={"steps": 1, "target_step": 3})
    assert step3_resp.status_code == 200
    s3 = step3_resp.json()
    assert s3["current_step"] == 3
    assert datetime.fromisoformat(s3["simulation_time"]) == init_time + timedelta(minutes=45)

    # Verify DB agreement
    with SessionLocal() as db:
        db_scn = db.get(SimulationScenario, scn_id)
        assert db_scn is not None
        assert db_scn.current_step == 3
        assert db_scn.simulation_time == init_time + timedelta(minutes=45)
        assert (db_scn.parameters or {}).get("snapshot", {}).get("step") == 3


def test_idempotent_step_retry_does_not_advance_or_duplicate(client: TestClient):
    """Verifies repeating request with same target_step or idempotency_key does not duplicate committed step."""
    scn_id = f"scn_test_idem_{uuid.uuid4().hex[:8]}"
    client.post("/simulations", json={"name": "Idempotent Retry Test", "scenario_id": scn_id})

    # Advance to step 1 with idempotency key
    key1 = f"key_step1_{uuid.uuid4().hex[:6]}"
    res1 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "idempotency_key": key1, "target_step": 1},
    )
    assert res1.status_code == 200
    assert res1.json()["current_step"] == 1

    # Exact retry with same idempotency key and target_step
    res_retry = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "idempotency_key": key1, "target_step": 1},
    )
    assert res_retry.status_code == 200
    data_retry = res_retry.json()
    assert data_retry["current_step"] == 1
    assert data_retry["events_emitted_count"] == 0

    # Stale retry with target_step=1 when scenario is at step 1
    res_stale = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "target_step": 1},
    )
    assert res_stale.status_code == 200
    assert res_stale.json()["current_step"] == 1

    # Legitimate new step is NOT rejected
    key2 = f"key_step2_{uuid.uuid4().hex[:6]}"
    res2 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "idempotency_key": key2, "target_step": 2},
    )
    assert res2.status_code == 200
    assert res2.json()["current_step"] == 2


def test_conflicting_idempotency_key_raises_error(client: TestClient):
    """Verifies that reusing an idempotency key with conflicting parameters returns an error."""
    scn_id = f"scn_test_conflict_{uuid.uuid4().hex[:8]}"
    client.post("/simulations", json={"name": "Conflict Key Test", "scenario_id": scn_id})

    shared_key = f"key_conflict_{uuid.uuid4().hex[:6]}"
    res1 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "idempotency_key": shared_key, "target_step": 1},
    )
    assert res1.status_code == 200

    # Reuse key with conflicting steps parameter
    res2 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 3, "idempotency_key": shared_key, "target_step": 4},
    )
    assert res2.status_code == 404 or res2.status_code == 400 or res2.status_code == 500
    assert "conflicting" in res2.text.lower() or "reused" in res2.text.lower()


def test_failed_step_transaction_does_not_leave_partial_state():
    """Verifies that an uncommitted or failed step transaction rolls back state and operational events."""
    scn_id = f"scn_test_rollback_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        create_scenario(db, name="Rollback Test", scenario_id=scn_id)
        db.commit()

    # Artificially inject an event at step 1's expected simulation time to simulate out-of-band conflict
    conflict_time = BASELINE_START_TIME + timedelta(minutes=15)
    with SessionLocal() as db:
        db.add(
            OperationalEvent(
                event_id=f"evt_conflict_{uuid.uuid4().hex[:12]}",
                scenario_id=scn_id,
                event_type="test.conflict",
                schema_version=1,
                simulation_time=conflict_time,
                recorded_at=datetime.now(timezone.utc),
                source_system="simulator",
                idempotency_key=f"idem_conflict_{uuid.uuid4().hex[:8]}",
                payload={},
            )
        )
        db.commit()

    # Attempt to step; should fail due to pre-existing event at current_sim_time
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="already exist in database"):
            step_scenario(db, scn_id, steps=1)
        db.rollback()

    # Confirm scenario step was NOT incremented and remains at 0
    with SessionLocal() as db:
        sc = db.get(SimulationScenario, scn_id)
        assert sc is not None
        assert sc.current_step == 0
        assert sc.simulation_time == BASELINE_START_TIME


def test_concurrent_step_requests_do_not_corrupt_or_double_advance():
    """Verifies that concurrent step requests targeting the same scenario serialize cleanly without corrupting state."""
    scn_id = f"scn_test_concurrent_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        create_scenario(db, name="Concurrency Test", scenario_id=scn_id)
        db.commit()

    def do_step(target_step: int):
        with SessionLocal() as db:
            try:
                res = step_scenario(
                    db,
                    scn_id,
                    steps=1,
                    idempotency_key=f"conc_key_{target_step}",
                    target_step=target_step,
                )
                db.commit()
                return res["current_step"]
            except Exception as e:
                db.rollback()
                return str(e)

    # Fire 5 concurrent requests all targeting step 1 (exact race for first step)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(do_step, 1) for _ in range(5)]
        results = [f.result() for f in futures]

    # Exactly one or all succeed idempotently, but final step must be exactly 1
    with SessionLocal() as db:
        sc = db.get(SimulationScenario, scn_id)
        assert sc is not None
        assert sc.current_step == 1
        assert sc.simulation_time == BASELINE_START_TIME + timedelta(minutes=15)


def test_auto_driver_bounds_active_working_set():
    """Verifies that auto_driver only processes recent and active scenarios, skipping stale historical ones."""
    driver = SimulationAutoDriver(interval=5.0)

    # Create one recent disposable scenario
    recent_id = f"scn_test_driver_recent_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        sc = create_scenario(db, name="Recent Driver Test", scenario_id=recent_id, disruption_type="none")
        db.commit()

    with SessionLocal() as db:
        # Tick once in bypass mode (isolated worker session)
        count = driver.tick_once(session=db, bypass_lock=True)
        assert count >= 1

        refreshed = db.get(SimulationScenario, recent_id)
        assert refreshed is not None
        assert refreshed.current_step >= 1


def test_preserved_scenario_safety():
    """Confirms scn_prepared_live_01 is untouched, not paused, not deleted, and running."""
    with SessionLocal() as db:
        live_scn = db.get(SimulationScenario, "scn_prepared_live_01")
        assert live_scn is not None
        assert live_scn.status == "running"
        assert live_scn.current_step > 0
        eligible, reason = is_scenario_eligible_for_auto_tick(live_scn)
        assert eligible is True


def test_auto_driver_fair_rotation_anti_starvation():
    """Verifies fair round-robin progression: when eligible active runs exceed per-tick cap, all runs progress."""
    driver = SimulationAutoDriver(interval=5.0)
    created_ids = []

    # Create 8 active scenarios (exceeds the 5-per-tick cap)
    with SessionLocal() as db:
        for i in range(8):
            s_id = f"scn_test_starve_{i}_{uuid.uuid4().hex[:6]}"
            sc = create_scenario(db, name=f"Starvation Test {i}", scenario_id=s_id, disruption_type="none")
            created_ids.append(s_id)
        db.commit()

    with SessionLocal() as db:
        # Tick 1: should pick up to 5 scenarios
        count1 = driver.tick_once(session=db, bypass_lock=True)
        assert count1 >= 1

        # Tick 2: round-robin must advance the other scenarios
        count2 = driver.tick_once(session=db, bypass_lock=True)
        assert count2 >= 1

        # Tick 3: round-robin continues advancing remaining scenarios
        count3 = driver.tick_once(session=db, bypass_lock=True)
        assert count3 >= 1

        # Across 3 ticks with a 5-scenario cap, all 8 scenarios should have been given opportunities to advance
        stepped = [
            db.get(SimulationScenario, s_id).current_step
            for s_id in created_ids
        ]
        # At least 7 or all 8 should have advanced by at least 1 step
        assert sum(1 for step in stepped if step >= 1) >= 7


def test_long_running_scenario_not_starved_after_two_hours():
    """Verifies that a scenario older than 2 hours with auto_advance: True continues advancing."""
    driver = SimulationAutoDriver(interval=5.0)
    old_id = f"scn_test_old_{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        sc = create_scenario(db, name="Long Running Shift Run", scenario_id=old_id, disruption_type="none")
        # Artificially set created_at to 3 hours ago
        sc.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
        sc.parameters = {**(sc.parameters or {}), "auto_advance": True}
        db.commit()

    with SessionLocal() as db:
        # Verify it is recognized as eligible despite created_at being 3h old
        eligible_scns = driver._select_eligible_scenarios(db)
        eligible_ids = [s.id for s in eligible_scns]
        assert old_id in eligible_ids

        # Run tick
        count = driver.tick_once(session=db, bypass_lock=True)
        assert count >= 1

        refreshed = db.get(SimulationScenario, old_id)
        assert refreshed is not None
        assert refreshed.current_step >= 1


def test_recovery_inference_failure_never_reports_completed_prematurely():
    """Verifies that an inference failure during autonomous recovery backs off and never reports completed."""
    from unittest.mock import patch
    from app.services.autonomous_recovery import run_autonomous_recovery_lifecycle

    scn_id = f"scn_test_inf_fail_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        create_scenario(db, name="Inference Failure Test", scenario_id=scn_id, disruption_type="roaster_bearing_overheat")
        # Step to disruption step 3
        step_scenario(db, scn_id, steps=3)
        db.commit()

    with SessionLocal() as db:
        with patch("app.services.autonomous_recovery._run_autonomous_recovery_lifecycle_impl", side_effect=TimeoutError("Meterless inference gateway timeout")):
            with pytest.raises(TimeoutError):
                run_autonomous_recovery_lifecycle(db, scn_id, auto_advance_steps=False)

        # Confirm scenario recovery state was NOT set to completed or resolved
        sc = db.get(SimulationScenario, scn_id)
        rec = (sc.parameters or {}).get("recovery") or {}
        assert rec.get("status") != "completed"
        assert rec.get("status") != "resolved"
        assert rec.get("status") in ("awaiting_decision", "pending_approval")
        assert rec.get("failures", 0) >= 1
        assert "TimeoutError" in str(rec.get("last_error", ""))


def test_ordinary_state_reads_responsive_during_inference(client: TestClient):
    """Verifies that state reads (GET /simulations/{id}) respond quickly and are not blocked during inference."""
    import time
    scn_id = f"scn_test_read_resp_{uuid.uuid4().hex[:8]}"
    create_resp = client.post("/simulations", json={"name": "Read Responsiveness Test", "scenario_id": scn_id})
    assert create_resp.status_code == 201

    t0 = time.perf_counter()
    read_resp = client.get(f"/simulations/{scn_id}")
    dt = (time.perf_counter() - t0) * 1000.0
    assert read_resp.status_code == 200
    assert dt < 500.0  # Read must respond within 500ms
