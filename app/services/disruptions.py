"""
Disruption Simulator Catalogue and Execution Engine for FactoryOps.

Provides:
- Complete catalogue of industrial disruptions (Supply Chain, Machinery, Quality).
- Dynamic eligibility evaluation based on live scenario state.
- Random mode selection with uniform probability across eligible disruptions and parameter generation.
- Consequence propagation to machines, batches, inventory, stages, and orders.
- Audit trail logging via OperationalEvent and Meterless World Model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import random
from typing import Any, Dict, List, Literal, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperationalEvent, SimulationScenario
from app.services.simulation import (
    BASELINE_START_TIME,
    get_scenario_world_model,
)
from app.world_model import Provenance

logger = logging.getLogger("factoryops.disruptions")


@dataclass
class DisruptionDefinition:
    type_id: str
    name: str
    category: Literal["Supply Chain", "Machinery & Equipment", "Inventory & Quality"]
    explanation: str
    prerequisites: str
    default_parameters: Dict[str, Any]
    parameter_schema: Dict[str, Any]
    severity: Literal["low", "medium", "high", "critical"] = "medium"


DISRUPTION_CATALOGUE: Dict[str, DisruptionDefinition] = {
    "supplier_customs_delay": DisruptionDefinition(
        type_id="supplier_customs_delay",
        name="Customs Clearance Hold on Inbound Beans",
        category="Supply Chain",
        severity="medium",
        explanation=(
            "Port authorities in Rotterdam impose an administrative customs hold on shipment "
            "SHP-2026-COCOA-01 (+6h / +24 steps). Raw cocoa bean silo runs dry before shipment releases, "
            "causing roaster starvation and downstream customer delivery risk."
        ),
        prerequisites="Scenario must have an active in-transit shipment not already subject to customs hold.",
        default_parameters={"delay_hours": 6.0},
        parameter_schema={
            "delay_hours": {"type": "float", "min": 2.0, "max": 12.0, "default": 6.0, "unit": "hours"}
        },
    ),
    "supplier_shortfall": DisruptionDefinition(
        type_id="supplier_shortfall",
        name="Supplier Delivery Quantity Shortfall",
        category="Supply Chain",
        severity="medium",
        explanation=(
            "Harbor weighbridge inspection reveals inbound shipment delivers only 6,000 kg instead of "
            "the ordered 15,000 kg (-60% shortfall) due to agricultural supply deficits in West Africa."
        ),
        prerequisites="Scenario has an inbound shipment that has not yet completed delivery.",
        default_parameters={"delivered_quantity_kg": 6000.0},
        parameter_schema={
            "delivered_quantity_kg": {"type": "float", "min": 3000.0, "max": 10000.0, "default": 6000.0, "unit": "kg"}
        },
    ),
    "roaster_bearing_overheat": DisruptionDefinition(
        type_id="roaster_bearing_overheat",
        name="Drum Roaster Driveshaft Bearing Overheat",
        category="Machinery & Equipment",
        severity="high",
        explanation=(
            "Drum Roaster ROAST-01 driveshaft bearing temperature exceeds 120°C threshold (142.5°C) with "
            "severe vibration (4.2 mm/s), threatening catastrophic main bearing seizure."
        ),
        prerequisites="ROAST-01 is operating nominally and is not currently offline or undergoing recovery.",
        default_parameters={"temperature_c": 142.5, "vibration_rms": 4.2},
        parameter_schema={
            "temperature_c": {"type": "float", "min": 125.0, "max": 160.0, "default": 142.5, "unit": "°C"},
            "vibration_rms": {"type": "float", "min": 2.6, "max": 6.0, "default": 4.2, "unit": "mm/s"},
        },
    ),
    "equipment_failure": DisruptionDefinition(
        type_id="equipment_failure",
        name="Grinding Mill Drive Motor Burnout",
        category="Machinery & Equipment",
        severity="critical",
        explanation=(
            "Triple-Roll Liquor Grinder MILL-01 suffers motor burnout (168°C, 8.6 mm/s vibration), "
            "tripping main breaker and halting cocoa nibs to liquor refining."
        ),
        prerequisites="MILL-01 is currently operational and not offline or recovering.",
        default_parameters={"temperature_c": 168.0, "vibration_rms": 8.6},
        parameter_schema={
            "temperature_c": {"type": "float", "min": 150.0, "max": 190.0, "default": 168.0, "unit": "°C"},
            "vibration_rms": {"type": "float", "min": 5.0, "max": 12.0, "default": 8.6, "unit": "mm/s"},
        },
    ),
    "reduced_machine_capacity": DisruptionDefinition(
        type_id="reduced_machine_capacity",
        name="Hydraulic Press Pressure Loss",
        category="Machinery & Equipment",
        severity="medium",
        explanation=(
            "Hydraulic Cocoa Butter Press PRESS-01 loses hydraulic line pressure, cutting throughput "
            "from 600 kg/h to 240 kg/h (40% rated capacity) and causing a critical bottleneck for butter extraction."
        ),
        prerequisites="PRESS-01 is currently operational and not degraded.",
        default_parameters={"capacity_pct": 40.0},
        parameter_schema={
            "capacity_pct": {"type": "float", "min": 20.0, "max": 60.0, "default": 40.0, "unit": "%"}
        },
    ),
    "quality_contamination": DisruptionDefinition(
        type_id="quality_contamination",
        name="Raw Bean High Moisture Quarantine",
        category="Inventory & Quality",
        severity="high",
        explanation=(
            "Quality control test on Silo 1 detects bean moisture at 9.2% (ISO safety limit 7.0%). "
            "Lot LOT-2026-001 is immediately placed on quarantine hold to prevent mold development."
        ),
        prerequisites="Raw cocoa bean lot is currently approved and not already on quarantine hold.",
        default_parameters={"moisture_pct": 9.2},
        parameter_schema={
            "moisture_pct": {"type": "float", "min": 8.0, "max": 12.0, "default": 9.2, "unit": "%"}
        },
    ),
}


def sync_incident_severity(session: Session, scenario_id: str) -> Optional[str]:
    """Re-derives the active incident's severity from live evidence.

    Severity at trigger time can change as consequences evolve (stock depletes,
    deadlines approach, recovery mitigates), so the stored incident, the
    /incidents and /recovery/impact APIs, and the UI badges all read the same
    evidence-based value. Inline step-triggered disruptions (roaster/mill) get an
    incident record here too. Returns the live severity, or None if no incident.
    """
    from sqlalchemy.orm.attributes import flag_modified
    from app.services.recovery import detect_and_trace_impact

    scenario = session.get(SimulationScenario, scenario_id)
    if not scenario:
        return None
    try:
        impact = detect_and_trace_impact(session, scenario_id)
    except Exception as e:
        logger.warning("Severity sync skipped for %s: %s", scenario_id, e)
        return None

    params = dict(scenario.parameters or {})
    snapshot = params.get("snapshot", {})
    incidents = list(snapshot.get("active_incidents") or params.get("active_incidents") or [])
    has_evidence = len(impact.evidence) > 0
    changed = False

    open_match = [
        inc for inc in incidents
        if inc.get("status") != "resolved" and impact.disruption_type in (inc.get("type"), inc.get("disruption_type"))
    ]
    if has_evidence and not open_match and impact.disruption_type in DISRUPTION_CATALOGUE:
        defn = DISRUPTION_CATALOGUE[impact.disruption_type]
        step = snapshot.get("step", scenario.current_step)
        incidents.append({
            "incident_id": f"inc_{scenario_id}_{impact.disruption_type}_{step}",
            "type": impact.disruption_type,
            "disruption_type": impact.disruption_type,
            "name": defn.name,
            "category": defn.category,
            "severity": impact.severity,
            "triggered_at_step": step,
            "triggered_at_time": snapshot.get("simulation_time"),
            "status": "awaiting_decision",
            "affected_entities": [impact.disrupted_machine_id, impact.affected_batch_id, impact.affected_order_id],
            "parameters": {},
            "is_random": False,
        })
        changed = True
    elif has_evidence:
        for inc in open_match:
            if inc.get("severity") != impact.severity:
                inc["severity"] = impact.severity
                changed = True

    if changed:
        snapshot["active_incidents"] = incidents
        params["active_incidents"] = incidents
        params["snapshot"] = snapshot
        scenario.parameters = params
        flag_modified(scenario, "parameters")
        session.flush()
    return impact.severity if has_evidence else None


def get_disruption_catalogue_list() -> List[Dict[str, Any]]:
    """Returns the serialized catalogue of supported disruptions."""
    return [
        {
            "type_id": d.type_id,
            "name": d.name,
            "category": d.category,
            "severity": d.severity,
            "explanation": d.explanation,
            "prerequisites": d.prerequisites,
            "default_parameters": d.default_parameters,
            "parameter_schema": d.parameter_schema,
        }
        for d in DISRUPTION_CATALOGUE.values()
    ]


def _extract_snapshot(target: Any, scenario_id: Optional[str] = None) -> Dict[str, Any]:
    if isinstance(target, dict):
        return target
    if isinstance(target, Session):
        scenario = target.get(SimulationScenario, scenario_id)
        if not scenario:
            raise ValueError(f"Scenario '{scenario_id}' not found.")
        return scenario.parameters.get("snapshot", {})
    if hasattr(target, "parameters"):
        return target.parameters.get("snapshot", {})
    raise TypeError(f"Cannot extract snapshot from target of type {type(target)}")


def evaluate_disruption_eligibility(
    target: Any,
    arg2: str,
    arg3: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Evaluates whether a specific disruption type can occur in the given scenario snapshot.
    Supports either (snapshot, disruption_type) or (session, scenario_id, disruption_type).
    Returns (eligible: bool, reason: str).
    """
    if arg3 is not None:
        snapshot = _extract_snapshot(target, arg2)
        disruption_type = arg3
    else:
        snapshot = _extract_snapshot(target)
        disruption_type = arg2

    if disruption_type not in DISRUPTION_CATALOGUE:
        return False, f"Unknown disruption type '{disruption_type}'."

    machines = snapshot.get("machines", {})
    shipments = snapshot.get("inbound_shipments", {})
    inventory = snapshot.get("inventory", {})

    if disruption_type == "supplier_customs_delay":
        shp = next(iter(shipments.values()), None)
        if not shp:
            return False, "No inbound shipments configured in scenario snapshot."
        if shp.get("status") == "customs_hold":
            return False, "Shipment is already under customs hold."
        if shp.get("status") == "delivered":
            return False, "Shipment has already been delivered."
        return True, "Eligible: Inbound shipment is in transit."

    if disruption_type == "supplier_shortfall":
        shp = next(iter(shipments.values()), None)
        if not shp:
            return False, "No inbound shipments configured."
        if shp.get("status") == "delivered":
            return False, "Shipment has already arrived."
        return True, "Eligible: Inbound shipment pending arrival."

    if disruption_type == "roaster_bearing_overheat":
        roast = machines.get("machine:plant:ROAST-01")
        if not roast:
            return False, "Machine ROAST-01 not found."
        if roast.get("status") in ("degraded", "maintenance", "recovering", "failed"):
            return False, f"ROAST-01 is already degraded ({roast.get('status')})."
        return True, "Eligible: ROAST-01 operating nominally."

    if disruption_type == "equipment_failure":
        mill = machines.get("machine:plant:MILL-01")
        if not mill:
            return False, "Machine MILL-01 not found."
        if mill.get("status") in ("failed", "maintenance", "recovering"):
            return False, f"MILL-01 is currently {mill.get('status')}."
        return True, "Eligible: MILL-01 operating nominally."

    if disruption_type == "reduced_machine_capacity":
        press = machines.get("machine:plant:PRESS-01")
        if not press:
            return False, "Machine PRESS-01 not found."
        if press.get("status") in ("failed", "degraded", "maintenance"):
            return False, f"PRESS-01 is already {press.get('status')}."
        return True, "Eligible: PRESS-01 operating at nominal 100% capacity."

    if disruption_type == "quality_contamination":
        lot = inventory.get("inventory_lot:wms:LOT-2026-001")
        if not lot:
            return False, "Raw bean inventory lot not found."
        if lot.get("quality_status") == "QUARANTINE_HOLD":
            return False, "Inventory lot is already quarantined."
        return True, "Eligible: Raw cocoa lot is approved and in storage."

    return False, "Prerequisites not met."


def get_eligible_disruptions(
    target: Any,
    scenario_id: Optional[str] = None,
) -> Any:
    """
    Returns eligible disruptions.
    If called with (session, scenario_id) or scenario object: returns list of detailed catalogue dicts.
    If called with (snapshot): returns list of eligible disruption_type IDs (strings).
    """
    snapshot = _extract_snapshot(target, scenario_id)

    if scenario_id is not None or not isinstance(target, dict):
        result = []
        for d_type, d_def in DISRUPTION_CATALOGUE.items():
            ok, reason = evaluate_disruption_eligibility(snapshot, d_type)
            result.append({
                "type_id": d_def.type_id,
                "name": d_def.name,
                "category": d_def.category,
                # Typical severity at default parameters; a triggered incident's
                # severity is recomputed from live evidence.
                "severity": d_def.severity,
                "explanation": d_def.explanation,
                "prerequisites": d_def.prerequisites,
                "default_parameters": d_def.default_parameters,
                "parameter_schema": d_def.parameter_schema,
                "eligible": ok,
                "ineligibility_reason": None if ok else reason,
            })
        return result

    # Snapshot-only call: return list of string IDs
    eligible = []
    for d_type in DISRUPTION_CATALOGUE.keys():
        ok, _ = evaluate_disruption_eligibility(snapshot, d_type)
        if ok:
            eligible.append(d_type)
    return eligible


def select_random_disruption(
    target: Any,
    scenario_id_or_seed: Any = None,
    seed: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Selects one eligible disruption uniformly at random and generates valid parameters.
    Supports either (snapshot, seed) or (session, scenario_id, seed).
    Returns (chosen_type, parameters).
    """
    if isinstance(target, (Session, SimulationScenario)) or (isinstance(scenario_id_or_seed, str) and not isinstance(target, dict)):
        snapshot = _extract_snapshot(target, scenario_id_or_seed)
        actual_seed = seed
    else:
        snapshot = _extract_snapshot(target)
        actual_seed = scenario_id_or_seed if isinstance(scenario_id_or_seed, int) else seed

    eligible = []
    for d_type in DISRUPTION_CATALOGUE.keys():
        ok, _ = evaluate_disruption_eligibility(snapshot, d_type)
        if ok:
            eligible.append(d_type)
    if not eligible:
        raise ValueError("No disruptions are currently eligible in the current scenario state.")

    rng = random.Random(actual_seed) if actual_seed is not None else random.Random()
    chosen_type = rng.choice(eligible)
    defn = DISRUPTION_CATALOGUE[chosen_type]

    # Generate valid parameters within configured bounds
    params = {}
    for param_name, schema in defn.parameter_schema.items():
        if schema.get("type") == "float":
            val = rng.uniform(schema["min"], schema["max"])
            params[param_name] = round(val, 1)
        elif schema.get("type") == "int":
            params[param_name] = rng.randint(schema["min"], schema["max"])
        else:
            params[param_name] = schema.get("default")

    return chosen_type, params


def apply_disruption(
    session: Session,
    scenario_id: str,
    disruption_type: str,
    parameters: Optional[Dict[str, Any]] = None,
    operator_id: str = "operator_console",
) -> Dict[str, Any]:
    """
    Applies a disruption to the scenario snapshot, updates World Model facts,
    emits an append-only OperationalEvent, and persists the new state atomically.
    """
    scenario = session.execute(
        select(SimulationScenario)
        .where(SimulationScenario.id == scenario_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not scenario:
        raise ValueError(f"Scenario '{scenario_id}' not found.")

    params = scenario.parameters or {}
    snapshot = params.get("snapshot", {})
    current_step = snapshot.get("step", scenario.current_step)
    sim_time = datetime.fromisoformat(snapshot.get("simulation_time", scenario.simulation_time.isoformat()))
    world = get_scenario_world_model(session, scenario_id)

    # 1. Handle Random mode selection
    is_random = (disruption_type == "random")
    random_selection_meta = None
    if is_random:
        chosen_type, gen_params = select_random_disruption(snapshot)
        disruption_type = chosen_type
        parameters = gen_params
        random_selection_meta = {"selected_by": "random_generator", "reason": "uniform over eligible disruptions"}
    else:
        eligible, reason = evaluate_disruption_eligibility(snapshot, disruption_type)
        if not eligible:
            raise ValueError(f"Disruption '{disruption_type}' cannot be triggered: {reason}")
        if parameters is None:
            parameters = dict(DISRUPTION_CATALOGUE[disruption_type].default_parameters)

    defn = DISRUPTION_CATALOGUE[disruption_type]
    scenario.disruption_type = disruption_type

    # 2. Mutate Scenario State according to Disruption Type
    emitted_events = []
    affected_entities = []
    incident_id = f"inc_{scenario_id}_{disruption_type}_{current_step}"

    if disruption_type == "supplier_customs_delay":
        delay_hours = float(parameters.get("delay_hours", 6.0))
        delay_steps = int(round(delay_hours * 4.0))  # 15m steps
        shipments = snapshot.get("inbound_shipments", {})
        shp = next(iter(shipments.values()), None)
        if shp:
            shp["status"] = "customs_hold"
            shp["delay_hours"] = delay_hours
            shp["disruption_reason"] = f"Customs clearance inspection hold (+{delay_hours}h)"
            new_arr_step = shp.get("scheduled_arrival_step", 8) + delay_steps
            shp["revised_arrival_step"] = new_arr_step
            new_arr_time = sim_time + timedelta(hours=delay_hours)
            shp["revised_arrival_time"] = new_arr_time.isoformat()
            affected_entities.append(shp["id"])

        # Flag BATCH-101 and ORD-9402 as At Risk
        if "batches" in snapshot and "production_batch:mes:BATCH-101" in snapshot["batches"]:
            snapshot["batches"]["production_batch:mes:BATCH-101"]["status"] = "delayed"
            snapshot["batches"]["production_batch:mes:BATCH-101"]["delay_hours"] = delay_hours
            affected_entities.append("production_batch:mes:BATCH-101")
        if "orders" in snapshot and "customer_order:erp:ORD-9402" in snapshot["orders"]:
            snapshot["orders"]["customer_order:erp:ORD-9402"]["status"] = "at_risk"
            affected_entities.append("customer_order:erp:ORD-9402")

        evt = {
            "event_type": "shipment.customs_hold",
            "evidence_id": f"EV-CUSTOMS-HOLD-{scenario_id}-{current_step}",
            "entity_id": shp["id"] if shp else scenario_id,
            "payload": {
                "shipment_id": shp["id"] if shp else "SHP-2026-COCOA-01",
                "delay_hours": delay_hours,
                "delay_steps": delay_steps,
                "revised_arrival_step": new_arr_step if shp else 32,
                "reason": "Administrative customs inspection hold at Rotterdam port terminal.",
            },
        }
        emitted_events.append(evt)
        world.assert_fact(
            about="shipment:sup:SHP-2026-COCOA-01",
            predicate="status",
            value="customs_hold",
            source=Provenance(kind="telemetry", at=sim_time.isoformat(), run_id=scenario_id, by="port_authority"),
            confidence=0.99,
        )

    elif disruption_type == "supplier_shortfall":
        delivered_kg = float(parameters.get("delivered_quantity_kg", 6000.0))
        shipments = snapshot.get("inbound_shipments", {})
        shp = next(iter(shipments.values()), None)
        if shp:
            shp["quantity_kg"] = delivered_kg
            shp["disruption_reason"] = f"Delivery shortfall: delivered {delivered_kg} kg vs 15,000 kg ordered."
            affected_entities.append(shp["id"])

        evt = {
            "event_type": "shipment.shortfall",
            "evidence_id": f"EV-SHORTFALL-{scenario_id}-{current_step}",
            "entity_id": shp["id"] if shp else scenario_id,
            "payload": {
                "shipment_id": shp["id"] if shp else "SHP-2026-COCOA-01",
                "delivered_kg": delivered_kg,
                "ordered_kg": 15000.0,
                "deficit_kg": 15000.0 - delivered_kg,
            },
        }
        emitted_events.append(evt)

    elif disruption_type == "roaster_bearing_overheat":
        temp_c = float(parameters.get("temperature_c", 142.5))
        vib_rms = float(parameters.get("vibration_rms", 4.2))
        roaster = snapshot.get("machines", {}).get("machine:plant:ROAST-01")
        if roaster:
            roaster["status"] = "degraded"
            roaster["health_score"] = 0.55
            roaster["temperature_c"] = temp_c
            roaster["vibration_rms"] = vib_rms
            roaster["current_throughput_kg_h"] = 180.0
            roaster["current_throughput_kgh"] = 180.0
            affected_entities.append("machine:plant:ROAST-01")

        if "batches" in snapshot and "production_batch:mes:BATCH-101" in snapshot["batches"]:
            snapshot["batches"]["production_batch:mes:BATCH-101"]["status"] = "delayed"
            snapshot["batches"]["production_batch:mes:BATCH-101"]["delay_hours"] = 3.5
            affected_entities.append("production_batch:mes:BATCH-101")
        if "orders" in snapshot and "customer_order:erp:ORD-9402" in snapshot["orders"]:
            snapshot["orders"]["customer_order:erp:ORD-9402"]["status"] = "at_risk"
            affected_entities.append("customer_order:erp:ORD-9402")

        evt = {
            "event_type": "machine.overheated",
            "evidence_id": f"EV-ROAST-TEMP-{scenario_id}-{current_step}",
            "entity_id": "machine:plant:ROAST-01",
            "payload": {
                "temperature_c": temp_c,
                "vibration_rms": vib_rms,
                "threshold_temp_c": 120.0,
                "threshold_vib_rms": 2.5,
            },
        }
        emitted_events.append(evt)
        world.assert_fact(
            about="machine:plant:ROAST-01",
            predicate="status",
            value="degraded",
            source=Provenance(kind="telemetry", at=sim_time.isoformat(), run_id=scenario_id, by="sensor_scada"),
            confidence=0.99,
        )

    elif disruption_type == "equipment_failure":
        temp_c = float(parameters.get("temperature_c", 168.0))
        vib_rms = float(parameters.get("vibration_rms", 8.6))
        mill = snapshot.get("machines", {}).get("machine:plant:MILL-01")
        if mill:
            mill["status"] = "failed"
            mill["health_score"] = 0.20
            mill["temperature_c"] = temp_c
            mill["vibration_rms"] = vib_rms
            mill["current_throughput_kg_h"] = 0.0
            mill["current_throughput_kgh"] = 0.0
            affected_entities.append("machine:plant:MILL-01")

        if "batches" in snapshot and "production_batch:mes:BATCH-103" in snapshot["batches"]:
            snapshot["batches"]["production_batch:mes:BATCH-103"]["status"] = "delayed"
            snapshot["batches"]["production_batch:mes:BATCH-103"]["delay_hours"] = 6.0
            affected_entities.append("production_batch:mes:BATCH-103")
        if "orders" in snapshot and "customer_order:erp:ORD-9405" in snapshot["orders"]:
            snapshot["orders"]["customer_order:erp:ORD-9405"]["status"] = "at_risk"
            affected_entities.append("customer_order:erp:ORD-9405")

        evt = {
            "event_type": "machine.failed",
            "evidence_id": f"EV-MILL-FAILURE-{scenario_id}-{current_step}",
            "entity_id": "machine:plant:MILL-01",
            "payload": {
                "temperature_c": temp_c,
                "vibration_rms": vib_rms,
                "message": "Grinding mill drive motor thermal trip and mechanical seizure.",
            },
        }
        emitted_events.append(evt)
        world.assert_fact(
            about="machine:plant:MILL-01",
            predicate="status",
            value="failed",
            source=Provenance(kind="telemetry", at=sim_time.isoformat(), run_id=scenario_id, by="sensor_scada"),
            confidence=1.0,
        )

    elif disruption_type == "reduced_machine_capacity":
        cap_pct = float(parameters.get("capacity_pct", 40.0))
        press = snapshot.get("machines", {}).get("machine:plant:PRESS-01")
        if press:
            nominal_cap = 600.0
            new_cap = round(nominal_cap * (cap_pct / 100.0), 1)
            press["status"] = "degraded"
            press["rated_capacity_kg_h"] = new_cap
            press["rated_capacity_kgh"] = new_cap
            press["current_throughput_kg_h"] = new_cap
            press["health_score"] = 0.65
            affected_entities.append("machine:plant:PRESS-01")

        if "batches" in snapshot and "production_batch:mes:BATCH-104" in snapshot["batches"]:
            snapshot["batches"]["production_batch:mes:BATCH-104"]["status"] = "delayed"
            snapshot["batches"]["production_batch:mes:BATCH-104"]["delay_hours"] = 2.5
            affected_entities.append("production_batch:mes:BATCH-104")

        evt = {
            "event_type": "machine.capacity_reduced",
            "evidence_id": f"EV-PRESS-PRESS-{scenario_id}-{current_step}",
            "entity_id": "machine:plant:PRESS-01",
            "payload": {
                "capacity_pct": cap_pct,
                "throughput_kg_h": new_cap if press else 240.0,
                "message": f"Hydraulic press pressure loss: capacity throttled to {cap_pct}%.",
            },
        }
        emitted_events.append(evt)
        world.assert_fact(
            about="machine:plant:PRESS-01",
            predicate="status",
            value="degraded",
            source=Provenance(kind="telemetry", at=sim_time.isoformat(), run_id=scenario_id, by="sensor_scada"),
            confidence=0.95,
        )

    elif disruption_type == "quality_contamination":
        moisture = float(parameters.get("moisture_pct", 9.2))
        lot = snapshot.get("inventory", {}).get("inventory_lot:wms:LOT-2026-001")
        if lot:
            lot["quality_status"] = "QUARANTINE_HOLD"
            lot["quality_grade"] = "HOLD"
            lot["moisture_pct"] = moisture
            affected_entities.append("inventory_lot:wms:LOT-2026-001")

        evt = {
            "event_type": "quality.quarantine_hold",
            "evidence_id": f"EV-QC-MOISTURE-{scenario_id}-{current_step}",
            "entity_id": "inventory_lot:wms:LOT-2026-001",
            "payload": {
                "moisture_pct": moisture,
                "threshold_pct": 7.0,
                "message": f"Moisture content {moisture}% exceeds safe storage threshold 7.0%.",
            },
        }
        emitted_events.append(evt)
        world.assert_fact(
            about="inventory_lot:wms:LOT-2026-001",
            predicate="quality_status",
            value="QUARANTINE_HOLD",
            source=Provenance(kind="qc_lab", at=sim_time.isoformat(), run_id=scenario_id, by="qa_inspector"),
            confidence=1.0,
        )

    # 3. Add to active incidents in snapshot
    # Derive evidence-grounded dynamic severity
    scenario.parameters = params
    try:
        from app.services.recovery import detect_and_trace_impact
        impact = detect_and_trace_impact(session, scenario_id)
        if impact.disruption_type == disruption_type and impact.evidence:
            disruption_severity = impact.severity
        else:
            # Another, earlier incident dominates detection; fall back to the typical value.
            disruption_severity = defn.severity
    except Exception as e:
        logger.warning("Could not trace dynamic impact severity: %s; falling back to default.", e)
        disruption_severity = defn.severity if defn else "critical"

    active_incidents = snapshot.get("active_incidents", [])
    incident_record = {
        "incident_id": incident_id,
        "type": disruption_type,
        "disruption_type": disruption_type,
        "name": defn.name if defn else disruption_type,
        "category": defn.category if defn else "Unknown",
        "severity": disruption_severity,
        "triggered_at_step": current_step,
        "triggered_at_time": sim_time.isoformat(),
        "status": "awaiting_decision",
        "affected_entities": affected_entities,
        "parameters": parameters,
        "is_random": is_random,
        "random_meta": random_selection_meta,
    }
    # Avoid duplicate incidents of same type
    active_incidents = [inc for inc in active_incidents if inc.get("disruption_type") != disruption_type and inc.get("type") != disruption_type]
    active_incidents.append(incident_record)
    snapshot["active_incidents"] = active_incidents
    params["active_incidents"] = active_incidents

    # 4. Clear any stale recovery decision so new decision is required
    params["recovery"] = {
        "status": "awaiting_decision",
        "incident_id": incident_id,
        "disruption_type": disruption_type,
        "severity": disruption_severity,
    }

    # 5. Persist OperationalEvents
    for e_data in emitted_events:
        raw_key = f"{scenario_id}|{current_step}|{e_data['event_type']}|{e_data['entity_id']}"
        idemp = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
        op_event = OperationalEvent(
            event_id=f"evt_{scenario_id}_{current_step}_{e_data['event_type'][:12]}",
            scenario_id=scenario_id,
            event_type=e_data["event_type"],
            schema_version=1,
            simulation_time=sim_time,
            recorded_at=datetime.now(timezone.utc),
            source_system="simulator",
            source_record_id=e_data["evidence_id"],
            entity_refs=affected_entities,
            idempotency_key=idemp,
            payload=e_data["payload"],
        )
        session.add(op_event)

    from sqlalchemy.orm.attributes import flag_modified

    scenario.parameters = {
        **params,
        "snapshot": snapshot,
        "world_model_state": world.state_shape(),
    }
    flag_modified(scenario, "parameters")
    session.flush()

    return {
        "success": True,
        "status": "applied",
        "scenario_id": scenario_id,
        "disruption_type": disruption_type,
        "disruption_name": defn.name,
        "current_step": current_step,
        "parameters": parameters,
        "is_random": is_random,
        "random_selection_meta": random_selection_meta,
        "affected_entities": affected_entities,
        "active_incidents_count": len(active_incidents),
    }
