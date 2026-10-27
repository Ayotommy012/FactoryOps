with open('app/services/simulation.py', 'rb') as f:
    raw = f.read()

content = raw.replace(b'\r\r\n', b'\n').replace(b'\r\n', b'\n').decode('utf-8')

target1 = """        parameters={
            "snapshot": initial_snapshot,
            "random_seed": random_seed,
            "disruption_type": disruption_type,
            "world_model_state": world.state_shape(),
            "autonomous_recovery": {"enabled": bool(autonomous_recovery)},
        },"""

replacement1 = """        parameters={
            "snapshot": initial_snapshot,
            "random_seed": random_seed,
            "disruption_type": disruption_type,
            "world_model_state": world.state_shape(),
            "autonomous_recovery": {"enabled": bool(autonomous_recovery)},
            "auto_advance": True,
            "last_active_at": datetime.now(timezone.utc).isoformat(),
        },"""

assert target1 in content, "Target 1 not found"
content = content.replace(target1, replacement1, 1)

target2 = """    if idempotency_key:
        keys_dict = dict((scenario.parameters or {}).get("step_idempotency_keys") or {})
        keys_dict[idempotency_key] = {
            "step": scenario.current_step,
            "steps": steps,
            "target_step": target_step,
            "simulation_time": scenario.simulation_time.isoformat(),
        }
        scenario.parameters = {**scenario.parameters, "step_idempotency_keys": keys_dict}
        session.flush()

    from app.services.disruptions import sync_incident_severity"""

replacement2 = """    # Update active tracking timestamp
    scenario.parameters = {
        **(scenario.parameters or {}),
        "last_active_at": datetime.now(timezone.utc).isoformat(),
    }

    if idempotency_key:
        keys_dict = dict((scenario.parameters or {}).get("step_idempotency_keys") or {})
        keys_dict[idempotency_key] = {
            "step": scenario.current_step,
            "steps": steps,
            "target_step": target_step,
            "simulation_time": scenario.simulation_time.isoformat(),
        }
        scenario.parameters = {**scenario.parameters, "step_idempotency_keys": keys_dict}
        session.flush()

    from app.services.disruptions import sync_incident_severity"""

assert target2 in content, "Target 2 not found"
content = content.replace(target2, replacement2, 1)

with open('app/services/simulation.py', 'wb') as f:
    f.write(content.replace('\n', '\r\n').encode('utf-8'))

print("Successfully updated app/services/simulation.py")
