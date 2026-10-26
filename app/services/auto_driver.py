"""
Backend-Managed Automatic Simulation Timing Driver.

Advances eligible FactoryOps simulation scenarios by 1 discrete step (15 simulated minutes)
every 5 seconds of real time.

Single-driver guarantees across application restarts and multiple worker processes are
enforced via PostgreSQL session-scoped advisory locks.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session as OrmSession
from app.database import SessionLocal
from app.models import SimulationScenario
from app.services.simulation import (
    is_scenario_eligible_for_auto_tick,
    step_scenario,
)

logger = logging.getLogger("factoryops.auto_driver")

SIMULATION_DRIVER_LOCK_ID = 84729103
DEFAULT_TICK_INTERVAL_SECONDS = 5.0


class SimulationAutoDriver:
    """
    Manages the 5-second automatic simulation progression loop with single-worker
    election via PostgreSQL advisory locks.
    """

    def __init__(self, interval: float = DEFAULT_TICK_INTERVAL_SECONDS):
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Starts the background asyncio tick loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("SimulationAutoDriver started with %ss interval", self.interval)

    async def stop(self):
        """Stops the background driver loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("SimulationAutoDriver stopped")

    async def _run_loop(self):
        # Do not run background loop during automated pytest execution
        if os.environ.get("PYTEST_CURRENT_TEST"):
            logger.info("Pytest detected; background simulation auto-driver loop disabled.")
            return

        while self._running:
            try:
                await asyncio.sleep(self.interval)
                if not self._running:
                    break
                # Execute tick across all eligible scenarios in a worker thread
                await asyncio.to_thread(self.tick_once)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in simulation auto-driver loop: %s", e)

    @staticmethod
    def should_attempt_autonomous_recovery(session: OrmSession, scn: SimulationScenario) -> bool:
        """An opted-in scenario with live disruption evidence whose incident has not
        already been handled, claimed, or put into failure back-off."""
        from app.services.autonomous_recovery import HANDLED_RECOVERY_STATUSES, incident_key_for
        from app.services.recovery import detect_and_trace_impact
        from app.services.simulation import is_autonomous_recovery_enabled

        if not is_autonomous_recovery_enabled(scn):
            return False
        rec = (scn.parameters or {}).get("recovery") or {}
        status = rec.get("status")
        if status == "investigating":
            return False  # claimed; stale claims are reclaimed by the lifecycle itself
        next_attempt = rec.get("next_attempt_at")
        if next_attempt and datetime.fromisoformat(next_attempt) > datetime.now(timezone.utc):
            return False
        try:
            impact = detect_and_trace_impact(session, scn.id)
        except Exception:
            return False
        if not impact.evidence:
            return False
        if status in HANDLED_RECOVERY_STATUSES and rec.get("incident_key") == incident_key_for(scn, impact):
            return False
        if status in ("in_progress",):
            return False  # a recovery is executing; let it complete before re-evaluating
        return True

    def tick_once(self, session: Optional[OrmSession] = None, bypass_lock: bool = False) -> int:
        """
        Executes one tick across all eligible scenarios using leader election.
        Returns the number of scenarios ticked.
        """
        own_session = False
        if session is None:
            session = SessionLocal()
            own_session = True

        ticked_count = 0
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
                has_lock = False

            # Query scenarios eligible for progression
            scenarios = session.execute(
                select(SimulationScenario).where(
                    SimulationScenario.status.in_(["running", "recovering"])
                )
            ).scalars().all()

            lifecycles_this_tick = 0
            for scn in scenarios:
                # One autonomous lifecycle per tick bounds Meterless traffic: the
                # upstream lane rate-limits bursts, so incidents queue, not stampede.
                if lifecycles_this_tick == 0 and self.should_attempt_autonomous_recovery(session, scn):
                    lifecycles_this_tick += 1
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
            if has_lock:
                try:
                    session.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": SIMULATION_DRIVER_LOCK_ID},
                    )
                except Exception:
                    pass
            if own_session:
                session.close()

        return ticked_count


auto_driver = SimulationAutoDriver()
