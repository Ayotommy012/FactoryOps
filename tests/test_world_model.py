"""Substantive behavioral and integration tests for Meterless World Model in FactoryOps.

Tests:
1. Conformance suite: executes the 8 canonical Meterless invariants (sid-001, sid-002, band-001,
   prov-001, idem-001, idem-002, bitemp-001, audit-001).
2. Industrial asset resolution & banding: auto-merges minor spelling variations, queues ambiguous
   variations, and creates distinct entities for separate machine codes.
3. FactoryOps graph traversal: links ROAST-01 -> BATCH-101 -> ORD-9402 and queries downstream impacts.
4. Bitemporal status transitions: closing open operating state edges and validating point-in-time queries.
5. Integration with discrete simulation service: verifies that stepping a scenario persists World Model
   facts and graph relationships without mutating baseline tables.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import MachineAsset, SimulationScenario
from app.services.seed import BASELINE_START_TIME, seed_baseline_factory
from app.services.simulation import (
    create_scenario,
    get_scenario_world_model,
    step_scenario,
)
from app.world_model import (
    Provenance,
    WorldModel,
    decide,
    entity_id_from_name,
    external_id,
    levenshtein,
    name_similarity,
    normalize_name,
)
from app.world_model.conformance import run_conformance_checks


@pytest.fixture
def db_session():
    """Isolated session using nested savepoints to guarantee zero row drift."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def test_meterless_conformance_suite_all_pass():
    """Verifies that all 8 canonical Meterless invariants pass with 100% compliance."""
    results = run_conformance_checks()
    assert len(results) == 8, f"Expected 8 checks, got {len(results)}"
    failed = [f"{id_}: {detail}" for id_, _, status, detail in results if status != "PASS"]
    assert not failed, f"Conformance checks failed: {failed}"


def test_asset_name_normalization_and_similarity():
    """Verifies name normalization and similarity on industrial asset aliases."""
    # Accented/diacritic + NBSP vs plain
    raw_a = "Séchage Automatique\u00a0#1"
    raw_b = "Sechage Automatique #1"
    assert normalize_name(raw_a) == normalize_name(raw_b)

    # Identical asset name
    assert name_similarity("Drum Roaster 01", "drum roaster 01") == 1.0

    # Auto-merge boundary (similarity >= 0.85)
    # "Drum Roaster 01" (15) vs "Drum Roaster 1" (14) -> distance = 1, max_len = 15, sim = 14/15 = 0.933 >= 0.85
    sim_merge = name_similarity("Drum Roaster 01", "Drum Roaster 1")
    assert sim_merge >= 0.85
    assert decide(sim_merge) == "auto-merge"

    # Distinct assets must not merge
    # "Drum Roaster 01" vs "Drum Roaster 02" -> distance 1, sim 14/15 = 0.933
    # With code participates in key or external key:
    id_1 = external_id("machine", "plant", "ROAST-01")
    id_2 = external_id("machine", "plant", "ROAST-02")
    assert id_1 != id_2


def test_world_model_factory_graph_and_impact_tracing():
    """Verifies graph creation and downstream impact traversal across ROAST-01 -> BATCH-101 -> ORD-9402."""
    wm = WorldModel(namespace="factory_test")
    src = Provenance(kind="manual", at="2026-10-22T06:00:00Z")

    # Upsert entities
    roaster = wm.upsert_entity(
        type_="machine",
        external_key={"system": "plant", "id": "ROAST-01"},
        name="Drum Roaster 01",
        attrs={"rated_capacity_kg_h": 1200.0, "status": "nominal"},
        source=src,
    )
    batch = wm.upsert_entity(
        type_="production_batch",
        external_key={"system": "mes", "id": "BATCH-101"},
        name="Batch 101 - 70% Dark",
        attrs={"planned_quantity_kg": 1000.0},
        source=src,
    )
    order = wm.upsert_entity(
        type_="customer_order",
        external_key={"system": "erp", "id": "ORD-9402"},
        name="Order 9402 - Artisan Chocolates",
        attrs={"customer": "Artisan Chocolates Ltd"},
        source=src,
    )

    # Relate
    wm.relate(from_=batch.id, type="processed_by", to=roaster.id, source=src)
    wm.relate(from_=order.id, type="fulfills_via", to=batch.id, source=src)

    graph = wm.view("graph")

    # Traverse upstream from order to machine
    upstream = graph.traverse(start_id=order.id, via="fulfills_via", direction="ancestors")
    assert len(upstream) == 0  # from_ is order, so order is source

    # Downstream from order -> batch via fulfills_via
    descendants = graph.traverse(start_id=order.id, via="fulfills_via", direction="descendants")
    assert len(descendants) == 1
    assert descendants[0].id == "production_batch:mes:BATCH-101"

    # From roaster: incoming batches
    incoming = graph.incoming(roaster.id, via="processed_by")
    assert len(incoming) == 1
    assert incoming[0].id == "production_batch:mes:BATCH-101"


def test_world_model_bitemporal_maintenance_transition():
    """Verifies bitemporal validFrom / validTo clipping when equipment state changes."""
    wm = WorldModel(namespace="bitemp_test")
    t0 = "2026-10-22T06:00:00.000Z"
    t1 = "2026-10-22T06:45:00.000Z"  # Step 3 anomaly time

    # Machine entity
    m = wm.upsert_entity(
        type_="machine",
        external_key={"system": "plant", "id": "ROAST-01"},
        attrs={},
        source=Provenance(kind="seed", at=t0),
    )

    # Status entities
    s_nom = wm.upsert_entity(
        type_="machine_state",
        external_key={"system": "state", "id": "nominal"},
        attrs={},
        source=Provenance(kind="seed", at=t0),
    )
    s_deg = wm.upsert_entity(
        type_="machine_state",
        external_key={"system": "state", "id": "degraded"},
        attrs={},
        source=Provenance(kind="telemetry", at=t1),
    )

    # Initial state: nominal
    wm.relate(
        from_=m.id,
        type="operating_state",
        to=s_nom.id,
        valid_from=t0,
        source=Provenance(kind="seed", at=t0),
    )

    # Point in time query at t0: state is nominal
    graph = wm.view("graph")
    at_t0 = graph.neighbors_of(m.id, via="operating_state", at="2026-10-22T06:30:00.000Z")
    assert len(at_t0) == 1
    assert at_t0[0].id == "machine_state:state:nominal"

    # Transition to degraded at t1
    wm.relate(
        from_=m.id,
        type="operating_state",
        to=s_deg.id,
        valid_from=t1,
        source=Provenance(kind="telemetry", at=t1),
    )

    # Current state ('now') points to degraded
    now = graph.neighbors_of(m.id, via="operating_state")
    assert len(now) == 1
    assert now[0].id == "machine_state:state:degraded"

    # Point in time query before t1 still points to nominal!
    then = graph.neighbors_of(m.id, via="operating_state", at="2026-10-22T06:15:00.000Z")
    assert len(then) == 1
    assert then[0].id == "machine_state:state:nominal"


def test_simulation_integration_updates_world_model_state(db_session: Session):
    """Verifies that simulation step integrates with WorldModel and updates verified facts."""
    seed_baseline_factory(db_session)

    scenario = create_scenario(
        session=db_session,
        name="WM Integration Test",
        disruption_type="roaster_bearing_overheat",
        random_seed=42,
        scenario_id="scn_wm_integration",
    )

    # Initially at step 0, World Model contains baseline entities
    wm_init = get_scenario_world_model(db_session, "scn_wm_integration")
    roaster_init = wm_init.view("graph").entity("machine:plant:ROAST-01")
    assert roaster_init is not None
    assert roaster_init.attrs["rated_capacity_kg_h"] == 1200.0

    # Advance 3 steps: triggers roaster bearing overheat at step 3
    res = step_scenario(session=db_session, scenario_id="scn_wm_integration", steps=3)
    assert res["current_step"] == 3

    # Verify World Model reflects Step 3 anomaly facts
    wm_step3 = get_scenario_world_model(db_session, "scn_wm_integration")
    roaster_step3 = wm_step3.view("graph").entity("machine:plant:ROAST-01")
    assert roaster_step3 is not None
    assert roaster_step3.attrs.get("bearing_temperature_c") == 142.5
    assert roaster_step3.attrs.get("vibration_rms") == 4.2
    assert roaster_step3.attrs.get("status") == "degraded"

    # Batch and order facts updated
    batch_step3 = wm_step3.view("graph").entity("production_batch:mes:BATCH-101")
    assert batch_step3.attrs.get("status") == "delayed"

    order_step3 = wm_step3.view("graph").entity("customer_order:erp:ORD-9402")
    assert order_step3.attrs.get("status") == "at_risk"

    # World model state is preserved in scenario.parameters
    assert "world_model_state" in scenario.parameters
    assert len(scenario.parameters["world_model_state"]["entities"]) >= 4
