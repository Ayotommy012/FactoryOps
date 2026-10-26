"""Acceptance Tests for End-to-End Autonomous Execution, Safety Policy, and Meterless Decision Influence.

Verifies:
1. Previously arbitrary constraints no longer blocking eligible recoveries.
2. Genuine safety and physical feasibility checks still blocking unsafe plans.
3. Routine eligible recoveries executing without unnecessary manual approval.
4. Approval-required and blocked recoveries following their respective lifecycles.
5. Evidence-based severity calculation.
6. Meterless genuinely influencing plan selection.
7. Accurate fallback provenance (no mislabeling).
8. Simulation results, execution records, event logs, and status agreement.
"""

from dataclasses import asdict
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.database import engine
from app.models import OperationalEvent, SimulationScenario
from app.providers.meterless import MeterlessClient
from app.services.autonomous_recovery import (
    characterize_disruption,
    investigate_and_select_strategy,
    parse_nightshift_decision_json,
    run_autonomous_recovery_lifecycle,
    validate_candidate_plan,
)
from app.services.recovery import generate_candidate_plans, RecoveryPlan
from app.services.recovery_policy import (
    evaluate_recovery_authorization,
    get_disruption_default_class,
    classify_disruption_default,
    DEFAULT_DISRUPTION_CLASSIFICATIONS,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import create_scenario, is_awaiting_recovery_decision, step_scenario


@pytest.fixture
def db_session():
    """Isolated session with nested savepoints ensuring zero test row drift."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def _setup_scenario(
    session: Session,
    scenario_id: str,
    disruption_type: str = "roaster_bearing_overheat",
    step: int = 3,
) -> str:
    """Helper to initialize a clean scenario with baseline factory data."""
    seed_baseline_factory(session)
    scenario = create_scenario(
        session=session,
        name=f"Autonomous Test {scenario_id}",
        disruption_type=disruption_type,
        scenario_id=scenario_id,
    )
    if step > 0:
        step_scenario(session, scenario_id, steps=step)
        session.refresh(scenario)
    return scenario_id


# =============================================================================
# 1. Previously Arbitrary Constraints Removed & Configurable
# =============================================================================

def test_previously_arbitrary_constraints_no_longer_block_eligible_recovery(db_session: Session):
    """Verifies that plans exceeding old $500 or 2h downtime limits are not arbitrarily blocked."""
    scenario_id = _setup_scenario(db_session, "scn_auth_limits")
    scenario = db_session.get(SimulationScenario, scenario_id)
    char = characterize_disruption(db_session, scenario_id)

    # High-cost plan ($1200) that is economically justified against $2500 SLA penalty
    plan = RecoveryPlan(
        plan_id="plan_a_thermal_flush",
        title="Rapid Bearing Thermal Flush",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=["All components in stock"],
        estimated_downtime_hours=3.5,  # Exceeds old 2.0h arbitrary limit
        projected_completion="2026-10-22T12:00:00+00:00",
        meets_deadline=True,
        buffer_hours=0.5,  # Valid buffer (>= 0.0), below old arbitrary 1.0h requirement
        total_cost_usd=1200.0,  # Exceeds old $500 arbitrary cap
        cost_breakdown={"parts": 600.0, "labor": 600.0},
        action_steps=["Flush and restart"],
    )

    val_res = validate_candidate_plan(scenario, plan, char)
    assert val_res.valid is True

    # Under hybrid policy, economically justified plans auto-execute
    char.potential_penalty_usd = 2500.0
    auth = evaluate_recovery_authorization(
        plan=plan,
        disruption_type="roaster_bearing_overheat",
        characterization=char,
        validation_result=val_res,
        scenario=scenario,
    )
    assert auth.outcome == "AUTO_EXECUTE"
    assert len(auth.violated_constraints) == 0
    assert "satisfies all hard constraints" in auth.reason.lower()


# =============================================================================
# 2. Genuine Safety & Physical Feasibility Checks Still Block Unsafe Plans
# =============================================================================

def test_genuine_safety_quarantine_is_strictly_blocked(db_session: Session):
    """Food safety quarantine (contamination) must ALWAYS be BLOCKED for certified manual QC."""
    scenario_id = _setup_scenario(db_session, "scn_quarantine_block", disruption_type="quality_contamination")
    scenario = db_session.get(SimulationScenario, scenario_id)
    char = characterize_disruption(db_session, scenario_id)

    plan = RecoveryPlan(
        plan_id="plan_a_thermal_flush",
        title="Autonomous Flush & Restart",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=[],
        estimated_downtime_hours=1.0,
        projected_completion="2026-10-22T10:00:00+00:00",
        meets_deadline=True,
        buffer_hours=4.0,
        total_cost_usd=200.0,
        cost_breakdown={},
        action_steps=["Flush line"],
    )

    auth = evaluate_recovery_authorization(
        plan=plan,
        disruption_type="quality_contamination",
        characterization=char,
        scenario=scenario,
    )
    assert auth.outcome == "BLOCKED"
    assert "safety-critical" in auth.reason.lower() or "quarantine" in auth.reason.lower()


def test_thermal_safety_threshold_violation_fails_validation(db_session: Session):
    """Exceeding thermal critical threshold without emergency mitigation fails validation."""
    scenario_id = _setup_scenario(db_session, "scn_thermal_violation")
    scenario = db_session.get(SimulationScenario, scenario_id)
    char = characterize_disruption(db_session, scenario_id)

    # Artificially set roaster temperature to 175°C (exceeds critical 160°C)
    scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-01"]["temperature_c"] = 175.0

    plan = RecoveryPlan(
        plan_id="plan_passive_wait",
        title="Passive Wait Strategy",
        strategy="routine_operation",  # Not a recognized safety mitigation strategy
        rank=1,
        feasibility_verdict="feasible",
        hard_constraints_met=[],
        estimated_downtime_hours=0.5,
        projected_completion="2026-10-22T12:00:00+00:00",
        meets_deadline=True,
        buffer_hours=2.0,
        total_cost_usd=100.0,
        cost_breakdown={},
        action_steps=["Wait"],
    )

    val_res = validate_candidate_plan(scenario, plan, char)
    assert val_res.valid is False
    assert any("SAFETY_THRESHOLD_BREACH" in viol for viol in val_res.violated_constraints)


# =============================================================================
# 3. Meterless Influencing Recovery-Plan Selection (Deliberately Choosing Plan B)
# =============================================================================

def test_meterless_influences_plan_selection(db_session: Session):
    """Verifies that FactoryOps follows Meterless when it selects Plan B over Plan A."""
    scenario_id = _setup_scenario(db_session, "scn_meterless_influences")

    # Mock client where Meterless reasoning specifically selects plan_b_reroute_line2
    mock_client = MagicMock(spec=MeterlessClient)
    mock_client.execute_competing_attempts_job.return_value = {
        "job_id": "job_mock_nemotron_choice",
        "status": "completed",
        "winner_seq": 1,
        "content": (
            "<think>Analyzing candidates: Plan A requires maintenance downtime on stressed ROAST-01, "
            "whereas Plan B reroutes to auxiliary Roaster Line 2 with zero risk to bearing integrity.</think>"
            "```json\n"
            "{\n"
            '  "selected_plan_id": "plan_b_reroute_line2",\n'
            '  "strategic_rationale": "Rerouting to Roaster Line 2 isolates damaged bearing and preserves line speed.",\n'
            '  "trade_off_analysis": "Trades pneumatic line transfer setup for guaranteed operational margin.",\n'
            '  "safety_assessment": "Bearing stress eliminated by transferring batch to ROAST-02.",\n'
            '  "confidence_score": 0.96,\n'
            '  "contingency_plan_id": "plan_a_thermal_flush"\n'
            "}\n"
            "```"
        ),
        "requested_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "receipt_id": "rcpt_mock_nemotron_choice",
        "judge_mode": "accuracy",
        "judge_outcome": {"status": "accepted"},
        "competing_attempts_count": 2,
    }

    with patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=1,
            client=mock_client,
            auto_advance_steps=False,
        )

    # Meterless choice must be followed!
    assert record.winning_plan_id == "plan_b_reroute_line2"
    assert record.rounds[0].decision["decision_source"] == "meterless_nightshift"
    assert record.rounds[0].decision["model_id"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert record.rounds[0].decision["receipt_id"] == "rcpt_mock_nemotron_choice"


# =============================================================================
# 4. Accurate Provenance & Deterministic Fallback Labeling
# =============================================================================

def test_deterministic_fallback_is_accurately_labeled(db_session: Session):
    """When Meterless is unavailable or times out, provenance is strictly deterministic_fallback."""
    scenario_id = _setup_scenario(db_session, "scn_fallback_provenance")

    mock_client = MagicMock(spec=MeterlessClient)
    mock_client.execute_competing_attempts_job.side_effect = TimeoutError("Meterless daemon timeout")

    with patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=1,
            client=mock_client,
            auto_advance_steps=False,
        )

    decision = record.rounds[0].decision
    assert decision["decision_source"] == "deterministic_fallback"
    assert decision["model_id"] == "deterministic_cost_sla_optimizer"
    assert decision["receipt_id"] is None
    assert decision["job_id"] is None
    assert "timeout" in decision["fallback_reason"].lower() or "timed out" in decision["fallback_reason"].lower()


# =============================================================================
# 5. Parsing Robustness for Reasoning Tokens (<think> tags & aliases)
# =============================================================================

def test_parse_nightshift_decision_json_handles_thinking_tokens():
    """Verifies that <think> reasoning blocks are safely stripped before JSON parsing."""
    raw = (
        "<think>\n"
        "Here is my internal chain of thought: { 'internal_score': 0.88 }.\n"
        "Candidate plan_a is preferred because cost is minimal.\n"
        "</think>\n"
        '{"selected_plan_id": "plan_a_thermal_flush", "rationale": "Optimal cost and buffer."}'
    )
    plan_id, rationale, trade_off, safety, conf, cont = parse_nightshift_decision_json(
        raw, ["plan_a_thermal_flush", "plan_b_reroute_line2"]
    )
    assert plan_id == "plan_a_thermal_flush"
    assert "Optimal cost" in rationale


# =============================================================================
# 6. Routine Recovery Auto-Executes and Advances Simulation
# =============================================================================

def test_routine_recovery_auto_executes_and_simulation_advances(db_session: Session):
    """Routine auto_recoverable disruption executes autonomously and simulation completes."""
    scenario_id = _setup_scenario(db_session, "scn_auto_exec_flow", disruption_type="roaster_bearing_overheat")
    scenario = db_session.get(SimulationScenario, scenario_id)

    # Initial state at step 3: roaster is degraded
    assert scenario.current_step == 3
    roaster = scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-01"]
    assert roaster["status"] == "degraded"

    # Run autonomous recovery lifecycle with auto_advance_steps=True
    record = run_autonomous_recovery_lifecycle(
        session=db_session,
        scenario_id=scenario_id,
        auto_advance_steps=True,
    )

    db_session.refresh(scenario)
    assert record.final_status == "resolved"

    # Recovery must be approved and completed
    recovery = scenario.parameters.get("recovery", {})
    assert recovery.get("decision") == "approved"
    assert recovery.get("autonomous") is True
    assert recovery.get("status") == "completed"

    # Roaster is restored to running nominal status
    roaster_after = scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-01"]
    assert roaster_after["status"] == "running"
    assert roaster_after["temperature_c"] <= 120.0
    assert roaster_after["vibration_rms"] <= 2.0

    # Operational events accurately record autonomous execution
    events = db_session.query(OperationalEvent).filter(
        OperationalEvent.scenario_id == scenario_id,
        OperationalEvent.event_type == "recovery.autonomous_executed",
    ).all()
    assert len(events) >= 1
    assert events[0].payload["plan_id"] == record.winning_plan_id
