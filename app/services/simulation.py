"""
Deterministic Discrete-Event Simulation Service for Virtual Cocoa Processing.

Advances simulated factory time in discrete steps (e.g. 15 minutes) with
reproducible seed, deterministic disruption triggers, explicit event ordering,
and strict scenario isolation.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CustomerOrder,
    InventoryLot,
    MachineAsset,
    OperationalEvent,
    ProductionBatch,
    SimulationScenario,
    Supplier,
)
from app.services.seed import BASELINE_START_TIME, seed_baseline_factory
from app.world_model import (
    Provenance,
    WorldModel,
    entity_id_from_name,
    external_id,
    relationship_id,
)


def build_scenario_world_model(scenario_id: str, snapshot: dict) -> WorldModel:
    """Builds and initializes a World Model instance populated with canonical

    FactoryOps domain entities, relationships, and continuity anchors.
    """
    world = WorldModel(namespace=scenario_id)
    sim_time_iso = snapshot.get("simulation_time", BASELINE_START_TIME.isoformat())
    src = Provenance(kind="seed", at=sim_time_iso, run_id=scenario_id, by="simulator:init")

    # 1. Register all Machines from snapshot
    for m_id, m in snapshot.get("machines", {}).items():
        world.upsert_entity(
            type_="machine",
            external_key={"system": "plant", "id": m.get("code", m_id)},
            name=m.get("code", m_id),
            attrs={
                "code": m.get("code"),
                "station": m.get("station"),
                "rated_capacity_kg_h": m.get("rated_capacity_kg_h", 1200.0),
                "status": m.get("status", "nominal"),
                "health_score": m.get("health_score", 1.0),
                "bearing_temperature_c": m.get("bearing_temperature_c", m.get("temperature_c")),
                "vibration_rms": m.get("vibration_rms"),
            },
            source=src,
        )

    # 2. Register all Inventory Lots from snapshot
    for lot_id, lot in snapshot.get("inventory", {}).items():
        world.upsert_entity(
            type_="inventory_lot",
            external_key={"system": "wms", "id": lot.get("lot_code", lot_id)},
            name=f"Lot {lot.get('lot_code', lot_id)}",
            attrs={
                "lot_code": lot.get("lot_code"),
                "material_type": lot.get("material_type"),
                "current_quantity_kg": lot.get("current_quantity_kg", 0.0),
            },
            source=src,
        )

    # 3. Register all Production Batches from snapshot & link to machines and lots
    for b_id, b in snapshot.get("batches", {}).items():
        world.upsert_entity(
            type_="production_batch",
            external_key={"system": "mes", "id": b.get("batch_code", b_id)},
            name=f"Batch {b.get('batch_code', b_id)}",
            attrs={
                "batch_code": b.get("batch_code"),
                "recipe_name": b.get("recipe_name"),
                "planned_quantity_kg": b.get("planned_quantity_kg", 1000.0),
                "actual_quantity_kg": b.get("actual_quantity_kg", 0.0),
                "status": b.get("status", "scheduled"),
                "delay_hours": b.get("delay_hours", 0.0),
            },
            source=src,
        )

        # Relate batch -> machine (processed_by)
        machine_ref = b.get("machine_id")
        if machine_ref and machine_ref in snapshot.get("machines", {}):
            world.relate(
                from_=b_id,
                type="processed_by",
                to=machine_ref,
                source=src,
                valid_from=sim_time_iso,
            )

        # Relate batch -> lot (consumes)
        lot_ref = b.get("input_lot_id")
        if lot_ref and lot_ref in snapshot.get("inventory", {}):
            world.relate(
                from_=b_id,
                type="consumes",
                to=lot_ref,
                source=src,
                valid_from=sim_time_iso,
            )

    # 4. Register all Customer Orders from snapshot & link to batches
    for o_id, o in snapshot.get("orders", {}).items():
        world.upsert_entity(
            type_="customer_order",
            external_key={"system": "erp", "id": o.get("order_code", o_id)},
            name=f"Order {o.get('order_code', o_id)} - {o.get('customer_name', '')}",
            attrs={
                "order_code": o.get("order_code"),
                "customer_name": o.get("customer_name"),
                "product_sku": o.get("product_sku"),
                "quantity_kg": o.get("quantity_kg", 1000.0),
                "status": o.get("status", "confirmed"),
                "deadline": o.get("deadline", "2026-10-22T18:00:00+00:00"),
            },
            source=src,
        )

        # Relate order -> batch (fulfills_via)
        batch_ref = o.get("assigned_batch_id")
        if batch_ref and batch_ref in snapshot.get("batches", {}):
            world.relate(
                from_=o_id,
                type="fulfills_via",
                to=batch_ref,
                source=src,
                valid_from=sim_time_iso,
            )

    return world


def _generate_idempotency_key(scenario_id: str, step: int, event_type: str, entity_id: str) -> str:
    """Computes a deterministic content-addressed idempotency key."""
    raw = f"{scenario_id}|{step}|{event_type}|{entity_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_initial_scenario_snapshot(session: Session) -> dict:
    """
    Constructs an isolated state snapshot from the baseline factory data.
    The scenario owns this snapshot and mutates it during steps without
    modifying baseline database tables.
    """
    # Ensure baseline is seeded
    seed_baseline_factory(session)

    machines = session.execute(select(MachineAsset)).scalars().all()
    inventory = session.execute(select(InventoryLot)).scalars().all()
    batches = session.execute(select(ProductionBatch)).scalars().all()
    orders = session.execute(select(CustomerOrder)).scalars().all()

    return {
        "step": 0,
        "simulation_time": BASELINE_START_TIME.isoformat(),
        "machines": {
            m.id: {
                "id": m.id,
                "machine_id": m.id,
                "code": m.code,
                "name": m.name,
                "station": m.station,
                "status": m.operating_status,
                "health_score": float(m.health_score),
                "temperature_c": float(m.current_temp_c) if m.current_temp_c else 115.0,
                "vibration_rms": float(m.current_vibration_rms) if m.current_vibration_rms else 1.8,
                "rated_capacity_kg_h": float(m.rated_capacity_kg_h),
                "rated_capacity_kgh": float(m.rated_capacity_kg_h),
                "current_throughput_kg_h": 0.0,
                "current_throughput_kgh": 0.0,
            }
            for m in machines
        },
        "inventory": {
            lot.id: {
                "id": lot.id,
                "lot_id": lot.id,
                "lot_code": lot.lot_code,
                "material_type": lot.material_type,
                "storage_location": lot.storage_location,
                "current_quantity_kg": float(lot.current_quantity_kg),
                "quantity_kg": float(lot.current_quantity_kg),
                "quality_status": lot.quality_status,
                "quality_grade": lot.quality_status,
            }
            for lot in inventory
        },
        "batches": {
            b.id: {
                "id": b.id,
                "batch_id": b.id,
                "batch_code": b.batch_code,
                "recipe_name": b.recipe_name,
                "target_product": b.target_product,
                "planned_quantity_kg": float(b.planned_quantity_kg),
                "actual_quantity_kg": float(b.actual_quantity_kg),
                "status": b.status,
                "delay_hours": float(b.delay_hours),
                "input_lot_id": b.input_lot_id,
                "machine_id": b.machine_id,
            }
            for b in batches
        },
        "orders": {
            o.id: {
                "id": o.id,
                "order_id": o.id,
                "order_code": o.order_code,
                "customer_name": o.customer_name,
                "product_name": o.product_sku,
                "product_sku": o.product_sku,
                "quantity_kg": float(o.quantity_kg),
                "ordered_quantity_kg": float(o.quantity_kg),
                "deadline": o.deadline.isoformat(),
                "delivery_deadline": o.deadline.isoformat(),
                "status": o.status,
                "assigned_batch_id": o.assigned_batch_id,
                "penalty_per_day_late": float(o.penalty_per_day_late) if o.penalty_per_day_late else 350.0,
                "penalty_usd_per_hour": round(float(o.penalty_per_day_late or 350.0) / 24.0, 2),
            }
            for o in orders
        },
        "inbound_shipments": {
            "shipment:sup:SHP-2026-COCOA-01": {
                "id": "shipment:sup:SHP-2026-COCOA-01",
                "shipment_code": "SHP-2026-COCOA-01",
                "po_number": "PO-2026-88",
                "supplier_name": "Abidjan Cocoa Union",
                "origin": "San Pedro Port, Côte d'Ivoire",
                "destination": "Port of Rotterdam / Factory Silo 1",
                "carrier": "Hapag-Lloyd Ocean Express",
                "quantity_kg": 15000.0,
                "variety": "Forastero Grade 1",
                "moisture_pct": 6.8,
                "scheduled_arrival_step": 8,
                "scheduled_arrival_time": (BASELINE_START_TIME + timedelta(hours=2.0)).isoformat(),
                "revised_arrival_step": 8,
                "revised_arrival_time": (BASELINE_START_TIME + timedelta(hours=2.0)).isoformat(),
                "status": "in_transit",
                "disruption_reason": None,
            }
        },
        "silos": {
            "silo_01": {
                "id": "silo_01",
                "code": "SILO-01",
                "name": "Raw Cocoa Bean Silo 1",
                "capacity_kg": 20000.0,
                "current_stock_kg": 3600.0,
                "quality_status": "approved",
                "moisture_pct": 6.5,
            }
        },
        "production_cycle": {
            "cycle_number": 1,
            "active_stage_num": 1,
            "stage_step_counter": 0,
            "stage_duration_steps": {
                "1": 1,
                "2": 2,
                "3": 1,
                "4": 1,
                "5": 1,
                "6": 1,
                "7": 1,
            },
            "completed_cycles": 0,
        },
    }


def create_scenario(
    session: Session,
    name: str,
    disruption_type: str = "none",
    random_seed: int = 42,
    step_size_minutes: int = 15,
    scenario_id: Optional[str] = None,
    autonomous_recovery: bool = True,
) -> SimulationScenario:
    """
    Initializes a new isolated simulation scenario.

    `autonomous_recovery` opts the scenario into the background autonomous
    recovery loop. Scenarios created before this flag existed carry no flag and
    are never picked up automatically.
    """
    if not scenario_id:
        scenario_id = f"scn_{uuid.uuid4().hex[:12]}"

    initial_snapshot = build_initial_scenario_snapshot(session)
    world = build_scenario_world_model(scenario_id, initial_snapshot)

    scenario = SimulationScenario(
        id=scenario_id,
        name=name,
        description=f"Simulation scenario '{name}' ({disruption_type})",
        disruption_type=disruption_type,
        status="running",
        random_seed=random_seed,
        simulation_time=BASELINE_START_TIME,
        step_size_minutes=step_size_minutes,
        current_step=0,
        parameters={
            "snapshot": initial_snapshot,
            "random_seed": random_seed,
            "disruption_type": disruption_type,
            "world_model_state": world.state_shape(),
            "autonomous_recovery": {"enabled": bool(autonomous_recovery)},
        },
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(scenario)

    # Emit scenario initialized event
    init_event = OperationalEvent(
        event_id=f"evt_{scenario_id}_0_init",
        scenario_id=scenario_id,
        event_type="scenario.initialized",
        schema_version=1,
        simulation_time=BASELINE_START_TIME,
        recorded_at=datetime.now(timezone.utc),
        source_system="simulator",
        source_record_id=scenario_id,
        entity_refs=[scenario_id],
        idempotency_key=_generate_idempotency_key(scenario_id, 0, "scenario.initialized", scenario_id),
        payload={
            "step": 0,
            "disruption_type": disruption_type,
            "random_seed": random_seed,
            "step_size_minutes": step_size_minutes,
        },
    )
    session.add(init_event)
    session.flush()

    return scenario


def is_autonomous_recovery_enabled(scenario: SimulationScenario) -> bool:
    return bool(((scenario.parameters or {}).get("autonomous_recovery") or {}).get("enabled"))


def requires_human_decision(scenario: SimulationScenario) -> bool:
    """True when an operator must act (as opposed to the autonomous loop)."""
    recovery = (scenario.parameters or {}).get("recovery") or {}
    if recovery.get("status") in ("pending_approval", "blocked"):
        return True
    if not is_autonomous_recovery_enabled(scenario):
        return is_awaiting_recovery_decision(scenario)
    return False


def is_awaiting_recovery_decision(scenario: SimulationScenario) -> bool:
    """
    Checks if the scenario has experienced a disruption anomaly that requires
    a human operator recovery decision before it can proceed automatically.
    """
    if scenario.status not in ("running", "pending", "degraded"):
        return False

    params = scenario.parameters or {}
    recovery = params.get("recovery") or {}
    # If recovery has already completed or decision has already been applied, not awaiting
    if recovery.get("status") == "completed":
        return False
    if recovery.get("decision") in ("approved", "rejected"):
        return False
    if recovery.get("status") in ("in_progress", "recovering"):
        return False
    if recovery.get("status") in ("pending_approval", "blocked"):
        return True

    # If recovery authorization has already evaluated to AUTO_EXECUTE
    auth = recovery.get("authorization") or {}
    if auth.get("outcome") == "AUTO_EXECUTE":
        return False

    snapshot = params.get("snapshot") or {}
    machines = snapshot.get("machines") or {}

    has_anomaly = False
    # 1. Any machine in degraded or failed status
    for m in machines.values():
        if m.get("status") in ("degraded", "failed"):
            has_anomaly = True
            break
        if float(m.get("temperature_c", 0.0)) > 120.0 or float(m.get("vibration_rms", 0.0)) > 2.5:
            has_anomaly = True
            break

    # 2. Inbound shipment on customs hold or shortfall
    if not has_anomaly:
        shipments = snapshot.get("inbound_shipments") or {}
        for shp in shipments.values():
            if shp.get("status") in ("customs_hold", "delayed"):
                has_anomaly = True
                break

    # 3. Or step >= 3 in an active disruption scenario (legacy label heuristic;
    #    autonomous scenarios rely on observed state only)
    if recovery.get("status") == "no_action_required":
        return False
    if not has_anomaly and not is_autonomous_recovery_enabled(scenario) and scenario.disruption_type != "none" and scenario.current_step >= 3:
        has_anomaly = True

    if not has_anomaly:
        return False

    # Check hybrid recovery policy: if policy mode is manual_only, always require human approval
    policy_mode = params.get("recovery_policy", {}).get("mode", "hybrid")
    if policy_mode == "manual_only":
        return True

    # Autonomous scenarios: an unevaluated anomaly holds the clock until the
    # autonomous lifecycle decides (execute / approval / blocked). The category
    # of the disruption plays no part; the per-plan policy does.
    if is_autonomous_recovery_enabled(scenario):
        return True

    # Legacy scenarios (no autonomous flag): previous category-based behaviour
    from app.services.recovery_policy import classify_disruption_default
    classification = classify_disruption_default(scenario.disruption_type)
    if classification in ("approval_required", "safety_critical"):
        return True

    # For auto_recoverable disruptions, routine simulated recoveries can proceed autonomously
    return False


def is_scenario_eligible_for_auto_tick(scenario: SimulationScenario) -> tuple[bool, str]:
    """
    Evaluates whether a scenario should advance automatically on an automatic tick.
    Returns (eligible: bool, reason: str).
    """
    if scenario.status not in ("running", "recovering"):
        return False, f"Status is '{scenario.status}' (only 'running' and 'recovering' progress)"

    params = scenario.parameters or {}
    if params.get("auto_advance") is False:
        return False, "Automatic progression paused by operator"

    snapshot = params.get("snapshot") or {}
    batches = snapshot.get("batches") or {}
    if batches and all(
        float(b.get("actual_quantity_kg", 0.0)) >= float(b.get("planned_quantity_kg", 1.0))
        for b in batches.values()
    ):
        return False, "All production batches are completed"

    if is_awaiting_recovery_decision(scenario):
        if requires_human_decision(scenario):
            return False, "Awaiting human operator recovery decision"
        return False, "Awaiting autonomous recovery decision"

    return True, "Eligible"


# Catalogue disruptions with no inline step-3 trigger in the stepping loop; a
# scenario created with one of these types gets it applied for real at step 3.
SCHEDULED_DISRUPTION_STEP = 3
SCHEDULED_CATALOGUE_DISRUPTIONS = (
    "supplier_customs_delay",
    "supplier_shortfall",
    "reduced_machine_capacity",
    "quality_contamination",
)


def step_scenario(
    session: Session,
    scenario_id: str,
    steps: int = 1,
) -> Dict[str, Any]:
    """
    Advances the scenario by N steps, applying a scheduled catalogue disruption
    when the clock reaches step 3, and re-syncs incident severity with the live
    evidence afterwards so stored incidents, APIs and UI badges agree.
    """
    scenario = session.get(SimulationScenario, scenario_id)
    params = (scenario.parameters or {}) if scenario else {}
    start_step = (params.get("snapshot") or {}).get("step", scenario.current_step if scenario else 0)
    needs_schedule = bool(
        scenario
        and scenario.disruption_type in SCHEDULED_CATALOGUE_DISRUPTIONS
        and not params.get("scheduled_disruption_applied")
        and start_step < SCHEDULED_DISRUPTION_STEP <= start_step + steps
    )
    if not needs_schedule:
        result = _step_scenario_impl(session, scenario_id, steps)
    else:
        pre_steps = SCHEDULED_DISRUPTION_STEP - start_step
        result = _step_scenario_impl(session, scenario_id, pre_steps)
        from app.services.disruptions import apply_disruption, evaluate_disruption_eligibility
        eligible, _reason = evaluate_disruption_eligibility(session, scenario_id, scenario.disruption_type)
        if eligible:
            apply_disruption(session, scenario_id, scenario.disruption_type, None, operator_id="scenario_schedule")
        scenario.parameters = {**scenario.parameters, "scheduled_disruption_applied": True}
        session.flush()
        if steps - pre_steps > 0:
            result = _step_scenario_impl(session, scenario_id, steps - pre_steps)
        else:
            result = {**result, "snapshot": scenario.parameters.get("snapshot", {})}

    from app.services.disruptions import sync_incident_severity
    sync_incident_severity(session, scenario_id)
    return result


def _step_scenario_impl(
    session: Session,
    scenario_id: str,
    steps: int = 1,
) -> Dict[str, Any]:
    """
    Advances the simulation scenario by N discrete steps.
    Enforces deterministic discrete event transitions, scenario isolation,
    and row-level locking to serialize concurrent step requests.
    """
    from app.services.recovery import PLAN_DOWNTIME_HOURS, calculate_plan_duration_steps

    scenario = session.execute(
        select(SimulationScenario)
        .where(SimulationScenario.id == scenario_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not scenario:
        raise ValueError(f"Scenario '{scenario_id}' not found.")

    if scenario.status not in ("running", "pending", "recovering"):
        raise ValueError(f"Cannot step scenario '{scenario_id}' in status '{scenario.status}'.")

    snapshot = deepcopy(scenario.parameters["snapshot"])
    world = build_scenario_world_model(scenario_id, snapshot)
    emitted_events: List[OperationalEvent] = []
    recorded_at = datetime.now(timezone.utc)
    recovery_param = deepcopy(scenario.parameters.get("recovery") or {})

    roaster_id = "machine:plant:ROAST-01"
    batch_id = "production_batch:mes:BATCH-101"
    lot_id = "inventory_lot:wms:LOT-2026-001"
    order_id = "customer_order:erp:ORD-9402"

    for _ in range(steps):
        current_step = snapshot["step"] + 1
        current_sim_time = datetime.fromisoformat(snapshot["simulation_time"]) + timedelta(
            minutes=scenario.step_size_minutes
        )

        step_events_data = []

        # ---------------------------------------------------------------------
        # Recovery Progress & Completion Lifecycle Tracking
        # ---------------------------------------------------------------------
        is_recovery_approved = (recovery_param.get("decision") == "approved")
        is_recovery_already_completed = (recovery_param.get("status") == "completed")
        is_recovery_in_progress = is_recovery_approved and (recovery_param.get("status") == "in_progress")
        plan_id = recovery_param.get("selected_plan", "")

        completed_step = recovery_param.get("completed_step")
        if completed_step is None and is_recovery_approved:
            duration_steps = calculate_plan_duration_steps(plan_id, scenario.step_size_minutes)
            start_step = recovery_param.get("start_step", current_step - 1)
            completed_step = start_step + duration_steps
            recovery_param["completed_step"] = completed_step
            recovery_param["duration_steps"] = duration_steps

        recovery_just_completed = False
        if is_recovery_in_progress and completed_step is not None:
            remaining_steps = max(0, completed_step - current_step)
            recovery_param["remaining_steps"] = remaining_steps
            if current_step >= completed_step:
                recovery_param["status"] = "completed"
                recovery_param["remaining_steps"] = 0
                recovery_param["finished_at_step"] = current_step
                recovery_param["finished_at_time"] = current_sim_time.isoformat()
                scenario.status = "running"
                is_recovery_in_progress = False
                is_recovery_already_completed = True
                recovery_just_completed = True

        # ---------------------------------------------------------------------
        # Disruption Checks at Step 3
        # ---------------------------------------------------------------------
        is_roaster_disrupted = (
            scenario.disruption_type in ("roaster_bearing_overheat", "combined", "interacting_constraint")
            and current_step >= 3
            and not is_recovery_already_completed
            and not is_recovery_in_progress
        )
        is_equipment_failed = (
            scenario.disruption_type in ("equipment_failure",)
            and current_step >= 3
            and not is_recovery_already_completed
            and not is_recovery_in_progress
        )
        is_interacting = (
            scenario.disruption_type in ("interacting_constraint",)
            and current_step >= 3
            and not is_recovery_already_completed
            and not is_recovery_in_progress
        )

        roaster = snapshot["machines"].get(roaster_id)
        batch = snapshot["batches"].get(batch_id)
        lot = snapshot["inventory"].get(lot_id)
        order = snapshot["orders"].get(order_id)

        # Restore machine telemetry and emit recovery completion event if recovery just completed
        if recovery_just_completed:
            # Only the equipment the completed plan actually repairs is restored.
            restores_roaster = plan_id in ("plan_a_thermal_flush", "plan_b_reroute_line2", "plan_c_split_expedite")
            restores_mill = plan_id == "plan_a_motor_swap"
            restores_press = plan_id == "plan_a_hydraulic_reseal"
            if roaster and restores_roaster:
                roaster["status"] = "running"
                roaster["health_score"] = 0.98
                roaster["temperature_c"] = 115.0
                roaster["vibration_rms"] = 1.6
                roaster["current_throughput_kg_h"] = 1200.0
                roaster["current_throughput_kgh"] = 1200.0
            if batch and batch.get("status") == "delayed":
                batch["status"] = "in_progress"

            mill = snapshot["machines"].get("machine:plant:MILL-01")
            batch_mill = snapshot["batches"].get("production_batch:mes:BATCH-103")
            if mill and restores_mill and mill.get("status") in ("maintenance", "failed"):
                mill["status"] = "running"
                mill["temperature_c"] = 68.0
                mill["vibration_rms"] = 1.2
                mill["health_score"] = 0.98
                mill["rated_capacity_kg_h"] = 1000.0
                mill["rated_capacity_kgh"] = 1000.0
            if batch_mill and batch_mill.get("status") == "delayed":
                batch_mill["status"] = "in_progress"

            press = snapshot["machines"].get("machine:plant:PRESS-01")
            batch_press = snapshot["batches"].get("production_batch:mes:BATCH-104")
            if press and restores_press and press.get("status") in ("maintenance", "degraded"):
                press["status"] = "running"
                press["rated_capacity_kg_h"] = 600.0
                press["rated_capacity_kgh"] = 600.0
                press["current_throughput_kg_h"] = 600.0
                press["health_score"] = 0.98
            if batch_press and batch_press.get("status") == "delayed":
                batch_press["status"] = "in_progress"

            # Resolve active incidents in snapshot
            if "active_incidents" in snapshot:
                for inc in snapshot["active_incidents"]:
                    inc["status"] = "resolved"
                    inc["resolved_at_step"] = current_step

            if order and order.get("status") == "at_risk":
                order["status"] = "confirmed"

            step_events_data.append({
                "event_type": "recovery.completed",
                "evidence_id": f"EV-REC-COMPL-{scenario_id}-{current_step}",
                "entity_id": scenario_id,
                "payload": {
                    "plan_id": plan_id,
                    "completed_step": current_step,
                    "simulation_time": current_sim_time.isoformat(),
                    "downtime_hours": recovery_param.get("duration_hours", 0.75),
                    "message": f"Recovery plan '{plan_id}' completed maintenance successfully. Nominal line speed restored.",
                },
            })
            step_events_data.append({
                "event_type": "incident.resolved",
                "evidence_id": f"EV-INCIDENT-RESOLVED-{scenario_id}-{current_step}",
                "entity_id": scenario_id,
                "payload": {
                    "scenario_id": scenario_id,
                    "plan_id": plan_id,
                    "resolved_at_step": current_step,
                    "simulation_time": current_sim_time.isoformat(),
                    "resolution": "Recovery maintenance executed and verified. Nominal machinery metrics restored.",
                },
            })
            world.assert_fact(
                about=roaster_id,
                predicate="status",
                value="running",
                source=Provenance(
                    kind="recovery",
                    at=current_sim_time.isoformat(),
                    run_id=scenario_id,
                    by="recovery_intelligence",
                ),
                confidence=1.0,
            )
            world.assert_fact(
                about=order_id,
                predicate="status",
                value="confirmed",
                source=Provenance(
                    kind="recovery",
                    at=current_sim_time.isoformat(),
                    run_id=scenario_id,
                    by="recovery_intelligence",
                ),
                confidence=1.0,
            )
            world._store.log_audit(
                kind="recovery_completed",
                actor="recovery_intelligence",
                summary=f"Recovery plan '{plan_id}' completed maintenance at step {current_step}",
                event_id=f"evt_rec_done_{scenario_id}_{current_step}",
                run_id=scenario_id,
                at=current_sim_time.isoformat(),
            )
            world._store.log_audit(
                kind="incident_resolved",
                actor="recovery_intelligence",
                summary=f"Incident resolved by plan '{plan_id}' at step {current_step}. Nominal throughput restored.",
                event_id=f"evt_inc_res_{scenario_id}_{current_step}",
                run_id=scenario_id,
                at=current_sim_time.isoformat(),
            )

        # 1. Roaster Anomaly
        if is_roaster_disrupted:
            if current_step == 3:
                roaster["status"] = "degraded"
                roaster["health_score"] = 0.55
                roaster["temperature_c"] = 142.5  # threshold 120.0
                roaster["vibration_rms"] = 4.2    # threshold 2.5

                batch["status"] = "delayed"
                batch["delay_hours"] = 3.5

                order["status"] = "at_risk"

                # Assert verified facts and provenance in Meterless World Model
                wm_src = Provenance(
                    kind="telemetry",
                    at=current_sim_time.isoformat(),
                    by="sensor:telemetry_feed",
                    run_id=scenario_id,
                )
                world.assert_fact(
                    about=roaster_id,
                    predicate="bearing_temperature_c",
                    value=142.5,
                    source=wm_src,
                    confidence=0.99,
                )
                world.assert_fact(
                    about=roaster_id,
                    predicate="vibration_rms",
                    value=4.2,
                    source=wm_src,
                    confidence=0.98,
                )
                world.assert_fact(
                    about=roaster_id,
                    predicate="status",
                    value="degraded",
                    source=wm_src,
                    confidence=1.0,
                )
                world.assert_fact(
                    about=batch_id,
                    predicate="status",
                    value="delayed",
                    source=Provenance(
                        kind="simulator",
                        at=current_sim_time.isoformat(),
                        run_id=scenario_id,
                    ),
                    confidence=1.0,
                )
                world.assert_fact(
                    about=order_id,
                    predicate="status",
                    value="at_risk",
                    source=Provenance(
                        kind="simulator",
                        at=current_sim_time.isoformat(),
                        run_id=scenario_id,
                    ),
                    confidence=1.0,
                )

                # 1. Temperature Anomaly Event (EV-ROAST-TEMP)
                step_events_data.append({
                    "event_type": "machine.overheated",
                    "evidence_id": "EV-ROAST-TEMP",
                    "entity_id": roaster_id,
                    "payload": {
                        "metric": "bearing_temperature_c",
                        "observed_value": 142.5,
                        "threshold_value": 120.0,
                        "unit": "celsius",
                        "station": roaster["station"],
                        "message": "Drum Roaster bearing temperature exceeded safety threshold (142.5C vs 120C).",
                    },
                })

                # 2. Vibration Anomaly Event (EV-ROAST-VIB)
                step_events_data.append({
                    "event_type": "machine.vibration_high",
                    "evidence_id": "EV-ROAST-VIB",
                    "entity_id": roaster_id,
                    "payload": {
                        "metric": "vibration_velocity_rms",
                        "observed_value": 4.2,
                        "threshold_value": 2.5,
                        "unit": "mm/s",
                        "station": roaster["station"],
                        "message": "Severe bearing vibration detected (4.2 mm/s vs 2.5 mm/s threshold).",
                    },
                })

                # 3. Batch Delay Event (EV-BATCH-DELAY)
                step_events_data.append({
                    "event_type": "batch.delayed",
                    "evidence_id": "EV-BATCH-DELAY",
                    "entity_id": batch_id,
                    "payload": {
                        "batch_code": batch["batch_code"],
                        "delay_hours": 3.5,
                        "reason": "Roaster thermal degradation forced throughput slowdown.",
                    },
                })

                # 4. Order At Risk Event
                step_events_data.append({
                    "event_type": "order.at_risk",
                    "evidence_id": f"EV-ORDER-{order['order_code']}",
                    "entity_id": order_id,
                    "payload": {
                        "order_code": order["order_code"],
                        "customer": order["customer_name"],
                        "deadline": order["deadline"],
                        "message": "Batch delay risks missing customer delivery deadline.",
                    },
                })

        # 2. Interacting Constraint Compound Disruption Events at Step 3
        if is_interacting and current_step == 3:
            winnower = snapshot["machines"].get("machine:plant:WINNOW-01")
            if winnower:
                winnower["status"] = "degraded"
                winnower["health_score"] = 0.60
            step_events_data.append({
                "event_type": "inventory.shortage",
                "evidence_id": "EV-RAW-SHORTAGE",
                "entity_id": lot_id,
                "payload": {
                    "lot_code": lot["lot_code"],
                    "metric": "available_quantity_kg",
                    "observed_value": lot["current_quantity_kg"],
                    "message": "Raw cocoa bean buffer experiencing upstream supply allocation bottleneck.",
                },
            })
            step_events_data.append({
                "event_type": "machine.bottleneck",
                "evidence_id": "EV-WINNOW-BACKPRESSURE",
                "entity_id": "machine:plant:WINNOW-01",
                "payload": {
                    "machine_code": "WINNOW-01",
                    "metric": "chute_backpressure_psi",
                    "observed_value": 14.5,
                    "threshold_value": 10.0,
                    "message": "Winnowing cyclone hopper experiencing downstream backpressure bottleneck.",
                },
            })

        # 3. Equipment Failure Anomaly at Step 3 (Grinding Mill Breakdown)
        if is_equipment_failed:
            mill = snapshot["machines"].get("machine:plant:MILL-01")
            batch_mill = snapshot["batches"].get("production_batch:mes:BATCH-103")
            order_mill = snapshot["orders"].get("customer_order:erp:ORD-9405")
            if mill and current_step == 3:
                mill["status"] = "failed"
                mill["health_score"] = 0.10
                mill["temperature_c"] = 168.0
                mill["vibration_rms"] = 8.6
                if batch_mill:
                    batch_mill["status"] = "delayed"
                    batch_mill["delay_hours"] = 6.0
                if order_mill:
                    order_mill["status"] = "at_risk"

                wm_src = Provenance(
                    kind="telemetry",
                    at=current_sim_time.isoformat(),
                    by="sensor:telemetry_feed",
                    run_id=scenario_id,
                )
                world.assert_fact(
                    about="machine:plant:MILL-01",
                    predicate="status",
                    value="failed",
                    source=wm_src,
                    confidence=1.0,
                )
                world.assert_fact(
                    about="machine:plant:MILL-01",
                    predicate="bearing_temperature_c",
                    value=168.0,
                    source=wm_src,
                    confidence=0.99,
                )
                world.assert_fact(
                    about="machine:plant:MILL-01",
                    predicate="vibration_rms",
                    value=8.6,
                    source=wm_src,
                    confidence=0.99,
                )
                if batch_mill:
                    world.assert_fact(
                        about="production_batch:mes:BATCH-103",
                        predicate="status",
                        value="delayed",
                        source=wm_src,
                        confidence=1.0,
                    )
                if order_mill:
                    world.assert_fact(
                        about="customer_order:erp:ORD-9405",
                        predicate="status",
                        value="at_risk",
                        source=wm_src,
                        confidence=1.0,
                    )

                step_events_data.append({
                    "event_type": "machine.failed",
                    "evidence_id": "EV-MILL-FAIL",
                    "entity_id": "machine:plant:MILL-01",
                    "payload": {
                        "machine_code": "MILL-01",
                        "station": mill.get("station", "Grinding"),
                        "message": "Impact Grinding Mill drive motor catastrophic bearing seizure.",
                    },
                })
                step_events_data.append({
                    "event_type": "machine.overheated",
                    "evidence_id": "EV-MILL-TEMP",
                    "entity_id": "machine:plant:MILL-01",
                    "payload": {
                        "metric": "bearing_temperature_c",
                        "observed_value": 168.0,
                        "threshold_value": 120.0,
                        "unit": "celsius",
                        "station": mill.get("station", "Grinding"),
                        "message": "Grinding Mill stator temperature exceeded emergency trip threshold.",
                    },
                })
                step_events_data.append({
                    "event_type": "machine.vibration_high",
                    "evidence_id": "EV-MILL-VIB",
                    "entity_id": "machine:plant:MILL-01",
                    "payload": {
                        "metric": "vibration_velocity_rms",
                        "observed_value": 8.6,
                        "threshold_value": 2.5,
                        "unit": "mm/s",
                        "station": mill.get("station", "Grinding"),
                        "message": "Severe unbalance vibration leading to immediate automatic trip.",
                    },
                })
                if batch_mill:
                    step_events_data.append({
                        "event_type": "batch.delayed",
                        "evidence_id": "EV-BATCH103-DELAY",
                        "entity_id": "production_batch:mes:BATCH-103",
                        "payload": {
                            "batch_code": batch_mill["batch_code"],
                            "delay_hours": 6.0,
                            "reason": "Grinding mill motor failure stopped milling operations.",
                        },
                    })
                if order_mill:
                    step_events_data.append({
                        "event_type": "order.at_risk",
                        "evidence_id": f"EV-ORDER-{order_mill['order_code']}",
                        "entity_id": "customer_order:erp:ORD-9405",
                        "payload": {
                            "order_code": order_mill["order_code"],
                            "customer": order_mill["customer_name"],
                            "deadline": order_mill["deadline"],
                            "message": "Cocoa liquor supply halt risks missing confectionery shipment deadline.",
                        },
                    })

        # ---------------------------------------------------------------------
        # Material Conservation & Capacity Progress for BATCH-101
        # ---------------------------------------------------------------------
        if is_recovery_in_progress:
            if "thermal_flush" in plan_id:
                processed_kg = 0.0
                if roaster:
                    roaster["status"] = "maintenance"
                    roaster["current_throughput_kg_h"] = 0.0
                    roaster["current_throughput_kgh"] = 0.0
            elif "reroute_line2" in plan_id:
                processed_kg = 250.0
                if roaster:
                    roaster["status"] = "maintenance"
                    roaster["current_throughput_kg_h"] = 1000.0
                    roaster["current_throughput_kgh"] = 1000.0
            else:
                processed_kg = 150.0
        elif is_roaster_disrupted:
            # In degraded state, throughput drops to 15% (45 kg per 15-min step)
            processed_kg = 45.0
            if roaster:
                roaster["current_throughput_kg_h"] = 180.0
                roaster["current_throughput_kgh"] = 180.0
        else:
            # Nominal throughput: 1200 kg/h / 4 steps/h = 300 kg per 15 min
            processed_kg = 300.0
            if roaster:
                roaster["status"] = "running"
                roaster["current_throughput_kg_h"] = 1200.0
                roaster["current_throughput_kgh"] = 1200.0
            if batch and batch["status"] == "scheduled":
                batch["status"] = "in_progress"

        # Advance processing for BATCH-101 (Strict material conservation)
        actual_advance = 0.0
        if batch and lot and batch["actual_quantity_kg"] < batch["planned_quantity_kg"]:
            remaining_batch = batch["planned_quantity_kg"] - batch["actual_quantity_kg"]
            available_lot = lot["current_quantity_kg"]
            actual_advance = min(processed_kg, remaining_batch, available_lot)
            batch["actual_quantity_kg"] += actual_advance
            lot["current_quantity_kg"] = max(0.0, lot["current_quantity_kg"] - actual_advance)

            step_events_data.append({
                "event_type": "batch.step_progress",
                "evidence_id": f"EV-PROG-{batch['batch_code']}-{current_step}",
                "entity_id": batch_id,
                "payload": {
                    "step": current_step,
                    "processed_kg": actual_advance,
                    "total_actual_kg": batch["actual_quantity_kg"],
                    "planned_kg": batch["planned_quantity_kg"],
                },
            })

            if batch["actual_quantity_kg"] >= batch["planned_quantity_kg"]:
                batch["status"] = "completed"
                step_events_data.append({
                    "event_type": "batch.completed",
                    "evidence_id": f"EV-COMPL-{batch['batch_code']}",
                    "entity_id": batch_id,
                    "payload": {"batch_code": batch["batch_code"], "quantity_kg": batch["actual_quantity_kg"]},
                })

        # ---------------------------------------------------------------------
        # Multi-stage Batch Processing & Material Conservation for Other Batches
        # ---------------------------------------------------------------------
        for other_batch_id, other_batch in snapshot.get("batches", {}).items():
            if other_batch_id == batch_id:
                continue  # already processed BATCH-101 above

            if other_batch["actual_quantity_kg"] >= other_batch["planned_quantity_kg"]:
                continue

            machine_id = other_batch.get("machine_id")
            input_lot_id = other_batch.get("input_lot_id")
            other_mach = snapshot.get("machines", {}).get(machine_id) if machine_id else None
            other_lot = snapshot.get("inventory", {}).get(input_lot_id) if input_lot_id else None

            # Calculate step capacity based on machine status
            if other_mach:
                if other_mach.get("status") in ("failed", "offline"):
                    batch_capacity = 0.0
                elif other_mach.get("status") == "degraded":
                    batch_capacity = (other_mach.get("rated_capacity_kg_h", 1000.0) / (60.0 / scenario.step_size_minutes)) * 0.25
                else:
                    batch_capacity = other_mach.get("rated_capacity_kg_h", 1000.0) / (60.0 / scenario.step_size_minutes)
                    other_mach["status"] = "running"
            else:
                batch_capacity = 250.0

            remaining_qty = other_batch["planned_quantity_kg"] - other_batch["actual_quantity_kg"]
            avail_inv = other_lot["current_quantity_kg"] if other_lot else float("inf")
            advance_kg = min(batch_capacity, remaining_qty, avail_inv)

            if advance_kg > 0:
                other_batch["actual_quantity_kg"] += advance_kg
                if other_lot:
                    other_lot["current_quantity_kg"] = max(0.0, other_lot["current_quantity_kg"] - advance_kg)
                if other_batch["status"] == "scheduled":
                    other_batch["status"] = "in_progress"
                if other_batch["actual_quantity_kg"] >= other_batch["planned_quantity_kg"]:
                    other_batch["status"] = "completed"

        # ---------------------------------------------------------------------
        # Inbound Shipment Delivery & Silo Replenishment
        # ---------------------------------------------------------------------
        silos = snapshot.setdefault("silos", {})
        silo_01 = silos.setdefault("silo_01", {"id": "silo_01", "current_stock_kg": 3600.0})
        if actual_advance > 0:
            silo_01["current_stock_kg"] = max(0.0, float(silo_01.get("current_stock_kg", 3600.0)) - actual_advance)

        shipments = snapshot.get("inbound_shipments", {})
        for shp in shipments.values():
            revised_step = shp.get("revised_arrival_step", shp.get("scheduled_arrival_step", 8))
            if current_step >= revised_step and shp.get("status") in ("in_transit", "customs_hold", "delayed"):
                shp["status"] = "delivered"
                deliv_kg = float(shp.get("quantity_kg", 15000.0))
                silo_01["current_stock_kg"] += deliv_kg
                step_events_data.append({
                    "event_type": "shipment.delivered",
                    "evidence_id": f"EV-DELIVERY-{shp.get('shipment_code', 'SHP')}-{current_step}",
                    "entity_id": shp.get("id", scenario_id),
                    "payload": {
                        "shipment_code": shp.get("shipment_code"),
                        "delivered_kg": deliv_kg,
                        "step": current_step,
                        "simulation_time": current_sim_time.isoformat(),
                        "message": f"Shipment {shp.get('shipment_code')} arrived and pumped {deliv_kg} kg beans into Silo 1.",
                    },
                })
                world.assert_fact(
                    about="silo:plant:SILO-01",
                    predicate="stock_level_kg",
                    value=silo_01["current_stock_kg"],
                    source=Provenance(
                        kind="telemetry",
                        at=current_sim_time.isoformat(),
                        run_id=scenario_id,
                        by="silo_weighbridge",
                    ),
                    confidence=1.0,
                )

        # ---------------------------------------------------------------------
        # Continuous Seven-Stage Sequential Production Line Lifecycle
        # ---------------------------------------------------------------------
        prod_cycle = snapshot.setdefault("production_cycle", {
            "cycle_number": 1,
            "active_stage_num": 1,
            "stage_step_counter": 0,
            "stage_duration_steps": {
                "1": 1,
                "2": 2,
                "3": 1,
                "4": 1,
                "5": 1,
                "6": 1,
                "7": 1,
            },
            "completed_cycles": 0,
        })

        active_st_num = prod_cycle.get("active_stage_num", 1)
        durations = prod_cycle.get("stage_duration_steps", {})
        target_dur = int(durations.get(str(active_st_num), 1))

        # Check if active stage is blocked/inhibited by disruptions or anomalies
        is_active_stage_inhibited = False
        inhibit_reason = None

        if active_st_num == 1:
            lot_beans = snapshot.get("inventory", {}).get(lot_id, {})
            silo_cur = snapshot.get("silos", {}).get("silo_01", {})
            if (lot_beans.get("quality_status") == "QUARANTINE_HOLD"
                or silo_cur.get("quality_status") == "QUARANTINE_HOLD"):
                is_active_stage_inhibited = True
                inhibit_reason = "Raw bean silo on quarantine hold."
            elif float(silo_cur.get("current_stock_kg", 3600.0)) <= 0:
                is_active_stage_inhibited = True
                inhibit_reason = "Raw bean silo empty (stockout)."
        elif active_st_num == 2:
            if is_roaster_disrupted or (roaster and roaster.get("status") in ("degraded", "maintenance", "failed")):
                is_active_stage_inhibited = True
                inhibit_reason = "Roaster bearing thermal runaway / maintenance recovery."
        elif active_st_num == 4:
            mill_m = snapshot.get("machines", {}).get("machine:plant:MILL-01", {})
            if mill_m.get("status") in ("failed", "offline", "maintenance"):
                is_active_stage_inhibited = True
                inhibit_reason = "Grinding mill drive motor failure."

        stage_meta = {
            1: ("CLEAN-01", "Cleaning & Destoning"),
            2: ("ROAST-01", "Drum Roasting"),
            3: ("WINNOW-01", "Winnowing"),
            4: ("MILL-01", "Liquor Grinding"),
            5: ("PRESS-01", "Hydraulic Pressing"),
            6: ("PULV-01", "Cake Pulverizing"),
            7: ("PACK-01", "Packing & Fulfillment"),
        }

        if not is_active_stage_inhibited:
            prod_cycle["stage_step_counter"] = prod_cycle.get("stage_step_counter", 0) + 1
            cur_counter = prod_cycle["stage_step_counter"]

            if cur_counter >= target_dur:
                # Active stage finishes
                m_code, st_name = stage_meta.get(active_st_num, ("M-01", f"Stage {active_st_num}"))
                step_events_data.append({
                    "event_type": "production_stage.completed",
                    "evidence_id": f"EV-STAGE-{active_st_num}-COMPL-{current_step}",
                    "entity_id": f"stage:{active_st_num}:{m_code}",
                    "payload": {
                        "cycle_number": prod_cycle["cycle_number"],
                        "stage_num": active_st_num,
                        "machine_code": m_code,
                        "stage_name": st_name,
                        "completed_at_step": current_step,
                    },
                })

                if active_st_num < 7:
                    prod_cycle["active_stage_num"] = active_st_num + 1
                    prod_cycle["stage_step_counter"] = 0
                    next_st = active_st_num + 1
                    next_code, next_name = stage_meta.get(next_st, ("M-01", f"Stage {next_st}"))
                    step_events_data.append({
                        "event_type": "production_stage.started",
                        "evidence_id": f"EV-STAGE-{next_st}-START-{current_step}",
                        "entity_id": f"stage:{next_st}:{next_code}",
                        "payload": {
                            "cycle_number": prod_cycle["cycle_number"],
                            "stage_num": next_st,
                            "machine_code": next_code,
                            "stage_name": next_name,
                            "started_at_step": current_step,
                        },
                    })
                else:
                    # Stage 7 finished -> complete cycle!
                    completed_cyc = prod_cycle.get("completed_cycles", 0) + 1
                    prod_cycle["completed_cycles"] = completed_cyc
                    prod_cycle["cycle_number"] = prod_cycle.get("cycle_number", 1) + 1
                    prod_cycle["active_stage_num"] = 1
                    prod_cycle["stage_step_counter"] = 0

                    step_events_data.append({
                        "event_type": "production_cycle.completed",
                        "evidence_id": f"EV-CYCLE-{completed_cyc}-COMPL-{current_step}",
                        "entity_id": scenario_id,
                        "payload": {
                            "completed_cycle": completed_cyc,
                            "new_cycle_number": prod_cycle["cycle_number"],
                            "completed_at_step": current_step,
                            "simulation_time": current_sim_time.isoformat(),
                        },
                    })
                    first_code, first_name = stage_meta[1]
                    step_events_data.append({
                        "event_type": "production_stage.started",
                        "evidence_id": f"EV-STAGE-1-START-{current_step}",
                        "entity_id": f"stage:1:{first_code}",
                        "payload": {
                            "cycle_number": prod_cycle["cycle_number"],
                            "stage_num": 1,
                            "machine_code": first_code,
                            "stage_name": first_name,
                            "started_at_step": current_step,
                        },
                    })
        else:
            step_events_data.append({
                "event_type": "production_stage.inhibited",
                "evidence_id": f"EV-STAGE-{active_st_num}-INHIBIT-{current_step}",
                "entity_id": f"stage:{active_st_num}",
                "payload": {
                    "cycle_number": prod_cycle.get("cycle_number", 1),
                    "stage_num": active_st_num,
                    "reason": inhibit_reason,
                    "step": current_step,
                },
            })

        # Step completed event
        step_events_data.append({
            "event_type": "simulation.step_completed",
            "evidence_id": f"EV-STEP-{scenario_id}-{current_step}",
            "entity_id": scenario_id,
            "payload": {
                "step": current_step,
                "simulation_time": current_sim_time.isoformat(),
                "roaster_status": roaster["status"] if roaster else "unknown",
                "roaster_temp_c": roaster["temperature_c"] if roaster else None,
            },
        })

        # Persist operational events with explicit event ordering
        for idx, ev_data in enumerate(step_events_data):
            ev_id = f"evt_{scenario_id}_{current_step}_{idx}_{ev_data['event_type']}"
            op_event = OperationalEvent(
                event_id=ev_id,
                scenario_id=scenario_id,
                event_type=ev_data["event_type"],
                schema_version=1,
                simulation_time=current_sim_time,
                recorded_at=recorded_at,
                source_system="simulator",
                source_record_id=ev_data["entity_id"],
                entity_refs=[ev_data["entity_id"], ev_data["evidence_id"]],
                idempotency_key=_generate_idempotency_key(
                    scenario_id, current_step, ev_data["event_type"], ev_data["entity_id"]
                ),
                payload=ev_data["payload"],
            )
            session.add(op_event)
            emitted_events.append(op_event)

        # Update snapshot
        snapshot["step"] = current_step
        snapshot["simulation_time"] = current_sim_time.isoformat()

    # Update scenario metadata
    scenario.current_step = snapshot["step"]
    scenario.simulation_time = datetime.fromisoformat(snapshot["simulation_time"])
    scenario.parameters = {
        **scenario.parameters,
        "snapshot": snapshot,
        "world_model_state": world.state_shape(),
        "recovery": recovery_param,
    }
    session.flush()

    return {
        "scenario_id": scenario_id,
        "current_step": scenario.current_step,
        "simulation_time": scenario.simulation_time.isoformat(),
        "status": scenario.status,
        "disruption_type": scenario.disruption_type,
        "events_emitted_count": len(emitted_events),
        "snapshot": snapshot,
        "world_model_state": scenario.parameters["world_model_state"],
    }


def get_scenario_world_model(session: Session, scenario_id: str) -> WorldModel:
    """Returns the instantiated WorldModel for a scenario reflecting its current state."""
    scenario = session.execute(
        select(SimulationScenario).where(SimulationScenario.id == scenario_id)
    ).scalar_one_or_none()
    if not scenario:
        raise ValueError(f"Scenario '{scenario_id}' not found.")
    snapshot = scenario.parameters.get("snapshot", {})
    world = build_scenario_world_model(scenario_id, snapshot)
    recovery_info = scenario.parameters.get("recovery") or {}
    has_recovered = (recovery_info.get("status") == "completed")
    is_recovering = (scenario.status == "recovering" or recovery_info.get("status") == "in_progress")
    if (
        snapshot.get("step", 0) >= 3
        and scenario.disruption_type in ("roaster_bearing_overheat", "combined")
        and not has_recovered
        and not is_recovering
    ):
        sim_time = snapshot.get("simulation_time", BASELINE_START_TIME.isoformat())
        src = Provenance(kind="telemetry", at=sim_time, by="sensor:feed", run_id=scenario_id)
        world.assert_fact(about="machine:plant:ROAST-01", predicate="bearing_temperature_c", value=142.5, source=src, confidence=0.99)
        world.assert_fact(about="machine:plant:ROAST-01", predicate="vibration_rms", value=4.2, source=src, confidence=0.98)
        world.assert_fact(about="machine:plant:ROAST-01", predicate="status", value="degraded", source=src, confidence=1.0)
        world.assert_fact(about="production_batch:mes:BATCH-101", predicate="status", value="delayed", source=src, confidence=1.0)
        world.assert_fact(about="customer_order:erp:ORD-9402", predicate="status", value="at_risk", source=src, confidence=1.0)
    return world


def get_scenario_timeline(session: Session, scenario_id: str) -> List[Dict[str, Any]]:
    """
    Returns the chronological timeline of operational events for a scenario.
    Explicit ordering: simulation_time ASC, id ASC.
    """
    stmt = (
        select(OperationalEvent)
        .where(OperationalEvent.scenario_id == scenario_id)
        .order_by(OperationalEvent.simulation_time.asc(), OperationalEvent.id.asc())
    )
    events = session.execute(stmt).scalars().all()

    return [
        {
            "id": e.id,
            "event_id": e.event_id,
            "event_type": e.event_type,
            "simulation_time": e.simulation_time.isoformat(),
            "recorded_at": e.recorded_at.isoformat(),
            "source_record_id": e.source_record_id,
            "entity_refs": e.entity_refs,
            "idempotency_key": e.idempotency_key,
            "payload": e.payload,
        }
        for e in events
    ]
