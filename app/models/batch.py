"""Production Batch domain model."""

from datetime import datetime
from decimal import Decimal
from typing import List
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ProductionBatch(Base, TimestampMixin):
    """Production batch moving through cocoa processing stations."""
    __tablename__ = "production_batches"
    __table_args__ = (
        CheckConstraint("planned_quantity_kg > 0", name="chk_batch_positive_planned_qty"),
        CheckConstraint("actual_quantity_kg >= 0", name="chk_batch_non_negative_actual_qty"),
        CheckConstraint("delay_hours >= 0", name="chk_batch_non_negative_delay"),
        Index("ix_production_batches_status", "status"),
        Index("ix_production_batches_schedule", "scheduled_start", "scheduled_end"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Stable canonical ID, e.g. production_batch:mes:BATCH-101")
    batch_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    recipe_name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_product: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="roasted_nibs, cocoa_liquor, dark_couverture, cocoa_butter, cocoa_powder",
    )
    planned_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    actual_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=Decimal("0.000"))
    input_lot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("inventory_lots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    machine_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("machine_assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="scheduled",
        comment="scheduled, in_progress, completed, quarantined, delayed",
    )
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delay_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0.00"))

    # Relationships
    input_lot: Mapped["InventoryLot | None"] = relationship("InventoryLot", back_populates="consumed_by_batches")
    machine: Mapped["MachineAsset | None"] = relationship("MachineAsset", back_populates="batches")
    orders_fulfilled: Mapped[List["CustomerOrder"]] = relationship(
        "CustomerOrder",
        back_populates="assigned_batch",
    )

    def __repr__(self) -> str:
        return f"<ProductionBatch id={self.id!r} code={self.batch_code!r} status={self.status!r} product={self.target_product!r}>"
