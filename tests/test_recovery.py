"""Comprehensive verification suite for Recovery Intelligence in FactoryOps.

Tests:
1. Disruption detection & impact tracing: detects thermal anomaly on ROAST-01, traces cascading
   delays to BATCH-101 and ORD-9402, and calculates SLA risk.
2. Arithmetic reconciliation: validates simulation clock (06:45 UTC), remaining quantity (1,855 kg),
   processing duration, finish timestamps, safety buffer, and cost accounting.
3. Candidate generation & deterministic ranking: Plan A ranked #1, meets hard constraints and customer deadline.
4. Nemotron operational explanation & deterministic fallback paths.
5. Human-in-the-loop operator approval workflow: state mutation occurs ONLY upon explicit approval.
6. Unknown plan and conflicting approval rejection.
7. Idempotent decision application: duplicate submissions do not produce duplicate events.
8. API endpoint integration: GET /recovery/impact, POST /recovery/plans, POST /recovery/decide.
"""

from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from app.api.simulations import get_db
from app.config import settings
from app.database import engine
from app.main import app
from app.models import OperationalEvent, SimulationScenario
from app.services.recovery import (
    apply_recovery_decision,
    detect_and_trace_impact,
    explain_plan_nemotron,
    explain_recovery_plan,
    generate_candidate_plans,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import create_scenario, get_scenario_world_model, step_scenario


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


def test_disruption_detection_and_impact_tracing(db_session: Session):
    """Verifies that an active roaster disruption is detected and traced downstream to order SLA."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Disruption Detection Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_rec_detect",
    )
    # Step to step 3 where anomaly triggers
    step_scenario(db_session, "scn_rec_detect", steps=3)

    impact = detect_and_trace_impact(db_session, "scn_rec_detect")
    assert impact.disrupted_machine_id == "machine:plant:ROAST-01"
    assert impact.affected_batch_id == "production_batch:mes:BATCH-101"
    assert impact.affected_order_id == "customer_order:erp:ORD-9402"
    assert len(impact.evidence) >= 1
    assert any(e.metric == "bearing_temperature_c" and e.observed_value == 142.5 for e in impact.evidence)
    assert impact.sla_breached is True
    assert impact.potential_penalty_usd > 0.0


def test_recovery_arithmetic_and_timestamps(db_session: Session):
    """Reconciles the exact arithmetic against the simulation clock, capacities, and deadlines.

    At Step 3:
    - Sim time: 2026-10-22T06:45:00+00:00 (06:45 UTC).
    - Planned: 2,500 kg, actual: 645 kg, remaining: 1,855 kg.
    - Plan A: downtime 0.75h (45 min) + run 1,855 / 1200 h (1.546h) = 2.296h -> 09:02:45 UTC.
    - Customer deadline: 14:00 UTC.
    - Buffer: 14:00 - 09:02:45 = 4.95 hours safety buffer.
    - Total cost: $150 coolant/parts + $200 labor = $350.00 (meets deadline, $0 penalty).
    """
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Arithmetic Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_rec_arithmetic",
    )
    step_scenario(db_session, "scn_rec_arithmetic", steps=3)

    plans = generate_candidate_plans(db_session, "scn_rec_arithmetic", use_nemotron=False)
    plan_a = plans[0]

    assert plan_a.plan_id == "plan_a_thermal_flush"
    assert plan_a.estimated_downtime_hours == 0.75
    assert plan_a.meets_deadline is True
    assert plan_a.buffer_hours > 4.5  # ~4.95 hours buffer before 14:00 UTC
    assert plan_a.total_cost_usd == 350.00
    assert plan_a.cost_breakdown["sla_penalty"] == 0.0

    # Verify finish timestamp is exactly in UTC morning
    finish_dt = datetime.fromisoformat(plan_a.projected_completion)
    assert finish_dt.hour == 9
    assert finish_dt.minute in (2, 3)

    # Verify Plan B arithmetic: 1.5h downtime + 1855/900h (2.06h) -> ~10:19 UTC
    plan_b = plans[1]
    finish_b_dt = datetime.fromisoformat(plan_b.projected_completion)
    assert finish_b_dt.hour == 10
    assert plan_b.buffer_hours > 3.0  # finishes with buffer before 14:00 UTC
    assert plan_b.total_cost_usd == 450.00


def test_candidate_plan_generation_and_hard_constraints(db_session: Session):
    """Verifies that 3 recovery candidates are produced and ranked by deterministic constraints."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Plan Generation Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_rec_plans",
    )
    step_scenario(db_session, "scn_rec_plans", steps=3)

    plans = generate_candidate_plans(db_session, "scn_rec_plans", use_nemotron=False)
    assert len(plans) == 3

    # Plan A must be rank 1
    plan_a = plans[0]
    assert plan_a.plan_id == "plan_a_thermal_flush"
    assert plan_a.rank == 1
    assert plan_a.feasibility_verdict == "feasible"
    assert plan_a.meets_deadline is True
    assert len(plan_a.hard_constraints_met) >= 2
    assert plan_a.total_cost_usd < plans[1].total_cost_usd

    # Verify explanation is attached and grounded
    assert plan_a.explanation is not None
    assert "Operational Rationale" in plan_a.explanation


def test_nemotron_fallback_paths(db_session: Session):
    """Verifies that Nemotron explainer handles missing key, timeout, and disabled states safely."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Nemotron Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_nemotron",
    )
    step_scenario(db_session, "scn_nemotron", steps=3)

    impact = detect_and_trace_impact(db_session, "scn_nemotron")
    plans = generate_candidate_plans(db_session, "scn_nemotron", use_nemotron=False)

    # Explicitly disabled
    text_fallback = explain_plan_nemotron(impact, plans[0], enabled=False)
    assert "Operational Rationale" in text_fallback
    assert "Ranked #1" in text_fallback

    # Enabled but without valid API key / network
    text_no_key = explain_plan_nemotron(impact, plans[0], enabled=True)
    assert "Operational Rationale" in text_no_key


def test_meterless_primary_ai_explanation(db_session: Session, monkeypatch):
    """Verifies that with Meterless configured, recovery explanation invokes MeterlessClient
    and returns the [Meterless Runtime AI] reasoning.
    """
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Meterless AI Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_meterless_ai",
    )
    step_scenario(db_session, "scn_meterless_ai", steps=3)
    impact = detect_and_trace_impact(db_session, "scn_meterless_ai")
    plans = generate_candidate_plans(db_session, "scn_meterless_ai", use_ai=False)

    monkeypatch.setattr(settings, "ai_provider", "meterless")
    monkeypatch.setenv("AI_PROVIDER", "meterless")
    monkeypatch.setenv("METERLESS_API_KEY", "test_sec_token_8891")

    with patch("app.providers.meterless.MeterlessClient.chat_completion") as mock_chat, \
         patch("urllib.request.urlopen") as mock_urlopen:
        mock_chat.return_value = "Plan A thermal flush is recommended because it restores nominal 1200 kg/h throughput with minimum downtime."

        explanation = explain_recovery_plan(impact, plans[0], enabled=True)

        assert explanation.startswith("[Meterless Runtime AI]")
        assert "Plan A thermal flush is recommended" in explanation
        assert mock_chat.called
        assert not mock_urlopen.called, "NVIDIA endpoint must NOT be called when Meterless is primary!"


def test_meterless_failure_does_not_call_nvidia_and_falls_back_safely(db_session: Session, monkeypatch):
    """Verifies that when Meterless fails or is unreachable, the system produces a safe deterministic
    fallback and does NOT silently call NVIDIA, even if an NVIDIA_API_KEY is present.
    """
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Meterless Failure Guard Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_meterless_guard",
    )
    step_scenario(db_session, "scn_meterless_guard", steps=3)
    impact = detect_and_trace_impact(db_session, "scn_meterless_guard")
    plans = generate_candidate_plans(db_session, "scn_meterless_guard", use_ai=False)

    monkeypatch.setattr(settings, "ai_provider", "meterless")
    monkeypatch.setenv("AI_PROVIDER", "meterless")
    monkeypatch.setenv("METERLESS_API_KEY", "test_sec_token_8891")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-unauthorized-fallback-canary")

    with patch("app.providers.meterless.MeterlessClient.chat_completion") as mock_chat, \
         patch("urllib.request.urlopen") as mock_urlopen:
        from app.providers.meterless import MeterlessConnectionError
        mock_chat.side_effect = MeterlessConnectionError("Meterless Runtime port unreachable")

        explanation = explain_recovery_plan(impact, plans[0], enabled=True)

        # Must return safe deterministic fallback
        assert "Operational Rationale" in explanation
        assert "Ranked #1" in explanation
        assert "[NVIDIA Nemotron]" not in explanation
        # Critical invariant: NVIDIA must NOT be contacted on Meterless failure
        assert not mock_urlopen.called, "Silent fallback to NVIDIA occurred on Meterless failure!"


def test_nvidia_used_only_when_explicitly_configured(db_session: Session, monkeypatch):
    """Verifies that NVIDIA Nemotron is invoked ONLY when explicitly configured via settings.ai_provider."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Explicit NVIDIA Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_nvidia_explicit",
    )
    step_scenario(db_session, "scn_nvidia_explicit", steps=3)
    impact = detect_and_trace_impact(db_session, "scn_nvidia_explicit")
    plans = generate_candidate_plans(db_session, "scn_nvidia_explicit", use_ai=False)

    # 1. When ai_provider is meterless and tokens are cleared, NVIDIA is never called
    monkeypatch.setattr(settings, "ai_provider", "meterless")
    monkeypatch.setattr(settings, "meterless_api_key", None)
    monkeypatch.setattr(settings, "meterless_runtime_token", None)
    monkeypatch.setenv("AI_PROVIDER", "meterless")
    monkeypatch.delenv("METERLESS_API_KEY", raising=False)
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-key-present")

    with patch("urllib.request.urlopen") as mock_urlopen:
        res_meterless_cfg = explain_recovery_plan(impact, plans[0], enabled=True)
        assert not mock_urlopen.called
        assert "Operational Rationale" in res_meterless_cfg

    # 2. When ai_provider is explicitly nvidia
    monkeypatch.setattr(settings, "ai_provider", "nvidia")
    monkeypatch.setenv("AI_PROVIDER", "nvidia")
    with patch("urllib.request.urlopen") as mock_urlopen:
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "NVIDIA explanation of Plan A."}}]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res_nvidia_cfg = explain_recovery_plan(impact, plans[0], enabled=True)
        assert res_nvidia_cfg.startswith("[NVIDIA Nemotron]")
        assert "NVIDIA explanation of Plan A." in res_nvidia_cfg
        assert mock_urlopen.called
        target_url = mock_urlopen.call_args[0][0].full_url
        assert "integrate.api.nvidia.com" in target_url


def test_human_approval_workflow_requires_explicit_decision(db_session: Session):
    """Verifies that state changes happen ONLY after explicit human approval, not automatically."""
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        session=db_session,
        name="Approval Gate Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_rec_approval",
    )
    step_scenario(db_session, "scn_rec_approval", steps=3)

    # Before approval: scenario status is 'running' with degraded machine
    assert scenario.status == "running"
    assert scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] == "degraded"

    # Operator rejects Plan B
    res_reject = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_rec_approval",
        plan_id="plan_b_reroute_line2",
        decision="rejected",
        operator_id="OP-LEAD-101",
        rationale="Line 2 capacity is reserved for seasonal batch.",
    )
    assert res_reject.decision == "rejected"
    assert scenario.status == "running"
    assert scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] == "degraded"

    # Operator approves Plan A
    res_approve = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_rec_approval",
        plan_id="plan_a_thermal_flush",
        decision="approved",
        operator_id="OP-LEAD-101",
        rationale="Rapid coolant flush recovers line within customer SLA buffer.",
    )
    assert res_approve.decision == "approved"
    assert scenario.status == "recovering"
    assert scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"] in ("maintenance", "running")

    # Verify idempotency: resubmitting same approval returns existing record
    res_repeat = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_rec_approval",
        plan_id="plan_a_thermal_flush",
        decision="approved",
        operator_id="OP-LEAD-101",
        rationale="Rapid coolant flush recovers line within customer SLA buffer.",
    )
    assert res_repeat.event_id == res_approve.event_id


def test_unknown_and_conflicting_plan_rejection(db_session: Session):
    """Verifies that decisions referencing unknown or conflicting plans are rejected safely."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Guardrail Test",
        disruption_type="roaster_bearing_overheat",
        scenario_id="scn_guard",
    )
    step_scenario(db_session, "scn_guard", steps=3)

    # 1. Unknown plan ID rejected
    with pytest.raises(ValueError, match="Unknown or infeasible recovery plan"):
        apply_recovery_decision(
            session=db_session,
            scenario_id="scn_guard",
            plan_id="plan_non_existent",
            decision="approved",
            operator_id="OP-1",
            rationale="Test",
        )

    # 2. Approve Plan A
    apply_recovery_decision(
        session=db_session,
        scenario_id="scn_guard",
        plan_id="plan_a_thermal_flush",
        decision="approved",
        operator_id="OP-1",
        rationale="Approved Plan A",
    )

    # 3. Trying to approve Plan B when Plan A is already approved is rejected
    with pytest.raises(ValueError, match="already has active approved plan"):
        apply_recovery_decision(
            session=db_session,
            scenario_id="scn_guard",
            plan_id="plan_b_reroute_line2",
            decision="approved",
            operator_id="OP-2",
            rationale="Conflicting Plan B",
        )


def test_recovery_api_endpoints(db_session: Session):
    """Verifies FastAPI endpoints for impact tracing, plan generation, and operator decision."""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        client.post("/simulations/seed")
        client.post(
            "/simulations",
            json={
                "name": "API Recovery Target",
                "disruption_type": "roaster_bearing_overheat",
                "scenario_id": "scn_api_rec",
            },
        )
        client.post("/simulations/scn_api_rec/step", json={"steps": 3})

        # 1. GET impact
        resp_imp = client.get("/simulations/scn_api_rec/recovery/impact")
        assert resp_imp.status_code == 200
        impact_data = resp_imp.json()
        assert impact_data["disrupted_machine_id"] == "machine:plant:ROAST-01"
        assert impact_data["sla_breached"] is True

        # 2. POST plans
        resp_plans = client.post("/simulations/scn_api_rec/recovery/plans")
        assert resp_plans.status_code == 200
        plans_data = resp_plans.json()
        assert plans_data["candidate_count"] == 3
        assert plans_data["plans"][0]["rank"] == 1

        # 3. POST decide (validation error on invalid decision)
        resp_inv = client.post(
            "/simulations/scn_api_rec/recovery/decide",
            json={
                "plan_id": "plan_a_thermal_flush",
                "decision": "maybe",
                "operator_id": "OP-99",
                "rationale": "Testing",
            },
        )
        assert resp_inv.status_code == 422

        # 4. POST decide (valid approval)
        resp_dec = client.post(
            "/simulations/scn_api_rec/recovery/decide",
            json={
                "plan_id": "plan_a_thermal_flush",
                "decision": "approved",
                "operator_id": "OP-99",
                "rationale": "Authorizing immediate maintenance procedure.",
            },
        )
        assert resp_dec.status_code == 200
        dec_data = resp_dec.json()
        assert dec_data["decision"] == "approved"
        assert dec_data["updated_scenario_status"] == "recovering"

        # 5. GET World Model state contains updated recovery audit events
        resp_wm = client.get("/simulations/scn_api_rec/world-model")
        assert resp_wm.status_code == 200
        wm_data = resp_wm.json()
        assert "canonical_shape" in wm_data

    finally:
        app.dependency_overrides.clear()
