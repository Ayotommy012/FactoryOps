"""Autonomous recovery lifecycle, Meterless back-pressure, and severity consistency.

Covers the three properties FactoryOps must demonstrate:
1. Different disruptions receive evidence-based severities.
2. Repeated Meterless calls survive rate limiting without duplicate requests or
   permanently stuck recoveries.
3. A routine, eligible disruption completes autonomous recovery with no manual
   approval, while approval-required and unsafe plans keep their policies.
"""

from __future__ import annotations

import io
import json
import threading
import time
from unittest.mock import MagicMock, patch
import urllib.error

import pytest
from sqlalchemy.orm import Session

from app.database import engine
from app.models import OperationalEvent, SimulationScenario
from app.providers.meterless import (
    MeterlessClient,
    MeterlessRateLimitError,
    is_rate_limited_response,
    parse_retry_after,
    strip_reasoning,
)
from app.services.auto_driver import SimulationAutoDriver
from app.services.autonomous_recovery import (
    LifecycleAlreadyRunning,
    MAX_LIFECYCLE_FAILURES,
    run_autonomous_recovery_lifecycle,
)
from app.services.disruptions import apply_disruption
from app.services.recovery import (
    detect_and_trace_impact,
    explain_recovery_plans,
    generate_candidate_plans,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import create_scenario, step_scenario

NVIDIA_429_BODY = (
    b'{"error":{"message":"429: {\\"status\\":429,\\"title\\":\\"Too Many Requests\\"}",'
    b'"code":"provider_error","meterless_detail":"nvidia_429"}}'
)


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def _http_error(code: int, body: bytes, headers: dict | None = None) -> urllib.error.HTTPError:
    hdrs = MagicMock()
    hdrs.get = lambda k, d=None: (headers or {}).get(k, d)
    err = urllib.error.HTTPError("http://127.0.0.1:8088/v1/chat/completions", code, "err", hdrs, io.BytesIO(body))
    err.headers = hdrs
    return err


def _ok(content: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


def _press_scenario(db_session: Session, scenario_id: str) -> str:
    seed_baseline_factory(db_session)
    create_scenario(db_session, name="Press capacity loss", disruption_type="reduced_machine_capacity", scenario_id=scenario_id)
    step_scenario(db_session, scenario_id, steps=3)  # scheduled disruption applies at step 3
    return scenario_id


# =============================================================================
# 1. Meterless client back-pressure
# =============================================================================

def test_runtime_502_wrapping_nvidia_429_is_classified_as_rate_limit():
    assert is_rate_limited_response(502, NVIDIA_429_BODY.decode())
    assert is_rate_limited_response(429, "")
    assert not is_rate_limited_response(502, '{"error":"upstream connect error"}')
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert parse_retry_after(None) is None


def test_strip_reasoning_removes_think_blocks():
    assert strip_reasoning("<think>weighing options</think>\n{\"a\": 1}") == '{"a": 1}'
    assert strip_reasoning("chain of thought</think>Final answer") == "Final answer"


def test_identical_concurrent_requests_share_one_upstream_call(monkeypatch):
    """Single-flight: two threads asking the same thing produce one HTTP request."""
    calls = []

    def slow_urlopen(req, timeout=None):
        calls.append(req)
        time.sleep(0.3)
        return _ok("shared answer")

    client = MeterlessClient(token="test-token")
    results = []
    with patch("urllib.request.urlopen", side_effect=slow_urlopen):
        threads = [
            threading.Thread(target=lambda: results.append(client.chat_completion([{"role": "user", "content": "same"}])))
            for _ in range(2)
        ]
        [t.start() for t in threads]
        [t.join() for t in threads]

    assert results == ["shared answer", "shared answer"]
    assert len(calls) == 1


def test_rate_limit_cooldown_is_shared_across_clients(monkeypatch):
    """After one client sees a 429, a second client waits out the shared cooldown
    instead of hitting the upstream immediately."""
    monkeypatch.setenv("METERLESS_MAX_RETRIES", "0")
    first = MeterlessClient(token="test-token")
    with patch("urllib.request.urlopen", side_effect=_http_error(502, NVIDIA_429_BODY, {"Retry-After": "5"})):
        with pytest.raises(MeterlessRateLimitError):
            first.chat_completion([{"role": "user", "content": "one"}])

    second = MeterlessClient(token="test-token")
    with patch("urllib.request.urlopen") as mock_open:
        with pytest.raises(MeterlessRateLimitError, match="cooling down"):
            second.chat_completion([{"role": "user", "content": "two"}], retry_budget_sec=1.0)
    assert mock_open.call_count == 0  # failed fast, upstream untouched


def test_backoff_retries_until_upstream_recovers(monkeypatch):
    monkeypatch.setenv("METERLESS_MAX_RETRIES", "4")
    err = _http_error(502, NVIDIA_429_BODY)
    slept = []
    client = MeterlessClient(token="test-token")
    with patch("urllib.request.urlopen", side_effect=[err, _http_error(502, NVIDIA_429_BODY), _ok("recovered")]) as mock_open:
        with patch("time.sleep", side_effect=slept.append):
            assert client.chat_completion([{"role": "user", "content": "x"}]) == "recovered"
    assert mock_open.call_count == 3
    assert len(slept) == 2 and all(s > 0 for s in slept)


# =============================================================================
# 2. Explanations: one call per candidate set, cached per incident
# =============================================================================

def test_plan_explanations_use_one_call_and_survive_clock_drift(db_session: Session, monkeypatch):
    monkeypatch.setenv("METERLESS_API_KEY", "test-token")
    monkeypatch.setenv("AI_PROVIDER", "meterless")
    scn = _press_scenario(db_session, "scn_expl_batch")
    impact = detect_and_trace_impact(db_session, scn)
    plans = generate_candidate_plans(db_session, scn, use_ai=False)
    reply = json.dumps({p.plan_id: f"Rationale for {p.plan_id} grounded in the supplied cost and timeline figures." for p in plans})

    with patch.object(MeterlessClient, "chat_completion", return_value=f"<think>reasoning</think>{reply}") as chat:
        explain_recovery_plans(impact, plans, enabled=True)
        assert chat.call_count == 1
        assert all(p.explanation.startswith("[Meterless Runtime AI]") for p in plans)

        # Next simulated step: projected completions drift, explanation stays cached.
        step_scenario(db_session, scn, steps=1)
        later = generate_candidate_plans(db_session, scn, use_ai=False)
        explain_recovery_plans(detect_and_trace_impact(db_session, scn), later, enabled=True)
        assert chat.call_count == 1


def test_rate_limited_explanations_fall_back_without_hammering(db_session: Session, monkeypatch):
    monkeypatch.setenv("METERLESS_API_KEY", "test-token")
    monkeypatch.setenv("AI_PROVIDER", "meterless")
    scn = _press_scenario(db_session, "scn_expl_429")
    impact = detect_and_trace_impact(db_session, scn)
    plans = generate_candidate_plans(db_session, scn, use_ai=False)

    with patch.object(MeterlessClient, "chat_completion", side_effect=MeterlessRateLimitError("429", attempts=5)) as chat:
        explain_recovery_plans(impact, plans, enabled=True)
        explain_recovery_plans(impact, plans, enabled=True)  # polling client
    assert chat.call_count == 1  # failure is remembered briefly
    assert all("Operational Rationale" in p.explanation for p in plans)
    assert all(p.authorization_outcome == "AUTO_EXECUTE" for p in plans if p.plan_id == "plan_a_hydraulic_reseal")


# =============================================================================
# 3. Autonomous lifecycle
# =============================================================================

def test_routine_press_disruption_recovers_end_to_end_without_approval(db_session: Session):
    """Detect -> investigate -> select -> validate -> execute -> verify -> complete,
    driven exactly as the background auto-driver does, with no approval click."""
    scn = _press_scenario(db_session, "scn_e2e_press")
    scenario = db_session.get(SimulationScenario, scn)
    impact = detect_and_trace_impact(db_session, scn)
    assert impact.disruption_type == "reduced_machine_capacity"
    assert impact.severity == "medium"

    assert SimulationAutoDriver.should_attempt_autonomous_recovery(db_session, scenario) is True
    record = run_autonomous_recovery_lifecycle(db_session, scn, auto_advance_steps=False)
    db_session.refresh(scenario)

    assert record.final_status == "resolved"
    recovery = scenario.parameters["recovery"]
    assert recovery["autonomous"] is True
    assert recovery["decision"] == "approved"
    assert recovery["selected_plan"] in ("plan_a_hydraulic_reseal", "plan_b_press_liquor_buffer")
    assert recovery["status"] == "in_progress"
    assert recovery["incident_key"]

    # The handled incident is not re-run by the driver while the plan executes.
    assert SimulationAutoDriver.should_attempt_autonomous_recovery(db_session, scenario) is False

    # The clock advances the recovery to completion and the press is restored.
    step_scenario(db_session, scn, steps=recovery["duration_steps"])
    db_session.refresh(scenario)
    assert scenario.parameters["recovery"]["status"] == "completed"
    if recovery["selected_plan"] == "plan_a_hydraulic_reseal":
        press = scenario.parameters["snapshot"]["machines"]["machine:plant:PRESS-01"]
        assert press["status"] == "running" and press["rated_capacity_kg_h"] == 600.0
        assert detect_and_trace_impact(db_session, scn).evidence == []

    events = {e.event_type for e in db_session.query(OperationalEvent).filter(OperationalEvent.scenario_id == scn)}
    assert {"recovery.autonomous_executed", "recovery.lifecycle_completed", "recovery.completed"} <= events
    assert "recovery.approved" not in events  # no human approval was involved


def test_meterless_choice_changes_the_executed_plan(db_session: Session, monkeypatch):
    """With cloud jobs disabled, Meterless still decides via the chat endpoint; picking
    the non-default candidate changes what executes."""
    monkeypatch.setenv("METERLESS_API_KEY", "test-token")
    scn = _press_scenario(db_session, "scn_meterless_choice")
    reply = json.dumps({
        "selected_plan_id": "plan_b_press_liquor_buffer",
        "strategic_rationale": "Avoids stopping the press during the shift.",
        "trade_off_analysis": "Reseal is cheaper per hour but halts pressing.",
        "safety_assessment": "Press stays within hydraulic envelope at reduced rate.",
        "confidence_score": 0.8,
        "contingency_plan_id": "plan_a_hydraulic_reseal",
    })
    with patch.object(MeterlessClient, "preview_route", return_value={"raced": ["nvidia/nemotron-3-ultra-550b-a55b"]}), \
         patch.object(MeterlessClient, "chat_completion", return_value=reply) as chat, \
         patch.object(MeterlessClient, "submit_job") as submit_job:
        record = run_autonomous_recovery_lifecycle(db_session, scn, auto_advance_steps=False)

    decision = record.rounds[0].decision
    assert chat.call_count == 1
    assert submit_job.call_count == 0  # paid cloud jobs stay disabled
    assert decision["decision_source"] == "meterless_runtime"
    assert record.selected_plan_id == "plan_b_press_liquor_buffer"
    assert "NIGHTSHIFT_ALLOW_CLOUD_JOBS=false" in decision["uncertainty_notes"]


def test_meterless_selected_reseal_locks_out_press_then_restores_it(db_session: Session, monkeypatch):
    """Reproduces the live run: Meterless picks the reseal; the press must produce
    nothing while locked out, then return to 600 kg/h with the incident resolved."""
    monkeypatch.setenv("METERLESS_API_KEY", "test-token")
    scn = _press_scenario(db_session, "scn_reseal_live_repro")
    reply = json.dumps({"selected_plan_id": "plan_a_hydraulic_reseal", "strategic_rationale": "Fix root cause.",
                        "confidence_score": 0.9})
    with patch.object(MeterlessClient, "preview_route", return_value={}), \
         patch.object(MeterlessClient, "chat_completion", return_value=reply):
        record = run_autonomous_recovery_lifecycle(db_session, scn, auto_advance_steps=False)
    assert record.selected_plan_id == "plan_a_hydraulic_reseal"

    scenario = db_session.get(SimulationScenario, scn)
    batch_before = scenario.parameters["snapshot"]["batches"]["production_batch:mes:BATCH-104"]["actual_quantity_kg"]
    step_scenario(db_session, scn, steps=1)
    db_session.refresh(scenario)
    snap = scenario.parameters["snapshot"]
    assert snap["machines"]["machine:plant:PRESS-01"]["status"] == "maintenance"
    assert snap["batches"]["production_batch:mes:BATCH-104"]["actual_quantity_kg"] == batch_before

    duration = scenario.parameters["recovery"]["duration_steps"]
    step_scenario(db_session, scn, steps=duration)
    db_session.refresh(scenario)
    press = scenario.parameters["snapshot"]["machines"]["machine:plant:PRESS-01"]
    assert scenario.parameters["recovery"]["status"] == "completed"
    assert press["status"] == "running" and press["rated_capacity_kg_h"] == 600.0
    assert detect_and_trace_impact(db_session, scn).evidence == []
    assert all(i["status"] == "resolved" for i in scenario.parameters["active_incidents"])
    assert scenario.parameters["active_incidents"] == scenario.parameters["snapshot"]["active_incidents"]


def test_rate_limited_decision_falls_back_and_still_executes(db_session: Session, monkeypatch):
    monkeypatch.setenv("METERLESS_API_KEY", "test-token")
    scn = _press_scenario(db_session, "scn_decision_429")
    with patch.object(MeterlessClient, "preview_route", return_value={}), \
         patch.object(MeterlessClient, "chat_completion", side_effect=MeterlessRateLimitError("429", attempts=5)):
        record = run_autonomous_recovery_lifecycle(db_session, scn, auto_advance_steps=False)
    decision = record.rounds[0].decision
    assert decision["decision_source"] == "deterministic_fallback"
    assert "rate limited" in decision["fallback_reason"]
    assert record.final_status == "resolved"
    assert db_session.get(SimulationScenario, scn).parameters["recovery"]["status"] == "in_progress"


def test_concurrent_lifecycle_for_same_incident_is_rejected(db_session: Session):
    scn = _press_scenario(db_session, "scn_claim")
    scenario = db_session.get(SimulationScenario, scn)
    from datetime import datetime, timezone
    scenario.parameters = {
        **scenario.parameters,
        "recovery": {"status": "investigating", "lifecycle_id": "lc_other", "claimed_at": datetime.now(timezone.utc).isoformat()},
    }
    db_session.flush()
    with pytest.raises(LifecycleAlreadyRunning):
        run_autonomous_recovery_lifecycle(db_session, scn, auto_advance_steps=False)
    assert SimulationAutoDriver.should_attempt_autonomous_recovery(db_session, scenario) is False


def test_stale_claim_is_reclaimed_not_stuck(db_session: Session):
    scn = _press_scenario(db_session, "scn_stale_claim")
    scenario = db_session.get(SimulationScenario, scn)
    scenario.parameters = {
        **scenario.parameters,
        "recovery": {"status": "investigating", "lifecycle_id": "lc_dead", "claimed_at": "2026-01-01T00:00:00+00:00"},
    }
    db_session.flush()
    record = run_autonomous_recovery_lifecycle(db_session, scn, auto_advance_steps=False)
    assert record.final_status == "resolved"


def test_lifecycle_failures_back_off_then_escalate_to_human(db_session: Session):
    scn = _press_scenario(db_session, "scn_failures")
    scenario = db_session.get(SimulationScenario, scn)
    with patch("app.services.autonomous_recovery._run_autonomous_recovery_lifecycle_impl", side_effect=RuntimeError("boom")):
        for attempt in range(1, MAX_LIFECYCLE_FAILURES + 1):
            with pytest.raises(RuntimeError):
                run_autonomous_recovery_lifecycle(db_session, scn, auto_advance_steps=False)
            rec = db_session.get(SimulationScenario, scn).parameters["recovery"]
            if attempt < MAX_LIFECYCLE_FAILURES:
                assert rec["status"] == "awaiting_decision" and rec["next_attempt_at"]
                assert SimulationAutoDriver.should_attempt_autonomous_recovery(db_session, scenario) is False
                rec_params = dict(scenario.parameters)
                rec_params["recovery"] = {**rec, "next_attempt_at": None}
                scenario.parameters = rec_params
                db_session.flush()
    rec = db_session.get(SimulationScenario, scn).parameters["recovery"]
    assert rec["status"] == "pending_approval"
    assert "human decision required" in rec["escalation_reason"]


def test_critical_mill_failure_without_sop_waits_for_approval(db_session: Session):
    seed_baseline_factory(db_session)
    create_scenario(db_session, name="Mill burnout", disruption_type="equipment_failure", scenario_id="scn_mill_gate")
    step_scenario(db_session, "scn_mill_gate", steps=3)
    impact = detect_and_trace_impact(db_session, "scn_mill_gate")
    assert impact.severity == "critical"
    record = run_autonomous_recovery_lifecycle(db_session, "scn_mill_gate", auto_advance_steps=False)
    assert record.final_status == "pending_approval"
    rec = db_session.get(SimulationScenario, "scn_mill_gate").parameters["recovery"]
    assert rec["status"] == "pending_approval"
    assert rec.get("decision") is None
    machines = db_session.get(SimulationScenario, "scn_mill_gate").parameters["snapshot"]["machines"]
    assert machines["machine:plant:MILL-01"]["status"] == "failed"  # nothing executed


def test_contamination_plans_respect_food_safety(db_session: Session):
    seed_baseline_factory(db_session)
    create_scenario(db_session, name="Moisture", disruption_type="quality_contamination", scenario_id="scn_quarantine")
    step_scenario(db_session, "scn_quarantine", steps=3)
    plans = {p.plan_id: p for p in generate_candidate_plans(db_session, "scn_quarantine", use_ai=False)}
    assert set(plans) == {"plan_spot_market_buffer", "plan_recipe_line_throttle"}
    assert plans["plan_recipe_line_throttle"].authorization_outcome == "BLOCKED"
    assert plans["plan_spot_market_buffer"].authorization_outcome == "REQUIRES_APPROVAL"
    assert "QC" in plans["plan_spot_market_buffer"].authorization_reason


def test_roaster_flush_infeasible_above_seizure_threshold(db_session: Session):
    seed_baseline_factory(db_session)
    create_scenario(db_session, name="Hot roaster", disruption_type="none", scenario_id="scn_seizure")
    apply_disruption(db_session, "scn_seizure", "roaster_bearing_overheat", {"temperature_c": 155.0, "vibration_rms": 4.0})
    plans = {p.plan_id: p for p in generate_candidate_plans(db_session, "scn_seizure", use_ai=False)}
    assert plans["plan_a_thermal_flush"].feasibility_verdict == "infeasible"
    assert plans["plan_a_thermal_flush"].authorization_outcome == "BLOCKED"


def test_legacy_scenarios_are_not_picked_up_by_the_driver(db_session: Session):
    seed_baseline_factory(db_session)
    create_scenario(
        db_session, name="Legacy", disruption_type="reduced_machine_capacity",
        scenario_id="scn_legacy", autonomous_recovery=False,
    )
    step_scenario(db_session, "scn_legacy", steps=3)
    scenario = db_session.get(SimulationScenario, "scn_legacy")
    assert detect_and_trace_impact(db_session, "scn_legacy").evidence
    assert SimulationAutoDriver.should_attempt_autonomous_recovery(db_session, scenario) is False


def test_severities_differ_by_evidence_and_agree_across_api(db_session: Session):
    from fastapi.testclient import TestClient
    from app.api.simulations import get_db
    from app.main import app

    seed_baseline_factory(db_session)
    expected = {}
    for dtype in ("reduced_machine_capacity", "quality_contamination", "equipment_failure"):
        sid = f"scn_sev_{dtype[:10]}"
        create_scenario(db_session, name=dtype, disruption_type=dtype, scenario_id=sid)
        step_scenario(db_session, sid, steps=3)
        expected[sid] = detect_and_trace_impact(db_session, sid).severity
    assert set(expected.values()) == {"medium", "high", "critical"}

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        for sid, sev in expected.items():
            assert client.get(f"/simulations/{sid}/incidents").json()["severity"] == sev
            assert client.get(f"/simulations/{sid}/recovery/impact").json()["severity"] == sev
            incidents = client.get(f"/simulations/{sid}").json()["parameters"]["active_incidents"]
            assert incidents[-1]["severity"] == sev
        catalogue = client.get(f"/simulations/{sid}/disruptions/catalogue").json()["disruptions"]
        assert all(item.get("severity") in ("low", "medium", "high", "critical") for item in catalogue)
    finally:
        app.dependency_overrides.clear()
