with open('app/services/recovery.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = """    plans = [plan_a, plan_b, plan_c]
    _annotate_plan_authorizations(plans, disruption_type=impact.disruption_type, scenario=scenario, impact=impact)

    # Attach operational explanations: ONE Meterless call for the whole candidate
    # set (previously three back-to-back calls, which tripped the upstream 429).
    explain_recovery_plans(impact, plans, enabled=use_ai)"""

replacement = """    plans = [plan_a, plan_b, plan_c]
    _annotate_plan_authorizations(plans, disruption_type=impact.disruption_type, scenario=scenario, impact=impact)

    # Release DB transaction snapshot before long-running AI explanation to free PostgreSQL connections
    try:
        session.commit()
    except Exception:
        pass

    # Attach operational explanations: ONE Meterless call for the whole candidate
    # set (previously three back-to-back calls, which tripped the upstream 429).
    explain_recovery_plans(impact, plans, enabled=use_ai)"""

content_norm = content.replace('\r\n', '\n')
target_norm = target.replace('\r\n', '\n')
replacement_norm = replacement.replace('\r\n', '\n')

assert target_norm in content_norm, "Target not found in recovery.py"
new_content_norm = content_norm.replace(target_norm, replacement_norm, 1)

with open('app/services/recovery.py', 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(new_content_norm.replace('\n', '\r\n'))

print("Successfully patched recovery.py")
