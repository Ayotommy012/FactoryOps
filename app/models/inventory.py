"""Inventory Lot domain model."""

from datetime import datetime
from decimal import Decimal
from typing import List
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class InventoryLot(Base, TimestampMixin):
    """Raw material or WIP inventory lot stored in silos, tanks, or warehouses."""
    __tablename__ = "inventory_lots"
    __table_args__ = (
        CheckConstraint("current_quantity_kg >= 0", name="chk_inventory_non_negative"),
        Index("ix_inventory_lots_material_status", "material_type", "quality_status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Stable canonical ID, e.g. inventory_lot:wms:LOT-2026-001")
    lot_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    material_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="raw_cocoa_beans, roasted_nibs, cocoa_liquor, cocoa_butter, cocoa_powder",
    )
    supplier_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    storage_location: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="silo_01, silo_02, buffer_tank_01, warehouse_bay_a",
    )
    initial_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    current_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    moisture_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("6.50"))
    quality_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="approved",
        comment="quarantine, approved, consumed, rejected",
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    supplier: Mapped["Supplier | None"] = relationship("Supplier", back_populates="inventory_lots")
    consumed_by_batches: Mapped[List["ProductionBatch"]] = relationship(
        "ProductionBatch",
        back_populates="input_lot",
    )

    def __repr__(self) -> str:
        return f"<InventoryLot id={self.id!r} code={self.lot_code!r} material={self.material_type!r} qty={self.current_quantity_kg}>"
