"""
Baseline Factory Seed Service.

Seeds a deterministic virtual cocoa-processing factory into:
1. 'factory_source' schema (raw ERP, WMS, SCADA, MES operational tables for Zetaris federation)
2. 'public' schema (initial application-owned baseline domain entities)

Idempotent: safe to run multiple times.
"""

from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    CustomerOrder,
    InventoryLot,
    MachineAsset,
    ProductionBatch,
    SourceBatch,
    SourceInventory,
    SourceMachineTelemetry,
    SourceOrder,
    SourceSupplier,
    Supplier,
)

# Reference timestamp for deterministic baseline clock: 2026-10-22 06:00:00 UTC
BASELINE_START_TIME = datetime(2026, 10, 22, 6, 0, 0, tzinfo=timezone.utc)


def seed_baseline_factory(session: Session) -> dict:
    """
    Seeds the baseline cocoa factory operational environment.
    Returns a summary of seeded entities.
    """
    # 1. Check if all baseline machines and supplier are already seeded
    existing_sup = session.execute(
        select(Supplier).where(Supplier.code == "SUP-101")
    ).scalar_one_or_none()

    existing_mill = session.execute(
        select(MachineAsset).where(MachineAsset.code == "MILL-01")
    ).scalar_one_or_none()

    if existing_sup and existing_mill:
        return {"status": "already_seeded", "supplier_id": existing_sup.id}

    def _add_if_missing(model_cls, pkey, obj):
        if not session.get(model_cls, pkey):
            session.add(obj)

    # -------------------------------------------------------------------------
    # Schema 'factory_source': Raw Operational Source Records
    # -------------------------------------------------------------------------
    raw_sup = SourceSupplier(
        id="SUP-101",
        name="Abidjan Cocoa Union",
        country="Cote d'Ivoire",
        beans_variety="Forastero",
        historical_reliability=Decimal("0.9500"),
        lead_time_days=Decimal("7.00"),
        contract_price_usd_kg=Decimal("2.50"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    _add_if_missing(SourceSupplier, "SUP-101", raw_sup)

    raw_inv = SourceInventory(
        lot_id="LOT-2026-001",
        sku="raw_cocoa_beans",
        supplier_code="SUP-101",
        location_bin="silo_01",
        quantity_kg=Decimal("15000.000"),
        moisture_percentage=Decimal("6.50"),
        qc_disposition="APPROVED",
        arrival_timestamp=BASELINE_START_TIME,
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    _add_if_missing(SourceInventory, "LOT-2026-001", raw_inv)

    telemetry_exists = session.execute(
        select(SourceMachineTelemetry).where(
            SourceMachineTelemetry.machine_code == "ROAST-01",
            SourceMachineTelemetry.timestamp == BASELINE_START_TIME,
        )
    ).scalar_one_or_none()

    if not telemetry_exists:
        raw_telemetry_roaster = SourceMachineTelemetry(
            machine_code="ROAST-01",
            timestamp=BASELINE_START_TIME,
            temperature_celsius=Decimal("115.00"),
            vibration_rms_mm_s=Decimal("1.80"),
            power_draw_kw=Decimal("45.00"),
            status_flag="NORMAL",
        )
        raw_telemetry_winnower = SourceMachineTelemetry(
            machine_code="WINNOW-01",
            timestamp=BASELINE_START_TIME,
            temperature_celsius=Decimal("24.00"),
            vibration_rms_mm_s=Decimal("1.20"),
            power_draw_kw=Decimal("22.00"),
            status_flag="NORMAL",
        )
        session.add_all([raw_telemetry_roaster, raw_telemetry_winnower])

    raw_batch = SourceBatch(
        batch_id="BATCH-101",
        recipe_code="roasted_nibs_standard",
        input_lot_id="LOT-2026-001",
        assigned_machine_code="ROAST-01",
        target_quantity_kg=Decimal("2500.000"),
        actual_quantity_kg=Decimal("0.000"),
        stage="SCHEDULED",
        planned_start=BASELINE_START_TIME,
        planned_end=datetime(2026, 10, 22, 8, 30, 0, tzinfo=timezone.utc),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    _add_if_missing(SourceBatch, "BATCH-101", raw_batch)

    raw_order = SourceOrder(
        order_number="ORD-9402",
        customer_account="Artisan Chocolatier BV",
        sku_requested="SKU-COUV-70",
        weight_kg=Decimal("2000.000"),
        order_value_usd=Decimal("17000.00"),
        penalty_rate_per_day=Decimal("350.00"),
        requested_delivery_date=datetime(2026, 10, 22, 14, 0, 0, tzinfo=timezone.utc),
        order_status="OPEN",
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    _add_if_missing(SourceOrder, "ORD-9402", raw_order)

    # ─────────────────────────────────────────────────────────────────────────
    # Schema 'public': Reconciled Baseline Domain Entities
    # ─────────────────────────────────────────────────────────────────────────
    app_sup = Supplier(
        id="supplier:erp:SUP-101",
        code="SUP-101",
        name="Abidjan Cocoa Union",
        country="Cote d'Ivoire",
        material_type="raw_cocoa_beans_forastero",
        reliability_score=Decimal("0.9500"),
        lead_time_days=Decimal("7.00"),
        contract_price_per_kg=Decimal("2.50"),
        currency="USD",
        is_active=True,
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    _add_if_missing(Supplier, "supplier:erp:SUP-101", app_sup)

    # 1. Raw & Intermediate Inventory Lots across production stages
    lot_raw = InventoryLot(
        id="inventory_lot:wms:LOT-2026-001",
        lot_code="LOT-2026-001",
        material_type="raw_cocoa_beans",
        supplier_id="supplier:erp:SUP-101",
        storage_location="silo_01",
        initial_quantity_kg=Decimal("15000.000"),
        current_quantity_kg=Decimal("15000.000"),
        unit_cost=Decimal("2.50"),
        moisture_pct=Decimal("6.50"),
        quality_status="approved",
        received_at=BASELINE_START_TIME,
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    lot_nibs = InventoryLot(
        id="inventory_lot:wms:LOT-NIBS-01",
        lot_code="LOT-NIBS-01",
        material_type="roasted_cocoa_nibs",
        supplier_id="supplier:erp:SUP-101",
        storage_location="hopper_nibs_01",
        initial_quantity_kg=Decimal("2500.000"),
        current_quantity_kg=Decimal("2500.000"),
        unit_cost=Decimal("3.20"),
        moisture_pct=Decimal("1.80"),
        quality_status="approved",
        received_at=BASELINE_START_TIME,
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    lot_liquor = InventoryLot(
        id="inventory_lot:wms:LOT-LIQUOR-01",
        lot_code="LOT-LIQUOR-01",
        material_type="cocoa_liquor",
        supplier_id="supplier:erp:SUP-101",
        storage_location="heated_tank_01",
        initial_quantity_kg=Decimal("2000.000"),
        current_quantity_kg=Decimal("2000.000"),
        unit_cost=Decimal("4.50"),
        moisture_pct=Decimal("1.20"),
        quality_status="approved",
        received_at=BASELINE_START_TIME,
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    lot_powder = InventoryLot(
        id="inventory_lot:wms:LOT-POWDER-01",
        lot_code="LOT-POWDER-01",
        material_type="cocoa_powder",
        supplier_id="supplier:erp:SUP-101",
        storage_location="dry_bin_04",
        initial_quantity_kg=Decimal("1800.000"),
        current_quantity_kg=Decimal("1800.000"),
        unit_cost=Decimal("5.10"),
        moisture_pct=Decimal("3.50"),
        quality_status="approved",
        received_at=BASELINE_START_TIME,
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    for lot in [lot_raw, lot_nibs, lot_liquor, lot_powder]:
        _add_if_missing(InventoryLot, lot.id, lot)

    # 2. Complete 8-Machine Processing Assets covering all cocoa stages
    m_clean = MachineAsset(
        id="machine:plant:CLEAN-01",
        code="CLEAN-01",
        name="Raw Bean Destoner & Air Cleaner",
        station="cleaning",
        rated_capacity_kg_h=Decimal("2000.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("22.00"),
        current_vibration_rms=Decimal("0.80"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    m_roaster1 = MachineAsset(
        id="machine:plant:ROAST-01",
        code="ROAST-01",
        name="Industrial Drum Roaster 1",
        station="roasting",
        rated_capacity_kg_h=Decimal("1200.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("115.00"),
        current_vibration_rms=Decimal("1.80"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    m_roaster2 = MachineAsset(
        id="machine:plant:ROAST-02",
        code="ROAST-02",
        name="Auxiliary Drum Roaster 2 (Line 2)",
        station="roasting",
        rated_capacity_kg_h=Decimal("900.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("110.00"),
        current_vibration_rms=Decimal("1.50"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    m_winnower = MachineAsset(
        id="machine:plant:WINNOW-01",
        code="WINNOW-01",
        name="Air Classifier Winnowing Unit",
        station="winnowing",
        rated_capacity_kg_h=Decimal("1000.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("24.00"),
        current_vibration_rms=Decimal("1.20"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    m_mill = MachineAsset(
        id="machine:plant:MILL-01",
        code="MILL-01",
        name="Triple-Roll Fine Liquor Grinder",
        station="milling",
        rated_capacity_kg_h=Decimal("800.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("45.00"),
        current_vibration_rms=Decimal("1.60"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    m_press = MachineAsset(
        id="machine:plant:PRESS-01",
        code="PRESS-01",
        name="Hydraulic Cocoa Butter Press",
        station="pressing",
        rated_capacity_kg_h=Decimal("600.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("65.00"),
        current_vibration_rms=Decimal("2.10"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    m_pulv = MachineAsset(
        id="machine:plant:PULV-01",
        code="PULV-01",
        name="Centrifugal Cocoa Cake Pulverizer",
        station="pulverizing",
        rated_capacity_kg_h=Decimal("500.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("30.00"),
        current_vibration_rms=Decimal("1.40"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    m_pack = MachineAsset(
        id="machine:plant:PACK-01",
        code="PACK-01",
        name="Automated Packaging & Dispatch Line",
        station="packaging",
        rated_capacity_kg_h=Decimal("1500.00"),
        operating_status="idle",
        health_score=Decimal("1.0000"),
        current_temp_c=Decimal("21.00"),
        current_vibration_rms=Decimal("0.50"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    for m in [m_clean, m_roaster1, m_roaster2, m_winnower, m_mill, m_press, m_pulv, m_pack]:
        _add_if_missing(MachineAsset, m.id, m)

    # 3. Multi-Stage Production Batches
    b101 = ProductionBatch(
        id="production_batch:mes:BATCH-101",
        batch_code="BATCH-101",
        recipe_name="dark_couverture_70",
        target_product="roasted_nibs",
        planned_quantity_kg=Decimal("2500.000"),
        actual_quantity_kg=Decimal("0.000"),
        input_lot_id="inventory_lot:wms:LOT-2026-001",
        machine_id="machine:plant:ROAST-01",
        status="scheduled",
        scheduled_start=BASELINE_START_TIME,
        scheduled_end=datetime(2026, 10, 22, 8, 30, 0, tzinfo=timezone.utc),
        delay_hours=Decimal("0.00"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    b102 = ProductionBatch(
        id="production_batch:mes:BATCH-102",
        batch_code="BATCH-102",
        recipe_name="standard_winnowing",
        target_product="clean_nibs",
        planned_quantity_kg=Decimal("2000.000"),
        actual_quantity_kg=Decimal("0.000"),
        input_lot_id="inventory_lot:wms:LOT-NIBS-01",
        machine_id="machine:plant:WINNOW-01",
        status="scheduled",
        scheduled_start=BASELINE_START_TIME,
        scheduled_end=datetime(2026, 10, 22, 8, 45, 0, tzinfo=timezone.utc),
        delay_hours=Decimal("0.00"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    b103 = ProductionBatch(
        id="production_batch:mes:BATCH-103",
        batch_code="BATCH-103",
        recipe_name="liquor_fine_grind",
        target_product="cocoa_liquor",
        planned_quantity_kg=Decimal("1800.000"),
        actual_quantity_kg=Decimal("0.000"),
        input_lot_id="inventory_lot:wms:LOT-NIBS-01",
        machine_id="machine:plant:MILL-01",
        status="scheduled",
        scheduled_start=BASELINE_START_TIME,
        scheduled_end=datetime(2026, 10, 22, 9, 30, 0, tzinfo=timezone.utc),
        delay_hours=Decimal("0.00"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    b104 = ProductionBatch(
        id="production_batch:mes:BATCH-104",
        batch_code="BATCH-104",
        recipe_name="pure_press_extraction",
        target_product="cocoa_butter",
        planned_quantity_kg=Decimal("1500.000"),
        actual_quantity_kg=Decimal("0.000"),
        input_lot_id="inventory_lot:wms:LOT-LIQUOR-01",
        machine_id="machine:plant:PRESS-01",
        status="scheduled",
        scheduled_start=BASELINE_START_TIME,
        scheduled_end=datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc),
        delay_hours=Decimal("0.00"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    b105 = ProductionBatch(
        id="production_batch:mes:BATCH-105",
        batch_code="BATCH-105",
        recipe_name="cocoa_powder_micronize",
        target_product="cocoa_powder",
        planned_quantity_kg=Decimal("1200.000"),
        actual_quantity_kg=Decimal("0.000"),
        input_lot_id="inventory_lot:wms:LOT-POWDER-01",
        machine_id="machine:plant:PACK-01",
        status="scheduled",
        scheduled_start=BASELINE_START_TIME,
        scheduled_end=datetime(2026, 10, 22, 11, 0, 0, tzinfo=timezone.utc),
        delay_hours=Decimal("0.00"),
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    for b in [b101, b102, b103, b104, b105]:
        _add_if_missing(ProductionBatch, b.id, b)

    # 4. Customer Orders linked to Batches with explicit SLA deadlines
    o9402 = CustomerOrder(
        id="customer_order:erp:ORD-9402",
        order_code="ORD-9402",
        customer_name="Artisan Chocolatier BV",
        product_sku="SKU-COUV-70",
        quantity_kg=Decimal("2000.000"),
        unit_price=Decimal("8.50"),
        total_value=Decimal("17000.00"),
        penalty_per_day_late=Decimal("350.00"),
        priority="high",
        status="pending",
        deadline=datetime(2026, 10, 22, 14, 0, 0, tzinfo=timezone.utc),
        assigned_batch_id="production_batch:mes:BATCH-101",
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    o9405 = CustomerOrder(
        id="customer_order:erp:ORD-9405",
        order_code="ORD-9405",
        customer_name="Brussels Confectionery SA",
        product_sku="SKU-BUTTER-PRIME",
        quantity_kg=Decimal("1500.000"),
        unit_price=Decimal("11.00"),
        total_value=Decimal("16500.00"),
        penalty_per_day_late=Decimal("450.00"),
        priority="medium",
        status="pending",
        deadline=datetime(2026, 10, 22, 16, 30, 0, tzinfo=timezone.utc),
        assigned_batch_id="production_batch:mes:BATCH-104",
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    o9408 = CustomerOrder(
        id="customer_order:erp:ORD-9408",
        order_code="ORD-9408",
        customer_name="Alpine Sweets AG",
        product_sku="SKU-POWDER-12",
        quantity_kg=Decimal("1200.000"),
        unit_price=Decimal("7.20"),
        total_value=Decimal("8640.00"),
        penalty_per_day_late=Decimal("250.00"),
        priority="low",
        status="pending",
        deadline=datetime(2026, 10, 22, 19, 0, 0, tzinfo=timezone.utc),
        assigned_batch_id="production_batch:mes:BATCH-105",
        created_at=BASELINE_START_TIME,
        updated_at=BASELINE_START_TIME,
    )
    for o in [o9402, o9405, o9408]:
        _add_if_missing(CustomerOrder, o.id, o)

    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        existing = session.execute(
            select(Supplier).where(Supplier.code == "SUP-101")
        ).scalar_one_or_none()
        if existing:
            return {"status": "already_seeded", "supplier_id": existing.id}
        raise

    return {
        "status": "seeded",
        "supplier_id": app_sup.id,
        "lot_id": lot_raw.id,
        "machines": [m_roaster1.id, m_winnower.id, m_clean.id, m_roaster2.id, m_mill.id, m_press.id, m_pulv.id, m_pack.id],
        "batch_id": b101.id,
        "order_id": o9402.id,
        "all_lots": [lot_raw.id, lot_nibs.id, lot_liquor.id, lot_powder.id],
        "all_batches": [b101.id, b102.id, b103.id, b104.id, b105.id],
        "all_orders": [o9402.id, o9405.id, o9408.id],
    }
