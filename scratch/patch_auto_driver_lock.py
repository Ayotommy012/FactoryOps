with open('app/services/auto_driver.py', 'rb') as f:
    raw = f.read()

# Replace any \r\r\n or \r\n with \n
content_norm = raw.replace(b'\r\r\n', b'\n').replace(b'\r\n', b'\n').decode('utf-8')

content_norm = content_norm.replace(
    'from app.database import SessionLocal\n',
    'from app.database import SessionLocal, engine\n',
    1
)

target2 = """        ticked_count = 0
        has_lock = False
        try:
            if not bypass_lock:
                # Attempt to acquire advisory lock for single-worker execution
                try:
                    res = session.execute(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": SIMULATION_DRIVER_LOCK_ID},
                    ).scalar()
                    has_lock = bool(res)
                except Exception as e:
                    logger.debug("Advisory lock check failed (fallback to local execution): %s", e)
                    has_lock = True

                if not has_lock:
                    # Another worker process is currently the leader
                    return 0
            else:
                has_lock = False"""

replacement2 = """        ticked_count = 0
        has_lock = False
        lock_conn = None
        try:
            if not bypass_lock:
                # Acquire advisory lock on a dedicated connection so session.commit()
                # inside the loop does not recycle the locked connection back to pool
                try:
                    lock_conn = engine.connect()
                    res = lock_conn.execute(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": SIMULATION_DRIVER_LOCK_ID},
                    ).scalar()
                    has_lock = bool(res)
                except Exception as e:
                    logger.debug("Advisory lock check failed (fallback to local execution): %s", e)
                    has_lock = True

                if not has_lock:
                    if lock_conn is not None:
                        try:
                            lock_conn.close()
                        except Exception:
                            pass
                    return 0
            else:
                has_lock = False"""

assert target2 in content_norm, "Target 2 not found"
content_norm = content_norm.replace(target2, replacement2, 1)

target3 = """        finally:
            if has_lock:
                try:
                    session.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": SIMULATION_DRIVER_LOCK_ID},
                    )
                except Exception:
                    pass
            if own_session:
                session.close()"""

replacement3 = """        finally:
            if has_lock and lock_conn is not None:
                try:
                    lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": SIMULATION_DRIVER_LOCK_ID},
                    )
                except Exception:
                    pass
            if lock_conn is not None:
                try:
                    lock_conn.close()
                except Exception:
                    pass
            if own_session:
                session.close()"""

assert target3 in content_norm, "Target 3 not found"
content_norm = content_norm.replace(target3, replacement3, 1)

# Write out clean CRLF
with open('app/services/auto_driver.py', 'wb') as f:
    f.write(content_norm.replace('\n', '\r\n').encode('utf-8'))

print("Successfully cleaned and updated auto_driver.py")
