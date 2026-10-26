"""
Dynamic Seven-Stage Production Line & Supply-Chain Stockout Forecasting Engine.

Features:
- Authoritative state derivation for all seven cocoa manufacturing stages:
  1. Cleaning & Destoning (CLEAN-01)
  2. Drum Roasting (ROAST-01)
  3. Winnowing (WINNOW-01)
  4. Liquor Grinding (MILL-01)
  5. Hydraulic Pressing (PRESS-01)
  6. Cake Pulverizing (PULV-01)
  7. Packing & Fulfillment (PACK-01)
- Physical dependency propagation (upstream failure/starvation blocks downstream stages).
- Inventory depletion burn rate calculation and lookahead stockout forecasting.
- 30-step trajectory modeling with replenishment gap analysis.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any, Dict, List, Optional


def calculate_inventory_forecast(
    snapshot: Dict[str, Any],
    step_size_minutes: int = 15,
) -> Dict[str, Any]:
    """
    Computes lookahead inventory depletion and stockout risk for raw cocoa beans.
    Accounts for available usable inventory, machine consumption rate, and inbound shipments.
    """
    inventory = snapshot.get("inventory", {})
    silos = snapshot.get("silos", {})
    silo_data = silos.get("silo_01", {})

    if "current_stock_kg" in silo_data:
        usable_qty = float(silo_data["current_stock_kg"])
    else:
        lot = inventory.get("inventory_lot:wms:LOT-2026-001", {})
        usable_qty = float(lot.get("current_quantity_kg", 3600.0))

    lot = inventory.get("inventory_lot:wms:LOT-2026-001", {})
    is_quarantined = (
        lot.get("quality_status") == "QUARANTINE_HOLD"
        or silo_data.get("quality_status") == "QUARANTINE_HOLD"
    )

    current_step = snapshot.get("step", 0)
    sim_time_iso = snapshot.get("simulation_time", "2026-10-22T06:00:00+00:00")
    current_sim_time = datetime.fromisoformat(sim_time_iso)

    machines = snapshot.get("machines", {})
    roaster = machines.get("machine:plant:ROAST-01", {})
    roaster_status = roaster.get("status", "running")

    # Determine current burn rate in kg per 15-minute simulation step
    # Nominal roaster throughput is 1,200 kg/h -> 300 kg per 15-minute step
    nominal_tp = float(roaster.get("rated_capacity_kg_h", 1200.0))
    if is_quarantined or roaster_status in ("failed", "maintenance"):
        active_burn_rate_step = 0.0
    elif roaster_status == "degraded":
        active_burn_rate_step = float(roaster.get("current_throughput_kg_h", 180.0)) * (step_size_minutes / 60.0)
    else:
        tp = float(roaster.get("current_throughput_kg_h", 0.0))
        if tp <= 0.0:
            tp = nominal_tp
        active_burn_rate_step = tp * (step_size_minutes / 60.0)

    nominal_burn_rate_step = nominal_tp * (step_size_minutes / 60.0)

    # Calculate steps until stockout
    effective_qty = 0.0 if is_quarantined else max(0.0, usable_qty)
    if active_burn_rate_step > 0:
        steps_until_stockout = math.floor(effective_qty / active_burn_rate_step)
        projected_stockout_step = current_step + steps_until_stockout
        stockout_time = current_sim_time + timedelta(minutes=steps_until_stockout * step_size_minutes)
        projected_stockout_time_iso = stockout_time.isoformat()
    else:
        steps_until_stockout = 999 if effective_qty > 0 else 0
        projected_stockout_step = current_step + steps_until_stockout if effective_qty > 0 else current_step
        projected_stockout_time_iso = None

    # Inbound shipment tracking
    shipments = snapshot.get("inbound_shipments", {})
    shipment = next(iter(shipments.values()), None)
    if shipment:
        shipment_eta_step = shipment.get("revised_arrival_step", shipment.get("scheduled_arrival_step", 8))
        shipment_qty = float(shipment.get("quantity_kg", 15000.0))
        shipment_status = shipment.get("status", "in_transit")
        shipment_eta_time = shipment.get("revised_arrival_time", current_sim_time.isoformat())
    else:
        shipment_eta_step = current_step + 8
        shipment_qty = 15000.0
        shipment_status = "in_transit"
        shipment_eta_time = (current_sim_time + timedelta(hours=2.0)).isoformat()

    # Evaluation: does stockout occur before shipment replenishment?
    is_stockout_before_replenishment = (
        effective_qty <= 0 or (steps_until_stockout < (shipment_eta_step - current_step))
    )
    starvation_gap_steps = max(0, shipment_eta_step - projected_stockout_step) if is_stockout_before_replenishment else 0
    starvation_gap_hours = round(starvation_gap_steps * (step_size_minutes / 60.0), 2)

    # Affected downstream entities
    affected_stages = []
    if is_stockout_before_replenishment:
        affected_stages = [
            "Stage 01: Cleaning & Destoning (CLEAN-01)",
            "Stage 02: Drum Roasting (ROAST-01)",
        ]

    # Generate 30-step trajectory projection points
    trajectory = []
    sim_inv = effective_qty
    for i in range(25):
        future_step = current_step + i
        step_sim_time = current_sim_time + timedelta(minutes=i * step_size_minutes)

        # Replenishment arrival
        arrived = (future_step == shipment_eta_step and shipment_status != "delivered")
        if arrived:
            sim_inv += shipment_qty

        # Consumption
        sim_inv = max(0.0, sim_inv - (active_burn_rate_step if active_burn_rate_step > 0 else nominal_burn_rate_step))

        trajectory.append({
            "step": future_step,
            "simulation_time": step_sim_time.strftime("%H:%M"),
            "inventory_kg": round(sim_inv, 1),
            "is_replenishment_step": arrived,
            "is_stockout": sim_inv <= 0.0,
        })

    return {
        "usable_inventory_kg": effective_qty,
        "is_quarantined": is_quarantined,
        "active_burn_rate_kg_h": round(active_burn_rate_step * 4.0, 1),
        "active_burn_rate_kg_step": round(active_burn_rate_step, 1),
        "steps_until_stockout": steps_until_stockout if active_burn_rate_step > 0 else None,
        "projected_stockout_step": projected_stockout_step if active_burn_rate_step > 0 else None,
        "projected_stockout_time": projected_stockout_time_iso,
        "shipment": {
            "id": shipment.get("id") if shipment else "SHP-2026-COCOA-01",
            "po_number": shipment.get("po_number") if shipment else "PO-2026-88",
            "supplier_name": shipment.get("supplier_name") if shipment else "Abidjan Cocoa Union",
            "quantity_kg": shipment_qty,
            "status": shipment_status,
            "eta_step": shipment_eta_step,
            "eta_time": shipment_eta_time,
            "disruption_reason": shipment.get("disruption_reason") if shipment else None,
        },
        "is_stockout_before_replenishment": is_stockout_before_replenishment,
        "starvation_gap_steps": starvation_gap_steps,
        "starvation_gap_hours": starvation_gap_hours,
        "affected_stages": affected_stages,
        "affected_order_code": "ORD-9402",
        "affected_customer": "Artisan Chocolatier BV",
        "trajectory": trajectory,
    }


def derive_production_line_stages(
    snapshot: Dict[str, Any],
    scenario_status: str = "running",
    recovery_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Derives authoritative, physical operational states for all seven manufacturing stages.
    """
    machines = snapshot.get("machines", {})
    inventory = snapshot.get("inventory", {})
    batches = snapshot.get("batches", {})
    orders = snapshot.get("orders", {})

    recovery = recovery_info or {}
    is_scenario_recovering = (scenario_status == "recovering" or recovery.get("status") == "in_progress")
    remaining_rec_steps = recovery.get("remaining_steps", 0)

    # 1. Silo & Raw Material status
    silo_lot = inventory.get("inventory_lot:wms:LOT-2026-001", {})
    raw_beans_qty = float(silo_lot.get("current_quantity_kg", 0.0))
    is_silo_quarantined = (silo_lot.get("quality_status") == "QUARANTINE_HOLD")
    is_silo_empty = (raw_beans_qty <= 0.0)

    # 2. Machine states
    m_clean = machines.get("machine:plant:CLEAN-01", {})
    m_roast1 = machines.get("machine:plant:ROAST-01", {})
    m_roast2 = machines.get("machine:plant:ROAST-02", {})
    m_winnow = machines.get("machine:plant:WINNOW-01", {})
    m_mill = machines.get("machine:plant:MILL-01", {})
    m_press = machines.get("machine:plant:PRESS-01", {})
    m_pulv = machines.get("machine:plant:PULV-01", {})
    m_pack = machines.get("machine:plant:PACK-01", {})

    # Helper for batch progress
    def _batch_progress(batch_id: str) -> Tuple[float, str]:
        b = batches.get(batch_id, {})
        planned = float(b.get("planned_quantity_kg", 1.0))
        actual = float(b.get("actual_quantity_kg", 0.0))
        pct = min(100.0, round((actual / max(1.0, planned)) * 100.0, 1))
        return pct, b.get("status", "scheduled")

    pct_101, status_101 = _batch_progress("production_batch:mes:BATCH-101")
    pct_102, status_102 = _batch_progress("production_batch:mes:BATCH-102")
    pct_103, status_103 = _batch_progress("production_batch:mes:BATCH-103")
    pct_104, status_104 = _batch_progress("production_batch:mes:BATCH-104")
    pct_105, status_105 = _batch_progress("production_batch:mes:BATCH-105")

    # Production Cycle parameters
    prod_cycle = snapshot.get("production_cycle") or {
        "cycle_number": 1,
        "active_stage_num": 1,
        "stage_step_counter": 0,
        "stage_duration_steps": {"1": 1, "2": 2, "3": 1, "4": 1, "5": 1, "6": 1, "7": 1},
        "completed_cycles": 0,
    }
    active_num = prod_cycle.get("active_stage_num", 1)
    cycle_num = prod_cycle.get("cycle_number", 1)
    completed_cycles = prod_cycle.get("completed_cycles", 0)
    durations = prod_cycle.get("stage_duration_steps", {})
    stage_step_cnt = prod_cycle.get("stage_step_counter", 0)

    # -------------------------------------------------------------------------
    # STAGE 1: Cleaning & Destoning (CLEAN-01)
    # -------------------------------------------------------------------------
    if is_silo_quarantined:
        st1_status = "blocked"
        st1_reason = "Blocked: Silo 1 placed on quarantine hold (moisture anomaly)."
        st1_tp = 0.0
        prog_1 = 0.0
    elif is_silo_empty:
        st1_status = "blocked"
        st1_reason = "Blocked: Raw bean silo empty (stockout awaiting replenishment)."
        st1_tp = 0.0
        prog_1 = 0.0
    elif m_clean.get("status") in ("degraded", "failed"):
        st1_status = "offline"
        st1_reason = "Offline: Screen vibrator motor failure."
        st1_tp = 0.0
        prog_1 = 0.0
    elif 1 < active_num:
        st1_status = "completed"
        st1_reason = f"Completed: Destoned {raw_beans_qty} kg raw beans for Cycle #{cycle_num}."
        st1_tp = 0.0
        prog_1 = 100.0
    elif 1 == active_num:
        dur1 = int(durations.get("1", 1))
        st1_status = "running"
        st1_reason = f"Running: Destoning & air aspiration ({stage_step_cnt}/{dur1} steps in Cycle #{cycle_num})."
        st1_tp = float(m_clean.get("rated_capacity_kg_h", 2000.0))
        prog_1 = min(100.0, round((stage_step_cnt / max(1, dur1)) * 100.0, 1))
    else:
        st1_status = "pending"
        st1_reason = f"Pending: Scheduled for next manufacturing cycle."
        st1_tp = 0.0
        prog_1 = 0.0

    # -------------------------------------------------------------------------
    # STAGE 2: Drum Roasting (ROAST-01)
    # -------------------------------------------------------------------------
    if is_scenario_recovering and ("thermal_flush" in recovery.get("selected_plan", "") or "reroute" in recovery.get("selected_plan", "")):
        st2_status = "recovering"
        st2_reason = f"Under Maintenance: Thermal flush active ({remaining_rec_steps} steps left)."
        st2_tp = 0.0
        prog_2 = pct_101
    elif m_roast1.get("status") == "degraded" or float(m_roast1.get("temperature_c", 0.0)) > 120.0:
        st2_status = "at_risk"
        st2_reason = f"At Risk: Bearing thermal alarm ({m_roast1.get('temperature_c', 142.5)}°C vs 120°C limit). Throttled to 180 kg/h."
        st2_tp = float(m_roast1.get("current_throughput_kg_h", 180.0))
        prog_2 = pct_101
    elif st1_status == "blocked":
        st2_status = "blocked"
        st2_reason = "Blocked: Starved of raw cocoa beans from Cleaning stage."
        st2_tp = 0.0
        prog_2 = 0.0
    elif 2 < active_num:
        st2_status = "completed"
        st2_reason = f"Completed: Drum roasting targets met for Cycle #{cycle_num}."
        st2_tp = 0.0
        prog_2 = 100.0
    elif 2 == active_num:
        dur2 = int(durations.get("2", 2))
        st2_status = "running"
        st2_reason = f"Running: Drum roasting ({stage_step_cnt}/{dur2} steps in Cycle #{cycle_num})."
        st2_tp = float(m_roast1.get("rated_capacity_kg_h", 1200.0))
        prog_2 = min(100.0, round((stage_step_cnt / max(1, dur2)) * 100.0, 1))
    else:
        st2_status = "pending"
        st2_reason = f"Pending: Scheduled to start after Cleaning completes in Cycle #{cycle_num}."
        st2_tp = 0.0
        prog_2 = 0.0

    # -------------------------------------------------------------------------
    # STAGE 3: Winnowing (WINNOW-01)
    # -------------------------------------------------------------------------
    if st2_status in ("blocked", "recovering"):
        st3_status = "blocked"
        st3_reason = "Blocked: Insufficient roasted bean feed from Roaster."
        st3_tp = 0.0
        prog_3 = 0.0
    elif m_winnow.get("status") in ("degraded", "failed"):
        st3_status = "offline"
        st3_reason = "Offline: Blower fan belt slip."
        st3_tp = 0.0
        prog_3 = 0.0
    elif 3 < active_num:
        st3_status = "completed"
        st3_reason = f"Completed: Air classification separated shells for Cycle #{cycle_num}."
        st3_tp = 0.0
        prog_3 = 100.0
    elif 3 == active_num:
        dur3 = int(durations.get("3", 1))
        st3_status = "running"
        st3_reason = f"Running: Winnowing nibs from shells ({stage_step_cnt}/{dur3} steps in Cycle #{cycle_num})."
        st3_tp = float(m_winnow.get("rated_capacity_kg_h", 1000.0))
        prog_3 = min(100.0, round((stage_step_cnt / max(1, dur3)) * 100.0, 1))
    else:
        st3_status = "pending"
        st3_reason = f"Pending: Scheduled to start after Roasting completes in Cycle #{cycle_num}."
        st3_tp = 0.0
        prog_3 = 0.0

    # -------------------------------------------------------------------------
    # STAGE 4: Liquor Grinding (MILL-01)
    # -------------------------------------------------------------------------
    if is_scenario_recovering and "motor_swap" in recovery.get("selected_plan", ""):
        st4_status = "recovering"
        st4_reason = f"Under Maintenance: Emergency motor module replacement ({remaining_rec_steps} steps left)."
        st4_tp = 0.0
        prog_4 = pct_103
    elif m_mill.get("status") in ("failed", "critical") or float(m_mill.get("temperature_c", 0.0)) >= 150.0:
        st4_status = "offline"
        st4_reason = "Offline: Drive motor burnout & bearing trip (168°C)."
        st4_tp = 0.0
        prog_4 = 0.0
    elif st3_status == "blocked" and active_num <= 3:
        st4_status = "blocked"
        st4_reason = "Blocked: Nib buffer exhausted."
        st4_tp = 0.0
        prog_4 = 0.0
    elif 4 < active_num:
        st4_status = "completed"
        st4_reason = f"Completed: Liquor paste grinding targets met for Cycle #{cycle_num}."
        st4_tp = 0.0
        prog_4 = 100.0
    elif 4 == active_num:
        dur4 = int(durations.get("4", 1))
        st4_status = "running"
        st4_reason = f"Running: Triple-roll refining to liquor paste ({stage_step_cnt}/{dur4} steps in Cycle #{cycle_num})."
        st4_tp = float(m_mill.get("rated_capacity_kg_h", 800.0))
        prog_4 = min(100.0, round((stage_step_cnt / max(1, dur4)) * 100.0, 1))
    else:
        st4_status = "pending"
        st4_reason = f"Pending: Scheduled to start after Winnowing completes in Cycle #{cycle_num}."
        st4_tp = 0.0
        prog_4 = 0.0

    # -------------------------------------------------------------------------
    # STAGE 5: Hydraulic Pressing (PRESS-01)
    # -------------------------------------------------------------------------
    if st4_status in ("offline", "failed", "blocked"):
        st5_status = "blocked"
        st5_reason = "Blocked: Grinding mill offline, zero liquor feed available."
        st5_tp = 0.0
        prog_5 = 0.0
    elif m_press.get("status") == "degraded" or float(m_press.get("rated_capacity_kg_h", 600.0)) < 400.0:
        cur_cap = float(m_press.get("rated_capacity_kg_h", 240.0))
        st5_status = "at_risk"
        st5_reason = f"At Risk: Hydraulic line pressure loss. Output restricted to {cur_cap} kg/h (40% capacity)."
        st5_tp = cur_cap
        prog_5 = pct_104
    elif m_press.get("status") == "failed":
        st5_status = "offline"
        st5_reason = "Offline: Hydraulic ram pressure leak."
        st5_tp = 0.0
        prog_5 = 0.0
    elif 5 < active_num:
        st5_status = "completed"
        st5_reason = f"Completed: Hydraulic butter and cake pressing completed for Cycle #{cycle_num}."
        st5_tp = 0.0
        prog_5 = 100.0
    elif 5 == active_num:
        dur5 = int(durations.get("5", 1))
        st5_status = "running"
        st5_reason = f"Running: Extracting cocoa butter & cake under 450 bar ({stage_step_cnt}/{dur5} steps in Cycle #{cycle_num})."
        st5_tp = float(m_press.get("rated_capacity_kg_h", 600.0))
        prog_5 = min(100.0, round((stage_step_cnt / max(1, dur5)) * 100.0, 1))
    else:
        st5_status = "pending"
        st5_reason = f"Pending: Scheduled to start after Milling completes in Cycle #{cycle_num}."
        st5_tp = 0.0
        prog_5 = 0.0

    # -------------------------------------------------------------------------
    # STAGE 6: Cake Pulverizing (PULV-01)
    # -------------------------------------------------------------------------
    if st5_status in ("offline", "blocked"):
        st6_status = "blocked"
        st6_reason = "Blocked: No press cake delivered from Pressing stage."
        st6_tp = 0.0
        prog_6 = 0.0
    elif m_pulv.get("status") in ("degraded", "failed"):
        st6_status = "offline"
        st6_reason = "Offline: Pulverizer rotor imbalance."
        st6_tp = 0.0
        prog_6 = 0.0
    elif 6 < active_num:
        st6_status = "completed"
        st6_reason = f"Completed: Cocoa cake pulverizing targets met for Cycle #{cycle_num}."
        st6_tp = 0.0
        prog_6 = 100.0
    elif 6 == active_num:
        dur6 = int(durations.get("6", 1))
        st6_status = "running"
        st6_reason = f"Running: Impact milling cocoa cake to fine powder ({stage_step_cnt}/{dur6} steps in Cycle #{cycle_num})."
        st6_tp = float(m_pulv.get("rated_capacity_kg_h", 500.0))
        prog_6 = min(100.0, round((stage_step_cnt / max(1, dur6)) * 100.0, 1))
    else:
        st6_status = "pending"
        st6_reason = f"Pending: Scheduled to start after Pressing completes in Cycle #{cycle_num}."
        st6_tp = 0.0
        prog_6 = 0.0

    # -------------------------------------------------------------------------
    # STAGE 7: Packing & Fulfillment (PACK-01)
    # -------------------------------------------------------------------------
    o9402 = orders.get("customer_order:erp:ORD-9402", {})
    if o9402.get("status") == "at_risk":
        st7_status = "at_risk"
        st7_reason = "At Risk: Downstream order ORD-9402 deadline at risk of breach ($350/day penalty)."
        st7_tp = float(m_pack.get("rated_capacity_kg_h", 1500.0))
        prog_7 = pct_105
    elif 7 < active_num:
        st7_status = "completed"
        st7_reason = f"Completed: All packaging & dispatch completed for Cycle #{cycle_num}."
        st7_tp = 0.0
        prog_7 = 100.0
    elif 7 == active_num:
        dur7 = int(durations.get("7", 1))
        st7_status = "running"
        st7_reason = f"Running: Automated bag sealing, packing & dispatch ({stage_step_cnt}/{dur7} steps in Cycle #{cycle_num})."
        st7_tp = float(m_pack.get("rated_capacity_kg_h", 1500.0))
        prog_7 = min(100.0, round((stage_step_cnt / max(1, dur7)) * 100.0, 1))
    else:
        st7_status = "pending"
        st7_reason = f"Pending: Scheduled to start after Pulverizing completes in Cycle #{cycle_num}."
        st7_tp = 0.0
        prog_7 = 0.0

    return [
        {
            "num": 1,
            "id": "stage_01_cleaning",
            "name": "Cleaning & Destoning",
            "machine_code": "CLEAN-01",
            "machine_id": "machine:plant:CLEAN-01",
            "station": "cleaning",
            "status": st1_status,
            "throughput_kg_h": st1_tp,
            "rated_capacity_kg_h": float(m_clean.get("rated_capacity_kg_h", 2000.0)),
            "health_score": float(m_clean.get("health_score", 1.0)),
            "temperature_c": float(m_clean.get("temperature_c", 22.0)),
            "vibration_rms": float(m_clean.get("vibration_rms", 0.8)),
            "input_material": "Raw Forastero Cocoa Beans (Silo 1)",
            "input_available": not is_silo_empty and not is_silo_quarantined,
            "input_quantity_kg": raw_beans_qty,
            "output_product": "Cleaned Screened Cocoa Beans",
            "progress_pct": prog_1,
            "operational_reason": st1_reason,
            "upstream_stage_num": None,
            "downstream_stage_num": 2,
            "cycle_number": cycle_num,
            "completed_cycles": completed_cycles,
            "is_active": (active_num == 1),
        },
        {
            "num": 2,
            "id": "stage_02_roasting",
            "name": "Drum Roasting",
            "machine_code": "ROAST-01",
            "machine_id": "machine:plant:ROAST-01",
            "station": "roasting",
            "status": st2_status,
            "throughput_kg_h": st2_tp,
            "rated_capacity_kg_h": float(m_roast1.get("rated_capacity_kg_h", 1200.0)),
            "health_score": float(m_roast1.get("health_score", 1.0)),
            "temperature_c": float(m_roast1.get("temperature_c", 115.0)),
            "vibration_rms": float(m_roast1.get("vibration_rms", 1.8)),
            "input_material": "Cleaned Screened Cocoa Beans",
            "input_available": st1_status in ("running", "completed"),
            "input_quantity_kg": raw_beans_qty,
            "output_product": "Roasted Whole Cocoa Nibs",
            "progress_pct": prog_2,
            "operational_reason": st2_reason,
            "upstream_stage_num": 1,
            "downstream_stage_num": 3,
            "cycle_number": cycle_num,
            "completed_cycles": completed_cycles,
            "is_active": (active_num == 2),
        },
        {
            "num": 3,
            "id": "stage_03_winnowing",
            "name": "Winnowing",
            "machine_code": "WINNOW-01",
            "machine_id": "machine:plant:WINNOW-01",
            "station": "winnowing",
            "status": st3_status,
            "throughput_kg_h": st3_tp,
            "rated_capacity_kg_h": float(m_winnow.get("rated_capacity_kg_h", 1000.0)),
            "health_score": float(m_winnow.get("health_score", 1.0)),
            "temperature_c": float(m_winnow.get("temperature_c", 24.0)),
            "vibration_rms": float(m_winnow.get("vibration_rms", 1.2)),
            "input_material": "Roasted Whole Cocoa Nibs",
            "input_available": st2_status not in ("blocked", "recovering"),
            "input_quantity_kg": float(inventory.get("inventory_lot:wms:LOT-NIBS-01", {}).get("current_quantity_kg", 2000.0)),
            "output_product": "Deshelled Clean Cocoa Nibs",
            "progress_pct": prog_3,
            "operational_reason": st3_reason,
            "upstream_stage_num": 2,
            "downstream_stage_num": 4,
            "cycle_number": cycle_num,
            "completed_cycles": completed_cycles,
            "is_active": (active_num == 3),
        },
        {
            "num": 4,
            "id": "stage_04_milling",
            "name": "Liquor Grinding",
            "machine_code": "MILL-01",
            "machine_id": "machine:plant:MILL-01",
            "station": "milling",
            "status": st4_status,
            "throughput_kg_h": st4_tp,
            "rated_capacity_kg_h": float(m_mill.get("rated_capacity_kg_h", 800.0)),
            "health_score": float(m_mill.get("health_score", 1.0)),
            "temperature_c": float(m_mill.get("temperature_c", 45.0)),
            "vibration_rms": float(m_mill.get("vibration_rms", 1.6)),
            "input_material": "Deshelled Clean Cocoa Nibs",
            "input_available": st3_status != "blocked",
            "input_quantity_kg": float(inventory.get("inventory_lot:wms:LOT-NIBS-01", {}).get("current_quantity_kg", 2000.0)),
            "output_product": "Pure Cocoa Liquor (Refined Paste)",
            "progress_pct": prog_4,
            "operational_reason": st4_reason,
            "upstream_stage_num": 3,
            "downstream_stage_num": 5,
            "cycle_number": cycle_num,
            "completed_cycles": completed_cycles,
            "is_active": (active_num == 4),
        },
        {
            "num": 5,
            "id": "stage_05_pressing",
            "name": "Hydraulic Pressing",
            "machine_code": "PRESS-01",
            "machine_id": "machine:plant:PRESS-01",
            "station": "pressing",
            "status": st5_status,
            "throughput_kg_h": st5_tp,
            "rated_capacity_kg_h": float(m_press.get("rated_capacity_kg_h", 600.0)),
            "health_score": float(m_press.get("health_score", 1.0)),
            "temperature_c": float(m_press.get("temperature_c", 65.0)),
            "vibration_rms": float(m_press.get("vibration_rms", 2.1)),
            "input_material": "Pure Cocoa Liquor Paste",
            "input_available": st4_status not in ("offline", "blocked"),
            "input_quantity_kg": float(inventory.get("inventory_lot:wms:LOT-LIQUOR-01", {}).get("current_quantity_kg", 1800.0)),
            "output_product": "Cocoa Butter & Press Cake",
            "progress_pct": prog_5,
            "operational_reason": st5_reason,
            "upstream_stage_num": 4,
            "downstream_stage_num": 6,
            "cycle_number": cycle_num,
            "completed_cycles": completed_cycles,
            "is_active": (active_num == 5),
        },
        {
            "num": 6,
            "id": "stage_06_pulverizing",
            "name": "Cake Pulverizing",
            "machine_code": "PULV-01",
            "machine_id": "machine:plant:PULV-01",
            "station": "pulverizing",
            "status": st6_status,
            "throughput_kg_h": st6_tp,
            "rated_capacity_kg_h": float(m_pulv.get("rated_capacity_kg_h", 500.0)),
            "health_score": float(m_pulv.get("health_score", 1.0)),
            "temperature_c": float(m_pulv.get("temperature_c", 30.0)),
            "vibration_rms": float(m_pulv.get("vibration_rms", 1.4)),
            "input_material": "Cocoa Press Cake Blocks",
            "input_available": st5_status not in ("offline", "blocked"),
            "input_quantity_kg": float(inventory.get("inventory_lot:wms:LOT-POWDER-01", {}).get("current_quantity_kg", 1200.0)),
            "output_product": "Fine Defatted Cocoa Powder",
            "progress_pct": prog_6,
            "operational_reason": st6_reason,
            "upstream_stage_num": 5,
            "downstream_stage_num": 7,
            "cycle_number": cycle_num,
            "completed_cycles": completed_cycles,
            "is_active": (active_num == 6),
        },
        {
            "num": 7,
            "id": "stage_07_packaging",
            "name": "Packing & Fulfillment",
            "machine_code": "PACK-01",
            "machine_id": "machine:plant:PACK-01",
            "station": "packaging",
            "status": st7_status,
            "throughput_kg_h": st7_tp,
            "rated_capacity_kg_h": float(m_pack.get("rated_capacity_kg_h", 1500.0)),
            "health_score": float(m_pack.get("health_score", 1.0)),
            "temperature_c": float(m_pack.get("temperature_c", 21.0)),
            "vibration_rms": float(m_pack.get("vibration_rms", 0.5)),
            "input_material": "Finished Cocoa Powder & Couverture Butter",
            "input_available": True,
            "input_quantity_kg": 2500.0,
            "output_product": "Dispatched Export Shipments & Finished Retail Cartons",
            "progress_pct": prog_7,
            "operational_reason": st7_reason,
            "upstream_stage_num": 6,
            "downstream_stage_num": None,
            "cycle_number": cycle_num,
            "completed_cycles": completed_cycles,
            "is_active": (active_num == 7),
        },
    ]
