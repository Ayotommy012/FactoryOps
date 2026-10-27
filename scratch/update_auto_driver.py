with open('app/services/auto_driver.py', 'r', encoding='utf-8') as f:
    code = f.read()

old_block = '''    def _select_eligible_scenarios(self, session: OrmSession) -> list[SimulationScenario]:
        """
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
        """
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

        return ticked_count'''

new_block = '''    def _select_eligible_scenarios(self, session: OrmSession, max_count: int = 5) -> list[SimulationScenario]:
        """
        Selects a bounded, fair-rotating subset of active scenarios for progression.

        Eligibility:
        1. scn_prepared_live_01 is ALWAYS eligible (guaranteed dedicated progression slot).
        2. Scenarios with parameters['auto_advance'] == True (explicitly enabled/resumed).
        3. Active scenarios: not paused (auto_advance != False) AND
           (created_at >= now - 2h OR last_active_at >= now - 2h).
        4. Excludes scenarios that cannot step or recover (e.g. awaiting human decision,
           or finished batches).

        Fair Rotation & Anti-Starvation:
        - scn_prepared_live_01 is unconditionally included if running.
        - Freshly created or active runs without previous auto-ticks get high priority
          so user-initiated scenarios progress immediately.
        - Steady-state candidates are ordered by parameters['last_auto_tick_at'] ascending,
          ensuring fair round-robin advancement across all active scenarios without starvation.
        - Workload is capped at max_count (default 5) to bound CPU and database load.
        """
        now_utc = datetime.now(timezone.utc)
        cutoff_recent = now_utc - timedelta(hours=2)
        fresh_threshold = now_utc - timedelta(minutes=5)

        all_running = session.execute(
            select(SimulationScenario).where(
                SimulationScenario.status.in_(["running", "recovering"])
            )
        ).scalars().all()

        live_prepared: Optional[SimulationScenario] = None
        rotating_candidates: list[tuple[tuple[int, str], SimulationScenario]] = []

        for scn in all_running:
            params = scn.parameters or {}
            # Check for explicit operator pause
            if params.get("auto_advance") is False:
                continue

            # 1. Designated live scenario gets a guaranteed dedicated slot
            if scn.id == "scn_prepared_live_01":
                live_prepared = scn
                continue

            # Must be capable of stepping or recovering; don't waste bounded tick slots
            # on scenarios waiting for human intervention or whose batches are completed.
            is_auto_recoverable = self.should_attempt_autonomous_recovery(session, scn)
            is_tickable, _ = is_scenario_eligible_for_auto_tick(scn)
            if not is_auto_recoverable and not is_tickable:
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
                last_ticked = params.get("last_auto_tick_at")
                last_active_dt = None
                if last_active:
                    try:
                        last_active_dt = datetime.fromisoformat(last_active)
                    except Exception:
                        pass
                if last_active_dt is None:
                    last_active_dt = scn.created_at or now_utc

                if not last_ticked and last_active_dt >= fresh_threshold:
                    # Priority 0: Fresh user scenarios that have not yet had their first auto-tick (FIFO)
                    priority_key = (0, last_active_dt.isoformat())
                elif last_ticked:
                    # Priority 1: Steady-state scenarios rotating fairly by last auto-tick timestamp
                    priority_key = (1, str(last_ticked))
                else:
                    # Priority 2: Older never-ticked scenarios
                    priority_key = (2, last_active_dt.isoformat())

                rotating_candidates.append((priority_key, scn))

        # Sort rotating candidates by priority_key ascending
        rotating_candidates.sort(key=lambda item: item[0])

        selected: list[SimulationScenario] = []
        if live_prepared is not None:
            selected.append(live_prepared)

        # Fill remaining slots up to max_count (default 5 total)
        remaining_slots = max(1, max_count - len(selected))
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
                try:
                    params = dict(scn.parameters or {})
                    params["last_auto_tick_at"] = now_iso
                    scn.parameters = params
                    session.commit()
                except Exception:
                    session.rollback()
                continue
            try:
                step_scenario(session, scn.id, steps=1)
                # Update round-robin tracking timestamp on the scenario
                scn.parameters = {
                    **(scn.parameters or {}),
                    "last_auto_tick_at": now_iso,
                    "last_active_at": now_iso,
                    "auto_tick_failures": 0,
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
                try:
                    scn_ref = session.get(SimulationScenario, scn.id)
                    if scn_ref:
                        params = dict(scn_ref.parameters or {})
                        params["last_auto_tick_at"] = now_iso
                        fails = int(params.get("auto_tick_failures", 0)) + 1
                        params["auto_tick_failures"] = fails
                        if fails >= 3:
                            params["auto_advance"] = False
                            logger.error(
                                "Auto-tick paused for scenario '%s' after %d consecutive failures: %s",
                                scn.id, fails, step_err,
                            )
                        scn_ref.parameters = params
                        session.commit()
                except Exception:
                    session.rollback()

        return ticked_count'''

code_norm = code.replace('\r\n', '\n')
old_norm = old_block.replace('\r\n', '\n')
assert old_norm in code_norm, 'old_block not found in code'
code_updated = code_norm.replace(old_norm, new_block.replace('\r\n', '\n'))
with open('app/services/auto_driver.py', 'w', encoding='utf-8') as f:
    f.write(code_updated.replace('\n', '\r\n'))
print('Successfully updated app/services/auto_driver.py')
