with open('tests/test_disruptions_and_supply_chain.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Add import
if 'from sqlalchemy.orm.attributes import flag_modified' not in text:
    text = 'from sqlalchemy.orm.attributes import flag_modified\n' + text

text = text.replace(
    'scenario.parameters = {**scenario.parameters, "snapshot": snap}\n    db_session.flush()',
    'scenario.parameters = {**scenario.parameters, "snapshot": snap}\n    flag_modified(scenario, "parameters")\n    db_session.flush()'
)
text = text.replace(
    '"completed_step": 5,\n    }\n    db_session.flush()',
    '"completed_step": 5,\n    }\n    flag_modified(scenario, "parameters")\n    db_session.flush()'
)

with open('tests/test_disruptions_and_supply_chain.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Successfully patched test_disruptions_and_supply_chain.py')
