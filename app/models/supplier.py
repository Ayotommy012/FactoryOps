"""Supplier domain model."""

from decimal import Decimal
from typing import List
from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Supplier(Base, TimestampMixin):
    """Raw material supplier (e.g. cocoa bean co-operatives or traders)."""
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Stable canonical ID, e.g. supplier:erp:SUP-101")
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str] = mapped_column(String(64), nullable=False)
    material_type: Mapped[str] = mapped_column(String(64), nullable=False, default="raw_cocoa_beans")
    reliability_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        default=Decimal("0.9500"),
        comment="Historical delivery reliability probability [0.0000, 1.0000]",
    )
    lead_time_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("7.00"))
    contract_price_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("2.50"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    inventory_lots: Mapped[List["InventoryLot"]] = relationship(
        "InventoryLot",
        back_populates="supplier",
    )

    def __repr__(self) -> str:
        return f"<Supplier id={self.id!r} code={self.code!r} name={self.name!r}>"
