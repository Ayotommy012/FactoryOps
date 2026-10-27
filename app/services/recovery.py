"""Recovery Intelligence Service for Virtual Cocoa Operations.

Integrates deterministic hard constraints, graph-based consequence tracing,
ranked recovery plan generation, NVIDIA Nemotron operational explanations
(with deterministic fallback), and human operator approval workflows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Literal, Optional
from urllib import request as urllib_request
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import OperationalEvent, SimulationScenario
from app.services.simulation import get_scenario_world_model
from app.usl import UnifiedSemanticLayerService
from app.world_model import Provenance, WorldModel

logger = logging.getLogger("factoryops.recovery")


@dataclass
class DisruptionEvidence:
    metric: str
    observed_value: float
    threshold_value: float
    unit: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str
    record_kind: str = "measured_fact"
    provenance_source: str = "NASA/UC_IMS_Bearing_Run_to_Failure"


@dataclass
class ImpactAnalysis:
    scenario_id: str
    disrupted_machine_id: str
    affected_batch_id: str
    affected_order_id: str
    affected_customer: str
    order_deadline: str
    unintervened_completion: str
    delay_hours: float
    sla_breached: bool
    potential_penalty_usd: float
    remaining_quantity_kg: float
    evidence: List[DisruptionEvidence]
    provenance_summary: Dict[str, Any] = field(default_factory=dict)
    severity: Literal["low", "medium", "high", "critical"] = "low"
    disruption_type: str = "none"
    starvation_hours: float = 0.0
    mitigation: Optional[str] = None


SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# In-memory explanation cache: cache_key -> (explanation, timestamp)
_EXPLANATION_CACHE: Dict[str, Tuple[str, float]] = {}
_EXPLANATION_CACHE_TTL_SEC = 300.0  # 5 minutes


def _make_explanation_cache_key(impact: ImpactAnalysis, plan: RecoveryPlan, provider: str) -> str:
    # Keyed on the incident and the plan's decision-relevant attributes only.
    # projected_completion / buffer drift every simulated step; including them
    # turned every poll into a fresh upstream call.
    raw = (
        f"{impact.scenario_id}|{impact.disruption_type}|{impact.disrupted_machine_id}|{impact.severity}|"
        f"{plan.plan_id}|{plan.strategy}|{plan.feasibility_verdict}|{plan.meets_deadline}|"
        f"{round(plan.total_cost_usd, -1):.0f}|{provider}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _usable_raw_bean_kg(snapshot: Dict[str, Any]) -> float:
    """Usable raw-bean stock, from the same source the simulator consumes (Silo 1)."""
    silo = snapshot.get("silos", {}).get("silo_01")
    if silo and "current_stock_kg" in silo:
        return float(silo["current_stock_kg"])
    lot = snapshot.get("inventory", {}).get("inventory_lot:wms:LOT-2026-001", {})
    return float(lot.get("current_quantity_kg", 0.0))


def _calculate_impact_severity(
    evidence: List[DisruptionEvidence],
    sla_breached: bool = False,
    starvation_hours: float = 0.0,
    has_safety_hazard: bool = False,
    potential_penalty_usd: float = 0.0,
    default_severity: Optional[Literal["low", "medium", "high", "critical"]] = None,
) -> Literal["low", "medium", "high", "critical"]:
    # 1. Contamination / genuine safety hazards or production starvation > 0 or penalty >= 1000 => critical
    if has_safety_hazard or starvation_hours > 0.0 or potential_penalty_usd >= 1000.0:
        return "critical"

    # 2. Check evidence items
    max_level = 0
    max_sev: Literal["low", "medium", "high", "critical"] = "low"
    for ev in evidence:
        sev = getattr(ev, "severity", "medium")
        level = SEVERITY_ORDER.get(sev, 2)
        if level > max_level:
            max_level = level
            max_sev = sev  # type: ignore

    # 3. Check SLA breach / financial penalty escalation
    if potential_penalty_usd >= 500.0:
        if max_level < SEVERITY_ORDER["high"]:
            max_sev = "high"
            max_level = SEVERITY_ORDER["high"]
    elif sla_breached:
        if max_level < SEVERITY_ORDER["medium"]:
            max_sev = "medium"
            max_level = SEVERITY_ORDER["medium"]

    # 4. If no evidence and no default given, check if nominal
    if not evidence:
        return default_severity if default_severity else "low"

    # If default_severity provided and higher than current, consider it
    if default_severity and SEVERITY_ORDER.get(default_severity, 0) > max_level:
        max_sev = default_severity

    return max_sev


@dataclass
class RecoveryPlan:
    plan_id: str
    title: str
    strategy: Literal["maintenance", "reroute", "split_expedite"]
    rank: int
    feasibility_verdict: Literal["feasible", "infeasible"]
    hard_constraints_met: List[str]
    estimated_downtime_hours: float
    projected_completion: str
    meets_deadline: bool
    buffer_hours: float
    total_cost_usd: float
    cost_breakdown: Dict[str, float]
    action_steps: List[str]
    explanation: Optional[str] = None
    authorization_outcome: Optional[str] = None
    authorization_reason: Optional[str] = None


@dataclass
class RecoveryDecisionResult:
    scenario_id: str
    plan_id: str
    decision: Literal["approved", "rejected"]
    operator_id: str
    rationale: str
    applied_at: str
    event_id: str
    updated_scenario_status: str


def detect_and_trace_impact(session: Session, scenario_id: str) -> ImpactAnalysis:
    """Uses the Meterless World Model and scenario snapshot to detect active disruptions
    and trace cascading impacts downstream to orders and SLA commitments.
    """
    scenario = session.execute(
        select(SimulationScenario).where(SimulationScenario.id == scenario_id)
    ).scalar_one_or_none()
    if not scenario:
        raise ValueError(f"Scenario '{scenario_id}' not found.")

    snapshot = scenario.parameters.get("snapshot", {})
    world = get_scenario_world_model(session, scenario_id)
    graph = world.view("graph")

    # 0. Check for unknown / unsupported disruption type (fail-safe to critical)
    known_disruptions = {
        "baseline_nominal",
        "healthy_baseline",
        "nominal",
        "none",
        "combined",
        "interacting_constraint",
        "roaster_bearing_overheat",
        "equipment_failure",
        "reduced_machine_capacity",
        "quality_contamination",
        "supplier_customs_delay",
        "supplier_shortfall",
    }
    if scenario.disruption_type and scenario.disruption_type not in known_disruptions:
        evidence = [
            DisruptionEvidence(
                metric="unrecognized_anomaly",
                observed_value=1.0,
                threshold_value=0.0,
                unit="flag",
                severity="critical",
                message=f"Unrecognized disruption type '{scenario.disruption_type}'. Fail-safe to critical severity.",
                record_kind="disruption_classification",
                provenance_source="FactoryOps Disruption Classifier",
            )
        ]
        current_sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
        deadline_iso = "2026-10-22T14:00:00+00:00"
        deadline = datetime.fromisoformat(deadline_iso)
        unintervened_finish = current_sim_time + timedelta(hours=24.0)
        return ImpactAnalysis(
            scenario_id=scenario_id,
            disrupted_machine_id="plant:core",
            affected_batch_id="production_batch:mes:BATCH-101",
            affected_order_id="customer_order:erp:ORD-9402",
            affected_customer="Artisan Chocolatier BV",
            order_deadline=deadline.isoformat(),
            unintervened_completion=unintervened_finish.isoformat(),
            delay_hours=24.0,
            sla_breached=True,
            potential_penalty_usd=1000.0,
            remaining_quantity_kg=2500.0,
            evidence=evidence,
            provenance_summary={
                "disruption_classifier": "FactoryOps Unknown Disruption Fail-Safe Protocol",
                "semantic_layer": "Unified Semantic Layer (USL)",
            },
            severity="critical",
            disruption_type=scenario.disruption_type,
        )

    # 1. Check for Supply Chain Disruption: Supplier Shortfall
    # Detection below is evidence-only: each branch fires on observed state in the
    # snapshot, never on the scenario's disruption label alone (a label with no
    # observed consequence is not an incident, and a recovered incident must clear).
    shipments = snapshot.get("inbound_shipments", {})
    shp = next(iter(shipments.values()), None)
    mitigation = snapshot.get("supply_chain", {}).get("mitigation")
    is_shortfall = bool(
        shp and (float(shp.get("quantity_kg", 15000.0)) < 15000.0 or "shortfall" in str(shp.get("disruption_reason", "")).lower())
    )
    if is_shortfall:
        delivered_kg = float(shp.get("quantity_kg", 15000.0))

        current_silo_kg = _usable_raw_bean_kg(snapshot)
        deficit_kg = max(0.0, 15000.0 - delivered_kg)
        deficit_pct = (deficit_kg / 15000.0) * 100.0

        batch_id = "production_batch:mes:BATCH-101"
        order_id = "customer_order:erp:ORD-9402"
        batch_snap = snapshot.get("batches", {}).get(batch_id, {})
        order_snap = snapshot.get("orders", {}).get(order_id, {})
        planned_kg = float(batch_snap.get("planned_quantity_kg", 2500.0))
        actual_kg = float(batch_snap.get("actual_quantity_kg", 0.0))
        remaining_kg = max(0.0, planned_kg - actual_kg)
        current_sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
        deadline_iso = order_snap.get("deadline", "2026-10-22T14:00:00+00:00")
        deadline = datetime.fromisoformat(deadline_iso)
        delay_h = 2.0 if deficit_pct > 20.0 else 0.5
        if mitigation == "spot_market_buffer":
            delay_h = 0.5  # certified buffer lot received; only receiving/QA time remains
        unintervened_finish = current_sim_time + timedelta(hours=delay_h + (remaining_kg / 1200.0))
        sla_breached = unintervened_finish > deadline
        potential_penalty = 350.0 if sla_breached else 0.0

        # Calculate starvation / stockout impact:
        stockout_hours = round(current_silo_kg / 1200.0, 2)
        starvation_hours = round(max(0.0, delay_h - stockout_hours), 2) if (delivered_kg + current_silo_kg < planned_kg) else 0.0

        if starvation_hours > 0.0:
            ev_sev = "critical"
        elif deficit_pct > 75.0:
            ev_sev = "high"
        elif deficit_pct >= 20.0:
            ev_sev = "medium"
        else:
            ev_sev = "low"

        evidence = [
            DisruptionEvidence(
                metric="delivered_quantity_kg",
                observed_value=delivered_kg,
                threshold_value=15000.0,
                unit="kg",
                severity=ev_sev,
                message=f"Inbound cocoa shipment shortfall: received {delivered_kg:.0f} kg vs 15,000 kg ordered (-{deficit_kg:.0f} kg deficit, {deficit_pct:.1f}%).",
                record_kind="supply_chain_telemetry",
                provenance_source="Rotterdam Portbase / Ocean Freight Manifest",
            ),
        ]
        calc_sev = _calculate_impact_severity(
            evidence,
            sla_breached=sla_breached,
            starvation_hours=starvation_hours,
            potential_penalty_usd=potential_penalty,
        )

        return ImpactAnalysis(
            scenario_id=scenario_id,
            disrupted_machine_id="silo:plant:SILO-01",
            affected_batch_id=batch_id,
            affected_order_id=order_id,
            affected_customer=order_snap.get("customer_name", "Artisan Chocolatier BV"),
            order_deadline=deadline.isoformat(),
            unintervened_completion=unintervened_finish.isoformat(),
            delay_hours=delay_h,
            sla_breached=sla_breached,
            potential_penalty_usd=potential_penalty,
            remaining_quantity_kg=remaining_kg,
            evidence=evidence,
            provenance_summary={
                "supply_chain": "Port of Rotterdam Harbor Customs Clearance & Portbase EDI",
                "physical_observations": "FactoryOps Silo Weighbridge Telemetry",
                "scenario_projections": "FactoryOps Discrete-Event Simulator (15-min steps)",
                "semantic_layer": "Unified Semantic Layer (USL)",
            },
            severity=calc_sev,
            disruption_type="supplier_shortfall",
            starvation_hours=starvation_hours,
            mitigation=mitigation,
        )

    # 2. Check for Supply Chain Disruption: Customs Delay (until the shipment is delivered)
    is_customs_delay = bool(
        shp
        and shp.get("status") != "delivered"
        and (
            shp.get("status") in ("customs_hold", "delayed")
            or shp.get("revised_arrival_step", 8) > shp.get("scheduled_arrival_step", 8)
        )
    )
    if is_customs_delay:
        step_size_h = (scenario.step_size_minutes or 15) / 60.0
        remaining_delay_steps = max(0, int(shp.get("revised_arrival_step", 8)) - int(shp.get("scheduled_arrival_step", 8)))
        delay_h = float(shp.get("delay_hours") or (remaining_delay_steps * step_size_h))
        if mitigation == "customs_expedited":
            delay_h = round(remaining_delay_steps * step_size_h, 2)
        current_silo_kg = _usable_raw_bean_kg(snapshot)
        stockout_hours = round(current_silo_kg / 1200.0, 2)
        starvation_hours = round(max(0.0, delay_h - stockout_hours), 2)

        batch_id = "production_batch:mes:BATCH-101"
        order_id = "customer_order:erp:ORD-9402"
        batch_snap = snapshot.get("batches", {}).get(batch_id, {})
        order_snap = snapshot.get("orders", {}).get(order_id, {})
        planned_kg = float(batch_snap.get("planned_quantity_kg", 2500.0))
        actual_kg = float(batch_snap.get("actual_quantity_kg", 0.0))
        remaining_kg = max(0.0, planned_kg - actual_kg)
        current_sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
        deadline_iso = order_snap.get("deadline", "2026-10-22T14:00:00+00:00")
        deadline = datetime.fromisoformat(deadline_iso)
        # Production only stalls once Silo 1 runs dry, so the line delay is the
        # starvation gap, not the raw shipment delay.
        unintervened_finish = current_sim_time + timedelta(hours=starvation_hours + (remaining_kg / 1200.0))
        sla_breached = unintervened_finish > deadline
        potential_penalty = 350.0 if sla_breached else 0.0
        buffer_margin_h = stockout_hours - delay_h

        if starvation_hours > 0:
            ev_sev = "critical"
        elif sla_breached:
            ev_sev = "high"
        elif buffer_margin_h < 2.0:
            ev_sev = "medium"  # silo covers the hold, but with under 2h of margin
        else:
            ev_sev = "low"

        evidence = [
            DisruptionEvidence(
                metric="shipment_status",
                observed_value=float(delay_h),
                threshold_value=0.0,
                unit="hours delay",
                severity=ev_sev,
                message=f"Inbound shipment {shp.get('shipment_code', 'SHP-2026-COCOA-01')} customs clearance hold (+{delay_h}h).",
                record_kind="supply_chain_telemetry",
                provenance_source="Rotterdam Portbase / Ocean Freight Manifest",
            ),
            DisruptionEvidence(
                metric="silo_stockout_risk",
                observed_value=stockout_hours,
                threshold_value=delay_h,
                unit="hours buffer",
                severity=ev_sev,
                message=f"Silo 1 inventory ({current_silo_kg:.0f} kg) depletes in {stockout_hours}h; factory faces {starvation_hours}h production starvation gap.",
                record_kind="inventory_projection",
                provenance_source="FactoryOps Supply Chain Forecaster",
            ),
        ]
        potential_penalty = 350.0 if sla_breached else 0.0

        calc_sev = _calculate_impact_severity(
            evidence,
            sla_breached=sla_breached,
            starvation_hours=starvation_hours,
            potential_penalty_usd=potential_penalty,
        )

        return ImpactAnalysis(
            scenario_id=scenario_id,
            disrupted_machine_id="silo:plant:SILO-01",
            affected_batch_id=batch_id,
            affected_order_id=order_id,
            affected_customer=order_snap.get("customer_name", "Artisan Chocolatier BV"),
            order_deadline=deadline.isoformat(),
            unintervened_completion=unintervened_finish.isoformat(),
            delay_hours=delay_h,
            sla_breached=sla_breached,
            potential_penalty_usd=potential_penalty,
            remaining_quantity_kg=remaining_kg,
            evidence=evidence,
            provenance_summary={
                "supply_chain": "Port of Rotterdam Harbor Customs Clearance & Portbase EDI",
                "physical_observations": "FactoryOps Silo Weighbridge Telemetry",
                "scenario_projections": "FactoryOps Discrete-Event Simulator (15-min steps)",
                "semantic_layer": "Unified Semantic Layer (USL)",
            },
            severity=calc_sev,
            disruption_type="supplier_customs_delay",
            starvation_hours=starvation_hours,
            mitigation=mitigation,
        )

    # 3. Check for active disruptions: MILL-01 (equipment_failure)
    mill = graph.entity("machine:plant:MILL-01")
    mill_snap = snapshot.get("machines", {}).get("machine:plant:MILL-01", {})
    mill_failed = mill_snap.get("status") == "failed" or (mill is not None and mill.attrs.get("status") == "failed")
    mill_isolated = mill_snap.get("status") == "maintenance"
    mill_hot = float(mill_snap.get("temperature_c") or 0.0) >= 150.0 or (
        mill is not None and float(mill.attrs.get("bearing_temperature_c") or 0.0) >= 150.0
    )
    if (mill_failed or mill_hot) and not mill_isolated:
        temp_c = float(mill_snap.get("temperature_c") or (mill.attrs.get("bearing_temperature_c") if mill else 0.0) or 0.0)
        vib_rms = float(mill_snap.get("vibration_rms") or (mill.attrs.get("vibration_rms") if mill else 0.0) or 0.0)

        batch_id = "production_batch:mes:BATCH-103"
        order_id = "customer_order:erp:ORD-9405"
        batch_snap = snapshot.get("batches", {}).get(batch_id, {})
        order_snap = snapshot.get("orders", {}).get(order_id, {})
        planned_kg = float(batch_snap.get("planned_quantity_kg", 1500.0))
        actual_kg = float(batch_snap.get("actual_quantity_kg", 0.0))
        remaining_kg = max(0.0, planned_kg - actual_kg)
        current_sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
        deadline_iso = order_snap.get("deadline", "2026-10-22T17:00:00+00:00")
        deadline = datetime.fromisoformat(deadline_iso)
        delay_hours = 6.0
        unintervened_finish = current_sim_time + timedelta(hours=12.0)
        sla_breached = unintervened_finish > deadline
        potential_penalty = 500.0 if sla_breached else 0.0

        is_critical_failure = (temp_c >= 150.0 or vib_rms >= 5.0 or mill_snap.get("status") == "failed")
        is_high_failure = (temp_c >= 120.0 or vib_rms >= 2.5)
        ev_sev = "critical" if is_critical_failure else ("high" if is_high_failure else "medium")

        evidence: List[DisruptionEvidence] = [
            DisruptionEvidence(
                metric="status",
                observed_value=0.0,
                threshold_value=1.0,
                unit="state",
                severity=ev_sev,
                message="Grinding mill drive motor suffered bearing failure.",
            ),
            DisruptionEvidence(
                metric="bearing_temperature_c",
                observed_value=temp_c,
                threshold_value=120.0,
                unit="°C",
                severity=ev_sev,
                message=f"Grinding mill temperature {temp_c}°C (critical threshold 120.0°C).",
            ),
            DisruptionEvidence(
                metric="vibration_rms",
                observed_value=vib_rms,
                threshold_value=2.5,
                unit="mm/s",
                severity=ev_sev,
                message=f"Grinding mill vibration {vib_rms} mm/s (safety threshold 2.5 mm/s).",
            ),
        ]
        calc_sev = _calculate_impact_severity(
            evidence,
            sla_breached=sla_breached,
            has_safety_hazard=is_critical_failure,
            potential_penalty_usd=potential_penalty,
        )

        return ImpactAnalysis(
            scenario_id=scenario_id,
            disrupted_machine_id="machine:plant:MILL-01",
            affected_batch_id=batch_id,
            affected_order_id=order_id,
            affected_customer=order_snap.get("customer_name", "Swiss Confectionery AG"),
            order_deadline=deadline.isoformat(),
            unintervened_completion=unintervened_finish.isoformat(),
            delay_hours=delay_hours,
            sla_breached=sla_breached,
            potential_penalty_usd=potential_penalty,
            remaining_quantity_kg=remaining_kg,
            evidence=evidence,
            provenance_summary={
                "physical_observations": "NASA / UC IMS Bearing Run-to-Failure Benchmark (ISO 10816)",
                "scenario_projections": "FactoryOps Discrete-Event Simulator (15-min steps)",
                "semantic_layer": "Unified Semantic Layer (USL)",
            },
            severity=calc_sev,
            disruption_type="equipment_failure",
        )

    # 4. Check for reduced_machine_capacity (PRESS-01)
    press = graph.entity("machine:plant:PRESS-01")
    press_snap = snapshot.get("machines", {}).get("machine:plant:PRESS-01", {})
    press_degraded = (
        press_snap.get("status") == "degraded"
        or float(press_snap.get("rated_capacity_kg_h", 600.0)) < 600.0
    )
    if press_degraded and press_snap.get("status") != "maintenance":
        cap_val = float(press_snap.get("rated_capacity_kg_h", 600.0))
        cap_pct = round((cap_val / 600.0) * 100.0, 1)

        batch_id = "production_batch:mes:BATCH-104"
        order_id = "customer_order:erp:ORD-9402"
        batch_snap = snapshot.get("batches", {}).get(batch_id, {})
        order_snap = snapshot.get("orders", {}).get(order_id, {})
        planned_kg = float(batch_snap.get("planned_quantity_kg", 2000.0))
        actual_kg = float(batch_snap.get("actual_quantity_kg", 0.0))
        remaining_kg = max(0.0, planned_kg - actual_kg)
        current_sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
        deadline_iso = order_snap.get("deadline", "2026-10-22T14:00:00+00:00")
        deadline = datetime.fromisoformat(deadline_iso)
        delay_hours = float(batch_snap.get("delay_hours", 2.5))
        unintervened_finish = current_sim_time + timedelta(hours=delay_hours + (remaining_kg / max(1.0, cap_val)))
        sla_breached = unintervened_finish > deadline
        potential_penalty = 350.0 if sla_breached else 0.0

        if cap_pct <= 15.0:
            ev_sev = "critical"
        elif cap_pct <= 25.0 and sla_breached:
            ev_sev = "high"
        elif cap_pct <= 75.0:
            ev_sev = "medium"
        else:
            ev_sev = "low"

        evidence = [
            DisruptionEvidence(
                metric="hydraulic_pressure_capacity",
                observed_value=cap_pct,
                threshold_value=100.0,
                unit="%",
                severity=ev_sev,
                message=f"Hydraulic press PRESS-01 pressure loss: capacity throttled to {cap_pct}%.",
                record_kind="sensor_telemetry",
                provenance_source="FactoryOps Machine SCADA / Hydraulic Pressure Telemetry",
            ),
        ]
        calc_sev = _calculate_impact_severity(
            evidence,
            sla_breached=sla_breached,
            potential_penalty_usd=potential_penalty,
        )

        return ImpactAnalysis(
            scenario_id=scenario_id,
            disrupted_machine_id="machine:plant:PRESS-01",
            affected_batch_id=batch_id,
            affected_order_id=order_id,
            affected_customer=order_snap.get("customer_name", "Artisan Chocolatier BV"),
            order_deadline=deadline.isoformat(),
            unintervened_completion=unintervened_finish.isoformat(),
            delay_hours=delay_hours,
            sla_breached=sla_breached,
            potential_penalty_usd=potential_penalty,
            remaining_quantity_kg=remaining_kg,
            evidence=evidence,
            provenance_summary={
                "physical_observations": "FactoryOps Machine SCADA / Hydraulic Pressure Telemetry",
                "scenario_projections": "FactoryOps Discrete-Event Simulator (15-min steps)",
                "semantic_layer": "Unified Semantic Layer (USL)",
            },
            severity=calc_sev,
            disruption_type="reduced_machine_capacity",
            mitigation=snapshot.get("supply_chain", {}).get("press_mitigation"),
        )

    # 5. Check for quality_contamination (Silo 1 moisture hold)
    silo_lot = snapshot.get("inventory", {}).get("inventory_lot:wms:LOT-2026-001", {})
    if (
        silo_lot.get("quality_status") == "QUARANTINE_HOLD"
        or float(silo_lot.get("moisture_pct", 0.0)) > 7.0
    ) and mitigation != "quarantine_replacement_lot":
        moisture = float(silo_lot.get("moisture_pct", 0.0))
        batch_id = "production_batch:mes:BATCH-101"
        order_id = "customer_order:erp:ORD-9402"
        batch_snap = snapshot.get("batches", {}).get(batch_id, {})
        order_snap = snapshot.get("orders", {}).get(order_id, {})
        planned_kg = float(batch_snap.get("planned_quantity_kg", 2500.0))
        actual_kg = float(batch_snap.get("actual_quantity_kg", 0.0))
        remaining_kg = max(0.0, planned_kg - actual_kg)
        current_sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
        deadline_iso = order_snap.get("deadline", "2026-10-22T14:00:00+00:00")
        deadline = datetime.fromisoformat(deadline_iso)
        delay_hours = 4.0
        unintervened_finish = current_sim_time + timedelta(hours=delay_hours + (remaining_kg / 1200.0))
        sla_breached = unintervened_finish > deadline
        potential_penalty = 350.0 if sla_breached else 0.0

        # Contamination and genuine safety hazards must never be downgraded to low/medium.
        if moisture >= 11.0:
            ev_sev = "critical"
            has_safety_hazard = True
        else:
            ev_sev = "high"
            has_safety_hazard = True

        evidence = [
            DisruptionEvidence(
                metric="moisture_pct",
                observed_value=moisture,
                threshold_value=7.0,
                unit="%",
                severity=ev_sev,
                message=f"Raw bean moisture {moisture:.1f}% exceeds ISO safety limit 7.0%; placed on quarantine hold.",
                record_kind="quality_inspection",
                provenance_source="FactoryOps Silo Moisture & Quality Sensor Telemetry",
            ),
        ]
        calc_sev = "critical" if moisture >= 11.0 else "high"

        return ImpactAnalysis(
            scenario_id=scenario_id,
            disrupted_machine_id="silo:plant:SILO-01",
            affected_batch_id=batch_id,
            affected_order_id=order_id,
            affected_customer=order_snap.get("customer_name", "Artisan Chocolatier BV"),
            order_deadline=deadline.isoformat(),
            unintervened_completion=unintervened_finish.isoformat(),
            delay_hours=delay_hours,
            sla_breached=sla_breached,
            potential_penalty_usd=potential_penalty,
            remaining_quantity_kg=remaining_kg,
            evidence=evidence,
            provenance_summary={
                "physical_observations": "FactoryOps Silo Moisture & Quality Sensor Telemetry",
                "scenario_projections": "FactoryOps Discrete-Event Simulator (15-min steps)",
                "semantic_layer": "Unified Semantic Layer (USL)",
            },
            severity=calc_sev,
            disruption_type="quality_contamination",
        )

    # 6. Check ROAST-01 (default / roaster_bearing_overheat / interacting_constraint / baseline)
    roaster = graph.entity("machine:plant:ROAST-01")
    if not roaster:
        raise ValueError("Machine ROAST-01 not found in World Model graph.")

    roaster_snap = snapshot.get("machines", {}).get("machine:plant:ROAST-01", {})
    temp_c = roaster.attrs.get("bearing_temperature_c") or roaster.attrs.get("temperature_c")
    if temp_c is None:
        temp_c = roaster_snap.get("bearing_temperature_c") or roaster_snap.get("temperature_c")
    vib_rms = roaster.attrs.get("vibration_rms")
    if vib_rms is None:
        vib_rms = roaster_snap.get("vibration_rms")

    evidence: List[DisruptionEvidence] = []

    # Check for missing telemetry during an active disruption
    if (temp_c is None or vib_rms is None) and scenario.disruption_type in ("roaster_bearing_overheat", "combined", "interacting_constraint"):
        evidence.append(
            DisruptionEvidence(
                metric="sensor_telemetry_missing",
                observed_value=0.0,
                threshold_value=1.0,
                unit="status",
                severity="high",
                message="Critical bearing temperature or vibration telemetry is missing. Escalating to high severity for safety inspection.",
                record_kind="telemetry_error",
                provenance_source="FactoryOps Sensor Telemetry Monitor",
            )
        )
    else:
        temp_val = float(temp_c if temp_c is not None else 115.0)
        vib_val = float(vib_rms if vib_rms is not None else 1.8)

        if temp_val > 140.0:
            evidence.append(
                DisruptionEvidence(
                    metric="bearing_temperature_c",
                    observed_value=temp_val,
                    threshold_value=120.0,
                    unit="°C",
                    severity="critical",
                    message=f"Roaster bearing temperature {temp_val}°C exceeds critical threshold 140.0°C (thermal runaway).",
                )
            )
        elif temp_val > 120.0:
            evidence.append(
                DisruptionEvidence(
                    metric="bearing_temperature_c",
                    observed_value=temp_val,
                    threshold_value=120.0,
                    unit="°C",
                    severity="high",
                    message=f"Roaster bearing temperature {temp_val}°C exceeds safety threshold 120.0°C.",
                )
            )
        elif temp_val > 116.0:
            evidence.append(
                DisruptionEvidence(
                    metric="bearing_temperature_c",
                    observed_value=temp_val,
                    threshold_value=116.0,
                    unit="°C",
                    severity="medium",
                    message=f"Roaster bearing temperature {temp_val}°C shows minor thermal elevation.",
                )
            )

        if vib_val > 4.5:
            evidence.append(
                DisruptionEvidence(
                    metric="vibration_rms",
                    observed_value=vib_val,
                    threshold_value=2.5,
                    unit="mm/s",
                    severity="critical",
                    message=f"Roaster bearing vibration {vib_val} mm/s indicates severe mechanical damage.",
                )
            )
        elif vib_val > 2.5:
            evidence.append(
                DisruptionEvidence(
                    metric="vibration_rms",
                    observed_value=vib_val,
                    threshold_value=2.5,
                    unit="mm/s",
                    severity="high",
                    message=f"Roaster bearing vibration {vib_val} mm/s exceeds safety threshold 2.5 mm/s.",
                )
            )
        elif vib_val > 2.0:
            evidence.append(
                DisruptionEvidence(
                    metric="vibration_rms",
                    observed_value=vib_val,
                    threshold_value=2.0,
                    unit="mm/s",
                    severity="medium",
                    message=f"Roaster bearing vibration {vib_val} mm/s elevated above normal baseline.",
                )
            )

    # Trace downstream impacts via graph relationships
    incoming_batches = graph.incoming("machine:plant:ROAST-01", via="processed_by")
    batch_entity = incoming_batches[0] if incoming_batches else graph.entity("production_batch:mes:BATCH-101")
    batch_id = batch_entity.id if batch_entity else "production_batch:mes:BATCH-101"

    incoming_orders = graph.incoming(batch_id, via="fulfills_via")
    order_entity = incoming_orders[0] if incoming_orders else graph.entity("customer_order:erp:ORD-9402")
    order_id = order_entity.id if order_entity else "customer_order:erp:ORD-9402"

    batch_snap = snapshot.get("batches", {}).get(batch_id, {})
    order_snap = snapshot.get("orders", {}).get(order_id, {})

    planned_kg = float(batch_snap.get("planned_quantity_kg", 2500.0))
    actual_kg = float(batch_snap.get("actual_quantity_kg", 645.0))
    remaining_kg = max(0.0, planned_kg - actual_kg)

    current_sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
    deadline_iso = order_snap.get("deadline", "2026-10-22T14:00:00+00:00")
    deadline = datetime.fromisoformat(deadline_iso)

    if evidence:
        # In degraded state throughput is 180 kg/h (45 kg per 15-min step)
        degraded_hours = remaining_kg / 180.0
        unintervened_finish = current_sim_time + timedelta(hours=degraded_hours)
        nominal_remaining_hours = remaining_kg / 1200.0
        delay_hours = round(degraded_hours - nominal_remaining_hours, 2)
        sla_breached = unintervened_finish > deadline
        late_seconds = max(0.0, (unintervened_finish - deadline).total_seconds())
        late_days = late_seconds / 86400.0
        potential_penalty = round(max(350.0, late_days * 350.0), 2) if sla_breached else 0.0
        has_hazard = any(e.severity == "critical" for e in evidence)
        disruption_sev = _calculate_impact_severity(
            evidence,
            sla_breached=sla_breached,
            has_safety_hazard=has_hazard,
            potential_penalty_usd=potential_penalty,
            default_severity="high",
        )
        roaster_family = ("roaster_bearing_overheat", "combined", "interacting_constraint")
        disruption_t = scenario.disruption_type if scenario.disruption_type in roaster_family else "roaster_bearing_overheat"
    else:
        # Healthy nominal state
        delay_hours = 0.0
        nominal_remaining_hours = remaining_kg / 1200.0
        unintervened_finish = current_sim_time + timedelta(hours=nominal_remaining_hours)
        sla_breached = False
        potential_penalty = 0.0
        disruption_sev = "low"
        disruption_t = "none"

    return ImpactAnalysis(
        scenario_id=scenario_id,
        disrupted_machine_id="machine:plant:ROAST-01",
        affected_batch_id=batch_id,
        affected_order_id=order_id,
        affected_customer=order_snap.get("customer_name", "Artisan Chocolatier BV"),
        order_deadline=deadline.isoformat(),
        unintervened_completion=unintervened_finish.isoformat(),
        delay_hours=delay_hours,
        sla_breached=sla_breached,
        potential_penalty_usd=potential_penalty,
        remaining_quantity_kg=remaining_kg,
        evidence=evidence,
        provenance_summary={
            "physical_observations": "NASA / UC IMS Bearing Run-to-Failure Benchmark (ISO 10816)",
            "scenario_projections": "FactoryOps Discrete-Event Simulator (15-min steps)",
            "semantic_layer": "Unified Semantic Layer (USL)",
        },
        severity=disruption_sev,
        disruption_type=disruption_t,
    )


def _annotate_plan_authorizations(
    plans: List[RecoveryPlan],
    disruption_type: str,
    scenario: Optional[Any] = None,
    impact: Optional[ImpactAnalysis] = None,
) -> None:
    """Enriches candidate recovery plans with deterministic hybrid policy authorizations."""
    from app.services.recovery_policy import evaluate_recovery_authorization
    for plan in plans:
        auth = evaluate_recovery_authorization(
            plan, disruption_type=disruption_type, characterization=impact, scenario=scenario
        )
        plan.authorization_outcome = auth.outcome
        plan.authorization_reason = auth.reason


VALID_PLANS = {
    "plan_a_thermal_flush": "Rapid Bearing Thermal Flush & Controlled Restart",
    "plan_b_reroute_line2": "Batch Reroute to Auxiliary Roaster Line 2",
    "plan_c_split_expedite": "Batch Split & Certified Third-Party Finishing",
    "plan_a_motor_swap": "Emergency Motor Module Replacement & Recalibration",
    "plan_b_contract_grind": "Emergency Contract Grinding & Toll Finishing",
    "plan_c_batch_defer": "Order Descope & Split Fulfillment",
    # PRESS-01 Hydraulic Capacity Recovery Plans
    "plan_a_hydraulic_reseal": "Hydraulic Line Bleed & Seal Kit Replacement",
    "plan_b_press_liquor_buffer": "Liquor Buffer Tank Staging at Reduced Press Rate",
    # Supply Chain Recovery Plans
    "plan_spot_market_buffer": "Spot Market Buffer Procurement (5,000 kg Certified Lot)",
    "plan_recipe_line_throttle": "Recipe Line Throttle (60% Feed Rate Conservation)",
    "plan_customs_expedite": "Customs Broker Expedite & Priority Clearance",
}

# Which recovery plans physically address which disruption. A plan outside this
# map for the active disruption acts on the wrong equipment or material.
PLAN_APPLICABILITY: Dict[str, List[str]] = {
    "roaster_bearing_overheat": ["plan_a_thermal_flush", "plan_b_reroute_line2", "plan_c_split_expedite"],
    "combined": ["plan_a_thermal_flush", "plan_b_reroute_line2", "plan_c_split_expedite"],
    "interacting_constraint": ["plan_a_thermal_flush", "plan_b_reroute_line2", "plan_c_split_expedite"],
    "equipment_failure": ["plan_a_motor_swap", "plan_b_contract_grind", "plan_c_batch_defer"],
    "reduced_machine_capacity": ["plan_a_hydraulic_reseal", "plan_b_press_liquor_buffer"],
    "supplier_customs_delay": ["plan_spot_market_buffer", "plan_recipe_line_throttle", "plan_customs_expedite"],
    "supplier_shortfall": ["plan_spot_market_buffer", "plan_recipe_line_throttle"],
    "quality_contamination": ["plan_spot_market_buffer", "plan_recipe_line_throttle"],
}

# Bearing temperature at which a thermal flush can no longer prevent seizure.
ROASTER_SEIZURE_THRESHOLD_C = 150.0

# Estimated downtime hours associated with each recovery strategy
PLAN_DOWNTIME_HOURS: Dict[str, float] = {
    "plan_a_thermal_flush": 0.75,   # 45 minutes (3 steps at 15-min intervals)
    "plan_b_reroute_line2": 1.5,    # 90 minutes (6 steps at 15-min intervals)
    "plan_c_split_expedite": 0.5,   # 30 minutes (2 steps at 15-min intervals)
    "plan_a_motor_swap": 2.0,       # 120 minutes (8 steps at 15-min intervals)
    "plan_b_contract_grind": 3.0,   # 180 minutes (12 steps at 15-min intervals)
    "plan_c_batch_defer": 0.5,      # 30 minutes (2 steps at 15-min intervals)
    "plan_a_hydraulic_reseal": 1.0,     # 60 minutes (4 steps at 15-min intervals)
    "plan_b_press_liquor_buffer": 0.25, # 15 minutes (1 step) valve line-up
    # Supply Chain:
    "plan_spot_market_buffer": 0.5,   # 30 minutes (2 steps at 15-min intervals)
    "plan_recipe_line_throttle": 1.0, # 60 minutes (4 steps at 15-min intervals)
    "plan_customs_expedite": 1.0,     # 60 minutes (4 steps at 15-min intervals)
}


def calculate_plan_duration_steps(plan_id: str, step_size_minutes: int = 15) -> int:
    """Calculates the duration in discrete simulation steps for a recovery plan."""
    downtime_hours = PLAN_DOWNTIME_HOURS.get(plan_id, 0.75)
    return max(1, int(round((downtime_hours * 60.0) / max(1, step_size_minutes))))


def generate_candidate_plans(
    session: Session,
    scenario_id: str,
    use_ai: bool = True,
    use_nemotron: Optional[bool] = None,
) -> List[RecoveryPlan]:
    """Generates up to three deterministic recovery candidates evaluated against
    hard engineering constraints, ranked by SLA preservation and cost,
    and explained via the configured AI provider (Meterless primary with fallback).
    """
    if use_nemotron is not None:
        use_ai = use_nemotron

    impact = detect_and_trace_impact(session, scenario_id)
    scenario = session.execute(
        select(SimulationScenario).where(SimulationScenario.id == scenario_id)
    ).scalar_one()
    snapshot = scenario.parameters.get("snapshot", {})
    sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
    deadline = datetime.fromisoformat(impact.order_deadline)
    batch_snap = snapshot.get("batches", {}).get(impact.affected_batch_id, {})
    planned_kg = float(batch_snap.get("planned_quantity_kg", 2500.0))

    # -------------------------------------------------------------------------
    # Supply Chain Disruption Recovery Candidate Plans
    # -------------------------------------------------------------------------
    if impact.disrupted_machine_id == "silo:plant:SILO-01":
        # Federated external data query via Zetaris NDP
        usl = UnifiedSemanticLayerService()
        zetaris_ctx = usl.fetch_federated_recovery_context(impact.disruption_type)
        stock_info = zetaris_ctx.get("external_buffer_stock")
        weather_info = zetaris_ctx.get("transit_weather")

        spot_constraints: List[str] = []
        spot_feasible = True

        if stock_info and stock_info.get("quantity_on_hand_kg", 0) > 0:
            wh_name = stock_info["warehouse_name"]
            wh_qty = stock_info["quantity_on_hand_kg"]
            itm_id = stock_info["item_id"]
            spot_constraints.append(
                f"Zetaris Federated Metastore (TPCDS_DB.warehouse '{wh_name}'): Verified {wh_qty:.0f} kg available in external inventory ({itm_id})"
            )
            spot_feasible = True
        elif stock_info and stock_info.get("quantity_on_hand_kg", 0) <= 0:
            spot_constraints.append(
                "Zetaris Federated Metastore (TPCDS_DB.inventory): Regional external warehouse inventory OUT OF STOCK"
            )
            spot_feasible = False
        else:
            spot_constraints.append(
                "Regional warehouse (Amsterdam Terminal) confirms 5,000 kg organic cocoa beans available for immediate dispatch"
            )
            spot_feasible = True

        if weather_info:
            road_risk = weather_info.get("road_risk_level", "nominal")
            temp_f = weather_info.get("temperature_f", 72.0)
            precip = weather_info.get("precipitation_in", 0.0)
            spot_constraints.append(
                f"Zetaris Transit Telemetry (NY_TAXI.weather_data): Route environmental status is {road_risk} ({temp_f}°F, {precip} in precipitation)"
            )

        spot_constraints.extend([
            "Pre-approved supplier rating: A+ (Sanitary & organic certificates verified via USL)",
            "Transit window: 2 hours to plant receiving silo",
        ])

        if zetaris_ctx.get("zetaris_connected"):
            impact.provenance_summary["zetaris_facts"] = zetaris_ctx

        # Plan 1: Spot Market Buffer Procurement (Rank 1 - Preserves SLA, eliminates stockout)
        downtime_spot = 0.5  # 30 min receiving & QA
        finish_spot = sim_time + timedelta(hours=3.5)
        meets_spot = finish_spot <= deadline
        buffer_spot = round((deadline - finish_spot).total_seconds() / 3600.0, 2)
        cost_spot = {"spot_premium": 2250.0, "freight": 600.0, "sla_penalty": 0.0}
        plan_spot = RecoveryPlan(
            plan_id="plan_spot_market_buffer",
            title="Spot Market Buffer Procurement (5,000 kg Certified Lot)",
            strategy="reroute",
            rank=1,
            feasibility_verdict="feasible" if spot_feasible else "infeasible",
            hard_constraints_met=spot_constraints,
            estimated_downtime_hours=downtime_spot,
            projected_completion=finish_spot.isoformat(),
            meets_deadline=meets_spot,
            buffer_hours=buffer_spot,
            total_cost_usd=round(sum(cost_spot.values()), 2),
            cost_breakdown=cost_spot,
            action_steps=[
                "Issue emergency purchase order PO-2026-SPOT-01 to Amsterdam Buffer Depot.",
                "Dispatch local dedicated flatbed transport for 2-hour transfer.",
                "Stage and pump 5,000 kg into Silo 1 receiving hopper.",
                "Resume nominal 1,200 kg/h roasting and milling line feed.",
            ],
        )

        # Plan 2: Recipe Line Throttle (Rank 2 - Low cost, but extends processing time)
        downtime_throttle = 1.0  # 60 min parameter recalibration
        finish_throttle = sim_time + timedelta(hours=6.5)
        meets_throttle = finish_throttle <= deadline
        buffer_throttle = round((deadline - finish_throttle).total_seconds() / 3600.0, 2)
        cost_throttle = {"labor_adjustment": 100.0, "efficiency_loss": 350.0}
        plan_throttle = RecoveryPlan(
            plan_id="plan_recipe_line_throttle",
            title="Recipe Line Throttle (60% Feed Rate Conservation)",
            strategy="maintenance",
            rank=2,
            feasibility_verdict="feasible",
            hard_constraints_met=[
                "Recipe Dark 70% thermal curves verified stable at 180 kg/h reduced feed rate",
                "Operator console permits dynamic feed screw frequency adjustments",
            ],
            estimated_downtime_hours=downtime_throttle,
            projected_completion=finish_throttle.isoformat(),
            meets_deadline=meets_throttle,
            buffer_hours=buffer_throttle,
            total_cost_usd=round(sum(cost_throttle.values()), 2),
            cost_breakdown=cost_throttle,
            action_steps=[
                "Throttle Silo 1 extraction auger speed to 45 kg per 15-min step (180 kg/h).",
                "Recalibrate roaster burner profile to maintain core bean temperature at 135°C.",
                "Stretch remaining 3,600 kg inventory across 20 hours until customs clearance.",
            ],
        )

        # Plan 3: Customs Broker Expedite & Priority Clearance (Rank 3 - Fast clearance, broker fee)
        downtime_expedite = 1.0
        finish_expedite = sim_time + timedelta(hours=4.5)
        meets_expedite = finish_expedite <= deadline
        buffer_expedite = round((deadline - finish_expedite).total_seconds() / 3600.0, 2)
        cost_expedite = {"customs_priority_fee": 850.0, "bonded_transit_fee": 350.0, "sla_penalty": 0.0}
        plan_expedite = RecoveryPlan(
            plan_id="plan_customs_expedite",
            title="Customs Broker Expedite & Priority Clearance",
            strategy="split_expedite",
            rank=3,
            feasibility_verdict="feasible",
            hard_constraints_met=[
                "Port of Rotterdam bonded customs broker on 24/7 priority retainer",
                "Digital phyto-sanitary compliance documents uploaded to Portbase portal",
            ],
            estimated_downtime_hours=downtime_expedite,
            projected_completion=finish_expedite.isoformat(),
            meets_deadline=meets_expedite,
            buffer_hours=buffer_expedite,
            total_cost_usd=round(sum(cost_expedite.values()), 2),
            cost_breakdown=cost_expedite,
            action_steps=[
                "Authorize emergency customs clearance tariff payment via Portbase.",
                "Submit expedited sanitary inspection release petition to port authority.",
                "Advance container terminal pickup by 16 steps (4.0 hours).",
            ],
        )
        if impact.disruption_type == "quality_contamination":
            plan_spot.hard_constraints_met.append(
                "Replacement lot is staged separately; quarantined LOT-2026-001 is never blended or consumed"
            )
            plan_throttle.hard_constraints_met = [
                "Stretches remaining Silo 1 stock, which is the quarantined LOT-2026-001",
            ]
        applicable = PLAN_APPLICABILITY.get(impact.disruption_type, [])
        plans = [p for p in (plan_spot, plan_throttle, plan_expedite) if p.plan_id in applicable]
        for idx, p in enumerate(plans, start=1):
            p.rank = idx
        _annotate_plan_authorizations(plans, disruption_type=impact.disruption_type, scenario=scenario, impact=impact)
        explain_recovery_plans(impact, plans, enabled=use_ai)
        return plans

    # -------------------------------------------------------------------------
    # PRESS-01 Hydraulic Capacity Loss Recovery Candidate Plans
    # -------------------------------------------------------------------------
    if impact.disrupted_machine_id == "machine:plant:PRESS-01":
        press_snap = snapshot.get("machines", {}).get("machine:plant:PRESS-01", {})
        degraded_rate = max(1.0, float(press_snap.get("rated_capacity_kg_h", 240.0)))
        spares = snapshot.get("spare_parts", {})
        seal_kit_in_stock = int(spares.get("hydraulic_seal_kit", 2)) > 0

        downtime_a = PLAN_DOWNTIME_HOURS["plan_a_hydraulic_reseal"]
        finish_a = sim_time + timedelta(hours=downtime_a + impact.remaining_quantity_kg / 600.0)
        meets_a = finish_a <= deadline
        cost_a = {"seal_kit": 220.0, "labor": 160.0, "sla_penalty": 0.0 if meets_a else 350.0}
        plan_a = RecoveryPlan(
            plan_id="plan_a_hydraulic_reseal",
            title=VALID_PLANS["plan_a_hydraulic_reseal"],
            strategy="maintenance",
            rank=1,
            feasibility_verdict="feasible" if seal_kit_in_stock else "infeasible",
            hard_constraints_met=[
                "Hydraulic seal kit HSK-600 in maintenance stock" if seal_kit_in_stock
                else "Hydraulic seal kit HSK-600 OUT OF STOCK",
                "Press is depressurised and isolated before line bleed (LOTO)",
                "Qualified hydraulic technician on shift (Tech ID: T-8812)",
            ],
            estimated_downtime_hours=downtime_a,
            projected_completion=finish_a.isoformat(),
            meets_deadline=meets_a,
            buffer_hours=round((deadline - finish_a).total_seconds() / 3600.0, 2),
            total_cost_usd=round(sum(cost_a.values()), 2),
            cost_breakdown=cost_a,
            action_steps=[
                "Depressurise PRESS-01 and lock out hydraulic power unit.",
                "Bleed air from main ram line and replace worn seal kit HSK-600.",
                "Re-pressurise to 400 bar and verify leak-down < 2 bar/min.",
                "Resume pressing at rated 600 kg/h.",
            ],
        )

        downtime_b = PLAN_DOWNTIME_HOURS["plan_b_press_liquor_buffer"]
        finish_b = sim_time + timedelta(hours=downtime_b + impact.remaining_quantity_kg / degraded_rate)
        meets_b = finish_b <= deadline
        cost_b = {"buffer_tank_heating": 90.0, "labor": 60.0, "sla_penalty": 0.0 if meets_b else 350.0}
        plan_b = RecoveryPlan(
            plan_id="plan_b_press_liquor_buffer",
            title=VALID_PLANS["plan_b_press_liquor_buffer"],
            strategy="reroute",
            rank=2,
            feasibility_verdict="feasible",
            hard_constraints_met=[
                "Heated liquor buffer tank TK-02 available (8,000 kg capacity)",
                f"Press continues at degraded {degraded_rate:.0f} kg/h within hydraulic safety envelope",
            ],
            estimated_downtime_hours=downtime_b,
            projected_completion=finish_b.isoformat(),
            meets_deadline=meets_b,
            buffer_hours=round((deadline - finish_b).total_seconds() / 3600.0, 2),
            total_cost_usd=round(sum(cost_b.values()), 2),
            cost_breakdown=cost_b,
            action_steps=[
                "Divert upstream cocoa liquor surplus into heated buffer tank TK-02.",
                f"Continue pressing at {degraded_rate:.0f} kg/h; defer seal repair to next planned stop.",
            ],
        )
        plans = [plan_a, plan_b]
        _annotate_plan_authorizations(plans, disruption_type=impact.disruption_type, scenario=scenario, impact=impact)
        explain_recovery_plans(impact, plans, enabled=use_ai)
        return plans

    # -------------------------------------------------------------------------
    # MILL-01 Equipment Failure Recovery Candidate Plans
    # -------------------------------------------------------------------------
    if impact.disrupted_machine_id == "machine:plant:MILL-01":
        downtime_a = 2.0
        run_hours_a = impact.remaining_quantity_kg / 800.0
        finish_a = sim_time + timedelta(hours=downtime_a + run_hours_a)
        meets_a = finish_a <= deadline
        buffer_a = round((deadline - finish_a).total_seconds() / 3600.0, 2)
        cost_a = {"parts": 450.0, "labor": 300.0, "sla_penalty": 0.0 if meets_a else 500.0}
        plan_a = RecoveryPlan(
            plan_id="plan_a_motor_swap",
            title="Emergency Motor Module Replacement & Recalibration",
            strategy="maintenance",
            rank=1,
            feasibility_verdict="feasible",
            hard_constraints_met=[
                "Spare 45kW Siemens motor MTR-404 verified in maintenance stock",
                "Qualified electrical technician on shift (Tech ID: T-8812)",
            ],
            estimated_downtime_hours=downtime_a,
            projected_completion=finish_a.isoformat(),
            meets_deadline=meets_a,
            buffer_hours=buffer_a,
            total_cost_usd=round(sum(cost_a.values()), 2),
            cost_breakdown=cost_a,
            action_steps=[
                "Lock out / tag out electrical breaker for MILL-01.",
                "Uncouple driveshaft and remove seized motor assembly.",
                "Mount and laser-align spare motor MTR-404.",
                "Resume milling at rated 800 kg/h throughput.",
            ],
        )

        downtime_b = 3.0
        run_hours_b = impact.remaining_quantity_kg / 1000.0
        finish_b = sim_time + timedelta(hours=downtime_b + run_hours_b)
        meets_b = finish_b <= deadline
        buffer_b = round((deadline - finish_b).total_seconds() / 3600.0, 2)
        cost_b = {"tolling_service": 600.0, "transport": 250.0, "sla_penalty": 0.0 if meets_b else 500.0}
        plan_b = RecoveryPlan(
            plan_id="plan_b_contract_grind",
            title="Emergency Contract Grinding & Toll Finishing",
            strategy="reroute",
            rank=2,
            feasibility_verdict="feasible",
            hard_constraints_met=[
                "Regional partner CocoaCraft facility online",
                "Sanitary transit containers staged at loading dock",
            ],
            estimated_downtime_hours=downtime_b,
            projected_completion=finish_b.isoformat(),
            meets_deadline=meets_b,
            buffer_hours=buffer_b,
            total_cost_usd=round(sum(cost_b.values()), 2),
            cost_breakdown=cost_b,
            action_steps=[
                "Stage intermediate roasted nibs into sanitary transport totes.",
                "Transfer totes to certified toll processor CocoaCraft.",
                "Receive finished cocoa liquor for downstream pressing.",
            ],
        )

        downtime_c = 0.5
        finish_c = sim_time + timedelta(hours=8.0)
        meets_c = finish_c <= deadline
        buffer_c = round((deadline - finish_c).total_seconds() / 3600.0, 2)
        cost_c = {"expedited_freight": 300.0, "sla_penalty": 1200.0}
        plan_c = RecoveryPlan(
            plan_id="plan_c_batch_defer",
            title="Order Descope & Split Fulfillment",
            strategy="split_expedite",
            rank=3,
            feasibility_verdict="feasible",
            hard_constraints_met=[
                "Customer account team authorized split delivery waiver",
            ],
            estimated_downtime_hours=downtime_c,
            projected_completion=finish_c.isoformat(),
            meets_deadline=meets_c,
            buffer_hours=buffer_c,
            total_cost_usd=round(sum(cost_c.values()), 2),
            cost_breakdown=cost_c,
            action_steps=[
                "Fulfill initial 50% lot from existing finished goods buffer.",
                "Reschedule remaining batch quantity to next shift.",
            ],
        )
        plans = [plan_a, plan_b, plan_c]
        _annotate_plan_authorizations(plans, disruption_type=impact.disruption_type, scenario=scenario, impact=impact)
        explain_recovery_plans(impact, plans, enabled=use_ai)
        return plans

    # -------------------------------------------------------------------------
    # PLAN A: Rapid Bearing Thermal Flush & Controlled Restart (Maintenance)
    # -------------------------------------------------------------------------
    # Action: 45 min downtime (0.75h) to flush bearing & relubricate.
    # Restores 100% rated throughput: 1200 kg/h.
    downtime_a = 0.75  # 45 minutes
    run_hours_a = impact.remaining_quantity_kg / 1200.0
    finish_a = sim_time + timedelta(hours=downtime_a + run_hours_a)
    meets_deadline_a = finish_a <= deadline
    buffer_a = round((deadline - finish_a).total_seconds() / 3600.0, 2)
    late_seconds_a = max(0.0, (finish_a - deadline).total_seconds())
    penalty_a = round((late_seconds_a / 86400.0) * 350.0, 2) if not meets_deadline_a else 0.0
    cost_a = {"parts_coolant": 150.0, "labor": 200.0, "sla_penalty": penalty_a}

    # Extract current observed temperature from impact evidence or snapshot
    temp_obs = next(
        (e.observed_value for e in impact.evidence if e.metric == "bearing_temperature_c"),
        float(snapshot.get("machines", {}).get(impact.disrupted_machine_id, {}).get("temperature_c", 142.5)),
    )

    below_seizure = temp_obs < ROASTER_SEIZURE_THRESHOLD_C
    plan_a = RecoveryPlan(
        plan_id="plan_a_thermal_flush",
        title="Rapid Bearing Thermal Flush & Controlled Restart",
        strategy="maintenance",
        rank=1,
        feasibility_verdict="feasible" if below_seizure else "infeasible",
        hard_constraints_met=[
            (
                f"Bearing temperature {temp_obs:.1f}°C is below mechanical seizure threshold ({ROASTER_SEIZURE_THRESHOLD_C:.1f}°C)"
                if below_seizure else
                f"TEMPERATURE_EXCEEDS_SEIZURE_LIMIT: bearing {temp_obs:.1f}°C >= {ROASTER_SEIZURE_THRESHOLD_C:.1f}°C; flush cannot prevent seizure"
            ),
            "Synthetic food-grade lubricant ISO VG 460 is in stock (Lot LOT-LUBE-02)",
            "Qualified maintenance technician on shift (Tech ID: T-8812)",
        ],
        estimated_downtime_hours=downtime_a,
        projected_completion=finish_a.isoformat(),
        meets_deadline=meets_deadline_a,
        buffer_hours=buffer_a,
        total_cost_usd=round(sum(cost_a.values()), 2),
        cost_breakdown=cost_a,
        action_steps=[
            "Halt drum rotation and disengage drive motor under controlled purge.",
            "Apply high-pressure thermal flush to bearing housing and inspect seal integrity.",
            "Recharge with synthetic ISO VG 460 food-grade grease.",
            "Run 5-minute no-load vibration diagnostic to verify RMS < 1.5 mm/s.",
            "Resume Dark 70% roasting run at nominal 1200 kg/h capacity.",
        ],
    )

    # -------------------------------------------------------------------------
    # PLAN B: Batch Reroute to Auxiliary Roaster Line 2
    # -------------------------------------------------------------------------
    # Action: Pneumatic transfer to Line 2 auxiliary roaster (available after 1.5h).
    # Processing rate on Line 2: 900 kg/h.
    downtime_b = 1.5  # 90 minutes transfer and setup
    run_hours_b = impact.remaining_quantity_kg / 900.0
    finish_b = sim_time + timedelta(hours=downtime_b + run_hours_b)
    meets_deadline_b = finish_b <= deadline
    buffer_b = round((deadline - finish_b).total_seconds() / 3600.0, 2)
    late_seconds_b = max(0.0, (finish_b - deadline).total_seconds())
    penalty_b = round(max(250.0, (late_seconds_b / 86400.0) * 350.0), 2) if not meets_deadline_b else 0.0
    cost_b = {"transfer_sanitization": 250.0, "labor": 200.0, "sla_penalty": penalty_b}

    roast_02_status = snapshot.get("machines", {}).get("machine:plant:ROAST-02", {}).get("status", "idle")
    roast_02_available = roast_02_status in ("running", "idle", "ready")
    plan_b = RecoveryPlan(
        plan_id="plan_b_reroute_line2",
        title="Batch Reroute to Auxiliary Roaster Line 2",
        strategy="reroute",
        rank=2,
        feasibility_verdict="feasible" if roast_02_available else "infeasible",
        hard_constraints_met=[
            "Auxiliary Roaster ROAST-02 certified for Dark 70% recipe parameters"
            if roast_02_available else f"AUXILIARY_LINE_UNAVAILABLE: ROAST-02 status '{roast_02_status}'",
            "Pneumatic transfer ducting sanitized and dry",
        ],
        estimated_downtime_hours=downtime_b,
        projected_completion=finish_b.isoformat(),
        meets_deadline=meets_deadline_b,
        buffer_hours=buffer_b,
        total_cost_usd=round(sum(cost_b.values()), 2),
        cost_breakdown=cost_b,
        action_steps=[
            "Evacuate batch BATCH-101 from ROAST-01 buffer hopper into holding tote.",
            "Transfer tote via pneumatic transfer to auxiliary line ROAST-02.",
            "Initialize roasting profile under recipe standard DARK-70-REV2.",
        ],
    )

    # -------------------------------------------------------------------------
    # PLAN C: Batch Split & Expedited Courier Sub-contracting
    # -------------------------------------------------------------------------
    # Action: Split batch: dispatch current actual kg; contract remaining kg.
    downtime_c = 0.5  # 30 min packaging & handoff
    contract_processing_hours = 3.0  # 3 hours external turn
    finish_c = sim_time + timedelta(hours=downtime_c + contract_processing_hours)
    meets_deadline_c = finish_c <= deadline
    buffer_c = round((deadline - finish_c).total_seconds() / 3600.0, 2)
    late_seconds_c = max(0.0, (finish_c - deadline).total_seconds())
    penalty_c = round((late_seconds_c / 86400.0) * 350.0, 2) if not meets_deadline_c else 0.0
    ext_cost = round(impact.remaining_quantity_kg * 1.20, 2)
    cost_c = {"third_party_processing": ext_cost, "expedited_transport": 450.0, "sla_penalty": penalty_c}

    plan_c = RecoveryPlan(
        plan_id="plan_c_split_expedite",
        title="Batch Split & Certified Third-Party Finishing",
        strategy="split_expedite",
        rank=3,
        feasibility_verdict="feasible",
        hard_constraints_met=[
            "External partner CocoaCraft facility has audited HACCP certification",
            "Refrigerated courier transport available on 30-minute notice",
        ],
        estimated_downtime_hours=downtime_c,
        projected_completion=finish_c.isoformat(),
        meets_deadline=meets_deadline_c,
        buffer_hours=buffer_c,
        total_cost_usd=round(sum(cost_c.values()), 2),
        cost_breakdown=cost_c,
        action_steps=[
            f"Package and dispatch already-processed {int(planned_kg - impact.remaining_quantity_kg)} kg finished cocoa mass directly to customer.",
            f"Load {int(impact.remaining_quantity_kg)} kg beans into thermal logistics container.",
            "Dispatch to CocoaCraft facility under certified processing contract.",
        ],
    )

    plans = [plan_a, plan_b, plan_c]
    _annotate_plan_authorizations(plans, disruption_type=impact.disruption_type, scenario=scenario, impact=impact)

    # Release read transaction before long-running AI explanation to free PostgreSQL connections.
    # Rollback ensures no partial or unrelated caller changes are accidentally committed.
    try:
        if not session.dirty and not session.new:
            session.rollback()
    except Exception:
        pass

    # Attach operational explanations: ONE Meterless call for the whole candidate
    # set (previously three back-to-back calls, which tripped the upstream 429).
    explain_recovery_plans(impact, plans, enabled=use_ai)

    return plans


def explain_recovery_plan(
    impact: ImpactAnalysis,
    plan: RecoveryPlan,
    enabled: bool = True,
    provider: Optional[str] = None,
) -> str:
    """Produces evidence-grounded operational reasoning.

    Provider selection explicitly follows settings.ai_provider (default 'meterless').
    - When 'meterless': uses MeterlessClient for Nightshift inference. If Meterless fails or
      is unreachable, logs a sanitized warning and returns safe deterministic fallback text.
      Does NOT silently call NVIDIA.
    - When explicitly 'nvidia': uses NVIDIA Nemotron if configured.
    - When AI is disabled or unconfigured: returns safe deterministic fallback text immediately.
    """
    import time
    fallback_text = _deterministic_explanation(impact, plan)

    if not enabled:
        return fallback_text

    selected_provider = (provider or os.getenv("AI_PROVIDER") or settings.ai_provider or "meterless").lower()

    # Check in-memory explanation cache to prevent rapid polling / burst re-querying
    cache_key = _make_explanation_cache_key(impact, plan, selected_provider)
    now = time.time()
    if cache_key in _EXPLANATION_CACHE:
        cached_text, cached_time = _EXPLANATION_CACHE[cache_key]
        if (now - cached_time) < _EXPLANATION_CACHE_TTL_SEC:
            logger.debug("Returning cached explanation for %s (%s)", plan.plan_id, impact.scenario_id)
            return cached_text

    # Prompt grounded strictly in deterministic numbers
    prompt = (
        f"You are the FactoryOps Industrial Intelligence Copilot.\n"
        f"Explain why {plan.title} is ranked #{plan.rank} for scenario {impact.scenario_id}.\n\n"
        f"Evidence: {json.dumps([asdict(e) for e in impact.evidence])}\n"
        f"Candidate Plan: {json.dumps(asdict(plan))}\n"
        f"Target Customer: {impact.affected_customer}, Order Deadline: {impact.order_deadline}\n\n"
        f"Provide a concise operational rationale highlighting constraint feasibility, timeline recovery, and costs."
    )

    # 1. Primary Provider: Meterless Runtime (Nightshift)
    if selected_provider == "meterless":
        from app.providers.meterless import (
            MeterlessClient,
            MeterlessRateLimitError,
            resolve_meterless_token,
        )

        meterless_token = resolve_meterless_token()
        if not meterless_token or not meterless_token.strip() or meterless_token == "rt-local":
            logger.info("Meterless Runtime token not configured or placeholder; using deterministic explanation fallback.")
            return fallback_text

        try:
            base_url = os.getenv("METERLESS_BASE_URL") or settings.meterless_base_url or "http://127.0.0.1:8088/v1"
            model = os.getenv("METERLESS_MODEL") or settings.meterless_model or "meterless/local"
            timeout_sec = float(os.getenv("METERLESS_TIMEOUT_SECONDS", "30.0"))
            client = MeterlessClient(base_url=base_url, token=meterless_token, timeout=timeout_sec)
            res = client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=300,
                retry_budget_sec=_explanation_retry_budget(),
            )
            if res and len(res) > 30:
                out = f"[Meterless Runtime AI] {res}"
                _EXPLANATION_CACHE[cache_key] = (out, now)
                return out
            logger.warning("Meterless Runtime returned empty or truncated response; using deterministic explanation fallback.")
            return fallback_text
        except MeterlessRateLimitError as rle:
            logger.warning(
                "Meterless Runtime rate limit reached (%s); falling back to deterministic explanation without failing recovery.",
                rle,
            )
            return fallback_text
        except Exception as e:
            # Sanitized warning logging without leaking credentials or tokens
            err_msg = str(e).strip()
            if meterless_token and meterless_token in err_msg:
                err_msg = err_msg.replace(meterless_token, "[REDACTED]")
            logger.warning(
                f"Meterless Runtime call failed: {type(e).__name__} ({err_msg[:120]}); using safe deterministic explanation fallback."
            )
            return fallback_text

    # 2. Secondary Provider: NVIDIA Nemotron (Permitted ONLY when explicitly configured as provider)
    elif selected_provider == "nvidia":
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            logger.info("NVIDIA API key not configured; using deterministic explanation fallback.")
            return fallback_text

        try:
            req_data = json.dumps(
                {
                    "model": os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 300,
                }
            ).encode("utf-8")

            req = urllib_request.Request(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                data=req_data,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib_request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    message = choices[0].get("message", {})
                    content = message.get("content")
                    if content and isinstance(content, str) and content.strip():
                        return f"[NVIDIA Nemotron] {content.strip()}"
        except Exception as e:
            logger.warning(f"NVIDIA API call failed: {type(e).__name__}; using safe deterministic explanation fallback.")
            return fallback_text

    # Other providers or unknown: safe deterministic fallback
    return fallback_text


# Backward compatibility alias
explain_plan_nemotron = explain_recovery_plan


def _deterministic_explanation(impact: ImpactAnalysis, plan: RecoveryPlan) -> str:
    """Evidence-grounded explanation built only from deterministic numbers."""
    return (
        f"**Operational Rationale ({plan.title}):**\n"
        f"- **Root Cause:** {', '.join(e.message for e in impact.evidence)}\n"
        f"- **Hard Constraints:** {'; '.join(plan.hard_constraints_met)}.\n"
        f"- **Outcome:** Projected completion at {plan.projected_completion} "
        f"({'Meets customer deadline with ' + str(plan.buffer_hours) + 'h safety buffer' if plan.meets_deadline else 'Breaches deadline by ' + str(abs(plan.buffer_hours)) + 'h'}).\n"
        f"- **Cost Impact:** Total ${plan.total_cost_usd:.2f} (Savings of ${max(0.0, impact.potential_penalty_usd - plan.total_cost_usd):.2f} vs unintervened SLA breach).\n"
        f"- **Recommendation:** Ranked #{plan.rank} based on deterministic cost-benefit optimization."
    )


def _explanation_retry_budget() -> float:
    """Explanations are advisory and served to interactive requests: keep retries short."""
    try:
        return float(os.getenv("METERLESS_EXPLANATION_BUDGET_SEC", "20.0"))
    except ValueError:
        return 20.0


def extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    """Extracts the last well-formed JSON object from model output, ignoring any
    reasoning prose or <think> blocks emitted before it."""
    if not raw_text or not isinstance(raw_text, str):
        return None
    from app.providers.meterless import strip_reasoning
    import re
    cleaned = strip_reasoning(raw_text)
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    candidates = list(reversed(fenced))
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


# Batched explanations that recently failed: cache_key -> failure timestamp.
# Lets polling clients get the deterministic text immediately instead of
# re-queueing behind an upstream that is still rate limiting.
_EXPLANATION_FAILURES: Dict[str, float] = {}
_EXPLANATION_FAILURE_TTL_SEC = 60.0


def explain_recovery_plans(
    impact: ImpactAnalysis,
    plans: List[RecoveryPlan],
    enabled: bool = True,
    provider: Optional[str] = None,
) -> None:
    """Attaches explanations to every candidate plan using at most ONE Meterless call.

    The deterministic fallback is always available, so explanation failures never
    block plan generation, authorization, or recovery execution.
    """
    import time
    for plan in plans:
        plan.explanation = _deterministic_explanation(impact, plan)
    if not enabled or not plans:
        return

    selected_provider = (provider or os.getenv("AI_PROVIDER") or settings.ai_provider or "meterless").lower()
    if selected_provider != "meterless":
        for plan in plans:
            plan.explanation = explain_recovery_plan(impact, plan, enabled=enabled, provider=selected_provider)
        return

    from app.providers.meterless import MeterlessClient, resolve_meterless_token

    token = resolve_meterless_token()
    if not token or token == "rt-local":
        return

    cache_key = hashlib.sha256(
        "|".join(_make_explanation_cache_key(impact, p, "meterless-batch") for p in plans).encode("utf-8")
    ).hexdigest()
    now = time.time()
    cached = _EXPLANATION_CACHE.get(cache_key)
    if cached and (now - cached[1]) < _EXPLANATION_CACHE_TTL_SEC:
        explanations = json.loads(cached[0])
        for plan in plans:
            if plan.plan_id in explanations:
                plan.explanation = explanations[plan.plan_id]
        return
    failed_at = _EXPLANATION_FAILURES.get(cache_key)
    if failed_at and (now - failed_at) < _EXPLANATION_FAILURE_TTL_SEC:
        return

    plan_facts = [
        {
            "plan_id": p.plan_id,
            "title": p.title,
            "rank": p.rank,
            "feasibility": p.feasibility_verdict,
            "authorization": p.authorization_outcome,
            "authorization_reason": p.authorization_reason,
            "downtime_hours": p.estimated_downtime_hours,
            "meets_deadline": p.meets_deadline,
            "buffer_hours": p.buffer_hours,
            "total_cost_usd": p.total_cost_usd,
            "hard_constraints": p.hard_constraints_met,
        }
        for p in plans
    ]
    prompt = (
        "You are the FactoryOps Industrial Intelligence Copilot.\n"
        f"Incident: {impact.disruption_type} on {impact.disrupted_machine_id}, severity {impact.severity}.\n"
        f"Evidence: {json.dumps([asdict(e) for e in impact.evidence])}\n"
        f"Customer: {impact.affected_customer}, deadline {impact.order_deadline}, "
        f"unintervened penalty ${impact.potential_penalty_usd:.2f}.\n"
        f"Candidate plans (deterministic figures; do not invent numbers): {json.dumps(plan_facts)}\n\n"
        "For EACH plan, write a concise operational rationale (max 70 words) covering constraint "
        "feasibility, timeline and cost, using only the figures above.\n"
        'Respond ONLY with a JSON object mapping plan_id to its rationale string, e.g. {"plan_x": "..."}.'
    )

    try:
        base_url = os.getenv("METERLESS_BASE_URL") or settings.meterless_base_url or "http://127.0.0.1:8088/v1"
        model = os.getenv("METERLESS_MODEL") or settings.meterless_model or "meterless/auto"
        timeout_sec = float(os.getenv("METERLESS_TIMEOUT_SECONDS", "60.0"))
        client = MeterlessClient(base_url=base_url, token=token, timeout=timeout_sec)
        raw = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=1500,
            retry_budget_sec=_explanation_retry_budget(),
        )
    except Exception as e:
        _EXPLANATION_FAILURES[cache_key] = now
        err_msg = str(e).replace(token, "[REDACTED]")
        logger.warning(
            "Meterless batched explanation unavailable (%s: %s); deterministic explanations retained.",
            type(e).__name__, err_msg[:160],
        )
        return

    parsed = extract_json_object(raw) or {}
    explanations: Dict[str, str] = {}
    for plan in plans:
        text = parsed.get(plan.plan_id)
        if isinstance(text, str) and len(text.strip()) > 30:
            explanations[plan.plan_id] = f"[Meterless Runtime AI] {text.strip()}"
            plan.explanation = explanations[plan.plan_id]
    if explanations:
        _EXPLANATION_CACHE[cache_key] = (json.dumps(explanations), now)
    else:
        _EXPLANATION_FAILURES[cache_key] = now
        logger.warning("Meterless explanation response was not valid plan-keyed JSON; deterministic explanations retained.")


def apply_plan_effects(snapshot: Dict[str, Any], plan_id: str, disruption_type: str) -> List[str]:
    """Applies a recovery plan's physical effects to a scenario snapshot in place.

    Shared by human-approved and autonomous execution so both paths mutate the
    same equipment and material. Returns the mutated entity IDs.
    """
    machines = snapshot.setdefault("machines", {})
    batches = snapshot.setdefault("batches", {})
    supply = snapshot.setdefault("supply_chain", {})
    mutated: List[str] = []

    def _batch(batch_id: str, delay_hours: float) -> None:
        if batch_id in batches:
            batches[batch_id]["status"] = "in_progress"
            batches[batch_id]["delay_hours"] = delay_hours
            mutated.append(batch_id)

    if plan_id in ("plan_a_thermal_flush", "plan_b_reroute_line2", "plan_c_split_expedite"):
        if "machine:plant:ROAST-01" in machines:
            m = machines["machine:plant:ROAST-01"]
            m["status"] = "maintenance" if plan_id == "plan_a_thermal_flush" else "running"
            m["temperature_c"] = 115.0
            m["vibration_rms"] = 1.6
            m["health_score"] = 0.95
            mutated.append("machine:plant:ROAST-01")
        _batch("production_batch:mes:BATCH-101", 0.75)

    elif plan_id in ("plan_a_motor_swap", "plan_b_contract_grind", "plan_c_batch_defer"):
        if "machine:plant:MILL-01" in machines:
            m = machines["machine:plant:MILL-01"]
            # The burnt-out motor stays locked out either way; only the swap repairs it.
            m["status"] = "maintenance"
            if plan_id == "plan_a_motor_swap":
                m["temperature_c"] = 68.0
                m["vibration_rms"] = 1.2
                m["health_score"] = 0.95
            mutated.append("machine:plant:MILL-01")
        _batch("production_batch:mes:BATCH-103", 2.0)

    elif plan_id == "plan_a_hydraulic_reseal":
        if "machine:plant:PRESS-01" in machines:
            m = machines["machine:plant:PRESS-01"]
            m["status"] = "maintenance"  # depressurised & locked out during reseal
            m["current_throughput_kg_h"] = 0.0
            mutated.append("machine:plant:PRESS-01")
        spares = snapshot.setdefault("spare_parts", {})
        spares["hydraulic_seal_kit"] = max(0, int(spares.get("hydraulic_seal_kit", 2)) - 1)
        _batch("production_batch:mes:BATCH-104", 1.0)

    elif plan_id == "plan_b_press_liquor_buffer":
        supply["press_mitigation"] = "press_liquor_buffer"
        _batch("production_batch:mes:BATCH-104", 0.25)

    elif plan_id == "plan_spot_market_buffer":
        silo = snapshot.setdefault("silos", {}).setdefault("silo_01", {})
        inventory = snapshot.setdefault("inventory", {})
        if disruption_type == "quality_contamination":
            # Certified lot staged separately; never blended into the quarantined lot.
            inventory["inventory_lot:wms:LOT-SPOT-01"] = {
                "id": "inventory_lot:wms:LOT-SPOT-01",
                "current_quantity_kg": 5000.0,
                "quantity_kg": 5000.0,
                "quality_status": "approved",
                "moisture_pct": 6.5,
            }
            silo["current_stock_kg"] = 5000.0
            silo["quality_status"] = "approved"
            supply["mitigation"] = "quarantine_replacement_lot"
            mutated.append("inventory_lot:wms:LOT-SPOT-01")
        else:
            inv_lot = inventory.setdefault("inventory_lot:wms:LOT-2026-001", {})
            inv_lot["current_quantity_kg"] = float(inv_lot.get("current_quantity_kg", 0.0)) + 5000.0
            inv_lot["quantity_kg"] = inv_lot["current_quantity_kg"]
            silo["current_stock_kg"] = float(silo.get("current_stock_kg", 3600.0)) + 5000.0
            supply["mitigation"] = "spot_market_buffer"
            mutated.append("inventory_lot:wms:LOT-2026-001")
        mutated.append("silo:plant:SILO-01")
        _batch("production_batch:mes:BATCH-101", 0.5)

    elif plan_id == "plan_recipe_line_throttle":
        roaster = machines.setdefault("machine:plant:ROAST-01", {})
        roaster["current_throughput_kg_h"] = 180.0
        roaster["current_throughput_kgh"] = 180.0
        supply["throttled"] = True
        mutated.append("machine:plant:ROAST-01")

    elif plan_id == "plan_customs_expedite":
        for shp in snapshot.get("inbound_shipments", {}).values():
            shp["revised_arrival_step"] = max(shp.get("scheduled_arrival_step", 8), shp.get("revised_arrival_step", 32) - 16)
            shp["delay_hours"] = max(0.0, float(shp.get("delay_hours") or 0.0) - 4.0)
            mutated.append(shp.get("id", "shipment"))
        supply["mitigation"] = "customs_expedited"
        _batch("production_batch:mes:BATCH-101", 1.0)

    return mutated


def apply_recovery_decision(
    session: Session,
    scenario_id: str,
    plan_id: str,
    decision: Literal["approved", "rejected"],
    operator_id: str,
    rationale: str,
) -> RecoveryDecisionResult:
    """Executes the human-in-the-loop recovery decision:

    - Validates plan identity and feasibility against known candidate options.
    - Rejects stale/conflicting approvals if an approved plan is already active.
    - Enforces explicit human authorization before baseline or scenario state mutation.
    - Updates scenario machine and batch status if approved.
    - Logs decision event to PostgreSQL OperationalEvent log and World Model audit log.
    """
    if plan_id not in VALID_PLANS:
        raise ValueError(
            f"Unknown or infeasible recovery plan '{plan_id}'. Valid options: {list(VALID_PLANS.keys())}"
        )
    if decision not in ("approved", "rejected"):
        raise ValueError(f"Invalid decision '{decision}'. Must be 'approved' or 'rejected'.")

    scenario = session.execute(
        select(SimulationScenario)
        .where(SimulationScenario.id == scenario_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not scenario:
        raise ValueError(f"Scenario '{scenario_id}' not found.")

    # Guard against conflicting approvals
    current_rec = scenario.parameters.get("recovery")
    if current_rec and current_rec.get("decision") == "approved" and decision == "approved":
        if current_rec.get("selected_plan") != plan_id:
            raise ValueError(
                f"Scenario '{scenario_id}' already has active approved plan '{current_rec.get('selected_plan')}'. Conflicting approval rejected."
            )

    # Enforce hybrid policy safety gate: A BLOCKED plan can NEVER execute, even if an operator clicks Approve
    if decision == "approved":
        from app.services.recovery_policy import evaluate_recovery_authorization
        auth = evaluate_recovery_authorization(
            plan={"plan_id": plan_id},
            disruption_type=scenario.disruption_type,
            scenario=scenario,
        )
        if auth.outcome == "BLOCKED":
            raise ValueError(f"Plan '{plan_id}' is BLOCKED by safety policy: {auth.reason}. Cannot be approved.")

    applied_at = datetime.now(timezone.utc)
    snapshot = scenario.parameters.get("snapshot", {})
    world = get_scenario_world_model(session, scenario_id)

    event_type = f"recovery.{decision}"
    event_id = f"evt_rec_{scenario_id}_{plan_id}_{decision[:3]}"
    idempotency_raw = f"{scenario_id}|{plan_id}|{decision}|{operator_id}"
    idempotency_key = hashlib.sha256(idempotency_raw.encode("utf-8")).hexdigest()[:32]

    # Check if this exact decision was already applied (idempotency)
    existing_event = session.execute(
        select(OperationalEvent).where(OperationalEvent.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing_event:
        return RecoveryDecisionResult(
            scenario_id=scenario_id,
            plan_id=plan_id,
            decision=decision,
            operator_id=operator_id,
            rationale=rationale,
            applied_at=existing_event.recorded_at.isoformat(),
            event_id=existing_event.event_id,
            updated_scenario_status=scenario.status,
        )

    # Apply mutation if approved
    if decision == "approved":
        scenario.status = "recovering"
        apply_plan_effects(snapshot, plan_id, detect_and_trace_impact(session, scenario_id).disruption_type)

        # Log to World Model facts & audit trail
        world.assert_fact(
            about="machine:plant:ROAST-01",
            predicate="status",
            value="recovering",
            source=Provenance(
                kind="operator_action",
                at=applied_at.isoformat(),
                by=f"operator:{operator_id}",
                run_id=scenario_id,
            ),
            confidence=1.0,
        )
        world._store.log_audit(
            kind="recovery_approved",
            actor=f"operator:{operator_id}",
            summary=f"Approved plan {plan_id}: {rationale}",
            event_id=event_id,
            run_id=scenario_id,
            at=applied_at.isoformat(),
        )
    else:
        # Rejected plan leaves scenario and machine in existing operational status
        world.assert_fact(
            about="machine:plant:ROAST-01",
            predicate="recovery_status",
            value=f"plan_rejected:{plan_id}",
            source=Provenance(
                kind="operator_action",
                at=applied_at.isoformat(),
                by=f"operator:{operator_id}",
                run_id=scenario_id,
            ),
            confidence=1.0,
        )
        world._store.log_audit(
            kind="recovery_rejected",
            actor=f"operator:{operator_id}",
            summary=f"Rejected plan {plan_id}: {rationale}",
            event_id=event_id,
            run_id=scenario_id,
            at=applied_at.isoformat(),
        )

    if decision == "approved":
        step_size = scenario.step_size_minutes or 15
        downtime_hours = PLAN_DOWNTIME_HOURS.get(plan_id, 0.75)
        duration_steps = calculate_plan_duration_steps(plan_id, step_size)
        start_step = snapshot.get("step", scenario.current_step)
        completed_step = start_step + duration_steps

        recovery_payload = {
            "selected_plan": plan_id,
            "decision": decision,
            "operator_id": operator_id,
            "rationale": rationale,
            "applied_at": applied_at.isoformat(),
            "start_step": start_step,
            "duration_hours": downtime_hours,
            "duration_steps": duration_steps,
            "remaining_steps": duration_steps,
            "completed_step": completed_step,
            "status": "in_progress",
        }
    else:
        recovery_payload = {
            "selected_plan": plan_id,
            "decision": decision,
            "operator_id": operator_id,
            "rationale": rationale,
            "applied_at": applied_at.isoformat(),
            "status": "rejected",
        }

    scenario.parameters = {
        **scenario.parameters,
        "snapshot": snapshot,
        "world_model_state": world.state_shape(),
        "recovery": recovery_payload,
    }

    # Record append-only operational event
    op_event = OperationalEvent(
        event_id=event_id,
        scenario_id=scenario_id,
        event_type=event_type,
        schema_version=1,
        simulation_time=scenario.simulation_time,
        recorded_at=applied_at,
        source_system="recovery_intelligence",
        source_record_id=plan_id,
        entity_refs=[scenario_id, "machine:plant:ROAST-01", "production_batch:mes:BATCH-101"],
        idempotency_key=idempotency_key,
        payload={
            "plan_id": plan_id,
            "decision": decision,
            "operator_id": operator_id,
            "rationale": rationale,
            "duration_steps": recovery_payload.get("duration_steps", 0),
            "completed_step": recovery_payload.get("completed_step"),
        },
    )
    session.add(op_event)
    session.flush()

    return RecoveryDecisionResult(
        scenario_id=scenario_id,
        plan_id=plan_id,
        decision=decision,
        operator_id=operator_id,
        rationale=rationale,
        applied_at=applied_at.isoformat(),
        event_id=event_id,
        updated_scenario_status=scenario.status,
    )
