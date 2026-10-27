with open('app/services/auto_driver.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = """            # Query scenarios eligible for progression
            scenarios = session.execute(
                select(SimulationScenario).where(
                    SimulationScenario.status.in_(["running", "recovering"])
                )
            ).scalars().all()"""

replacement = """            # Query scenarios eligible for progression, prioritizing active and recently created runs.
            # Unbounded background ticking of historical scenarios from days ago causes database lock
            # contention and stalls API requests.
            now_utc = datetime.now(timezone.utc)
            cutoff_recent = now_utc - timedelta(hours=2)

            all_running = session.execute(
                select(SimulationScenario).where(
                    SimulationScenario.status.in_(["running", "recovering"])
                ).order_by(SimulationScenario.created_at.desc())
            ).scalars().all()

            scenarios = []
            for scn in all_running:
                # 1. Designated live scenario must always progress
                if scn.id == "scn_prepared_live_01":
                    scenarios.append(scn)
                # 2. Explicitly resumed or opted-in scenarios
                elif (scn.parameters or {}).get("auto_advance") is True:
                    scenarios.append(scn)
                # 3. Active scenarios created within the recent operating window and not paused
                elif scn.created_at and scn.created_at >= cutoff_recent and (scn.parameters or {}).get("auto_advance") is not False:
                    scenarios.append(scn)

            # Cap active progression set per tick to bound database transaction time
            scenarios = scenarios[:5]"""

# Normalize line endings
content_norm = content.replace('\r\n', '\n')
target_norm = target.replace('\r\n', '\n')
replacement_norm = replacement.replace('\r\n', '\n')

assert target_norm in content_norm, "Target not found in auto_driver.py"
new_content_norm = content_norm.replace(target_norm, replacement_norm, 1)

# Preserve original line ending style (CRLF)
with open('app/services/auto_driver.py', 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(new_content_norm.replace('\n', '\r\n'))

print("Successfully patched auto_driver.py")
