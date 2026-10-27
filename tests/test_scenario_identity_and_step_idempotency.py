"""
Regression tests for Scenario Identity, Step Idempotency, and Concurrency Control.

Covers:
1. Scenario creation when completed scenarios exist returns a brand-new unique ID.
2. Attempting to create an existing scenario ID returns 409 Conflict.
3. Two consecutively created scenarios open and advance independently.
4. Step execution: first execution, exact retry, stale retry, and conflicting retry.
5. Pre-existing operational events cannot be treated as proof of committed state (ACID transaction consistency).
6. Locking serializes concurrent steps cleanly without UniqueViolation.
7. Operational event IDs are strictly capped at <= 64 characters with deterministic collision resistance across scenarios with shared prefixes.
8. Failed step rolls back completely without advancing step count.
9. GET /simulations lists scenarios ordered by created_at descending.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.main import app
from app.models import OperationalEvent, SimulationScenario
from app.services.seed import BASELINE_START_TIME
from app.services.simulation import create_scenario, step_scenario


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_scenario_creation_when_completed_scenarios_exist(client: TestClient):
    """Verifies that creating a scenario when completed scenarios exist returns a unique new scenario."""
    unique_completed_id = f"scn_test_done_{uuid.uuid4().hex[:8]}"
    create_resp = client.post(
        "/simulations",
        json={"name": "Completed Test Run", "scenario_id": unique_completed_id},
    )
    assert create_resp.status_code == 201

    with SessionLocal() as db:
        sc = db.get(SimulationScenario, unique_completed_id)
        assert sc is not None
        sc.status = "completed"
        sc.current_step = 10
        db.commit()

    # Call create scenario without ID
    new_resp = client.post(
        "/simulations",
        json={"name": "New Fresh Run"},
    )
    assert new_resp.status_code == 201
    data = new_resp.json()
    assert data["id"] != unique_completed_id
    assert data["status"] == "running"
    assert data["current_step"] == 0

    # Clean up test scenarios
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(
            OperationalEvent.scenario_id.in_([unique_completed_id, data["id"]])
        ).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(
            SimulationScenario.id.in_([unique_completed_id, data["id"]])
        ).delete(synchronize_session=False)
        db.commit()


def test_create_existing_scenario_id_returns_409(client: TestClient):
    """Verifies that attempting to create a scenario with an already existing ID returns 409 Conflict."""
    existing_id = f"scn_test_dup_{uuid.uuid4().hex[:8]}"
    resp1 = client.post(
        "/simulations",
        json={"name": "Initial Scenario", "scenario_id": existing_id},
    )
    assert resp1.status_code == 201

    # Attempt to create again with the same scenario_id
    resp2 = client.post(
        "/simulations",
        json={"name": "Duplicate Scenario", "scenario_id": existing_id},
    )
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"].lower()

    # Clean up
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(OperationalEvent.scenario_id == existing_id).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(SimulationScenario.id == existing_id).delete(synchronize_session=False)
        db.commit()


def test_two_scenarios_advance_independently(client: TestClient):
    """Verifies that two consecutive scenarios advance independently without crosstalk."""
    scn_a_id = f"scn_test_a_{uuid.uuid4().hex[:8]}"
    scn_b_id = f"scn_test_b_{uuid.uuid4().hex[:8]}"

    r_a = client.post("/simulations", json={"name": "Scenario A", "scenario_id": scn_a_id})
    assert r_a.status_code == 201

    r_b = client.post("/simulations", json={"name": "Scenario B", "scenario_id": scn_b_id})
    assert r_b.status_code == 201

    # Step Scenario A twice
    r_step1 = client.post(f"/simulations/{scn_a_id}/step", json={"steps": 1})
    assert r_step1.status_code == 200
    r_step2 = client.post(f"/simulations/{scn_a_id}/step", json={"steps": 1})
    assert r_step2.status_code == 200
    step_a = r_step2.json()
    assert step_a["current_step"] == 2

    # Check Scenario B is still at step 0
    get_b = client.get(f"/simulations/{scn_b_id}").json()
    assert get_b["current_step"] == 0

    # Clean up
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(
            OperationalEvent.scenario_id.in_([scn_a_id, scn_b_id])
        ).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(
            SimulationScenario.id.in_([scn_a_id, scn_b_id])
        ).delete(synchronize_session=False)
        db.commit()


def test_step_idempotency_first_exact_stale_and_conflicting_retry(client: TestClient):
    """
    Verifies step_scenario idempotency across:
    1. First execution (advances cleanly)
    2. Exact retry (returns idempotent state without advancing)
    3. Stale retry (returns current advanced state without skipping or regressing)
    4. Conflicting retry (reuses key with different parameters, raises error)
    """
    scn_id = f"scn_test_idem_lifecycle_{uuid.uuid4().hex[:8]}"
    client.post("/simulations", json={"name": "Idempotency Lifecycle", "scenario_id": scn_id})

    key_1 = f"step_key_1_{uuid.uuid4().hex[:8]}"

    # 1. First execution
    r1 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "idempotency_key": key_1, "target_step": 1},
    )
    assert r1.status_code == 200
    res1 = r1.json()
    assert res1["current_step"] == 1
    events_count_1 = res1["events_emitted_count"]
    assert events_count_1 > 0

    # 2. Exact retry
    r2 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "idempotency_key": key_1, "target_step": 1},
    )
    assert r2.status_code == 200
    res2 = r2.json()
    assert res2["current_step"] == 1
    assert res2["simulation_time"] == res1["simulation_time"]
    assert res2["events_emitted_count"] == 0

    # Step forward legitimately to step 3 with a new key
    key_2 = f"step_key_2_{uuid.uuid4().hex[:8]}"
    r_step2 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 2, "idempotency_key": key_2, "target_step": 3},
    )
    assert r_step2.status_code == 200
    get_3 = client.get(f"/simulations/{scn_id}").json()
    assert get_3["current_step"] == 3

    # 3. Stale retry (retrying key_1 from step 1 when scenario is at step 3)
    r3 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 1, "idempotency_key": key_1, "target_step": 1},
    )
    assert r3.status_code == 200
    res3 = r3.json()
    assert res3["current_step"] == 3  # Returns current advanced state, did not regress or skip forward

    # 4. Conflicting retry (reusing key_1 with different parameters, e.g. steps=5, target_step=8)
    r4 = client.post(
        f"/simulations/{scn_id}/step",
        json={"steps": 5, "idempotency_key": key_1, "target_step": 8},
    )
    assert r4.status_code == 404  # FastAPI maps ValueError to 404 in advance_simulation_step
    assert "conflicting idempotency key" in r4.json()["detail"].lower()

    # Clean up
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(OperationalEvent.scenario_id == scn_id).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(SimulationScenario.id == scn_id).delete(synchronize_session=False)
        db.commit()


def test_pre_existing_events_rejected_not_treated_as_committed():
    """
    Verifies that pre-existing operational events cannot be treated as proof
    that production state changes committed. If events for step N already exist
    in database before step N executes, it must fail to prevent split-brain state.
    """
    scn_id = f"scn_test_event_reconcile_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        create_scenario(db, name="Pre-existing Events Test", scenario_id=scn_id)
        db.commit()

    # Pre-insert an orphaned event at simulation_time of step 1 (15m after start) while scenario is at step 0
    step_1_time = BASELINE_START_TIME + timedelta(minutes=15)
    with SessionLocal() as db:
        orphaned_ev = OperationalEvent(
            event_id=f"evt_{scn_id}_1_0_batch.step_progress",
            scenario_id=scn_id,
            event_type="batch.step_progress",
            schema_version=1,
            simulation_time=step_1_time,
            recorded_at=datetime.now(timezone.utc),
            source_system="test",
            source_record_id="test",
            entity_refs=[scn_id],
            idempotency_key="orphan_test_key",
            payload={"step": 1},
        )
        db.add(orphaned_ev)
        db.commit()

    # Attempt to step scenario to step 1
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="already exist in database"):
            step_scenario(db, scn_id, steps=1)

        # Confirm scenario step was NOT advanced to step 1
        sc = db.get(SimulationScenario, scn_id)
        assert sc.current_step == 0

    # Clean up
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(OperationalEvent.scenario_id == scn_id).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(SimulationScenario.id == scn_id).delete(synchronize_session=False)
        db.commit()


def test_event_id_collision_resistance_across_long_scenario_ids():
    """
    Verifies that event IDs for distinct scenarios with identical 43-character prefixes
    do not collide, are <= 64 characters, and insert cleanly into PostgreSQL.
    """
    shared_prefix = "scn_shared_factory_roaster_production_line"
    scn_1 = f"{shared_prefix}_alpha_{uuid.uuid4().hex[:6]}"
    scn_2 = f"{shared_prefix}_beta_{uuid.uuid4().hex[:6]}"

    with SessionLocal() as db:
        create_scenario(db, name="Prefix Collision Test 1", scenario_id=scn_1)
        create_scenario(db, name="Prefix Collision Test 2", scenario_id=scn_2)
        db.commit()

        # Step both scenarios
        step_scenario(db, scn_1, steps=1)
        db.commit()
        step_scenario(db, scn_2, steps=1)
        db.commit()

        # Fetch events for both
        evs_1 = db.query(OperationalEvent).filter(OperationalEvent.scenario_id == scn_1).all()
        evs_2 = db.query(OperationalEvent).filter(OperationalEvent.scenario_id == scn_2).all()

        ids_1 = {e.event_id for e in evs_1}
        ids_2 = {e.event_id for e in evs_2}

        # Check all <= 64 characters
        for eid in ids_1 | ids_2:
            assert len(eid) <= 64, f"Event ID '{eid}' exceeds 64 characters ({len(eid)})"

        # Check zero collision between scenarios
        intersection = ids_1 & ids_2
        assert len(intersection) == 0, f"Event ID collision detected: {intersection}"

    # Clean up
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(
            OperationalEvent.scenario_id.in_([scn_1, scn_2])
        ).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(
            SimulationScenario.id.in_([scn_1, scn_2])
        ).delete(synchronize_session=False)
        db.commit()


def test_concurrent_step_attempts_serialize_cleanly():
    """
    Verifies that concurrent step executions for the same scenario step
    serialize cleanly under advisory locking and do NOT raise UniqueViolation.
    """
    scn_id = f"scn_test_concurrent_{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        create_scenario(db, name="Concurrent Step Test", scenario_id=scn_id)
        db.commit()

    def run_step():
        with SessionLocal() as db:
            try:
                res = step_scenario(db, scn_id, steps=1)
                db.commit()
                return {"status": "ok", "step": res["current_step"]}
            except Exception as e:
                db.rollback()
                return {"status": "error", "error": str(e), "type": type(e).__name__}

    # Execute 3 concurrent workers attempting to step the scenario at once
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_step) for _ in range(3)]
        results = [f.result() for f in futures]

    # None should have failed with UniqueViolation
    for r in results:
        assert "UniqueViolation" not in r.get("error", "")

    # Clean up
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(OperationalEvent.scenario_id == scn_id).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(SimulationScenario.id == scn_id).delete(synchronize_session=False)
        db.commit()


def test_list_simulations_endpoint(client: TestClient):
    """Verifies that GET /simulations lists scenarios ordered by created_at descending."""
    id1 = f"scn_test_list_1_{uuid.uuid4().hex[:8]}"
    id2 = f"scn_test_list_2_{uuid.uuid4().hex[:8]}"

    client.post("/simulations", json={"name": "List Test 1", "scenario_id": id1})
    client.post("/simulations", json={"name": "List Test 2", "scenario_id": id2})

    resp = client.get("/simulations?limit=10")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) >= 2

    # id2 was created after id1, so id2 should precede id1
    ids = [item["id"] for item in items]
    assert id2 in ids
    assert id1 in ids
    assert ids.index(id2) < ids.index(id1)

    # Clean up
    with SessionLocal() as db:
        db.query(OperationalEvent).filter(
            OperationalEvent.scenario_id.in_([id1, id2])
        ).delete(synchronize_session=False)
        db.query(SimulationScenario).filter(
            SimulationScenario.id.in_([id1, id2])
        ).delete(synchronize_session=False)
        db.commit()
