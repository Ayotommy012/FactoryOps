with open('app/services/auto_driver.py', 'rb') as f:
    raw = f.read()

content = raw.replace(b'\r\r\n', b'\n').replace(b'\r\n', b'\n').decode('utf-8')

# Replace tick_once and add helper methods
target = """    def tick_once(self, session: Optional[OrmSession] = None, bypass_lock: bool = False) -> int:
        \"\"\"
        Executes one tick across all eligible scenarios using leader election.
        Returns the number of scenarios ticked.
        \"\"\"
        own_session = False
        if session is None:
            session = SessionLocal()
            own_session = True

        ticked_count = 0
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
                has_lock = False

            # Query scenarios eligible for progression, prioritizing active and recently created runs.
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
            scenarios = scenarios[:5]

            lifecycles_this_tick = 0
            for scn in scenarios:
                # One autonomous lifecycle per tick bounds Meterless traffic: the
                # upstream lane rate-limits bursts, so incidents queue, not stampede.
                worker_busy = self._lifecycle_thread is not None and self._lifecycle_thread.is_alive()
                if lifecycles_this_tick == 0 and not worker_busy and self.should_attempt_autonomous_recovery(session, scn):
                    lifecycles_this_tick += 1
                    if own_session:
                        self._lifecycle_thread = threading.Thread(
                            target=self._run_lifecycle_isolated, args=(scn.id,),
                            name=f"autonomous-recovery-{scn.id}", daemon=True,
                        )
                        self._lifecycle_thread.start()
                        continue
                    try:
                        from app.services.autonomous_recovery import run_autonomous_recovery_lifecycle
                        record = run_autonomous_recovery_lifecycle(session, scn.id, auto_advance_steps=False)
                        logger.info(
                            "Autonomous recovery on '%s': %s (plan %s)",
                            scn.id, record.final_status, record.selected_plan_id,
                        )
                    except Exception as rec_err:
                        session.rollback()
                        logger.warning("Autonomous recovery attempt failed for scenario %s: %s", scn.id, rec_err)
                    scn = session.get(SimulationScenario, scn.id)

                eligible, reason = is_scenario_eligible_for_auto_tick(scn)
                if not eligible:
                    continue
                try:
                    step_scenario(session, scn.id, steps=1)
                    session.commit()
                    ticked_count += 1
                    logger.info(
                        "Auto-tick: Scenario '%s' stepped to step %s (%s)",
                        scn.id, scn.current_step, scn.simulation_time,
                    )
                except Exception as step_err:
                    session.rollback()
                    logger.warning("Auto-tick failed for scenario '%s': %s", scn.id, step_err)

        except Exception as e:
            session.rollback()
            logger.error("Simulation auto-driver tick_once failed: %s", e)
        finally:
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
                session.close()

        return ticked_count"""

replacement = """    def _select_eligible_scenarios(self, session: OrmSession) -> list[SimulationScenario]:
        \"\"\"
        Selects a bounded, fair-rotating subset of active scenarios for progression.

        Eligibility:
        1. scn_prepared_live_01 is ALWAYS eligible (guaranteed dedicated progression slot).
        2. Scenarios with parameters['auto_advance'] == True (explicitly enabled/resumed).
        3. Active scenarios: not paused (auto_advance != False) AND
           (created_at >= now - 2h OR last_active_at >= now - 2h).

        Fair Rotation & Anti-Starvation:
        - scn_prepared_live_01 is unconditionally included if running.
        - Other candidates are ordered by parameters['last_auto_tick_at'] ascending (oldest/never-ticked first).
        - Up to 4 rotating candidates are selected, capping total tick workload to 5 scenarios.
        - This prevents starvation even when dozens of legitimate active runs exist.
        \"\"\"
        now_utc = datetime.now(timezone.utc)
        cutoff_recent = now_utc - timedelta(hours=2)

        all_running = session.execute(
            select(SimulationScenario).where(
                SimulationScenario.status.in_(["running", "recovering"])
            )
        ).scalars().all()

        live_prepared: Optional[SimulationScenario] = None
        rotating_candidates: list[tuple[str, SimulationScenario]] = []

        for scn in all_running:
            params = scn.parameters or {}
            # Check for explicit operator pause
            if params.get("auto_advance") is False:
                continue

            # 1. Designated live scenario gets a guaranteed dedicated slot
            if scn.id == "scn_prepared_live_01":
                live_prepared = scn
                continue

            # Check eligibility:
            # - Explicitly enabled auto_advance: True
            # - Or created within recent operating window
            # - Or stepped/active within recent operating window
            is_explicit = params.get("auto_advance") is True
            is_recent_created = bool(scn.created_at and scn.created_at >= cutoff_recent)
            last_active = params.get("last_active_at")
            is_recent_active = bool(last_active and datetime.fromisoformat(last_active) >= cutoff_recent)

            if is_explicit or is_recent_created or is_recent_active:
                last_ticked = params.get("last_auto_tick_at") or ""
                rotating_candidates.append((last_ticked, scn))

        # Sort rotating candidates by last_auto_tick_at ascending (oldest ticked or never ticked first)
        rotating_candidates.sort(key=lambda item: item[0])

        selected: list[SimulationScenario] = []
        if live_prepared is not None:
            selected.append(live_prepared)

        # Fill remaining slots up to MAX_TICK_BATCH_SIZE (default 5 total)
        remaining_slots = max(1, 5 - len(selected))
        for _, scn in rotating_candidates[:remaining_slots]:
            selected.append(scn)

        return selected

    def _tick_eligible_scenarios(self, session: OrmSession, own_session: bool) -> int:
        scenarios = self._select_eligible_scenarios(session)
        ticked_count = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        lifecycles_this_tick = 0
        for scn in scenarios:
            # One autonomous lifecycle per tick bounds Meterless traffic
            worker_busy = self._lifecycle_thread is not None and self._lifecycle_thread.is_alive()
            if lifecycles_this_tick == 0 and not worker_busy and self.should_attempt_autonomous_recovery(session, scn):
                lifecycles_this_tick += 1
                if own_session:
                    self._lifecycle_thread = threading.Thread(
                        target=self._run_lifecycle_isolated, args=(scn.id,),
                        name=f"autonomous-recovery-{scn.id}", daemon=True,
                    )
                    self._lifecycle_thread.start()
                    continue
                try:
                    from app.services.autonomous_recovery import run_autonomous_recovery_lifecycle
                    record = run_autonomous_recovery_lifecycle(session, scn.id, auto_advance_steps=False)
                    logger.info(
                        "Autonomous recovery on '%s': %s (plan %s)",
                        scn.id, record.final_status, record.selected_plan_id,
                    )
                except Exception as rec_err:
                    session.rollback()
                    logger.warning("Autonomous recovery attempt failed for scenario %s: %s", scn.id, rec_err)
                scn = session.get(SimulationScenario, scn.id)

            eligible, reason = is_scenario_eligible_for_auto_tick(scn)
            if not eligible:
                continue
            try:
                step_scenario(session, scn.id, steps=1)
                # Update round-robin tracking timestamp on the scenario
                scn.parameters = {
                    **(scn.parameters or {}),
                    "last_auto_tick_at": now_iso,
                    "last_active_at": now_iso,
                }
                session.commit()
                ticked_count += 1
                logger.info(
                    "Auto-tick: Scenario '%s' stepped to step %s (%s)",
                    scn.id, scn.current_step, scn.simulation_time,
                )
            except Exception as step_err:
                session.rollback()
                logger.warning("Auto-tick failed for scenario '%s': %s", scn.id, step_err)

        return ticked_count

    def tick_once(self, session: Optional[OrmSession] = None, bypass_lock: bool = False) -> int:
        \"\"\"
        Executes one tick across all eligible scenarios using leader election.
        Guarantees lock acquisition, execution, unlock, and connection closure on the
        dedicated connection across early returns and failures.
        \"\"\"
        own_session = False
        if session is None:
            session = SessionLocal()
            own_session = True

        ticked_count = 0
        has_lock = False
        lock_conn = None

        try:
            if not bypass_lock:
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
                    return 0

            ticked_count = self._tick_eligible_scenarios(session, own_session)

        except Exception as e:
            session.rollback()
            logger.error("Simulation auto-driver tick_once failed: %s", e)
        finally:
            if has_lock and lock_conn is not None:
                try:
                    lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": SIMULATION_DRIVER_LOCK_ID},
                    )
                except Exception as e:
                    logger.debug("Failed to release advisory lock: %s", e)
            if lock_conn is not None:
                try:
                    lock_conn.close()
                except Exception:
                    pass
            if own_session:
                session.close()

        return ticked_count"""

assert target in content, "Target not found in auto_driver.py"
content = content.replace(target, replacement, 1)

with open('app/services/auto_driver.py', 'wb') as f:
    f.write(content.replace('\n', '\r\n').encode('utf-8'))

print("Successfully hardened auto_driver.py")
