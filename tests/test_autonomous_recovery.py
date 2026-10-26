"""Comprehensive Verification Suite for Autonomous Disruption Investigation & Recovery.

Covers all 12 criteria specified in Section 4:
1. Single-round successful resolution (Investigate -> Decide -> Validate -> Execute -> Verify -> Complete).
2. Multi-round reassessment after initial failure / constraint violation.
3. Pre-execution hard-constraint validation & rejection of unsafe plans.
4. Handling of insufficient evidence or unresolvable constraints.
5. Resilient fallback on missing or malformed Nightshift responses.
6. Graceful degradation on Nightshift timeout / network connection error.
7. Independent outcome verification detecting unresolved metrics.
8. Recovery loop / cycle prevention preventing infinite reassessment.
9. Max attempt limits and boundary conditions.
10. Full audit trail and provenance verification (OperationalEvent + World Model facts).
11. Plant data-integrity and material-balance invariants.
12. API endpoint integration: POST /autonomous-recovery/run and GET /autonomous-recovery/history.
"""

from datetime import datetime, timezone
import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.simulations import get_db
from app.database import engine
from app.main import app
from app.models import OperationalEvent, SimulationScenario
from app.providers.meterless import MeterlessClient
from app.services.autonomous_recovery import (
    AutonomousRecoveryLifecycleRecord,
    AutonomousRecoveryRound,
    DisruptionCharacterization,
    PlanValidationResult,
    RecoveryExecutionResult,
    RecoveryVerificationResult,
    StrategicDecision,
    build_nightshift_investigation_prompt,
    characterize_disruption,
    execute_autonomous_recovery,
    investigate_and_select_strategy,
    parse_nightshift_decision_json,
    run_autonomous_recovery_lifecycle,
    validate_candidate_plan,
    verify_recovery_outcome,
)
from app.services.recovery import (
    ImpactAnalysis,
    RecoveryPlan,
    detect_and_trace_impact,
    generate_candidate_plans,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import create_scenario, get_scenario_world_model, step_scenario


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


def _setup_disrupted_scenario(db_session: Session, scenario_id: str = "scn_auto_test") -> str:
    """Helper to seed factory, create scenario, and step to active disruption."""
    seed_baseline_factory(db_session)
    create_scenario(
        session=db_session,
        name="Autonomous Recovery Test Scenario",
        disruption_type="roaster_bearing_overheat",
        scenario_id=scenario_id,
    )
    # Step 3 triggers the thermal bearing anomaly on ROAST-01
    step_scenario(db_session, scenario_id, steps=3)
    return scenario_id


# =============================================================================
# 1. Stage A — Characterize Disruption
# =============================================================================

def test_characterize_disruption_completeness(db_session: Session):
    """Verifies that Stage A produces a complete operational characterization packet."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_char_01")
    char = characterize_disruption(db_session, scenario_id)

    assert char.scenario_id == scenario_id
    assert char.has_active_disruption is True
    assert char.disrupted_machine_id == "machine:plant:ROAST-01"
    assert char.affected_batch_id == "production_batch:mes:BATCH-101"
    assert char.affected_order_id == "customer_order:erp:ORD-9402"
    assert char.potential_penalty_usd > 0.0

    # Multi-stage production line representation (7 stages)
    assert len(char.production_line_stages) == 7
    stage_names = [s["name"] for s in char.production_line_stages]
    assert "Cleaning & Destoning" in stage_names
    assert "Drum Roasting" in stage_names
    assert "Liquor Grinding" in stage_names

    # Inventory & Silos
    assert "raw_beans_lots" in char.inventory_summary
    assert "silos" in char.inventory_summary

    # Hard safety limits
    assert char.hard_safety_thresholds["roaster_temperature_critical_c"] == 140.0
    assert char.hard_safety_thresholds["minimum_safety_stock_kg"] == 500.0


# =============================================================================
# 2. Stage B & C — Prompt Construction & Nightshift Decision Parsing
# =============================================================================

def test_prompt_construction_and_parsing(db_session: Session):
    """Verifies grounded operational prompt construction and robust JSON parsing."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_prompt_01")
    char = characterize_disruption(db_session, scenario_id)
    candidates = generate_candidate_plans(db_session, scenario_id, use_ai=False)

    prompt = build_nightshift_investigation_prompt(char, candidates)
    assert "Virtual Cocoa Processing Plant" in prompt
    assert "HARD SAFETY CONSTRAINTS" in prompt
    assert "plan_a_thermal_flush" in prompt

    # Test clean JSON parsing
    clean_json = json.dumps({
        "selected_plan_id": "plan_a_thermal_flush",
        "strategic_rationale": "Directly restores roaster bearing temperature within SLA.",
        "trade_off_analysis": "Lowest downtime and zero SLA penalty.",
        "safety_assessment": "Thermal limit resolved safely.",
        "confidence_score": 0.95,
        "contingency_plan_id": "plan_b_reroute_line2",
    })
    plan_id, rat, trade, safe, conf, cont = parse_nightshift_decision_json(
        clean_json, [c.plan_id for c in candidates]
    )
    assert plan_id == "plan_a_thermal_flush"
    assert conf == 0.95
    assert cont == "plan_b_reroute_line2"

    # Test markdown code block extraction
    markdown_wrapped = f"```json\n{clean_json}\n```"
    p_id, _, _, _, _, _ = parse_nightshift_decision_json(
        markdown_wrapped, [c.plan_id for c in candidates]
    )
    assert p_id == "plan_a_thermal_flush"


# =============================================================================
# 3. Single-Round Successful Resolution (Deterministic Fallback)
# =============================================================================

def test_single_round_successful_resolution_deterministic(db_session: Session):
    """Validates full end-to-end autonomous resolution in a single round."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_single_det")

    record = run_autonomous_recovery_lifecycle(
        session=db_session,
        scenario_id=scenario_id,
        max_rounds=3,
        auto_advance_steps=True,
    )

    assert record.final_status == "resolved"
    assert len(record.rounds) == 1
    assert record.selected_plan_id == "plan_a_thermal_flush"
    assert record.total_cost_usd > 0.0

    round1 = record.rounds[0]
    assert round1.round_number == 1
    assert round1.validation["valid"] is True
    assert round1.execution is not None
    assert round1.execution["executed"] is True
    assert round1.verification is not None
    assert round1.verification["verified"] is True
    assert round1.verification["resolution_status"] == "resolved"

    # Scenario parameters should reflect the autonomous recovery
    scenario = db_session.get(SimulationScenario, scenario_id)
    assert "autonomous_recovery_history" in scenario.parameters
    assert scenario.parameters["autonomous_recovery_history"]["final_status"] == "resolved"


# =============================================================================
# 4. Single-Round Successful Resolution with Mock Nightshift Client
# =============================================================================

def test_single_round_successful_resolution_nightshift_mock(db_session: Session):
    """Verifies that Nightshift leads strategic decision-making with full provenance."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_single_nightshift")

    mock_client = MagicMock(spec=MeterlessClient)
    mock_client.preview_route.return_value = {
        "routed_mode": "deep",
        "raced": ["nvidia/nemotron-3-ultra-550b-a55b"],
        "candidates": [{"id": "nvidia/nemotron-3-ultra-550b-a55b"}],
    }
    mock_client.execute_competing_attempts_job.return_value = {
        "job_id": "job_mock_101",
        "status": "completed",
        "winner_seq": 1,
        "runner_up_seq": 2,
        "content": json.dumps({
            "selected_plan_id": "plan_a_thermal_flush",
            "strategic_rationale": "Nightshift Nemotron recommends rapid thermal flush to protect cocoa roast profile.",
            "trade_off_analysis": "Eliminates delay compared to auxiliary rerouting.",
            "safety_assessment": "Roaster thermal sensors projected to return to nominal 88C.",
            "confidence_score": 0.98,
            "contingency_plan_id": "plan_b_reroute_line2",
        }),
        "requested_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "receipt_id": "rcpt_mock_101",
        "judge_mode": "accuracy",
        "judge_outcome": {"status": "accepted", "basis": "consensus", "explanation": "Attempt 1 demonstrated complete thermal mitigation."},
        "competing_attempts_count": 2,
    }

    with patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=3,
            client=mock_client,
            auto_advance_steps=True,
        )

    assert record.final_status == "resolved"
    assert len(record.rounds) == 1
    assert record.selected_plan_id == "plan_a_thermal_flush"

    round1 = record.rounds[0]
    assert round1.decision["decision_source"] == "meterless_nightshift"
    assert "Nightshift Nemotron" in round1.decision["strategic_rationale"]
    assert round1.decision["confidence_score"] == 0.98
    assert round1.decision["job_id"] == "job_mock_101"
    assert round1.decision["receipt_id"] == "rcpt_mock_101"
    assert round1.decision["winner_seq"] == 1
    assert round1.decision["competing_attempts_count"] == 2
    assert round1.decision["judge_mode"] == "accuracy"
    assert round1.decision["resolved_model"] == "nvidia/nemotron-3-ultra-550b-a55b"

    # Provenance check via OperationalEvent and scenario parameters
    scenario = db_session.get(SimulationScenario, scenario_id)
    recovery_info = scenario.parameters.get("recovery", {})
    assert recovery_info["decision_source"] == "meterless_nightshift"
    assert recovery_info["operator_id"] == "agent:meterless_nightshift"
    assert recovery_info["autonomous"] is True
    assert recovery_info["job_id"] == "job_mock_101"
    assert recovery_info["receipt_id"] == "rcpt_mock_101"
    assert recovery_info["winner_seq"] == 1

    exec_event = db_session.execute(
        select(OperationalEvent)
        .where(
            OperationalEvent.scenario_id == scenario_id,
            OperationalEvent.event_type == "recovery.autonomous_executed",
        )
    ).scalar_one()
    assert exec_event.payload["decision_source"] == "meterless_nightshift"
    assert exec_event.payload["confidence_score"] == 0.98
    assert "Nightshift Nemotron" in exec_event.payload["rationale"]
    assert exec_event.payload["job_id"] == "job_mock_101"
    assert exec_event.payload["receipt_id"] == "rcpt_mock_101"
    assert exec_event.payload["winner_seq"] == 1


# =============================================================================
# 5. Pre-Execution Hard-Constraint Validation & Rejection
# =============================================================================

def test_hard_constraint_validation_rules(db_session: Session):
    """Stage D: Tests strict pre-execution constraint rejection."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_val_rules")
    scenario = db_session.get(SimulationScenario, scenario_id)
    char = characterize_disruption(db_session, scenario_id)
    candidates = generate_candidate_plans(db_session, scenario_id, use_ai=False)
    plan_b = next(p for p in candidates if p.plan_id == "plan_b_reroute_line2")

    # 1. Normal state: plan B should pass
    val_normal = validate_candidate_plan(scenario, plan_b, char)
    assert val_normal.valid is True

    # 2. Auxiliary line unavailable: ROAST-02 offline
    scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-02"]["status"] = "offline"
    val_offline = validate_candidate_plan(scenario, plan_b, char)
    assert val_offline.valid is False
    assert any("AUXILIARY_LINE_UNAVAILABLE" in v for v in val_offline.violated_constraints)

    # 3. Negative inventory check
    scenario.parameters["snapshot"]["inventory"]["inventory_lot:wms:LOT-2026-001"]["quantity_kg"] = -50.0
    val_inv = validate_candidate_plan(scenario, plan_b, char)
    assert val_inv.valid is False
    assert any("NEGATIVE_INVENTORY_DETECTED" in v for v in val_inv.violated_constraints)

    # 4. Invalid scenario state check
    scenario.status = "completed"
    val_status = validate_candidate_plan(scenario, plan_b, char)
    assert val_status.valid is False
    assert any("SCENARIO_STATE_INVALID" in v for v in val_status.violated_constraints)


# =============================================================================
# 6. Multi-Round Reassessment After Constraint Violation
# =============================================================================

def test_multi_round_reassessment_after_constraint_violation(db_session: Session):
    """Validates that a rejected candidate triggers feedback into round 2 for resolution."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_reassess_viol")
    scenario = db_session.get(SimulationScenario, scenario_id)

    # Make ROAST-02 offline so plan_b will violate constraints in round 1
    scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-02"]["status"] = "offline"
    db_session.flush()

    mock_client = MagicMock(spec=MeterlessClient)
    # Round 1 returns plan_b_reroute_line2 (which fails validation)
    # Round 2 returns plan_a_thermal_flush (which passes and resolves)
    mock_client.execute_competing_attempts_job.side_effect = [
        {
            "job_id": "job_mock_201",
            "status": "completed",
            "winner_seq": 1,
            "runner_up_seq": 2,
            "content": json.dumps({
                "selected_plan_id": "plan_b_reroute_line2",
                "strategic_rationale": "Initial proposal: reroute to auxiliary roaster line 2.",
                "trade_off_analysis": "Avoids roaster 1 shutdown.",
                "safety_assessment": "Line 2 believed ready.",
                "confidence_score": 0.85,
            }),
            "requested_model": "nvidia/nemotron-3-ultra-550b-a55b",
            "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
            "receipt_id": "rcpt_mock_201",
            "judge_mode": "accuracy",
            "judge_outcome": {"status": "accepted"},
            "competing_attempts_count": 2,
        },
        {
            "job_id": "job_mock_202",
            "status": "completed",
            "winner_seq": 2,
            "runner_up_seq": 1,
            "content": json.dumps({
                "selected_plan_id": "plan_a_thermal_flush",
                "strategic_rationale": "Reassessed proposal: ROAST-02 unavailable, applying thermal flush directly.",
                "trade_off_analysis": "Safe and compliant with current machine status.",
                "safety_assessment": "Direct bearing flush.",
                "confidence_score": 0.95,
            }),
            "requested_model": "nvidia/nemotron-3-ultra-550b-a55b",
            "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
            "receipt_id": "rcpt_mock_202",
            "judge_mode": "accuracy",
            "judge_outcome": {"status": "accepted"},
            "competing_attempts_count": 2,
        },
    ]

    with patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=3,
            client=mock_client,
            auto_advance_steps=True,
        )

    assert record.final_status == "resolved"
    assert len(record.rounds) == 2

    # Round 1 failed validation
    assert record.rounds[0].validation["valid"] is False
    assert "AUXILIARY_LINE_UNAVAILABLE" in record.rounds[0].reassessment_reason
    assert record.rounds[0].execution is None

    # Round 2 passed validation and succeeded
    assert record.rounds[1].validation["valid"] is True
    assert record.rounds[1].execution is not None
    assert record.rounds[1].verification["verified"] is True
    assert record.selected_plan_id == "plan_a_thermal_flush"


# =============================================================================
# 7. Multi-Round Reassessment After Verification Failure
# =============================================================================

def test_multi_round_reassessment_after_verification_failure(db_session: Session):
    """Validates that execution failure caught by post-verification triggers re-planning."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_reassess_verif")

    round_counter = 0

    def mock_verify_side_effect(session, scn_id, char, plan):
        nonlocal round_counter
        round_counter += 1
        if round_counter == 1:
            # Round 1 verification detects unresolved high temperature
            return RecoveryVerificationResult(
                verified=False,
                resolution_status="unresolved",
                metric_checks={"disrupted_machine": {"temperature_c": 138.0, "temperature_nominal": False}},
                unresolved_issues=["Bearing temperature remains critically elevated at 138.0C"],
                new_disruptions=[],
            )
        else:
            # Round 2 verification succeeds
            return RecoveryVerificationResult(
                verified=True,
                resolution_status="resolved",
                metric_checks={"disrupted_machine": {"temperature_c": 88.0, "temperature_nominal": True}},
                unresolved_issues=[],
                new_disruptions=[],
            )

    with patch("app.services.autonomous_recovery.verify_recovery_outcome", side_effect=mock_verify_side_effect):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=3,
            auto_advance_steps=False,
        )

    assert record.final_status == "resolved"
    assert len(record.rounds) == 2
    assert record.rounds[0].verification["verified"] is False
    assert record.rounds[1].verification["verified"] is True


# =============================================================================
# 8. Recovery Loop & Cycle Prevention
# =============================================================================

def test_recovery_loop_and_cycle_prevention(db_session: Session):
    """Verifies that an AI agent repeating a failed decision is caught by cycle detection."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_loop_prev")
    scenario = db_session.get(SimulationScenario, scenario_id)

    # Offline ROAST-02 causes plan_b to fail validation
    scenario.parameters["snapshot"]["machines"]["machine:plant:ROAST-02"]["status"] = "offline"
    db_session.flush()

    mock_client = MagicMock(spec=MeterlessClient)
    # Stubborn agent returns plan_b_reroute_line2 on both rounds
    mock_client.execute_competing_attempts_job.return_value = {
        "job_id": "job_mock_301",
        "status": "completed",
        "winner_seq": 1,
        "content": json.dumps({
            "selected_plan_id": "plan_b_reroute_line2",
            "strategic_rationale": "I insist on rerouting to line 2 despite constraints.",
            "trade_off_analysis": "None.",
            "safety_assessment": "Unchecked.",
            "confidence_score": 0.50,
        }),
        "requested_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "receipt_id": "rcpt_mock_301",
        "judge_mode": "accuracy",
        "judge_outcome": {"status": "accepted"},
        "competing_attempts_count": 2,
    }

    with patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=3,
            client=mock_client,
            auto_advance_steps=False,
        )

    assert record.final_status == "loop_detected"
    assert len(record.rounds) == 2
    assert "Cycle detected" in record.rounds[1].validation["reason"]


# =============================================================================
# 9. Max Attempt Limits Boundary Condition
# =============================================================================

def test_max_attempt_limits_boundary(db_session: Session):
    """Verifies that the loop terminates deterministically when max_rounds is reached."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_max_rounds")

    # Mock validator to reject all candidate plans
    with patch(
        "app.services.autonomous_recovery.validate_candidate_plan",
        return_value=PlanValidationResult(
            valid=False,
            plan_id="any",
            hard_constraints_passed=[],
            violated_constraints=["SYNTHETIC_GLOBAL_BLOCK: Factory wide safety hold."],
            risk_notes=[],
        ),
    ):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=2,
            auto_advance_steps=False,
        )

    assert record.final_status == "max_attempts_exceeded"
    assert len(record.rounds) == 2


# =============================================================================
# 10. Missing / Malformed Nightshift Responses & Error Handling
# =============================================================================

def test_malformed_nightshift_response_triggers_deterministic_fallback(db_session: Session):
    """Verifies that non-JSON or garbled AI responses safely degrade to deterministic fallback."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_malformed_ai")

    mock_client = MagicMock(spec=MeterlessClient)
    mock_client.execute_competing_attempts_job.return_value = {
        "job_id": "job_mock_401",
        "status": "completed",
        "winner_seq": 1,
        "content": "I am an AI assistant and I think you should check the roaster.",
        "requested_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "receipt_id": "rcpt_mock_401",
        "judge_mode": "accuracy",
        "judge_outcome": {"status": "accepted"},
        "competing_attempts_count": 2,
    }

    with patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=2,
            client=mock_client,
            auto_advance_steps=True,
        )

    assert record.final_status == "resolved"
    assert len(record.rounds) == 1
    assert record.rounds[0].decision["decision_source"] == "deterministic_fallback"
    assert "deterministic_cost_sla_optimizer" in record.rounds[0].decision["model_id"]
    assert "schema" in record.rounds[0].decision["fallback_reason"].lower()


def test_nightshift_timeout_triggers_deterministic_fallback(db_session: Session):
    """Verifies that network timeout to Nightshift daemon safely triggers deterministic fallback."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_timeout_ai")

    mock_client = MagicMock(spec=MeterlessClient)
    mock_client.execute_competing_attempts_job.side_effect = TimeoutError("Meterless daemon at 127.0.0.1:8088 timed out")

    with patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        record = run_autonomous_recovery_lifecycle(
            session=db_session,
            scenario_id=scenario_id,
            max_rounds=2,
            client=mock_client,
            auto_advance_steps=True,
        )

    assert record.final_status == "resolved"
    assert record.rounds[0].decision["decision_source"] == "deterministic_fallback"
    assert "timed out" in record.rounds[0].decision["fallback_reason"].lower()


def test_nightshift_cloud_jobs_gate_fallback(db_session: Session):
    """Verifies that when NIGHTSHIFT_ALLOW_CLOUD_JOBS is false, route preview is inspected
    without submitting cloud jobs, engaging safe deterministic fallback with full reason logging.
    """
    scenario_id = _setup_disrupted_scenario(db_session, "scn_cloud_gate")
    char = characterize_disruption(db_session, scenario_id)
    candidates = generate_candidate_plans(db_session, scenario_id, use_ai=False)

    with patch.dict("os.environ", {"NIGHTSHIFT_ALLOW_CLOUD_JOBS": "false"}), \
         patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        decision = investigate_and_select_strategy(
            characterization=char,
            candidate_plans=candidates,
            client=None,  # No client passed -> checks allow_cloud_jobs gate
        )

    assert decision.decision_source == "deterministic_fallback"
    assert decision.job_id is None
    assert decision.receipt_id is None
    assert "NIGHTSHIFT_ALLOW_CLOUD_JOBS=false" in (decision.fallback_reason or "")
    assert decision.requested_model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert decision.resolved_model is None
    assert decision.selected_plan_id == "plan_a_thermal_flush"


def test_nightshift_real_client_instance_safely_gated(db_session: Session):
    """Verifies that passing a real MeterlessClient instance without NIGHTSHIFT_ALLOW_CLOUD_JOBS=true
    is strictly blocked from submitting cloud jobs and safely falls back deterministically.
    """
    scenario_id = _setup_disrupted_scenario(db_session, "scn_real_client_gate")
    char = characterize_disruption(db_session, scenario_id)
    candidates = generate_candidate_plans(db_session, scenario_id, use_ai=False)

    real_client = MeterlessClient(token="sec_token_test")

    with patch.dict("os.environ", {"NIGHTSHIFT_ALLOW_CLOUD_JOBS": "false"}), \
         patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        decision = investigate_and_select_strategy(
            characterization=char,
            candidate_plans=candidates,
            client=real_client,  # Real client, NOT a mock!
        )

    assert decision.decision_source == "deterministic_fallback"
    assert decision.job_id is None
    assert decision.resolved_model is None
    assert "NIGHTSHIFT_ALLOW_CLOUD_JOBS=false" in (decision.fallback_reason or "")


def test_nightshift_single_cloud_job_limit_per_lifecycle(db_session: Session):
    """Verifies that even if NIGHTSHIFT_ALLOW_CLOUD_JOBS is true, live cloud inference
    is strictly capped to at most ONE job per recovery lifecycle (Round 1 only).
    Subsequent reassessment rounds strictly fall back to deterministic evaluation.
    """
    scenario_id = _setup_disrupted_scenario(db_session, "scn_single_job_cap")
    char = characterize_disruption(db_session, scenario_id)
    candidates = generate_candidate_plans(db_session, scenario_id, use_ai=False)

    # Simulate Round 2 where attempted_plan_ids is already populated
    with patch.dict("os.environ", {"NIGHTSHIFT_ALLOW_CLOUD_JOBS": "true"}), \
         patch("app.services.autonomous_recovery.resolve_meterless_token", return_value="sec_token_test"):
        decision = investigate_and_select_strategy(
            characterization=char,
            candidate_plans=candidates,
            attempted_plan_ids=["plan_b_reroute_line2"],  # Prior attempt exists
            client=None,
        )

    assert decision.decision_source == "deterministic_fallback"
    assert decision.job_id is None
    assert decision.resolved_model is None
    assert "Single live cloud job limit reached" in (decision.fallback_reason or "")


# =============================================================================
# 11. Full Audit Trail & Provenance Verification
# =============================================================================

def test_full_audit_trail_and_operational_events(db_session: Session):
    """Stage H: Verifies that append-only operational events and provenance are written."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_audit_trail")

    record = run_autonomous_recovery_lifecycle(
        session=db_session,
        scenario_id=scenario_id,
        max_rounds=2,
        auto_advance_steps=True,
    )

    # Query operational events from PostgreSQL
    events = db_session.execute(
        select(OperationalEvent)
        .where(OperationalEvent.scenario_id == scenario_id)
        .order_by(OperationalEvent.recorded_at.asc())
    ).scalars().all()

    event_types = [e.event_type for e in events]
    assert "recovery.autonomous_executed" in event_types
    assert "recovery.lifecycle_completed" in event_types

    lifecycle_event = next(e for e in events if e.event_type == "recovery.lifecycle_completed")
    assert lifecycle_event.payload["lifecycle_id"] == record.lifecycle_id
    assert lifecycle_event.payload["final_status"] == "resolved"
    assert len(lifecycle_event.payload["rounds"]) >= 1


# =============================================================================
# 12. Plant Data Integrity & Non-Negative Inventory Invariants
# =============================================================================

def test_data_integrity_and_material_invariants(db_session: Session):
    """Verifies that plant inventory and batch balances remain non-negative and valid."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_data_invariants")

    record = run_autonomous_recovery_lifecycle(
        session=db_session,
        scenario_id=scenario_id,
        max_rounds=2,
        auto_advance_steps=True,
    )
    assert record.final_status == "resolved"

    scenario = db_session.get(SimulationScenario, scenario_id)
    snapshot = scenario.parameters.get("snapshot", {})

    # Check all inventory lots
    for lot_id, lot_data in snapshot.get("inventory", {}).items():
        qty = float(lot_data.get("quantity_kg", 0.0))
        assert qty >= 0.0, f"Lot {lot_id} has negative stock: {qty}"

    # Check all silos
    for silo_id, silo_data in snapshot.get("silos", {}).items():
        stock = float(silo_data.get("current_stock_kg", 0.0))
        cap = float(silo_data.get("capacity_kg", 50000.0))
        assert stock >= 0.0, f"Silo {silo_id} stock negative: {stock}"
        assert stock <= cap, f"Silo {silo_id} overflow: {stock} > {cap}"


# =============================================================================
# 13. API Endpoint Integration: POST & GET
# =============================================================================

def test_autonomous_recovery_api_endpoints(db_session: Session):
    """Tests the FastAPI routes for triggering autonomous recovery and inspecting history."""
    scenario_id = _setup_disrupted_scenario(db_session, "scn_api_auto")

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        # 1. Trigger autonomous recovery
        run_resp = client.post(
            f"/simulations/{scenario_id}/autonomous-recovery/run",
            json={"max_rounds": 2, "auto_advance_steps": True},
        )
        assert run_resp.status_code == 200, run_resp.text
        data = run_resp.json()
        assert data["scenario_id"] == scenario_id
        assert data["final_status"] == "resolved"
        assert len(data["rounds"]) >= 1

        # 2. Query history
        hist_resp = client.get(f"/simulations/{scenario_id}/autonomous-recovery/history")
        assert hist_resp.status_code == 200, hist_resp.text
        hist_data = hist_resp.json()
        assert hist_data["scenario_id"] == scenario_id
        assert hist_data["has_autonomous_history"] is True
        assert hist_data["summary"]["final_status"] == "resolved"
        assert len(hist_data["audit_events"]) >= 1

        # 3. 404 for unknown scenario
        err_resp = client.post("/simulations/scn_unknown_xyz/autonomous-recovery/run")
        assert err_resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_verify_live_inference_provenance_rejects_deterministic_fallback():
    """Confirms that verify_live_inference_provenance reports LIVE INFERENCE NOT VERIFIED
    when a lifecycle resolved successfully via deterministic fallback.
    """
    from app.services.autonomous_recovery import verify_live_inference_provenance

    record = {
        "final_status": "resolved",
        "selected_plan_id": "plan_a_thermal_flush",
        "audit_event_id": "evt_test_123",
        "rounds": [
            {
                "round_number": 1,
                "decision": {
                    "decision_source": "deterministic_fallback",
                    "model_id": "deterministic_cost_sla_optimizer",
                    "job_id": None,
                    "receipt_id": None,
                    "competing_attempts_count": None,
                    "judge_outcome": None,
                    "resolved_model": None,
                    "fallback_reason": "Nightshift cloud execution held pending explicit user authorization (NIGHTSHIFT_ALLOW_CLOUD_JOBS=false).",
                },
            }
        ],
    }

    res = verify_live_inference_provenance(record)
    assert res.verified is False
    assert res.verdict == "LIVE INFERENCE NOT VERIFIED"
    assert "deterministic_fallback" in res.reason


def test_verify_live_inference_provenance_rejects_missing_or_contradictory_evidence():
    """Confirms that verify_live_inference_provenance rejects records with missing job_id,
    attempt count != 2, unresolved model, missing judge outcome, or missing receipt.
    """
    from app.services.autonomous_recovery import verify_live_inference_provenance

    valid_decision = {
        "decision_source": "meterless_nightshift",
        "job_id": "job_valid_123",
        "competing_attempts_count": 2,
        "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "judge_outcome": {"winner_seq": 1, "status": "completed"},
        "receipt_id": "rec_signed_123",
    }

    # 1. Missing job_id
    d_no_job = dict(valid_decision, job_id="")
    res = verify_live_inference_provenance({"rounds": [{"decision": d_no_job}]})
    assert res.verified is False
    assert "job_id is missing" in res.reason

    # 2. Competing attempts count != 2
    d_single_att = dict(valid_decision, competing_attempts_count=1)
    res = verify_live_inference_provenance({"rounds": [{"decision": d_single_att}]})
    assert res.verified is False
    assert "competing attempts count is 1" in res.reason

    # 3. Unspecified model
    d_unspec = dict(valid_decision, resolved_model="unspecified_model")
    res = verify_live_inference_provenance({"rounds": [{"decision": d_unspec}]})
    assert res.verified is False
    assert "not verified from runtime attempt metadata" in res.reason

    # 4. Missing judge outcome
    d_no_judge = dict(valid_decision, judge_outcome=None)
    res = verify_live_inference_provenance({"rounds": [{"decision": d_no_judge}]})
    assert res.verified is False
    assert "Judge outcome record is missing" in res.reason

    # 5. Missing receipt ID
    d_no_receipt = dict(valid_decision, receipt_id=None)
    res = verify_live_inference_provenance({"rounds": [{"decision": d_no_receipt}]})
    assert res.verified is False
    assert "receipt ID is missing" in res.reason


def test_verify_live_inference_provenance_success_with_client_and_audit(db_session: Session):
    """Confirms that verify_live_inference_provenance returns LIVE INFERENCE VERIFIED
    when full provenance is consistent across client and database audit event.
    """
    from unittest.mock import MagicMock
    from app.services.autonomous_recovery import verify_live_inference_provenance
    from app.models import OperationalEvent

    scenario_id = _setup_disrupted_scenario(db_session, "scn_audit_test")

    # Setup audit event in db
    event = OperationalEvent(
        event_id="evt_audit_verified_99",
        scenario_id=scenario_id,
        event_type="recovery.lifecycle_completed",
        schema_version=1,
        simulation_time=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        source_system="autonomous_recovery_orchestrator",
        source_record_id="lc_99",
        idempotency_key="idemp_key_99",
        payload={
            "final_status": "resolved",
            "winning_plan_id": "plan_b_reroute_line2",
        },
    )
    db_session.add(event)
    db_session.commit()

    record = {
        "final_status": "resolved",
        "selected_plan_id": "plan_b_reroute_line2",
        "audit_event_id": "evt_audit_verified_99",
        "rounds": [
            {
                "round_number": 1,
                "decision": {
                    "decision_source": "meterless_nightshift",
                    "job_id": "job_real_live_99",
                    "competing_attempts_count": 2,
                    "winner_seq": 1,
                    "resolved_model": "nvidia/nemotron-3-ultra-550b-a55b",
                    "judge_outcome": {"winner_seq": 1, "status": "completed"},
                    "receipt_id": "rec_live_signature_99",
                },
            }
        ],
    }

    mock_client = MagicMock()
    mock_client.get_job.return_value = {"id": "job_real_live_99", "status": "completed"}
    mock_client.get_job_attempt.side_effect = lambda jid, seq: {
        "seq": seq,
        "content": f"Decision proposal from attempt {seq}",
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
    }
    mock_client.get_job_outcome.return_value = {"winner_seq": 1, "status": "completed"}
    mock_client.get_job_receipt.return_value = {"receipt_id": "rec_live_signature_99"}

    res = verify_live_inference_provenance(record, client=mock_client, session=db_session)
    assert res.verified is True
    assert res.verdict == "LIVE INFERENCE VERIFIED"
    assert res.competing_attempts_verified is True
    assert res.audit_event_verified is True

    # Confirm read-only: submit_job was never called!
    assert not hasattr(mock_client, "submit_job") or mock_client.submit_job.call_count == 0


def test_powershell_runner_initial_gate_check_aborts():
    """Confirms that scripts/run_live_recovery_test.ps1 strictly aborts if
    NIGHTSHIFT_ALLOW_CLOUD_JOBS is already set prior to execution.
    """
    import os
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_live_recovery_test.ps1"
    assert script_path.exists(), "run_live_recovery_test.ps1 script must exist."

    # Run with gate pre-enabled -> must fail-closed immediately with exit code 1
    env = dict(os.environ, NIGHTSHIFT_ALLOW_CLOUD_JOBS="true")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "ABORT: NIGHTSHIFT_ALLOW_CLOUD_JOBS" in (proc.stdout + proc.stderr)


def test_powershell_runner_contains_safeguards_and_ps51_compatibility():
    """Confirms that scripts/run_live_recovery_test.ps1 implements Safeguard A,
    Safeguard B, and avoids PowerShell 7-only syntax like '??'.
    """
    from pathlib import Path

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_live_recovery_test.ps1"
    content = script_path.read_text(encoding="utf-8")

    # Safeguard A: exit code 2 and failure propagation
    assert "exit 2" in content
    assert "$pyExitCode" in content

    # Safeguard B: restoration verification and critical exit 10
    assert "$restorationVerified" in content
    assert "exit 10" in content

    # PS 5.1 compatibility: no '??' null-coalescing operator
    assert "??" not in content
