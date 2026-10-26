"""Machine Asset domain model."""

from datetime import datetime
from decimal import Decimal
from typing import List
from sqlalchemy import CheckConstraint, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class MachineAsset(Base, TimestampMixin):
    """Factory processing equipment (roaster, winnower, mill, conche, press)."""
    __tablename__ = "machine_assets"
    __table_args__ = (
        CheckConstraint("health_score >= 0.0 AND health_score <= 1.0", name="chk_machine_health_bounds"),
        CheckConstraint("rated_capacity_kg_h > 0", name="chk_machine_positive_capacity"),
        Index("ix_machine_assets_station_status", "station", "operating_status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Stable canonical ID, e.g. machine:plant:ROAST-01")
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    station: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Station in production flow: cleaning, roasting, winnowing, milling, conching, pressing",
    )
    rated_capacity_kg_h: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="Nominal throughput capacity in kg per hour",
    )
    operating_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="idle",
        comment="idle, running, degraded, breakdown, maintenance",
    )
    health_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        default=Decimal("1.0000"),
        comment="Equipment health index [0.0000, 1.0000]",
    )
    current_temp_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    current_vibration_rms: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    last_maintenance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    batches: Mapped[List["ProductionBatch"]] = relationship(
        "ProductionBatch",
        back_populates="machine",
    )

    def __repr__(self) -> str:
        return f"<MachineAsset id={self.id!r} code={self.code!r} station={self.station!r} status={self.operating_status!r}>"
