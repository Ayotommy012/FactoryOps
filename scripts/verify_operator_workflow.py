"""Verification script exercising the complete 9-step operator workflow end-to-end."""

import os
import sys
import uuid
sys.path.insert(0, os.path.abspath("."))

import json
from fastapi.testclient import TestClient
from app.main import app

def run_workflow_verification():
    client = TestClient(app)

    print("=================================================================")
    print("STEP 1 — Load or Create Scenario")
    print("=================================================================")
    create_payload = {
        "name": "Audit Roaster Disruption Run",
        "disruption_type": "roaster_bearing_overheat",
        "random_seed": 42,
        "step_size_minutes": 15,
        "scenario_id": f"scn_audit_workflow_{uuid.uuid4().hex[:6]}",
    }
    res_create = client.post("/simulations", json=create_payload)
    print("POST /simulations status:", res_create.status_code)
    assert res_create.status_code == 201, res_create.text
    scn_data = res_create.json()
    scenario_id = scn_data["id"]
    print(f"Scenario ID: {scenario_id}")
    print(f"Initial Status: {scn_data['status']}")
    print(f"Current Step: {scn_data['current_step']}")
    assert scn_data["current_step"] == 0

    print("\n=================================================================")
    print("STEP 2 — Advance Simulation to Anomaly (3 Steps / 45 Minutes)")
    print("=================================================================")
    res_step = client.post(f"/simulations/{scenario_id}/step", json={"steps": 3})
    print("POST /step status:", res_step.status_code)
    assert res_step.status_code == 200, res_step.text
    step_data = res_step.json()
    print(f"Advanced to Step: {step_data['current_step']}")
    print(f"Simulation Time: {step_data['simulation_time']}")
    print(f"Scenario Status: {step_data['status']}")
    print(f"Events Emitted: {step_data['events_emitted_count']}")
    assert step_data["current_step"] == 3
    roaster_status = step_data["snapshot"]["machines"]["machine:plant:ROAST-01"]["status"]
    print(f"ROAST-01 Status: {roaster_status}")
    assert roaster_status == "degraded"

    print("\n=================================================================")
    print("STEP 3 — Verify Resulting State and Active Incidents")
    print("=================================================================")
    res_incidents = client.get(f"/simulations/{scenario_id}/incidents")
    print("GET /incidents status:", res_incidents.status_code)
    assert res_incidents.status_code == 200, res_incidents.text
    incidents = res_incidents.json()
    print("Active Incidents:", incidents["has_active_incidents"])
    print("Incident Count:", incidents["incident_count"])
    print("Disrupted Machine:", incidents["disrupted_machine_id"])
    print("Affected Batch:", incidents["affected_batch_id"])
    print("Affected Order:", incidents["affected_order_id"])
    print("SLA Breached:", incidents["sla_breached"])
    print(f"Potential Penalty: ${incidents['potential_penalty_usd']:.2f}")
    assert incidents["has_active_incidents"] is True
    assert incidents["disrupted_machine_id"] == "machine:plant:ROAST-01"

    print("\n=================================================================")
    print("STEP 4 — Inspect Impact Evidence")
    print("=================================================================")
    res_impact = client.get(f"/simulations/{scenario_id}/recovery/impact")
    print("GET /recovery/impact status:", res_impact.status_code)
    assert res_impact.status_code == 200, res_impact.text
    impact = res_impact.json()
    print("Evidence Count:", len(impact["evidence"]))
    for ev in impact["evidence"]:
        print(f"  • {ev['metric']}: observed={ev['observed_value']}{ev['unit']} (threshold={ev['threshold_value']}{ev['unit']}) severity={ev['severity']}")
    print(f"Delay Hours: {impact['delay_hours']}h")
    print(f"Unintervened Completion: {impact['unintervened_completion']}")
    assert len(impact["evidence"]) >= 1

    print("\n=================================================================")
    print("STEP 5 — Retrieve Recovery Candidates (Ranked Plans A, B, C)")
    print("=================================================================")
    res_plans = client.post(f"/simulations/{scenario_id}/recovery/plans")
    print("POST /recovery/plans status:", res_plans.status_code)
    assert res_plans.status_code == 200, res_plans.text
    plans_data = res_plans.json()
    print("Candidate Count:", plans_data["candidate_count"])
    for p in plans_data["plans"]:
        print(f"  Rank #{p['rank']}: {p['title']} [{p['strategy']}]")
        print(f"    Feasibility: {p['feasibility_verdict']}, Meets Deadline: {p['meets_deadline']}, Buffer: {p['buffer_hours']}h")
        print(f"    Total Cost: ${p['total_cost_usd']:.2f}, Breakdown: {p['cost_breakdown']}")
        print(f"    Explanation: {p['explanation'][:100]}...")
    assert plans_data["candidate_count"] == 3
    plan_to_approve = plans_data["plans"][0]["plan_id"]

    print("\n=================================================================")
    print("STEP 6 — Approve Recovery Plan through Real API")
    print("=================================================================")
    decision_payload = {
        "plan_id": plan_to_approve,
        "decision": "approved",
        "operator_id": "OP-LEAD-CHIEF",
        "rationale": "Authoritative operator signoff: preserves SLA buffer with lowest total cost.",
    }
    res_decide = client.post(f"/simulations/{scenario_id}/recovery/decide", json=decision_payload)
    print("POST /recovery/decide status:", res_decide.status_code)
    assert res_decide.status_code == 200, res_decide.text
    decide_data = res_decide.json()
    print("Decision:", decide_data["decision"])
    print("Operator ID:", decide_data["operator_id"])
    print("Applied At:", decide_data["applied_at"])
    print("Audit Event ID:", decide_data["event_id"])
    print("Updated Scenario Status:", decide_data["updated_scenario_status"])
    assert decide_data["decision"] == "approved"
    assert decide_data["updated_scenario_status"] == "recovering"

    print("\n=================================================================")
    print("STEP 7 — Verify Resulting Backend State and Persisted Audit Event")
    print("=================================================================")
    res_state = client.get(f"/simulations/{scenario_id}")
    assert res_state.status_code == 200
    state_after = res_state.json()
    print("Scenario Status After Approval:", state_after["status"])
    assert state_after["status"] == "recovering"

    res_timeline = client.get(f"/simulations/{scenario_id}/timeline")
    assert res_timeline.status_code == 200
    events = res_timeline.json()["timeline"]
    decision_events = [e for e in events if e["event_type"] in ("recovery.approved", "recovery.decision_applied")]
    print("Decision Audit Events Found in Timeline:", len(decision_events))
    assert len(decision_events) >= 1
    d_ev = decision_events[0]
    print(f"  Recorded Event: {d_ev['event_id']}, Sim Time: {d_ev['simulation_time']}")
    print(f"  Decision Payload: {d_ev['payload']}")

    print("\n=================================================================")
    print("STEP 8 — Test Rejection Workflow on Independent Scenario")
    print("=================================================================")
    res_create_rej = client.post("/simulations", json={
        "name": "Audit Rejection Disruption Run",
        "disruption_type": "roaster_bearing_overheat",
        "random_seed": 42,
        "step_size_minutes": 15,
        "scenario_id": f"scn_audit_rejection_{uuid.uuid4().hex[:6]}",
    })
    assert res_create_rej.status_code == 201
    rej_scn_id = res_create_rej.json()["id"]

    client.post(f"/simulations/{rej_scn_id}/step", json={"steps": 3})

    reject_payload = {
        "plan_id": "plan_c_split_expedite",
        "decision": "rejected",
        "operator_id": "OP-LEAD-CHIEF",
        "rationale": "Plan C breaches customer delivery deadline by unacceptable margin.",
    }
    res_reject = client.post(f"/simulations/{rej_scn_id}/recovery/decide", json=reject_payload)
    print("POST /recovery/decide (reject) status:", res_reject.status_code)
    assert res_reject.status_code == 200, res_reject.text
    reject_data = res_reject.json()
    print("Decision:", reject_data["decision"])
    print("Scenario Status After Rejection:", reject_data["updated_scenario_status"])
    assert reject_data["decision"] == "rejected"
    assert reject_data["updated_scenario_status"] == "running"

    print("\n=================================================================")
    print("STEP 9 — Reload Frontend Console and Confirm Consistency")
    print("=================================================================")
    res_console = client.get("/console")
    assert res_console.status_code == 200
    assert '<div id="root"></div>' in res_console.text
    print("GET /console returned 200 OK with React mount root.")

    res_reload_scn = client.get(f"/simulations/{scenario_id}")
    assert res_reload_scn.status_code == 200
    reloaded = res_reload_scn.json()
    print("Reloaded Approved Scenario step:", reloaded["current_step"])
    print("Reloaded Approved Scenario status:", reloaded["status"])
    assert reloaded["current_step"] == 3
    assert reloaded["status"] == "recovering"

    print("\n=================================================================")
    print("ALL 9 WORKFLOW STEPS VERIFIED SUCCESSFULLY VIA REAL API!")
    print("=================================================================")

if __name__ == "__main__":
    run_workflow_verification()
