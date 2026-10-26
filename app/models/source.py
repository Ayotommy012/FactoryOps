"""
Operational source tables in the 'factory_source' schema.

These tables represent the underlying operational silos (ERP, MES, SCADA, WMS)
which Zetaris federates. FactoryOps queries these tables through Zetaris rather than
reading them directly as application state.
"""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SourceSupplier(Base, TimestampMixin):
    """Raw ERP supplier master record in factory_source schema."""
    __tablename__ = "source_suppliers"
    __table_args__ = {"schema": "factory_source"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. SUP-101
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str] = mapped_column(String(64), nullable=False)
    beans_variety: Mapped[str] = mapped_column(String(64), nullable=False)
    historical_reliability: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    lead_time_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    contract_price_usd_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class SourceInventory(Base, TimestampMixin):
    """Raw WMS inventory lot record in factory_source schema."""
    __tablename__ = "source_inventory"
    __table_args__ = {"schema": "factory_source"}

    lot_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. LOT-2026-001
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location_bin: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    moisture_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    qc_disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    arrival_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceMachineTelemetry(Base):
    """Raw SCADA sensor telemetry time-series record in factory_source schema."""
    __tablename__ = "source_machine_telemetry"
    __table_args__ = {"schema": "factory_source"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    machine_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    temperature_celsius: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    vibration_rms_mm_s: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    power_draw_kw: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    status_flag: Mapped[str] = mapped_column(String(32), nullable=False, default="NORMAL")


class SourceBatch(Base, TimestampMixin):
    """Raw MES production batch execution record in factory_source schema."""
    __tablename__ = "source_batches"
    __table_args__ = {"schema": "factory_source"}

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. BATCH-101
    recipe_code: Mapped[str] = mapped_column(String(64), nullable=False)
    input_lot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_machine_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    actual_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=Decimal("0.000"))
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    planned_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceOrder(Base, TimestampMixin):
    """Raw ERP sales order record in factory_source schema."""
    __tablename__ = "source_orders"
    __table_args__ = {"schema": "factory_source"}

    order_number: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. ORD-9402
    customer_account: Mapped[str] = mapped_column(String(128), nullable=False)
    sku_requested: Mapped[str] = mapped_column(String(64), nullable=False)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    order_value_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    penalty_rate_per_day: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    requested_delivery_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    order_status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
