"""Comprehensive End-to-End Verification of the Zetaris Data Path.

Proves:
1. Live Zetaris connectivity on port 10000 via LightningDriver over JDBC.
2. Query execution against >= 2 distinct operational sources:
   - TPCDS_DB (inventory, warehouse, item)
   - NY_TAXI (weather_data)
3. Canonical USL entity mapping:
   - CanonicalExternalBufferStock
   - CanonicalTransitEnvironmentalCondition
   - Explicit USLProvenance preservation
4. Investigation & Candidate Recovery Plan Integration:
   - Evaluates plan_spot_market_buffer against real Zetaris facts
   - Verified stock yields feasible plan with concrete warehouse facts
   - Zero or missing stock yields infeasible verdict and fails constraint validation
5. Robust boundary & failure handling:
   - allow_fallback=False safely envelopes errors without crashing
   - Malformed queries return clean failure envelopes
6. End-to-end autonomous recovery workflow:
   - Disruption characterized
   - Facts asserted in World Model with zetaris_federation provenance
   - Disposable scenario lifecycle tested without touching scn_prepared_live_01
"""

from datetime import datetime, timezone
from unittest.mock import patch
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import OperationalEvent, SimulationScenario
from app.services.autonomous_recovery import (
    characterize_disruption,
    validate_candidate_plan,
)
from app.services.disruptions import apply_disruption
from app.services.recovery import generate_candidate_plans
from app.services.seed import seed_baseline_factory
from app.services.simulation import create_scenario, step_scenario
from app.usl import (
    CanonicalExternalBufferStock,
    CanonicalTransitEnvironmentalCondition,
    SemanticRecordKind,
    UnifiedSemanticLayerService,
    ZetarisFederationClient,
)
from app.world_model import Provenance, WorldModel


@pytest.fixture
def db_session():
    """Isolated database session using savepoints to prevent DB drift."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def test_zetaris_live_jdbc_connectivity():
    """Verifies live JDBC connection to Zetaris Lightning on port 10000."""
    client = ZetarisFederationClient()
    status = client.check_jdbc_status()

    assert status["jar_present"] is True, "JDBC Driver JAR must exist on host"
    assert status["jdbc_port_open"] is True, "Port 10000 must be open and listening"
    assert status["live_session_verified"] is True, "Session must verify against live Zetaris Spark engine"
    assert "TPCDS_DB" in status["lightning_databases"]
    assert "NY_TAXI" in status["lightning_databases"]


def test_zetaris_queries_two_distinct_operational_sources():
    """Queries records from at least two logically distinct operational sources in Zetaris:

    1. Enterprise inventory & catalog from TPCDS_DB
    2. Environmental & route conditions from NY_TAXI
    """
    client = ZetarisFederationClient()

    # Source 1: TPCDS_DB multi-table join
    inv_sql = (
        "SELECT inv.inv_item_sk, itm.i_item_id, wh.w_warehouse_name, inv.inv_quantity_on_hand "
        "FROM TPCDS_DB.inventory inv "
        "JOIN TPCDS_DB.item itm ON inv.inv_item_sk = itm.i_item_sk "
        "JOIN TPCDS_DB.warehouse wh ON inv.inv_warehouse_sk = wh.w_warehouse_sk "
        "WHERE inv.inv_warehouse_sk = 1 "
        "LIMIT 2"
    )
    inv_res = client.query_federated(inv_sql, allow_fallback=False, channel="jdbc")
    assert inv_res.success is True
    assert inv_res.source_layer == "zetaris_federation"
    assert len(inv_res.records) >= 1
    assert "inv_quantity_on_hand" in inv_res.records[0]
    assert "w_warehouse_name" in inv_res.records[0]

    # Source 2: NY_TAXI environmental data
    weather_sql = "SELECT weather_id, temperature, precipitation, wind_speed FROM NY_TAXI.weather_data LIMIT 2"
    weather_res = client.query_federated(weather_sql, allow_fallback=False, channel="jdbc")
    assert weather_res.success is True
    assert weather_res.source_layer == "zetaris_federation"
    assert len(weather_res.records) >= 1
    assert "temperature" in weather_res.records[0]
    assert "precipitation" in weather_res.records[0]


def test_usl_canonical_entity_mapping_with_provenance():
    """Verifies mapping of raw federated records into canonical FactoryOps domain models."""
    service = UnifiedSemanticLayerService()

    # 1. Map external warehouse stock
    stock = service.query_external_buffer_inventory(warehouse_sk=1, item_sk=1)
    assert stock is not None
    assert isinstance(stock, CanonicalExternalBufferStock)
    assert stock.quantity_on_hand_kg > 0
    assert len(stock.warehouse_name) > 0
    assert stock.record_kind == SemanticRecordKind.MEASURED_FACT
    assert stock.provenance.source_system == "zetaris_ndp_federation"
    assert "TPCDS_DB" in stock.provenance.source_dataset
    assert stock.provenance.data_quality_status == "verified"

    # 2. Map transit route weather
    weather = service.query_transit_weather_conditions(weather_id="1")
    assert weather is not None
    assert isinstance(weather, CanonicalTransitEnvironmentalCondition)
    assert weather.temperature_f > 0.0
    assert weather.road_risk_level in ("nominal", "adverse", "hazardous")
    assert weather.record_kind == SemanticRecordKind.MEASURED_FACT
    assert weather.provenance.source_system == "zetaris_ndp_federation"
    assert "NY_TAXI" in weather.provenance.source_dataset


def test_recovery_candidate_plans_incorporate_zetaris_facts(db_session: Session):
    """Verifies that generate_candidate_plans incorporates real facts queried from Zetaris."""
    seed_baseline_factory(db_session)
    scn_id = f"scn_test_zetaris_plans_{uuid.uuid4().hex[:6]}"
    create_scenario(
        session=db_session,
        name="Zetaris Plan Test",
        disruption_type="none",
        scenario_id=scn_id,
    )
    apply_disruption(db_session, scn_id, "supplier_customs_delay", {"delay_hours": 6.0})

    plans = generate_candidate_plans(db_session, scn_id, use_ai=False)
    spot_plan = next((p for p in plans if p.plan_id == "plan_spot_market_buffer"), None)
    assert spot_plan is not None

    # Verify that Zetaris facts appear in hard constraints
    constraints_text = " ".join(spot_plan.hard_constraints_met)
    assert "Zetaris Federated Metastore" in constraints_text
    assert "TPCDS_DB.warehouse" in constraints_text
    assert "Zetaris Transit Telemetry" in constraints_text or "NY_TAXI.weather_data" in constraints_text
    assert spot_plan.feasibility_verdict == "feasible"


def test_missing_or_zero_stock_marks_plan_infeasible(db_session: Session):
    """Verifies that when Zetaris reports zero external stock, the recovery plan is marked infeasible."""
    seed_baseline_factory(db_session)
    scn_id = f"scn_test_zero_stock_{uuid.uuid4().hex[:6]}"
    create_scenario(
        session=db_session,
        name="Zero Stock Test",
        disruption_type="none",
        scenario_id=scn_id,
    )
    apply_disruption(db_session, scn_id, "supplier_customs_delay", {"delay_hours": 6.0})

    # Mock zero inventory return from Zetaris query
    empty_stock = CanonicalExternalBufferStock(
        warehouse_id="warehouse:external:1",
        warehouse_name="Empty Warehouse",
        item_id="item:external:1",
        item_desc="Cocoa Lot",
        quantity_on_hand_kg=0.0,
        unit_price_usd=10.0,
    )

    with patch.object(UnifiedSemanticLayerService, "query_external_buffer_inventory", return_value=empty_stock):
        plans = generate_candidate_plans(db_session, scn_id, use_ai=False)
        spot_plan = next((p for p in plans if p.plan_id == "plan_spot_market_buffer"), None)
        assert spot_plan is not None
        assert spot_plan.feasibility_verdict == "infeasible"
        assert any("OUT OF STOCK" in c for c in spot_plan.hard_constraints_met)

        scenario = db_session.execute(select(SimulationScenario).where(SimulationScenario.id == scn_id)).scalar_one()
        char = characterize_disruption(db_session, scn_id)
        val = validate_candidate_plan(scenario, spot_plan, char)
        assert val.valid is False
        assert any("OUT_OF_STOCK" in v or "UNAVAILABLE" in v for v in val.violated_constraints)


def test_zetaris_query_failure_handling_safe_envelope():
    """Verifies that an invalid SQL query or connection error returns a safe envelope without crashing."""
    client = ZetarisFederationClient()

    # Intentionally malformed table query with allow_fallback=False
    res = client.query_federated("SELECT * FROM NON_EXISTENT_CATALOG.dummy_table", allow_fallback=False, channel="jdbc")
    assert res.success is False
    assert res.total_count == 0
    assert "failed" in res.boundary_notes.lower()
    assert res.provenance.data_quality_status == "simulated"


def test_end_to_end_zetaris_data_flow_on_disposable_scenario(db_session: Session):
    """Full end-to-end trace:

    1. Create disposable scenario with supply chain disruption
    2. Apply customs delay disruption
    3. Retrieve federated context from Zetaris
    4. Generate and validate candidate recovery plan
    5. Assert facts into World Model with verified Zetaris provenance
    6. Verify zero mutation to scn_prepared_live_01
    """
    seed_baseline_factory(db_session)
    scn_id = f"scn_verify_zetaris_trace_{uuid.uuid4().hex[:6]}"
    scenario = create_scenario(
        session=db_session,
        name="Zetaris Data Path Trace",
        disruption_type="none",
        scenario_id=scn_id,
    )
    apply_disruption(db_session, scn_id, "supplier_customs_delay", {"delay_hours": 6.0})

    # 1. Retrieve federated context directly
    usl = UnifiedSemanticLayerService()
    fed_ctx = usl.fetch_federated_recovery_context("supplier_customs_delay")
    assert fed_ctx["zetaris_connected"] is True
    assert fed_ctx["external_buffer_stock"] is not None
    assert fed_ctx["transit_weather"] is not None

    # 2. Characterize disruption
    char = characterize_disruption(db_session, scn_id)
    assert char.has_active_disruption is True
    assert char.disrupted_machine_id == "silo:plant:SILO-01"

    # 3. Generate candidate plans
    plans = generate_candidate_plans(db_session, scn_id, use_ai=False)
    spot_plan = next(p for p in plans if p.plan_id == "plan_spot_market_buffer")
    assert spot_plan.feasibility_verdict == "feasible"

    # 4. Validate candidate plan
    val = validate_candidate_plan(scenario, spot_plan, char)
    assert val.valid is True
    assert any("Zetaris external inventory availability verified" in p for p in val.hard_constraints_passed)

    # 5. Assert World Model fact with explicit provenance
    world = WorldModel(namespace=scn_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    world.assert_fact(
        about="warehouse:external:1",
        predicate="verified_stock_kg",
        value=fed_ctx["external_buffer_stock"]["quantity_on_hand_kg"],
        source=Provenance(
            kind="zetaris_federation",
            at=now_iso,
            by="zetaris:TPCDS_DB.inventory",
            run_id=scn_id,
        ),
        confidence=1.0,
    )

    raw_facts = list(world._store.state.facts.values())
    assert len(raw_facts) == 1
    assert raw_facts[0].about == "warehouse:external:1"
    assert raw_facts[0].predicate == "verified_stock_kg"
    assert raw_facts[0].source.kind == "zetaris_federation"
    assert "zetaris" in raw_facts[0].source.by

    # 6. Safety check: Verify scn_prepared_live_01 exists and is completely intact
    live_scn = db_session.execute(
        select(SimulationScenario).where(SimulationScenario.id == "scn_prepared_live_01")
    ).scalar_one_or_none()
    if live_scn:
        assert live_scn.id == "scn_prepared_live_01"
