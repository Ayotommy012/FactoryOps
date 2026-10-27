with open('app/services/recovery.py', 'rb') as f:
    raw = f.read()

content = raw.replace(b'\r\r\n', b'\n').replace(b'\r\n', b'\n').decode('utf-8')

target = """    # Release DB transaction snapshot before long-running AI explanation to free PostgreSQL connections
    try:
        session.commit()
    except Exception:
        pass"""

replacement = """    # Release read transaction before long-running AI explanation to free PostgreSQL connections.
    # Rollback ensures no partial or unrelated caller changes are accidentally committed.
    try:
        if not session.dirty and not session.new:
            session.rollback()
    except Exception:
        pass"""

assert target in content, "Target not found in recovery.py"
content = content.replace(target, replacement, 1)

with open('app/services/recovery.py', 'wb') as f:
    f.write(content.replace('\n', '\r\n').encode('utf-8'))

print("Successfully updated app/services/recovery.py")
