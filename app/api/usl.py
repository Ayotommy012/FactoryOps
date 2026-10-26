"""API Router for Unified Semantic Layer (USL) & Data Federation."""

from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.simulations import get_db
from app.services.simulation import get_scenario_world_model
from app.usl import (
    NASABearingDataLoader,
    UnifiedSemanticLayerService,
    ZetarisFederationClient,
)

router = APIRouter(prefix="/usl", tags=["Unified Semantic Layer"])
usl_service = UnifiedSemanticLayerService()


class FederatedQueryRequest(BaseModel):
    sql: str = Field(..., description="SQL query to execute against federated data sources or PostgreSQL fallback")
    allow_fallback: bool = Field(default=True, description="Whether to allow transparent fallback to PostgreSQL factory_source schema")
    channel: Literal["auto", "jdbc", "rest"] = Field(default="auto", description="Execution channel: 'auto', 'jdbc', or 'rest'")


@router.get("/mappings")
def get_semantic_mappings():
    """Returns official USL entity mappings, unit conversions, and provenance rules."""
    mappings = usl_service.get_mapping_manifesto()
    return {
        "layer": "Unified Semantic Layer (USL)",
        "mapping_count": len(mappings),
        "mappings": mappings,
    }


@router.get("/nasa-bearing")
def get_nasa_bearing_data():
    """Returns genuine benchmark machinery observations from the NASA IMS Bearing dataset."""
    loader = NASABearingDataLoader()
    records = loader.load_records()
    return {
        "dataset": "NASA / UC Intelligent Maintenance Systems (IMS) Bearing Dataset",
        "record_count": len(records),
        "records": records,
    }


@router.get("/nasa-bearing/timeline")
def get_nasa_bearing_timeline():
    """Returns chronological run-to-failure degradation timeline with baseline velocity ratios."""
    loader = NASABearingDataLoader()
    timeline = loader.get_timeline()
    return {
        "dataset": "NASA / UC IMS Bearing Run-to-Failure Benchmark (ISO 10816)",
        "progression_count": len(timeline),
        "timeline": timeline,
    }


@router.get("/nasa-bearing/{condition}")
def get_nasa_bearing_by_condition(condition: str):
    """Retrieves a specific canonical machine condition from NASA IMS (nominal, warning, degraded, critical_failure)."""
    loader = NASABearingDataLoader()
    record = loader.get_record_by_condition(condition.lower())
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition record '{condition}' not found in benchmark dataset.",
        )
    return {
        "condition": condition,
        "record": record,
    }


@router.get("/zetaris/status")
def get_zetaris_status():
    """Returns Zetaris REST API connectivity, host JDBC driver presence, and metastore boundary documentation."""
    client = ZetarisFederationClient()
    connected = client.check_connection()
    jdbc_status = client.check_jdbc_status()
    return {
        "connected": connected,
        "base_url": client.base_url,
        "jdbc_environment": jdbc_status,
        "boundary_summary": (
            "Zetaris REST query interface available on port 8888; host environment uses REST adapter "
            "to execute Spark SQL without JVM dependencies. JDBC JAR verified on disk for containerized deployments."
        ),
    }


@router.get("/zetaris-query")
def run_zetaris_federation_query(
    sql: str = Query(default="SELECT 1 as ping", description="SQL query to execute through Zetaris NDP"),
    channel: Literal["auto", "jdbc", "rest"] = Query(default="auto", description="Execution channel"),
):
    """Executes a federated query via Zetaris or documents the exact metastore boundary."""
    client = ZetarisFederationClient()
    result = client.query_federated(sql, allow_fallback=True, channel=channel)
    return result


@router.post("/federated-query")
def execute_federated_query(payload: FederatedQueryRequest):
    """Executes an arbitrary SQL query via Zetaris federation or transparent PostgreSQL fallback with full provenance."""
    client = ZetarisFederationClient()
    result = client.query_federated(payload.sql, allow_fallback=payload.allow_fallback, channel=payload.channel)
    return result


@router.get("/provenance/{scenario_id}")
def get_scenario_provenance(scenario_id: str, session: Session = Depends(get_db)):
    """Traces the full lineage of facts in the scenario World Model, separating measured facts from simulated decisions."""
    try:
        world = get_scenario_world_model(session, scenario_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scenario '{scenario_id}' not found: {e}")

    provenance_data = usl_service.trace_scenario_provenance(world)
    return {
        "scenario_id": scenario_id,
        "layer": "Unified Semantic Layer (USL)",
        "provenance_trace": provenance_data,
    }

