"""Simulation Scenario model."""

from datetime import datetime
from typing import Any, Dict, List
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class SimulationScenario(Base, TimestampMixin):
    """Represents a virtual factory simulation scenario run."""
    __tablename__ = "simulation_scenarios"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    disruption_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Type of disruption: supplier_delay, machine_breakdown, quality_contamination, demand_surge, combined",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="Lifecycle status: pending, running, completed, cancelled",
    )
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    simulation_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Current simulated factory clock timestamp",
    )
    step_size_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parameters: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Relationships
    events: Mapped[List["OperationalEvent"]] = relationship(
        "OperationalEvent",
        back_populates="scenario",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<SimulationScenario id={self.id!r} name={self.name!r} status={self.status!r}>"
