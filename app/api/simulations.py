"""FastAPI simulation router."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import SimulationScenario
from app.services.disruptions import (
    apply_disruption,
    get_disruption_catalogue_list,
    get_eligible_disruptions,
    select_random_disruption,
)
from app.services.production_line import (
    calculate_inventory_forecast,
    derive_production_line_stages,
)
from app.services.recovery import (
    apply_recovery_decision,
    detect_and_trace_impact,
    generate_candidate_plans,
)
from app.services.seed import seed_baseline_factory
from app.services.simulation import (
    create_scenario,
    get_scenario_timeline,
    get_scenario_world_model,
    is_awaiting_recovery_decision,
    is_autonomous_recovery_enabled,
    is_scenario_eligible_for_auto_tick,
    requires_human_decision,
    step_scenario,
)

router = APIRouter(prefix="/simulations", tags=["Simulations"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CreateScenarioRequest(BaseModel):
    name: str = Field(..., json_schema_extra={"example": "Cocoa Roasting Disruption Run"})
    disruption_type: str = Field(default="none", json_schema_extra={"example": "roaster_bearing_overheat"})
    random_seed: int = Field(default=42, json_schema_extra={"example": 42})
    step_size_minutes: int = Field(default=15, json_schema_extra={"example": 15})
    scenario_id: Optional[str] = Field(default=None, json_schema_extra={"example": "scn_cocoa_01"})
    autonomous_recovery: bool = Field(default=True, json_schema_extra={"example": True})


class StepScenarioRequest(BaseModel):
    steps: int = Field(default=1, ge=1, le=100, json_schema_extra={"example": 1})
    idempotency_key: Optional[str] = Field(default=None, json_schema_extra={"example": "step_key_123"})
    target_step: Optional[int] = Field(default=None, json_schema_extra={"example": 4})


class ScenarioResponse(BaseModel):
    id: str
    name: str
    disruption_type: str
    status: str
    random_seed: int
    simulation_time: str
    current_step: int
    step_size_minutes: int
    snapshot: Dict[str, Any]
    parameters: Dict[str, Any] = Field(default_factory=dict)


class StepResponse(BaseModel):
    scenario_id: str
    current_step: int
    simulation_time: str
    status: str
    disruption_type: str
    events_emitted_count: int
    snapshot: Dict[str, Any]
    parameters: Dict[str, Any] = Field(default_factory=dict)


@router.post("/seed", status_code=status.HTTP_200_OK)
def seed_database(db: Session = Depends(get_db)):
    """Seeds baseline factory operational data in factory_source and public schemas."""
    res = seed_baseline_factory(db)
    db.commit()
    return res


@router.get("", response_model=List[ScenarioResponse])
def list_simulations(
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Lists recent simulation scenarios ordered by creation time descending."""
    query = db.query(SimulationScenario)
    if status:
        query = query.filter(SimulationScenario.status == status)
    scenarios = query.order_by(SimulationScenario.created_at.desc()).limit(limit).all()
    return [
        ScenarioResponse(
            id=s.id,
            name=s.name,
            disruption_type=s.disruption_type,
            status=s.status,
            random_seed=s.random_seed,
            simulation_time=s.simulation_time.isoformat(),
            current_step=s.current_step,
            step_size_minutes=s.step_size_minutes,
            snapshot=s.parameters.get("snapshot", {}) if s.parameters else {},
            parameters=s.parameters or {},
        )
        for s in scenarios
    ]


@router.post("", response_model=ScenarioResponse, status_code=status.HTTP_201_CREATED)
def create_new_simulation(
    payload: CreateScenarioRequest,
    db: Session = Depends(get_db),
):
    """Initializes a new isolated simulation scenario."""
    try:
        scenario = create_scenario(
            session=db,
            name=payload.name,
            disruption_type=payload.disruption_type,
            random_seed=payload.random_seed,
            step_size_minutes=payload.step_size_minutes,
            scenario_id=payload.scenario_id,
            autonomous_recovery=payload.autonomous_recovery,
        )
        db.commit()
        return ScenarioResponse(
            id=scenario.id,
            name=scenario.name,
            disruption_type=scenario.disruption_type,
            status=scenario.status,
            random_seed=scenario.random_seed,
            simulation_time=scenario.simulation_time.isoformat(),
            current_step=scenario.current_step,
            step_size_minutes=scenario.step_size_minutes,
            snapshot=scenario.parameters.get("snapshot", {}) if scenario.parameters else {},
            parameters=scenario.parameters or {},
        )
    except ValueError as e:
        db.rollback()
        err_msg = str(e)
        if "already exists" in err_msg.lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err_msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{scenario_id}/step", response_model=StepResponse)
def advance_simulation_step(
    scenario_id: str,
    payload: StepScenarioRequest,
    db: Session = Depends(get_db),
):
    """Advances an active simulation scenario by N discrete steps."""
    try:
        result = step_scenario(
            session=db,
            scenario_id=scenario_id,
            steps=payload.steps,
            idempotency_key=payload.idempotency_key,
            target_step=payload.target_step,
        )
        db.commit()
        return StepResponse(**result)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{scenario_id}", response_model=ScenarioResponse)
def get_simulation_state(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Retrieves current simulation scenario metadata and isolated state snapshot."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    return ScenarioResponse(
        id=scenario.id,
        name=scenario.name,
        disruption_type=scenario.disruption_type,
        status=scenario.status,
        random_seed=scenario.random_seed,
        simulation_time=scenario.simulation_time.isoformat(),
        current_step=scenario.current_step,
        step_size_minutes=scenario.step_size_minutes,
        snapshot=scenario.parameters.get("snapshot", {}),
        parameters=scenario.parameters or {},
    )


@router.get("/{scenario_id}/timeline")
def get_simulation_timeline_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns the chronological sequence of operational events emitted for this scenario."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    events = get_scenario_timeline(db, scenario_id)
    return {
        "scenario_id": scenario_id,
        "event_count": len(events),
        "timeline": events,
    }


class RecoveryDecisionRequest(BaseModel):
    plan_id: str = Field(..., json_schema_extra={"example": "plan_a_thermal_flush"})
    decision: str = Field(..., json_schema_extra={"example": "approved"})  # approved | rejected
    operator_id: str = Field(..., json_schema_extra={"example": "OP-774"})
    rationale: str = Field(..., json_schema_extra={"example": "Preserves customer delivery deadline with lowest cost."})


@router.get("/{scenario_id}/world-model")
def get_scenario_world_model_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns the Meterless World Model canonical state, entity graph, and verified facts."""
    try:
        wm = get_scenario_world_model(db, scenario_id)
        return {
            "scenario_id": scenario_id,
            "canonical_shape": wm.state_shape(),
            "audit_log": [
                {
                    "seq": a.seq,
                    "at": a.at,
                    "kind": a.kind,
                    "actor": a.actor,
                    "summary": a.summary,
                }
                for a in wm.audit()
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{scenario_id}/recovery/impact")
def get_recovery_impact_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Detects active disruptions and traces downstream batch/order SLA consequences."""
    try:
        impact = detect_and_trace_impact(db, scenario_id)
        return impact
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{scenario_id}/recovery/plans")
def get_recovery_plans_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Produces ranked recovery candidate plans with deterministic hard constraints and Nemotron explanations."""
    try:
        plans = generate_candidate_plans(db, scenario_id)
        return {
            "scenario_id": scenario_id,
            "candidate_count": len(plans),
            "plans": plans,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{scenario_id}/recovery/decide")
def submit_recovery_decision_endpoint(
    scenario_id: str,
    payload: RecoveryDecisionRequest,
    db: Session = Depends(get_db),
):
    """Enforces human-in-the-loop operator approval before applying recovery plan mutations."""
    if payload.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")
    try:
        result = apply_recovery_decision(
            session=db,
            scenario_id=scenario_id,
            plan_id=payload.plan_id,
            decision=payload.decision,  # type: ignore
            operator_id=payload.operator_id,
            rationale=payload.rationale,
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{scenario_id}/factory-state")
def get_factory_state_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns the comprehensive multi-stage factory operational snapshot across all 7 stages."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    snapshot = scenario.parameters.get("snapshot", {})
    return {
        "scenario_id": scenario.id,
        "name": scenario.name,
        "disruption_type": scenario.disruption_type,
        "status": scenario.status,
        "current_step": scenario.current_step,
        "simulation_time": scenario.simulation_time.isoformat(),
        "stages": [
            "Cleaning",
            "Roasting",
            "Winnowing",
            "Grinding",
            "Pressing",
            "Pulverizing",
            "Packing",
        ],
        "machines": snapshot.get("machines", {}),
        "inventory": snapshot.get("inventory", {}),
        "batches": snapshot.get("batches", {}),
        "orders": snapshot.get("orders", {}),
    }


@router.get("/{scenario_id}/incidents")
def get_scenario_incidents_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns active operational incidents, anomaly evidence, impacted assets, and SLA financial risk."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    try:
        impact = detect_and_trace_impact(db, scenario_id)
        has_incidents = len(impact.evidence) > 0 or impact.sla_breached
        return {
            "scenario_id": scenario_id,
            "has_active_incidents": has_incidents,
            "incident_count": len(impact.evidence),
            "severity": impact.severity,
            "disruption_type": impact.disruption_type,
            "disrupted_machine_id": impact.disrupted_machine_id if has_incidents else None,
            "affected_batch_id": impact.affected_batch_id if has_incidents else None,
            "affected_order_id": impact.affected_order_id if has_incidents else None,
            "affected_customer": impact.affected_customer if has_incidents else None,
            "sla_breached": impact.sla_breached,
            "potential_penalty_usd": impact.potential_penalty_usd,
            "evidence": [
                {
                    "metric": e.metric,
                    "observed_value": e.observed_value,
                    "threshold_value": e.threshold_value,
                    "unit": e.unit,
                    "severity": e.severity,
                    "message": e.message,
                }
                for e in impact.evidence
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{scenario_id}/recovery-options")
def get_recovery_options_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns ranked candidate recovery options with deterministic constraints and cost calculations."""
    try:
        plans = generate_candidate_plans(db, scenario_id)
        return {
            "scenario_id": scenario_id,
            "candidate_count": len(plans),
            "plans": plans,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{scenario_id}/pause")
def pause_simulation_auto_advance(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Pauses automatic 5-second simulation progression for this scenario."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    scenario.parameters = {
        **scenario.parameters,
        "auto_advance": False,
    }
    db.commit()
    return {
        "scenario_id": scenario_id,
        "auto_advance": False,
        "status": "paused",
    }


@router.post("/{scenario_id}/resume")
def resume_simulation_auto_advance(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Resumes automatic 5-second simulation progression for this scenario."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    scenario.parameters = {
        **scenario.parameters,
        "auto_advance": True,
    }
    db.commit()
    return {
        "scenario_id": scenario_id,
        "auto_advance": True,
        "status": "resumed",
    }


@router.get("/{scenario_id}/timing")
def get_simulation_timing_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns real-time status of the hybrid simulation timing driver."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    eligible, reason = is_scenario_eligible_for_auto_tick(scenario)
    awaiting = is_awaiting_recovery_decision(scenario)
    recovery_info = scenario.parameters.get("recovery", {})
    auto_adv = scenario.parameters.get("auto_advance", True)

    return {
        "scenario_id": scenario_id,
        "auto_advance": auto_adv,
        "interval_seconds": 5,
        "current_step": scenario.current_step,
        "simulation_time": scenario.simulation_time.isoformat(),
        "scenario_status": scenario.status,
        "is_awaiting_decision": awaiting,
        "requires_human_decision": requires_human_decision(scenario),
        "autonomous_recovery_enabled": is_autonomous_recovery_enabled(scenario),
        "is_eligible_for_auto_tick": eligible,
        "ineligibility_reason": None if eligible else reason,
        "recovery": recovery_info or None,
    }


@router.post("/{scenario_id}/auto-tick")
def trigger_simulation_auto_tick_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Executes a single automatic tick if the scenario is eligible."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    eligible, reason = is_scenario_eligible_for_auto_tick(scenario)
    if not eligible:
        return {
            "ticked": False,
            "reason": reason,
            "current_step": scenario.current_step,
            "simulation_time": scenario.simulation_time.isoformat(),
            "scenario_status": scenario.status,
        }

    result = step_scenario(db, scenario_id, steps=1)
    db.commit()
    return {
        "ticked": True,
        "current_step": result["current_step"],
        "simulation_time": result["simulation_time"],
        "scenario_status": result["status"],
    }


# =============================================================================
# Disruptions Simulator & Dynamic Production Line Endpoints
# =============================================================================

class TriggerDisruptionRequest(BaseModel):
    disruption_type: str = Field(..., json_schema_extra={"example": "supplier_customs_delay"})
    parameters: Optional[Dict[str, Any]] = Field(default=None)


@router.get("/{scenario_id}/disruptions/catalogue")
def get_disruptions_catalogue_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns the disruption simulator catalogue with live scenario eligibility flags."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    eligible_catalogue = get_eligible_disruptions(db, scenario_id)
    return {
        "scenario_id": scenario_id,
        "disruptions": eligible_catalogue,
    }


@router.post("/{scenario_id}/disruptions/trigger")
def trigger_disruption_endpoint(
    scenario_id: str,
    payload: TriggerDisruptionRequest,
    db: Session = Depends(get_db),
):
    """Triggers an industrial disruption from the catalogue or via Random selection."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    requested_type = payload.disruption_type.strip()
    params = payload.parameters or {}

    try:
        if requested_type.lower() == "random":
            chosen_type, gen_params = select_random_disruption(db, scenario_id)
            params = {**gen_params, **params}
            result = apply_disruption(db, scenario_id, chosen_type, params)
            result["random_selection"] = True
        else:
            result = apply_disruption(db, scenario_id, requested_type, params)
            result["random_selection"] = False

        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to apply disruption: {str(e)}")


@router.get("/{scenario_id}/production-line")
def get_production_line_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns authoritative derived status for all seven manufacturing stages."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    snapshot = scenario.parameters.get("snapshot", {})
    recovery_info = scenario.parameters.get("recovery", {})
    stages = derive_production_line_stages(snapshot, scenario.status, recovery_info)
    prod_cycle = snapshot.get("production_cycle") or {
        "cycle_number": 1,
        "active_stage_num": 1,
        "stage_step_counter": 0,
        "completed_cycles": 0,
    }
    return {
        "scenario_id": scenario_id,
        "current_step": scenario.current_step,
        "simulation_time": scenario.simulation_time.isoformat(),
        "production_cycle": prod_cycle,
        "stages": stages,
    }


@router.get("/{scenario_id}/supply-chain")
def get_supply_chain_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Returns inventory depletion lookahead forecast, stockout risk, and shipment tracking."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    snapshot = scenario.parameters.get("snapshot", {})
    forecast = calculate_inventory_forecast(snapshot, scenario.step_size_minutes)
    return {
        "scenario_id": scenario_id,
        "current_step": scenario.current_step,
        "simulation_time": scenario.simulation_time.isoformat(),
        **forecast,
    }


# =============================================================================
# Autonomous Recovery Endpoints (Stages A through H)
# =============================================================================

class AutonomousRecoveryRunRequest(BaseModel):
    max_rounds: int = Field(default=3, ge=1, le=5, json_schema_extra={"example": 3})
    auto_advance_steps: bool = Field(default=True, json_schema_extra={"example": True})
    provider: Optional[str] = Field(default=None, json_schema_extra={"example": "meterless"})


@router.post("/{scenario_id}/autonomous-recovery/run")
def run_autonomous_recovery_endpoint(
    scenario_id: str,
    payload: AutonomousRecoveryRunRequest = AutonomousRecoveryRunRequest(),
    db: Session = Depends(get_db),
):
    """Executes the full 8-stage autonomous recovery lifecycle using Nightshift/Nemotron."""
    from app.services.autonomous_recovery import LifecycleAlreadyRunning, run_autonomous_recovery_lifecycle
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    try:
        lifecycle_record = run_autonomous_recovery_lifecycle(
            session=db,
            scenario_id=scenario_id,
            max_rounds=payload.max_rounds,
            provider=payload.provider,
            auto_advance_steps=payload.auto_advance_steps,
        )
        from dataclasses import asdict
        return asdict(lifecycle_record)
    except LifecycleAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Autonomous recovery failed: {str(e)}")


@router.get("/{scenario_id}/autonomous-recovery/history")
def get_autonomous_recovery_history_endpoint(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """Retrieves the autonomous recovery audit trail and history for a scenario."""
    scenario = db.get(SimulationScenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    history = scenario.parameters.get("autonomous_recovery_history")
    from app.models import OperationalEvent
    from sqlalchemy import select
    events = db.execute(
        select(OperationalEvent)
        .where(
            OperationalEvent.scenario_id == scenario_id,
            OperationalEvent.event_type.in_([
                "recovery.autonomous_executed",
                "recovery.lifecycle_completed",
                "recovery.approval_required",
                "recovery.blocked",
                "recovery.completed",
            ]),
        )
        .order_by(OperationalEvent.recorded_at.desc())
    ).scalars().all()

    return {
        "scenario_id": scenario_id,
        "has_autonomous_history": history is not None,
        "autonomous_recovery_enabled": is_autonomous_recovery_enabled(scenario),
        "recovery": scenario.parameters.get("recovery"),
        "summary": history,
        "audit_events": [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "recorded_at": e.recorded_at.isoformat(),
                "payload": e.payload,
            }
            for e in events
        ],
    }

