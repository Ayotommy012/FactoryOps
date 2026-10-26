"""Focused domain model constraint and schema tests with strict transaction isolation."""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine
from app.models import (
    CustomerOrder,
    InventoryLot,
    MachineAsset,
    OperationalEvent,
    ProductionBatch,
    SimulationScenario,
    SourceSupplier,
    Supplier,
)


@pytest.fixture
def db_session():
    """
    Provides an isolated database session using nested savepoints.
    Any commits executed within a test are isolated to a savepoint and
    completely rolled back at fixture teardown, guaranteeing zero persistent rows.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def test_supplier_creation_and_uniqueness(db_session: Session):
    """Verifies supplier creation and unique code constraint."""
    sup = Supplier(
        id="supplier:erp:SUP-ISO-01",
        code="SUP-ISO-01",
        name="Abidjan Cocoa Union",
        country="Cote d'Ivoire",
        material_type="raw_cocoa_beans_forastero",
        reliability_score=Decimal("0.9650"),
        lead_time_days=Decimal("6.50"),
        contract_price_per_kg=Decimal("2.45"),
        currency="USD",
    )
    db_session.add(sup)
    db_session.commit()

    # Query back
    fetched = db_session.get(Supplier, "supplier:erp:SUP-ISO-01")
    assert fetched is not None
    assert fetched.code == "SUP-ISO-01"
    assert fetched.reliability_score == Decimal("0.9650")

    # Duplicate code must fail
    dup = Supplier(
        id="supplier:erp:SUP-ISO-02",
        code="SUP-ISO-01",  # duplicate code
        name="Duplicate Supplier",
        country="Ghana",
        material_type="raw_cocoa_beans",
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_inventory_lot_non_negative_constraint(db_session: Session):
    """Verifies that negative inventory quantities violate the check constraint."""
    now = datetime.now(timezone.utc)
    lot = InventoryLot(
        id="inventory_lot:wms:LOT-ISO-NEG",
        lot_code="LOT-ISO-NEG",
        material_type="raw_cocoa_beans",
        storage_location="silo_01",
        initial_quantity_kg=Decimal("1000.000"),
        current_quantity_kg=Decimal("-50.000"),  # Violates chk_inventory_non_negative
        received_at=now,
    )
    db_session.add(lot)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_machine_asset_constraints(db_session: Session):
    """Verifies health bounds and positive capacity constraints on machines."""
    # 1. Invalid health score > 1.0
    m_invalid = MachineAsset(
        id="machine:plant:ROAST-ISO-INV",
        code="ROAST-ISO-INV",
        name="Invalid Roaster",
        station="roasting",
        rated_capacity_kg_h=Decimal("1000.00"),
        health_score=Decimal("1.5000"),  # Out of bounds
    )
    db_session.add(m_invalid)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 2. Valid machine asset
    m_valid = MachineAsset(
        id="machine:plant:ROAST-ISO-01",
        code="ROAST-ISO-01",
        name="Drum Roaster 1",
        station="roasting",
        rated_capacity_kg_h=Decimal("1200.00"),
        operating_status="running",
        health_score=Decimal("0.9200"),
        current_temp_c=Decimal("138.50"),
        current_vibration_rms=Decimal("3.10"),
    )
    db_session.add(m_valid)
    db_session.commit()

    fetched = db_session.get(MachineAsset, "machine:plant:ROAST-ISO-01")
    assert fetched is not None
    assert fetched.rated_capacity_kg_h == Decimal("1200.00")


def test_production_batch_and_order_relationships(db_session: Session):
    """Verifies batch execution tracking and order fulfillment linkage."""
    now = datetime.now(timezone.utc)

    # Create valid batch
    batch = ProductionBatch(
        id="production_batch:mes:BATCH-ISO-01",
        batch_code="BATCH-ISO-01",
        recipe_name="dark_couverture_70",
        target_product="dark_couverture",
        planned_quantity_kg=Decimal("2500.000"),
        actual_quantity_kg=Decimal("2480.000"),
        status="completed",
        scheduled_start=now,
        scheduled_end=now,
        delay_hours=Decimal("1.50"),
    )
    db_session.add(batch)
    db_session.commit()

    # Create customer order fulfilling from this batch
    order = CustomerOrder(
        id="customer_order:erp:ORD-ISO-01",
        order_code="ORD-ISO-01",
        customer_name="Artisan Chocolates",
        product_sku="SKU-COUV-70",
        quantity_kg=Decimal("2000.000"),
        unit_price=Decimal("8.50"),
        total_value=Decimal("17000.00"),
        penalty_per_day_late=Decimal("500.00"),
        priority="high",
        status="ready_to_ship",
        deadline=now,
        assigned_batch_id="production_batch:mes:BATCH-ISO-01",
    )
    db_session.add(order)
    db_session.commit()

    # Verify relationship traversal
    fetched_order = db_session.get(CustomerOrder, "customer_order:erp:ORD-ISO-01")
    assert fetched_order is not None
    assert fetched_order.assigned_batch is not None
    assert fetched_order.assigned_batch.batch_code == "BATCH-ISO-01"


def test_operational_event_idempotency(db_session: Session):
    """Verifies that operational events enforce idempotency via unique idempotency_key."""
    now = datetime.now(timezone.utc)

    # First create a simulation scenario
    scenario = SimulationScenario(
        id="scn_iso_01",
        name="Test Scenario",
        disruption_type="machine_breakdown",
        simulation_time=now,
    )
    db_session.add(scenario)
    db_session.commit()

    evt1 = OperationalEvent(
        event_id="evt_iso_001",
        scenario_id="scn_iso_01",
        event_type="machine.overheated",
        simulation_time=now,
        recorded_at=now,
        source_system="scada",
        source_record_id="ROAST-01",
        idempotency_key="idemp_hash_iso_step1",
        payload={"temp": 142.5},
    )
    db_session.add(evt1)
    db_session.commit()

    # Re-inserting the same idempotency key must fail
    evt_duplicate = OperationalEvent(
        event_id="evt_iso_002",
        scenario_id="scn_iso_01",
        event_type="machine.overheated",
        simulation_time=now,
        recorded_at=now,
        source_system="scada",
        source_record_id="ROAST-01",
        idempotency_key="idemp_hash_iso_step1",  # Same idempotency key
        payload={"temp": 142.5},
    )
    db_session.add(evt_duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_factory_source_schema_separation(db_session: Session):
    """Verifies that factory_source operational tables exist separately from application tables."""
    raw_sup = SourceSupplier(
        id="SUP-RAW-ISO-01",
        name="Raw Cocoa Cooperative",
        country="Ghana",
        beans_variety="Criollo",
        historical_reliability=Decimal("0.9200"),
        lead_time_days=Decimal("8.00"),
        contract_price_usd_kg=Decimal("2.80"),
    )
    db_session.add(raw_sup)
    db_session.commit()

    fetched = db_session.get(SourceSupplier, "SUP-RAW-ISO-01")
    assert fetched is not None
    assert fetched.__table__.schema == "factory_source"


def test_regression_test_writes_do_not_persist_on_disk(db_session: Session):
    """Regression test proving that committed writes within db_session do NOT persist on disk."""
    canary = Supplier(
        id="supplier:erp:SUP-CANARY-ISOLATION",
        code="SUP-CANARY-ISO",
        name="Canary Test Supplier",
        country="Togo",
        material_type="raw_cocoa_beans",
    )
    db_session.add(canary)
    db_session.commit()

    # Visible within this session
    assert db_session.get(Supplier, "supplier:erp:SUP-CANARY-ISOLATION") is not None


def test_regression_verify_canary_absence_across_connection():
    """Verifies from a separate connection that the canary write was rolled back completely."""
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM public.suppliers WHERE id = 'supplier:erp:SUP-CANARY-ISOLATION';")
        ).scalar()
        assert count == 0, "Regression failure: Canary record was persisted on disk!"
