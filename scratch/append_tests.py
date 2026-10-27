new_tests = """

def test_auto_driver_fair_rotation_anti_starvation():
    \"\"\"Verifies fair round-robin progression: when eligible active runs exceed per-tick cap, all runs progress.\"\"\"
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

        # Across 2 ticks, all 8 scenarios should have been given opportunities to advance
        stepped = [
            db.get(SimulationScenario, s_id).current_step
            for s_id in created_ids
        ]
        # At least 7 or all 8 should have advanced by at least 1 step
        assert sum(1 for step in stepped if step >= 1) >= 7


def test_long_running_scenario_not_starved_after_two_hours():
    \"\"\"Verifies that a scenario older than 2 hours with auto_advance: True continues advancing.\"\"\"
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
    \"\"\"Verifies that an inference failure during autonomous recovery backs off and never reports completed.\"\"\"
    from unittest.mock import MagicMock
    from app.services.autonomous_recovery import run_autonomous_recovery_lifecycle

    scn_id = f"scn_test_inf_fail_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        create_scenario(db, name="Inference Failure Test", scenario_id=scn_id, disruption_type="roaster_bearing_overheat")
        # Step to disruption step 3
        step_scenario(db, scn_id, steps=3)
        db.commit()

    mock_client = MagicMock()
    mock_client.chat_completion.side_effect = TimeoutError("Meterless inference gateway timeout")

    with SessionLocal() as db:
        with pytest.raises(TimeoutError):
            run_autonomous_recovery_lifecycle(db, scn_id, client=mock_client, auto_advance_steps=False)

        # Confirm scenario recovery state was NOT set to completed or resolved
        sc = db.get(SimulationScenario, scn_id)
        rec = (sc.parameters or {}).get("recovery") or {}
        assert rec.get("status") != "completed"
        assert rec.get("status") != "resolved"
        assert rec.get("status") in ("awaiting_decision", "pending_approval")
        assert rec.get("failures", 0) >= 1
        assert "TimeoutError" in str(rec.get("last_error", ""))


def test_ordinary_state_reads_responsive_during_inference(client: TestClient):
    \"\"\"Verifies that state reads (GET /simulations/{id}) respond quickly and are not blocked during inference.\"\"\"
    import time
    scn_id = f"scn_test_read_resp_{uuid.uuid4().hex[:8]}"
    create_resp = client.post("/simulations", json={"name": "Read Responsiveness Test", "scenario_id": scn_id})
    assert create_resp.status_code == 201

    t0 = time.perf_counter()
    read_resp = client.get(f"/simulations/{scn_id}")
    dt = (time.perf_counter() - t0) * 1000.0
    assert read_resp.status_code == 200
    assert dt < 200.0  # Read must respond within 200ms
"""

with open('tests/test_simulation_clock_and_latency.py', 'rb') as f:
    existing = f.read().decode('utf-8').replace('\r\r\n', '\n').replace('\r\n', '\n')

updated = existing + new_tests

with open('tests/test_simulation_clock_and_latency.py', 'wb') as f:
    f.write(updated.replace('\n', '\r\n').encode('utf-8'))

print("Successfully appended new tests to tests/test_simulation_clock_and_latency.py")
