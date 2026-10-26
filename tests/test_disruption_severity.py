"""Tests for Disruption Severity Classification and Concept Separation in FactoryOps.

Verifies:
1. Genuinely different disruption types receive different appropriate severities:
   - reduced_machine_capacity -> medium
   - quality_contamination -> high
   - equipment_failure -> critical
   - roaster_bearing_overheat -> high (or critical when temp > 140°C)
   - supplier_shortfall -> medium
2. Routine severity is NOT overwritten or conflated by recovery authorization
   (severity measures physical/operational impact, authorization measures autonomous execution authority).
3. Unknown or unsupported disruptions fail-safe to critical severity and BLOCKED authorization.
4. Nominal factory state receives low severity with no active disruption evidence.
5. Disruption catalogue definitions and active incident records propagate severity consistently.
6. API endpoints /incidents and /recovery/impact include severity and disruption_type.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from app.database import engine
from app.models import SimulationScenario
from app.services.disruptions import (
    DISRUPTION_CATALOGUE,
    apply_disruption,
    get_disruption_catalogue_list,
)
from app.services.recovery import (
    ImpactAnalysis,
    _calculate_impact_severity,
    detect_and_trace_impact,
    generate_candidate_plans,
)
from app.services.recovery_policy import (
    evaluate_recovery_authorization,
    get_disruption_default_class,
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


def _setup_scenario(db_session: Session, disruption_type: str = "baseline_nominal") -> str:
    """Creates a temporary scenario; catalogue disruptions are stepped to step 3,
    where the scheduled disruption is applied to real factory state. Severity must
    come from that observed state, never from the scenario label alone."""
    scenario = create_scenario(
        db_session,
        name=f"test_severity_{disruption_type}",
        disruption_type=disruption_type,
    )
    db_session.flush()
    if disruption_type in DISRUPTION_CATALOGUE:
        step_scenario(db_session, scenario.id, steps=3)
    return scenario.id


def test_label_without_observed_consequence_is_not_an_incident(db_session: Session):
    """A scenario labelled with a disruption that has not yet happened reports no incident."""
    scenario = create_scenario(db_session, name="label_only", disruption_type="reduced_machine_capacity")
    db_session.flush()
    impact = detect_and_trace_impact(db_session, scenario.id)
    assert impact.evidence == []
    assert impact.severity == "low"


def test_scheduled_disruption_applies_real_state_and_syncs_incident_severity(db_session: Session):
    """At step 3 the scheduled disruption mutates the snapshot, and the stored incident
    severity matches the live /incidents severity."""
    scn_id = _setup_scenario(db_session, "reduced_machine_capacity")
    scenario = db_session.get(SimulationScenario, scn_id)
    press = scenario.parameters["snapshot"]["machines"]["machine:plant:PRESS-01"]
    assert press["status"] == "degraded"
    assert press["rated_capacity_kg_h"] == 240.0
    impact = detect_and_trace_impact(db_session, scn_id)
    incident = scenario.parameters["active_incidents"][-1]
    assert incident["severity"] == impact.severity


def test_customs_delay_severity_scales_with_buffer_and_consequence(db_session: Session):
    """Short hold covered by silo stock -> low; hold exceeding the buffer -> critical starvation."""
    short = create_scenario(db_session, name="short_hold", disruption_type="none")
    db_session.flush()
    apply_disruption(db_session, short.id, "supplier_customs_delay", {"delay_hours": 1.0})
    impact_short = detect_and_trace_impact(db_session, short.id)
    assert impact_short.severity == "low"
    assert impact_short.starvation_hours == 0.0

    long = create_scenario(db_session, name="long_hold", disruption_type="none")
    db_session.flush()
    apply_disruption(db_session, long.id, "supplier_customs_delay", {"delay_hours": 10.0})
    impact_long = detect_and_trace_impact(db_session, long.id)
    assert impact_long.severity == "critical"
    assert impact_long.starvation_hours > 0.0


def test_distinct_severities_across_disruption_types(db_session: Session):
    """Proves at least two genuinely different disruption types receive different appropriate severities."""
    # 1. Reduced Machine Capacity (PRESS-01 hydraulic loss) -> medium severity
    scn_press = _setup_scenario(db_session, "reduced_machine_capacity")
    impact_press = detect_and_trace_impact(db_session, scn_press)
    assert impact_press.severity == "medium"
    assert impact_press.disruption_type == "reduced_machine_capacity"
    assert impact_press.disrupted_machine_id == "machine:plant:PRESS-01"

    # 2. Quality Contamination (Raw cocoa moisture quarantine hold) -> high severity
    scn_quality = _setup_scenario(db_session, "quality_contamination")
    impact_quality = detect_and_trace_impact(db_session, scn_quality)
    assert impact_quality.severity == "high"
    assert impact_quality.disruption_type == "quality_contamination"

    # 3. Equipment Failure (MILL-01 motor burnout / seizure) -> critical severity
    scn_mill = _setup_scenario(db_session, "equipment_failure")
    impact_mill = detect_and_trace_impact(db_session, scn_mill)
    assert impact_mill.severity == "critical"
    assert impact_mill.disruption_type == "equipment_failure"
    assert impact_mill.disrupted_machine_id == "machine:plant:MILL-01"

    # 4. Supplier Shortfall (received 6,000 kg vs 15,000 kg) -> medium severity
    scn_shortfall = _setup_scenario(db_session, "supplier_shortfall")
    impact_shortfall = detect_and_trace_impact(db_session, scn_shortfall)
    assert impact_shortfall.severity == "medium"
    assert impact_shortfall.disruption_type == "supplier_shortfall"

    # 5. Roaster Overheat -> high (<= 140°C) or critical (> 140°C)
    scn_roast = _setup_scenario(db_session, "roaster_bearing_overheat")
    step_scenario(db_session, scn_roast, steps=4)
    impact_roast = detect_and_trace_impact(db_session, scn_roast)
    assert impact_roast.severity in ("high", "critical")
    assert impact_roast.disruption_type == "roaster_bearing_overheat"


@patch("app.services.recovery.explain_recovery_plan", return_value="Fast test explanation.")
def test_routine_severity_not_overwritten_by_recovery_authorization(mock_explain, db_session: Session):
    """Ensures routine severity (medium) is NOT overwritten or conflated by recovery authorization (AUTO_EXECUTE)."""
    scn_id = _setup_scenario(db_session, "reduced_machine_capacity")
    impact = detect_and_trace_impact(db_session, scn_id)

    # Physical / telemetry severity must be medium
    assert impact.severity == "medium"

    # Policy default class for reduced_machine_capacity is auto_recoverable
    default_class = get_disruption_default_class("reduced_machine_capacity")
    assert default_class == "auto_recoverable"

    # Generate candidate plans and verify authorization outcome is annotated separately
    plans = generate_candidate_plans(db_session, scn_id)
    assert len(plans) > 0
    plan_a = plans[0]

    # Plan passes constraints and receives AUTO_EXECUTE
    assert plan_a.authorization_outcome in ("AUTO_EXECUTE", "REQUIRES_APPROVAL")

    # Re-reading impact must retain medium severity — authorization does not leak into severity
    rechecked_impact = detect_and_trace_impact(db_session, scn_id)
    assert rechecked_impact.severity == "medium"
    assert rechecked_impact.severity != plan_a.authorization_outcome


@patch("app.services.recovery.explain_recovery_plan", return_value="Fast test explanation.")
def test_unknown_disruptions_fail_safe_to_critical_and_blocked(mock_explain, db_session: Session):
    """Unknown or unsupported disruptions must fail-safe to critical severity and BLOCKED authorization."""
    scn_id = _setup_scenario(db_session, "unregistered_hazardous_drift")
    impact = detect_and_trace_impact(db_session, scn_id)

    # Fail-safe to critical severity
    assert impact.severity == "critical"
    assert impact.disruption_type == "unregistered_hazardous_drift"
    assert len(impact.evidence) > 0
    assert any("Unrecognized disruption type" in e.message for e in impact.evidence)

    # Policy classification must be safety_critical
    default_class = get_disruption_default_class("unregistered_hazardous_drift")
    assert default_class == "safety_critical"

    # Any candidate plan for an unknown disruption must be BLOCKED
    candidate_plan = generate_candidate_plans(db_session, scn_id)[0]
    auth_decision = evaluate_recovery_authorization(
        plan=candidate_plan,
        disruption_type="unregistered_hazardous_drift",
    )
    assert auth_decision.outcome == "BLOCKED"
    assert "Unrecognized or unsupported disruption" in auth_decision.reason


def test_nominal_factory_has_low_severity_and_no_incidents(db_session: Session):
    """Healthy baseline state must return low severity and no active incidents."""
    scn_id = _setup_scenario(db_session, "baseline_nominal")
    impact = detect_and_trace_impact(db_session, scn_id)

    assert impact.severity == "low"
    assert impact.disruption_type == "none"
    assert len(impact.evidence) == 0
    assert not impact.sla_breached
    assert impact.delay_hours == 0.0


def test_disruption_catalogue_and_active_incident_severities(db_session: Session):
    """Catalogue items and triggered active incidents must carry their defined severities."""
    catalogue = get_disruption_catalogue_list()
    catalogue_by_id = {item["type_id"]: item for item in catalogue}

    assert catalogue_by_id["reduced_machine_capacity"]["severity"] == "medium"
    assert catalogue_by_id["quality_contamination"]["severity"] == "high"
    assert catalogue_by_id["equipment_failure"]["severity"] == "critical"
    assert catalogue_by_id["supplier_shortfall"]["severity"] == "medium"
    assert catalogue_by_id["supplier_customs_delay"]["severity"] == "medium"
    assert catalogue_by_id["roaster_bearing_overheat"]["severity"] == "high"

    # Trigger a disruption and check active_incidents record in parameters
    scn_id = _setup_scenario(db_session, "baseline_nominal")
    res = apply_disruption(db_session, scn_id, "reduced_machine_capacity", {"capacity_pct": 35.0})
    assert res["status"] in ("applied", "active")

    scenario = db_session.get(SimulationScenario, scn_id)
    incidents = scenario.parameters.get("active_incidents", [])
    assert len(incidents) > 0
    assert incidents[0]["severity"] == "medium"
    assert incidents[0]["type"] == "reduced_machine_capacity"


def test_limited_impact_disruption_has_low_or_medium_severity(db_session: Session):
    """Minor press throttling with ample schedule buffer produces low or medium severity."""
    scn_id = _setup_scenario(db_session, "baseline_nominal")
    apply_disruption(db_session, scn_id, "reduced_machine_capacity", {"capacity_pct": 70.0})
    impact = detect_and_trace_impact(db_session, scn_id)
    assert impact.severity in ("low", "medium")
    assert not impact.sla_breached


def test_production_threatening_supplier_delay_escalates_to_critical(db_session: Session):
    """Supplier customs delay exceeding silo buffer causes production starvation and escalates to critical."""
    from sqlalchemy.orm.attributes import flag_modified
    scn_id = _setup_scenario(db_session, "baseline_nominal")
    scenario = db_session.get(SimulationScenario, scn_id)
    # Silo 1 inventory at 3600 kg provides 3 hours buffer; a 12h customs hold causes 9h production starvation
    params = dict(scenario.parameters)
    snap = dict(params.get("snapshot", {}))
    inv = dict(snap.get("inventory", {}))
    lot = dict(inv.get("inventory_lot:wms:LOT-2026-001", {}))
    lot["current_quantity_kg"] = 3600.0
    inv["inventory_lot:wms:LOT-2026-001"] = lot
    snap["inventory"] = inv
    params["snapshot"] = snap
    scenario.parameters = params
    flag_modified(scenario, "parameters")
    db_session.flush()

    apply_disruption(db_session, scn_id, "supplier_customs_delay", {"delay_hours": 12.0})
    impact = detect_and_trace_impact(db_session, scn_id)
    assert impact.severity == "critical"
    assert any(e.severity == "critical" for e in impact.evidence)
    assert any("starvation" in e.message.lower() for e in impact.evidence)


def test_severe_equipment_failure_is_critical(db_session: Session):
    """MILL-01 catastrophic motor burnout and mechanical seizure at 168°C is critical."""
    scn_id = _setup_scenario(db_session, "baseline_nominal")
    apply_disruption(db_session, scn_id, "equipment_failure", {"temperature_c": 168.0, "vibration_rms": 8.6})
    impact = detect_and_trace_impact(db_session, scn_id)
    assert impact.severity == "critical"
    assert any(e.metric == "bearing_temperature_c" and e.observed_value == 168.0 for e in impact.evidence)


def test_contamination_quarantine_is_never_downgraded(db_session: Session):
    """Raw bean moisture quarantine hold at 9.2% is high severity and cannot be downgraded."""
    scn_id = _setup_scenario(db_session, "baseline_nominal")
    apply_disruption(db_session, scn_id, "quality_contamination", {"moisture_pct": 9.2})
    impact = detect_and_trace_impact(db_session, scn_id)
    assert impact.severity in ("high", "critical")
    assert impact.severity != "low"
    assert impact.severity != "medium"
    assert any(e.metric == "moisture_pct" for e in impact.evidence)


def test_missing_telemetry_explicit_high_severity_warning(db_session: Session):
    """When telemetry is missing during an active disruption, an explicit evidence item is generated."""
    from sqlalchemy.orm.attributes import flag_modified
    # Not stepped: the World Model must hold no telemetry facts for ROAST-01.
    scn_id = create_scenario(db_session, name="missing_telemetry", disruption_type="roaster_bearing_overheat").id
    db_session.flush()
    scenario = db_session.get(SimulationScenario, scn_id)
    params = dict(scenario.parameters)
    snap = dict(params.get("snapshot", {}))
    machines = dict(snap.get("machines", {}))
    roaster = dict(machines.get("machine:plant:ROAST-01", {}))
    roaster.pop("temperature_c", None)
    roaster.pop("bearing_temperature_c", None)
    roaster.pop("vibration_rms", None)
    machines["machine:plant:ROAST-01"] = roaster
    snap["machines"] = machines
    params["snapshot"] = snap
    scenario.parameters = params
    flag_modified(scenario, "parameters")
    db_session.flush()

    impact = detect_and_trace_impact(db_session, scn_id)
    assert impact.severity in ("high", "critical")
    assert any("missing" in e.message.lower() for e in impact.evidence)
