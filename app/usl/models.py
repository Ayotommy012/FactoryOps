"""Unified Semantic Layer (USL) Domain Models.

Defines canonical entity representations, measurement units, provenance anchors,
and explicit distinctions between measured facts and simulated decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


class SemanticRecordKind(str, Enum):
    MEASURED_FACT = "measured_fact"          # Genuine empirical observations (e.g. NASA IMS bearing sensor)
    SIMULATED_SCENARIO = "simulated_scenario" # Scenario progression, scheduled orders, synthetic disruptions
    DERIVED_ANALYSIS = "derived_analysis"     # Calculated SLA penalties, throughput rates, buffers
    OPERATOR_DECISION = "operator_decision"   # Human approval/rejection audit entries


@dataclass
class USLProvenance:
    source_system: str
    source_dataset: str
    original_record_id: str
    source_url_or_ref: str
    observed_at: str
    ingested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data_quality_status: Literal["verified", "estimated", "simulated"] = "verified"
    transformation_notes: str = ""


@dataclass
class CanonicalMachineCondition:
    entity_id: str                          # e.g. 'machine:plant:ROAST-01'
    source_bearing_id: str                   # e.g. 'bearing_1'
    timestamp: str
    vibration_rms_mms: float                 # Normalized velocity RMS in mm/s
    temperature_c: float                     # Temperature in Celsius
    operational_status: str                  # nominal | warning | degraded | critical_failure
    record_kind: SemanticRecordKind
    provenance: USLProvenance
    raw_attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalProductionBatch:
    batch_id: str                           # e.g. 'production_batch:mes:BATCH-101'
    batch_code: str
    recipe_name: str
    planned_quantity_kg: float
    actual_quantity_kg: float
    remaining_quantity_kg: float
    status: str
    assigned_machine_id: str
    record_kind: SemanticRecordKind
    provenance: USLProvenance


@dataclass
class CanonicalCustomerOrder:
    order_id: str                           # e.g. 'customer_order:erp:ORD-9402'
    order_code: str
    customer_name: str
    product_sku: str
    quantity_kg: float
    deadline: str
    status: str
    assigned_batch_id: str
    record_kind: SemanticRecordKind
    provenance: USLProvenance


@dataclass
class CanonicalExternalBufferStock:
    """Canonical representation of external warehouse stock queried via Zetaris NDP."""
    warehouse_id: str                       # e.g. 'warehouse:external:AAAAAAAABAAAAAAA'
    warehouse_name: str                     # e.g. 'Conventional childr' / Amsterdam Terminal
    item_id: str                            # e.g. 'item:external:AAAAAAAABAAAAAAA'
    item_desc: str                          # Description / specification of item
    quantity_on_hand_kg: float              # Available inventory quantity
    unit_price_usd: float                   # Unit cost in USD
    record_kind: SemanticRecordKind = SemanticRecordKind.MEASURED_FACT
    provenance: USLProvenance = field(
        default_factory=lambda: USLProvenance(
            source_system="zetaris_ndp_federation",
            source_dataset="TPCDS_DB.inventory+warehouse+item",
            original_record_id="",
            source_url_or_ref="jdbc:zetaris:lightning@localhost:10000",
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
    )


@dataclass
class CanonicalTransitEnvironmentalCondition:
    """Canonical representation of transit route weather queried via Zetaris NDP."""
    location_id: str                        # e.g. 'loc:weather:1'
    temperature_f: float                    # Ambient temperature in Fahrenheit
    precipitation_in: float                 # Precipitation in inches
    wind_speed_mph: float                   # Wind speed in mph
    road_risk_level: str                    # 'nominal' | 'adverse' | 'hazardous'
    record_kind: SemanticRecordKind = SemanticRecordKind.MEASURED_FACT
    provenance: USLProvenance = field(
        default_factory=lambda: USLProvenance(
            source_system="zetaris_ndp_federation",
            source_dataset="NY_TAXI.weather_data",
            original_record_id="",
            source_url_or_ref="jdbc:zetaris:lightning@localhost:10000",
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
    )


@dataclass
class USLMappingDefinition:
    source_entity: str
    canonical_entity: str
    key_mapping: Dict[str, str]
    unit_conversions: Dict[str, str]
    missing_value_policy: str
    timestamp_semantics: str
    provenance_rule: str
    record_kind: SemanticRecordKind = SemanticRecordKind.MEASURED_FACT
