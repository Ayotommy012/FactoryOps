"""Operational Event log model."""

from datetime import datetime
from typing import Any, Dict, List
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class OperationalEvent(Base):
    """
    Append-only operational event envelope per TRD Section 6.
    Ensures complete provenance, idempotency, and auditability.
    """
    __tablename__ = "operational_events"
    __table_args__ = (
        Index("ix_operational_events_sim_time", "simulation_time"),
        Index("ix_operational_events_type_recorded", "event_type", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    scenario_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("simulation_scenarios.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    simulation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, comment="erp, scada, mes, wms, simulator")
    source_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_refs: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Relationships
    scenario: Mapped["SimulationScenario | None"] = relationship("SimulationScenario", back_populates="events")

    def __repr__(self) -> str:
        return f"<OperationalEvent id={self.event_id!r} type={self.event_type!r} sim_time={self.simulation_time.isoformat()!r}>"
