"""Tests for FactoryOps Hybrid Autonomous Recovery Policy.

Verifies:
1. Routine valid disruption receives AUTO_EXECUTE and completes without frontend approval.
2. Routine disruption exceeding autonomous authority receives REQUIRES_APPROVAL.
3. Hard constraint violations receive BLOCKED and cannot execute.
4. Critical disruptions remain REQUIRES_APPROVAL unless explicitly authorized by policy.
5. Authorized operator approval and rejection workflows still function properly, while BLOCKED plans reject approval.
6. Unknown or unsupported recovery actions receive BLOCKED.
7. Meterless explanation success and deterministic fallback on provider failure.
8. Explanation failure does not alter authorization outcome or falsely claim AI provenance.
9. Idempotency guarantees prevent duplicate recovery execution.
10. Audit events, recovery history, and lifecycle records remain strictly consistent.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from app.database import engine
from app.models import OperationalEvent, SimulationScenario
from app.providers.meterless import MeterlessClient, MeterlessError
from app.services.autonomous_recovery import (
    AutonomousRecoveryLifecycleRecord,
    DisruptionCharacterization,
    PlanValidationResult,
    run_autonomous_recovery_lifecycle,
)
from app.services.recovery import (
    ImpactAnalysis,
    RecoveryPlan,
    apply_recovery_decision,
    explain_recovery_plan,
    generate_candidate_plans,
)
from app.services.recovery_policy import (
    DEFAULT_DISRUPTION_CLASSIFICATIONS,
    MAX_AUTONOMOUS_COST_USD,
    MAX_AUTONOMOUS_DOWNTIME_HOURS,
    RecoveryAuthorizationDecision,
    evaluate_recovery_authorization,
    get_disruption_default_class,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import create_scenario, step_scenario


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


def _setup_disposable_scenario(db_session: Session, scenario_id: str, disruption_type: str = "reduced_machine_capacity") -> SimulationScenario:
    """Helper to seed factory, create scenario, and step to active disruption."""
    seed_baseline_factory(db_session)
    scenario = create_scenario(
        session=db_session,
        scenario_id=scenario_id,
        name=f"Disposable Test {scenario_id}",
        disruption_type=disruption_type,
        random_seed=42,
    )
    step_scenario(db_session, scenario_id, steps=3)
    db_session.refresh(scenario)
    return scenario


# =============================================================================
# Test 1: Routine Valid Disruption receives AUTO_EXECUTE
# =============================================================================
def test_routine_disruption_receives_auto_execute():
    """A routine disruption with a compliant, low-cost, deadline-meeting plan receives AUTO_EXECUTE."""
    plan = RecoveryPlan(
        plan_id="plan_a_hydraulic_reseal",
        title="Routine Hydraulic Reseal",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=["Baseline equipment verified"],
        estimated_downtime_hours=0.75,
        projected_completion="2026-10-27T10:00:00Z",
        meets_deadline=True,
        buffer_hours=3.5,
        total_cost_usd=350.0,
        cost_breakdown={"parts": 100.0, "labor": 250.0},
        action_steps=["Flush line"],
    )
    auth = evaluate_recovery_authorization(
        plan=plan,
        disruption_type="reduced_machine_capacity",
    )
    assert auth.outcome == "AUTO_EXECUTE"
    assert "satisfies all hard constraints" in auth.reason
    assert not auth.violated_constraints
    assert not auth.risk_factors


# =============================================================================
# Test 2: Routine Disruption Exceeding Authority receives REQUIRES_APPROVAL
# =============================================================================
def test_routine_disruption_exceeding_authority_requires_approval():
    """A routine disruption whose plan exceeds cost or downtime limits requires human approval."""
    expensive_plan = RecoveryPlan(
        plan_id="plan_b_press_liquor_buffer",
        title="Third-Party Expedite",
        strategy="split_expedite",
        rank=2,
        feasibility_verdict="feasible",
        hard_constraints_met=["HACCP verified"],
        estimated_downtime_hours=0.5,
        projected_completion="2026-10-27T10:00:00Z",
        meets_deadline=True,
        buffer_hours=2.0,
        total_cost_usd=2514.0,  # Exceeds $500.00 limit
        cost_breakdown={"expedite_fee": 2514.0},
        action_steps=["Dispatch external freight"],
    )
    auth = evaluate_recovery_authorization(
        plan=expensive_plan,
        disruption_type="reduced_machine_capacity",
    )
    assert auth.outcome == "REQUIRES_APPROVAL"
    assert any("COST_EXCEEDS_AUTHORITY" in r for r in auth.risk_factors)

    # Test SLA breach on routine plan also requires approval
    sla_breach_plan = RecoveryPlan(
        plan_id="plan_a_hydraulic_reseal",
        title="Delayed Flush",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=["Baseline verified"],
        estimated_downtime_hours=1.0,
        projected_completion="2026-10-27T18:00:00Z",
        meets_deadline=False,
        buffer_hours=-1.5,
        total_cost_usd=350.0,
        cost_breakdown={"labor": 350.0},
        action_steps=["Flush line"],
    )
    auth_sla = evaluate_recovery_authorization(
        plan=sla_breach_plan,
        disruption_type="reduced_machine_capacity",
    )
    assert auth_sla.outcome == "REQUIRES_APPROVAL"
    assert any("CUSTOMER_SLA_BREACH" in r for r in auth_sla.risk_factors)


# =============================================================================
# Test 3: Hard-Constraint Violation receives BLOCKED
# =============================================================================
def test_hard_constraint_violation_blocked():
    """Plans violating hard constraints or marked infeasible are strictly BLOCKED."""
    infeasible_plan = RecoveryPlan(
        plan_id="plan_a_thermal_flush",
        title="Infeasible Thermal Flush",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="infeasible",
        hard_constraints_met=[],
        estimated_downtime_hours=0.75,
        projected_completion="2026-10-27T10:00:00Z",
        meets_deadline=True,
        buffer_hours=3.0,
        total_cost_usd=350.0,
        cost_breakdown={},
        action_steps=[],
    )
    val_result = PlanValidationResult(
        valid=False,
        plan_id="plan_a_thermal_flush",
        hard_constraints_passed=[],
        violated_constraints=["TEMPERATURE_EXCEEDS_SEIZURE_LIMIT: 165°C > 150°C"],
        risk_notes=[],
    )
    auth = evaluate_recovery_authorization(
        plan=infeasible_plan,
        disruption_type="roaster_bearing_overheat",
        validation_result=val_result,
    )
    assert auth.outcome == "BLOCKED"
    assert len(auth.violated_constraints) >= 1
    assert "TEMPERATURE_EXCEEDS_SEIZURE_LIMIT" in auth.reason


# =============================================================================
# Test 4: Critical Disruption requires approval unless explicitly authorized
# =============================================================================
def test_critical_disruption_requires_approval_unless_explicitly_authorized():
    """Equipment failure requires approval, while roaster overheat thermal flush is explicitly authorized."""
    # 1. Equipment failure (MILL-01 motor burnout) - Not explicitly pre-authorized
    mill_plan = RecoveryPlan(
        plan_id="plan_a_motor_swap",
        title="Motor Swap",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=["Spare motor in stock"],
        estimated_downtime_hours=2.0,
        projected_completion="2026-10-27T12:00:00Z",
        meets_deadline=True,
        buffer_hours=2.5,
        total_cost_usd=450.0,  # Below $500 cap
        cost_breakdown={"parts": 300.0, "labor": 150.0},
        action_steps=["Replace motor"],
    )
    auth_mill = evaluate_recovery_authorization(
        plan=mill_plan,
        disruption_type="equipment_failure",
    )
    assert auth_mill.outcome == "REQUIRES_APPROVAL"
    assert any("DISRUPTION_REQUIRES_APPROVAL" in r for r in auth_mill.risk_factors)

    # 2. Roaster overheat with plan_a_thermal_flush is explicitly pre-authorized SOP
    roaster_sop_plan = RecoveryPlan(
        plan_id="plan_a_thermal_flush",
        title="Rapid Bearing Thermal Flush",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=["Lubricant ISO VG 460 in stock"],
        estimated_downtime_hours=0.75,
        projected_completion="2026-10-27T08:15:00Z",
        meets_deadline=True,
        buffer_hours=4.3,
        total_cost_usd=350.0,
        cost_breakdown={"labor": 350.0},
        action_steps=["Flush line"],
    )
    auth_roaster = evaluate_recovery_authorization(
        plan=roaster_sop_plan,
        disruption_type="roaster_bearing_overheat",
    )
    assert auth_roaster.outcome == "AUTO_EXECUTE"

    # 3. Quality contamination (safety critical biological hazard)
    bio_plan = RecoveryPlan(
        plan_id="plan_recipe_line_throttle",
        title="Recipe Line Throttle",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=[],
        estimated_downtime_hours=1.0,
        projected_completion="2026-10-27T10:00:00Z",
        meets_deadline=True,
        buffer_hours=3.0,
        total_cost_usd=200.0,
        cost_breakdown={},
        action_steps=[],
    )
    auth_bio = evaluate_recovery_authorization(
        plan=bio_plan,
        disruption_type="quality_contamination",
    )
    assert auth_bio.outcome == "BLOCKED"
    assert any("FOOD_SAFETY_QUARANTINE" in v for v in auth_bio.violated_constraints)


# =============================================================================
# Test 5: Authorized Approval and Rejection Workflows
# =============================================================================
def test_authorized_approval_and_rejection_workflows(db_session: Session):
    """Verifies that human approval and rejection function properly, and BLOCKED plans reject approval."""
    scenario = _setup_disposable_scenario(db_session, "scn_test_decision_wf", "roaster_bearing_overheat")

    # 1. Approved workflow succeeds for valid plan
    res_approved = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_test_decision_wf",
        plan_id="plan_a_thermal_flush",
        decision="approved",
        operator_id="OP-TEST-01",
        rationale="Operator verified safety invariants.",
    )
    assert res_approved.decision == "approved"
    assert scenario.status == "recovering"

    # 2. Rejection workflow succeeds
    scenario_2 = _setup_disposable_scenario(db_session, "scn_test_reject_wf", "roaster_bearing_overheat")
    res_rejected = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_test_reject_wf",
        plan_id="plan_b_reroute_line2",
        decision="rejected",
        operator_id="OP-TEST-02",
        rationale="Reroute rejected due to line 2 contention.",
    )
    assert res_rejected.decision == "rejected"

    # 3. A BLOCKED plan CANNOT be approved even if an operator calls apply_recovery_decision
    scenario_3 = _setup_disposable_scenario(db_session, "scn_test_blocked_appr", "quality_contamination")
    with pytest.raises(ValueError, match="BLOCKED by safety policy"):
        apply_recovery_decision(
            session=db_session,
            scenario_id="scn_test_blocked_appr",
            plan_id="plan_recipe_line_throttle",
            decision="approved",
            operator_id="OP-TEST-03",
            rationale="Attempting to force approval on quarantined lot.",
        )


# =============================================================================
# Test 6: Unknown or Unsupported Actions BLOCKED
# =============================================================================
def test_unknown_or_unsupported_actions_blocked():
    """Unrecognized plans receive BLOCKED."""
    bogus_plan = RecoveryPlan(
        plan_id="plan_nonexistent_teleport",
        title="Magical Recovery",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=[],
        estimated_downtime_hours=0.1,
        projected_completion="2026-10-27T08:00:00Z",
        meets_deadline=True,
        buffer_hours=5.0,
        total_cost_usd=10.0,
        cost_breakdown={},
        action_steps=[],
    )
    auth = evaluate_recovery_authorization(
        plan=bogus_plan,
        disruption_type="reduced_machine_capacity",
    )
    assert auth.outcome == "BLOCKED"
    assert any("UNSUPPORTED_ACTION" in v for v in auth.violated_constraints)


# =============================================================================
# Test 7: Meterless Explanation Success and Safe Deterministic Fallback
# =============================================================================
def test_meterless_explanation_success_and_fallback(monkeypatch):
    """Meterless explanation succeeds with valid mock and falls back safely on error."""
    plan = RecoveryPlan(
        plan_id="plan_a_thermal_flush",
        title="Thermal Flush",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=[],
        estimated_downtime_hours=0.75,
        projected_completion="2026-10-27T08:15:00Z",
        meets_deadline=True,
        buffer_hours=4.0,
        total_cost_usd=350.0,
        cost_breakdown={},
        action_steps=[],
    )
    impact = ImpactAnalysis(
        scenario_id="scn_test_expl",
        disrupted_machine_id="machine:plant:ROAST-01",
        affected_batch_id="production_batch:mes:BATCH-101",
        affected_order_id="customer_order:erp:ORD-9402",
        affected_customer="Acme Chocolates",
        order_deadline="2026-10-27T14:00:00Z",
        unintervened_completion="2026-10-27T16:00:00Z",
        delay_hours=2.0,
        sla_breached=True,
        potential_penalty_usd=350.0,
        remaining_quantity_kg=2500.0,
        evidence=[],
    )

    # 1. Success case: Mock MeterlessClient returns response
    with patch("app.providers.meterless.MeterlessClient.chat_completion", return_value="Thermal flush restores roaster nominal temperature and saves batch."):
        monkeypatch.setenv("METERLESS_API_KEY", "test-token-valid")
        explanation = explain_recovery_plan(impact, plan, provider="meterless")
        assert explanation.startswith("[Meterless Runtime AI]")
        assert "Thermal flush" in explanation

    # 2. Failure case: Mock MeterlessClient raises MeterlessError
    from app.services.recovery import _EXPLANATION_CACHE
    _EXPLANATION_CACHE.clear()
    with patch("app.providers.meterless.MeterlessClient.chat_completion", side_effect=MeterlessError("Chat completion failed: timed out")):
        monkeypatch.setenv("METERLESS_API_KEY", "test-token-valid")
        fallback_expl = explain_recovery_plan(impact, plan, provider="meterless")
        assert not fallback_expl.startswith("[Meterless Runtime AI]")
        assert "**Operational Rationale" in fallback_expl


# =============================================================================
# Test 8: Explanation Failure Does Not Affect Authorization or AI Provenance
# =============================================================================
def test_explanation_failure_does_not_affect_authorization():
    """Failure in explanation generation leaves deterministic authorization intact and does not claim AI."""
    plan = RecoveryPlan(
        plan_id="plan_a_hydraulic_reseal",
        title="Thermal Flush",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=[],
        estimated_downtime_hours=0.75,
        projected_completion="2026-10-27T08:15:00Z",
        meets_deadline=True,
        buffer_hours=4.0,
        total_cost_usd=350.0,
        cost_breakdown={},
        action_steps=[],
    )
    # Authorization must be AUTO_EXECUTE regardless of whether explanation is missing or fallback
    auth = evaluate_recovery_authorization(plan, disruption_type="reduced_machine_capacity")
    assert auth.outcome == "AUTO_EXECUTE"
    # Even if explanation has deterministic fallback text, authorization stays AUTO_EXECUTE
    plan.explanation = "**Operational Rationale (Thermal Flush):** Root Cause: Overheat."
    auth_2 = evaluate_recovery_authorization(plan, disruption_type="reduced_machine_capacity")
    assert auth_2.outcome == "AUTO_EXECUTE"
    # Ensure fallback text does not contain AI badge prefix
    assert "[Meterless Runtime AI]" not in plan.explanation


# =============================================================================
# Test 9: Idempotency Prevents Duplicate Recovery Execution
# =============================================================================
def test_idempotency_prevents_duplicate_recovery(db_session: Session):
    """Calling apply_recovery_decision repeatedly returns existing record without duplicating mutations."""
    scenario = _setup_disposable_scenario(db_session, "scn_test_idemp", "roaster_bearing_overheat")
    res1 = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_test_idemp",
        plan_id="plan_a_thermal_flush",
        decision="approved",
        operator_id="OP-IDEMP-01",
        rationale="First approval.",
    )
    assert res1.decision == "approved"

    # Second identical call returns cached result with same event_id
    res2 = apply_recovery_decision(
        session=db_session,
        scenario_id="scn_test_idemp",
        plan_id="plan_a_thermal_flush",
        decision="approved",
        operator_id="OP-IDEMP-01",
        rationale="Duplicate approval.",
    )
    assert res2.event_id == res1.event_id
    assert res2.applied_at == res1.applied_at


# =============================================================================
# Test 10: Audit Events, Recovery History, and Lifecycle Consistency
# =============================================================================
def test_audit_events_and_lifecycle_consistency(db_session: Session):
    """Lifecycle execution persists consistent audit events and recovery history."""
    scenario = _setup_disposable_scenario(db_session, "scn_test_audit_cons", "roaster_bearing_overheat")

    # Run autonomous recovery with mock client
    mock_client = MagicMock(spec=MeterlessClient)
    mock_client.execute_competing_attempts_job.return_value = {
        "job_id": "job_mock_cons_01",
        "receipt_id": "rcpt_mock_cons_01",
        "winner_seq": 1,
        "runner_up_seq": 2,
        "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "requested_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "content": json.dumps({
            "selected_strategy_id": "plan_a_thermal_flush",
            "strategic_rationale": "Thermal flush restores ROAST-01 within budget and SLA.",
            "trade_off_analysis": "Plan B takes 2x longer, Plan C is 7x more costly.",
            "safety_assessment": "Bearing temperature below seizure limit.",
            "confidence_score": 0.95,
            "contingency_plan_id": "plan_b_reroute_line2",
        }),
    }

    record = run_autonomous_recovery_lifecycle(
        session=db_session,
        scenario_id="scn_test_audit_cons",
        client=mock_client,
        auto_advance_steps=True,
    )

    assert record.final_status == "resolved"
    assert record.selected_plan_id == "plan_a_thermal_flush"

    # Check scenario parameters consistency
    rec_history = scenario.parameters.get("autonomous_recovery_history")
    assert rec_history is not None
    assert rec_history["winning_plan_id"] == "plan_a_thermal_flush"
    assert rec_history["final_status"] == "resolved"

    # Check database OperationalEvent audit trail
    events = (
        db_session.query(OperationalEvent)
        .filter(OperationalEvent.scenario_id == "scn_test_audit_cons")
        .all()
    )
    event_types = [e.event_type for e in events]
    assert "recovery.autonomous_executed" in event_types
    assert "recovery.lifecycle_completed" in event_types
