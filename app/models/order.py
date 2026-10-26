"""Customer Order domain model."""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CustomerOrder(Base, TimestampMixin):
    """Customer purchase order with delivery deadlines and financial SLA penalties."""
    __tablename__ = "customer_orders"
    __table_args__ = (
        CheckConstraint("quantity_kg > 0", name="chk_order_positive_quantity"),
        CheckConstraint("unit_price >= 0", name="chk_order_non_negative_unit_price"),
        CheckConstraint("total_value >= 0", name="chk_order_non_negative_total_value"),
        CheckConstraint("penalty_per_day_late >= 0", name="chk_order_non_negative_penalty"),
        Index("ix_customer_orders_deadline_status", "deadline", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Stable canonical ID, e.g. customer_order:erp:ORD-9402")
    order_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    product_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    penalty_per_day_late: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("0.00"),
        comment="Contractual SLA penalty cost per 24h delay",
    )
    priority: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="normal",
        comment="normal, high, critical",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending, allocated, in_production, ready_to_ship, fulfilled, late",
    )
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_batch_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("production_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    assigned_batch: Mapped["ProductionBatch | None"] = relationship(
        "ProductionBatch",
        back_populates="orders_fulfilled",
    )

    def __repr__(self) -> str:
        return f"<CustomerOrder id={self.id!r} code={self.order_code!r} customer={self.customer_name!r} status={self.status!r}>"
