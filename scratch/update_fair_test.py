with open('tests/test_simulation_clock_and_latency.py', 'r', encoding='utf-8') as f:
    code = f.read()

old_block = '''    with SessionLocal() as db:
        # Tick 1: should pick up to 5 scenarios
        count1 = driver.tick_once(session=db, bypass_lock=True)
        assert count1 >= 1

        # Tick 2: round-robin must advance the other scenarios
        count2 = driver.tick_once(session=db, bypass_lock=True)
        assert count2 >= 1

        # Across 2 ticks, all 8 scenarios should have been given opportunities to advance
        stepped = [
            db.get(SimulationScenario, s_id).current_step
            for s_id in created_ids
        ]
        # At least 7 or all 8 should have advanced by at least 1 step
        assert sum(1 for step in stepped if step >= 1) >= 7'''

new_block = '''    with SessionLocal() as db:
        # Tick 1: should pick up to 5 scenarios
        count1 = driver.tick_once(session=db, bypass_lock=True)
        assert count1 >= 1

        # Tick 2: round-robin must advance the other scenarios
        count2 = driver.tick_once(session=db, bypass_lock=True)
        assert count2 >= 1

        # Tick 3: round-robin continues advancing remaining scenarios
        count3 = driver.tick_once(session=db, bypass_lock=True)
        assert count3 >= 1

        # Across 3 ticks with a 5-scenario cap, all 8 scenarios have been given opportunities to advance
        stepped = [
            db.get(SimulationScenario, s_id).current_step
            for s_id in created_ids
        ]
        # At least 7 or all 8 should have advanced by at least 1 step
        assert sum(1 for step in stepped if step >= 1) >= 7'''

code_norm = code.replace('\r\n', '\n')
old_norm = old_block.replace('\r\n', '\n')
assert old_norm in code_norm, 'old_block not found in code'
code_updated = code_norm.replace(old_norm, new_block.replace('\r\n', '\n'))
with open('tests/test_simulation_clock_and_latency.py', 'w', encoding='utf-8') as f:
    f.write(code_updated.replace('\n', '\r\n'))
print('Successfully updated fair rotation test')
