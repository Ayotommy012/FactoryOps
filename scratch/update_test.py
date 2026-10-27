with open('tests/test_simulation_clock_and_latency.py', 'r', encoding='utf-8') as f:
    code = f.read()

old_test = '''def test_recovery_inference_failure_never_reports_completed_prematurely():
    """Verifies that an inference failure during autonomous recovery backs off and never reports completed."""
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
        assert "TimeoutError" in str(rec.get("last_error", ""))'''

new_test = '''def test_recovery_inference_failure_never_reports_completed_prematurely():
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
        assert "TimeoutError" in str(rec.get("last_error", ""))'''

code_norm = code.replace('\r\n', '\n')
old_norm = old_test.replace('\r\n', '\n')
assert old_norm in code_norm, 'old_test not found in code'
code_updated = code_norm.replace(old_norm, new_test.replace('\r\n', '\n'))
with open('tests/test_simulation_clock_and_latency.py', 'w', encoding='utf-8') as f:
    f.write(code_updated.replace('\n', '\r\n'))
print('Successfully updated tests/test_simulation_clock_and_latency.py')
