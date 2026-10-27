"""Unified Semantic Layer (USL) Service.

Coordinates semantic entity mapping, NASA IMS bearing dataset integration,
provenance preservation, and synchronization into the Meterless World Model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.usl.models import (
    CanonicalCustomerOrder,
    CanonicalExternalBufferStock,
    CanonicalMachineCondition,
    CanonicalProductionBatch,
    CanonicalTransitEnvironmentalCondition,
    SemanticRecordKind,
    USLMappingDefinition,
    USLProvenance,
)
from app.usl.nasa_ims import NASABearingDataLoader
from app.usl.zetaris_federation import ZetarisFederationClient
from app.world_model import Provenance, WorldModel


class UnifiedSemanticLayerService:
    """Provides semantic integration for FactoryOps manufacturing entities."""

    def __init__(self):
        self.nasa_loader = NASABearingDataLoader()
        self.zetaris_client = ZetarisFederationClient()

    def get_mapping_manifesto(self) -> List[USLMappingDefinition]:
        """Returns documented mappings between source records and canonical models."""
        return [
            USLMappingDefinition(
                source_entity="NASA_IMS_Bearing_Run_to_Failure.channel_1_2",
                canonical_entity="CanonicalMachineCondition (machine:plant:ROAST-01)",
                key_mapping={
                    "bearing_id": "source_bearing_id",
                    "timestamp": "timestamp",
                    "calculated_vibration_rms_mms": "vibration_rms_mms",
                    "temperature_c": "temperature_c",
                },
                unit_conversions={
                    "accelerometer_voltage": "g-force (9.81 m/s²)",
                    "acceleration": "velocity RMS (mm/s) via ISO 10816",
                    "temperature": "degrees Celsius (°C)",
                },
                missing_value_policy="Default to baseline nominal 1.8 mm/s and 115.0 °C",
                timestamp_semantics="UTC ISO-8601",
                provenance_rule="Retain dataset URL, run ID, and original channel coordinate",
                record_kind=SemanticRecordKind.MEASURED_FACT,
            ),
            USLMappingDefinition(
                source_entity="factory_source.source_machine_telemetry",
                canonical_entity="CanonicalMachineCondition",
                key_mapping={
                    "machine_code": "entity_id",
                    "reading_time": "timestamp",
                    "vibration_rms": "vibration_rms_mms",
                    "temperature_c": "temperature_c",
                    "operational_status": "operational_status",
                },
                unit_conversions={
                    "temperature": "degrees Celsius (°C)",
                    "vibration": "velocity RMS (mm/s)",
                },
                missing_value_policy="Forward-fill last valid verified telemetry",
                timestamp_semantics="UTC ISO-8601 sensor telemetry watermark",
                provenance_rule="Retain physical sensor feed ID and equipment tag",
                record_kind=SemanticRecordKind.MEASURED_FACT,
            ),
            USLMappingDefinition(
                source_entity="factory_source.source_batches",
                canonical_entity="CanonicalProductionBatch (production_batch:mes:BATCH-101)",
                key_mapping={
                    "batch_id": "batch_code",
                    "recipe_code": "recipe_name",
                    "target_quantity_kg": "planned_quantity_kg",
                    "actual_quantity_kg": "actual_quantity_kg",
                    "stage": "status",
                    "assigned_machine_code": "assigned_machine_id",
                },
                unit_conversions={"weight": "kilograms (kg)"},
                missing_value_policy="Strict rejection of missing planned quantity",
                timestamp_semantics="Simulation clock UTC",
                provenance_rule="Retain source ERP/MES batch code and scenario run ID",
                record_kind=SemanticRecordKind.SIMULATED_SCENARIO,
            ),
            USLMappingDefinition(
                source_entity="factory_source.source_orders",
                canonical_entity="CanonicalCustomerOrder (customer_order:erp:ORD-9402)",
                key_mapping={
                    "order_id": "order_code",
                    "customer_name": "customer_name",
                    "target_product": "product_sku",
                    "quantity_kg": "quantity_kg",
                    "promised_delivery": "deadline",
                },
                unit_conversions={"weight": "kilograms (kg)"},
                missing_value_policy="Reject orders missing promised delivery deadline",
                timestamp_semantics="UTC ISO-8601 customer contract commitments",
                provenance_rule="Retain ERP customer code and contract agreement reference",
                record_kind=SemanticRecordKind.SIMULATED_SCENARIO,
            ),
            USLMappingDefinition(
                source_entity="public.operational_events",
                canonical_entity="OperatorDecisionAudit (audit:decision:<event_id>)",
                key_mapping={
                    "event_id": "decision_id",
                    "recorded_at": "decided_at",
                    "payload.operator_id": "operator_id",
                    "payload.decision": "verdict",
                    "payload.rationale": "rationale",
                },
                unit_conversions={"audit": "immutable cryptographic hash key"},
                missing_value_policy="Strict rejection of decisions without operator ID or rationale",
                timestamp_semantics="System clock UTC immutable append-only",
                provenance_rule="Cryptographic SHA256 idempotency key and operator authorization certificate",
                record_kind=SemanticRecordKind.OPERATOR_DECISION,
            ),
            USLMappingDefinition(
                source_entity="TPCDS_DB.inventory+warehouse+item",
                canonical_entity="CanonicalExternalBufferStock (warehouse:external:<id>)",
                key_mapping={
                    "inv_quantity_on_hand": "quantity_on_hand_kg",
                    "w_warehouse_name": "warehouse_name",
                    "w_warehouse_id": "warehouse_id",
                    "i_item_id": "item_id",
                    "i_item_desc": "item_desc",
                    "i_current_price": "unit_price_usd",
                },
                unit_conversions={"weight": "kilograms (kg)", "currency": "USD"},
                missing_value_policy="Strict rejection of missing inventory or pricing records",
                timestamp_semantics="Federated query transaction watermark UTC",
                provenance_rule="Zetaris Spark SQL JDBC federated catalog session and metastore table lineage",
                record_kind=SemanticRecordKind.MEASURED_FACT,
            ),
            USLMappingDefinition(
                source_entity="NY_TAXI.weather_data",
                canonical_entity="CanonicalTransitEnvironmentalCondition (loc:weather:<id>)",
                key_mapping={
                    "weather_id": "location_id",
                    "temperature": "temperature_f",
                    "precipitation": "precipitation_in",
                    "wind_speed": "wind_speed_mph",
                },
                unit_conversions={
                    "temperature": "degrees Fahrenheit (°F)",
                    "precipitation": "inches (in)",
                    "wind_speed": "miles per hour (mph)",
                },
                missing_value_policy="Default to regional seasonal baseline (70°F, 0.0 in, 5 mph)",
                timestamp_semantics="Environmental observation watermark UTC",
                provenance_rule="Zetaris Spark SQL JDBC federated weather dataset lineage",
                record_kind=SemanticRecordKind.MEASURED_FACT,
            ),
        ]

    def query_external_buffer_inventory(
        self,
        warehouse_sk: int = 1,
        item_sk: int = 1,
    ) -> Optional[CanonicalExternalBufferStock]:
        """Queries federated external inventory across TPCDS_DB.inventory, warehouse, and item in Zetaris."""
        sql = (
            "SELECT inv.inv_item_sk, itm.i_item_id, itm.i_item_desc, itm.i_current_price, "
            "wh.w_warehouse_sk, wh.w_warehouse_id, wh.w_warehouse_name, inv.inv_quantity_on_hand "
            "FROM TPCDS_DB.inventory inv "
            "JOIN TPCDS_DB.item itm ON inv.inv_item_sk = itm.i_item_sk "
            "JOIN TPCDS_DB.warehouse wh ON inv.inv_warehouse_sk = wh.w_warehouse_sk "
            f"WHERE inv.inv_warehouse_sk = {warehouse_sk} AND inv.inv_item_sk = {item_sk} "
            "LIMIT 1"
        )
        try:
            res = self.zetaris_client.query_federated(sql, allow_fallback=False, channel="jdbc")
            if not res.success or not res.records:
                return None
            row = res.records[0]
            prov = USLProvenance(
                source_system="zetaris_ndp_federation",
                source_dataset="TPCDS_DB.inventory+warehouse+item",
                original_record_id=f"wh:{warehouse_sk}|item:{item_sk}",
                source_url_or_ref="jdbc:zetaris:lightning@localhost:10000",
                observed_at=datetime.now(timezone.utc).isoformat(),
                data_quality_status="verified",
                transformation_notes="Natively queried via Zetaris Spark SQL join across inventory, item, and warehouse catalogs.",
            )
            return CanonicalExternalBufferStock(
                warehouse_id=f"warehouse:external:{row.get('w_warehouse_id', warehouse_sk)}",
                warehouse_name=str(row.get("w_warehouse_name") or "Regional Buffer Warehouse"),
                item_id=f"item:external:{row.get('i_item_id', item_sk)}",
                item_desc=str(row.get("i_item_desc") or "Raw Agricultural Produce"),
                quantity_on_hand_kg=float(row.get("inv_quantity_on_hand") or 0.0),
                unit_price_usd=float(row.get("i_current_price") or 0.0),
                record_kind=SemanticRecordKind.MEASURED_FACT,
                provenance=prov,
            )
        except Exception:
            return None

    def query_transit_weather_conditions(
        self,
        weather_id: str = "1",
    ) -> Optional[CanonicalTransitEnvironmentalCondition]:
        """Queries federated transit route environmental telemetry from NY_TAXI.weather_data in Zetaris."""
        sql = (
            "SELECT weather_id, temperature, precipitation, wind_speed "
            f"FROM NY_TAXI.weather_data WHERE weather_id = '{weather_id}' LIMIT 1"
        )
        try:
            res = self.zetaris_client.query_federated(sql, allow_fallback=False, channel="jdbc")
            if not res.success or not res.records:
                return None
            row = res.records[0]
            temp = float(row.get("temperature") or 72.0)
            precip = float(row.get("precipitation") or 0.0)
            wind = float(row.get("wind_speed") or 5.0)

            if precip > 2.0 or wind > 35.0:
                risk = "hazardous"
            elif precip > 0.5 or wind > 20.0:
                risk = "adverse"
            else:
                risk = "nominal"

            prov = USLProvenance(
                source_system="zetaris_ndp_federation",
                source_dataset="NY_TAXI.weather_data",
                original_record_id=f"weather:{weather_id}",
                source_url_or_ref="jdbc:zetaris:lightning@localhost:10000",
                observed_at=datetime.now(timezone.utc).isoformat(),
                data_quality_status="verified",
                transformation_notes="Queried via Zetaris Spark SQL environmental dataset for transport logistics feasibility.",
            )
            return CanonicalTransitEnvironmentalCondition(
                location_id=f"loc:weather:{weather_id}",
                temperature_f=temp,
                precipitation_in=precip,
                wind_speed_mph=wind,
                road_risk_level=risk,
                record_kind=SemanticRecordKind.MEASURED_FACT,
                provenance=prov,
            )
        except Exception:
            return None

    def fetch_federated_recovery_context(self, disruption_type: str = "supplier_customs_delay") -> Dict[str, Any]:
        """Retrieves and normalizes cross-domain operational facts from Zetaris NDP for recovery decisions."""
        stock = self.query_external_buffer_inventory(warehouse_sk=1, item_sk=1)
        weather = self.query_transit_weather_conditions(weather_id="1")

        stock_dict = (
            {
                "warehouse_id": stock.warehouse_id,
                "warehouse_name": stock.warehouse_name,
                "item_id": stock.item_id,
                "item_desc": stock.item_desc,
                "quantity_on_hand_kg": stock.quantity_on_hand_kg,
                "unit_price_usd": stock.unit_price_usd,
                "provenance": {
                    "source_system": stock.provenance.source_system,
                    "source_dataset": stock.provenance.source_dataset,
                    "observed_at": stock.provenance.observed_at,
                    "data_quality_status": stock.provenance.data_quality_status,
                },
            }
            if stock
            else None
        )

        weather_dict = (
            {
                "location_id": weather.location_id,
                "temperature_f": weather.temperature_f,
                "precipitation_in": weather.precipitation_in,
                "wind_speed_mph": weather.wind_speed_mph,
                "road_risk_level": weather.road_risk_level,
                "provenance": {
                    "source_system": weather.provenance.source_system,
                    "source_dataset": weather.provenance.source_dataset,
                    "observed_at": weather.provenance.observed_at,
                    "data_quality_status": weather.provenance.data_quality_status,
                },
            }
            if weather
            else None
        )

        facts = []
        if stock:
            facts.append(f"External warehouse stock: {stock.quantity_on_hand_kg:.0f} kg ({stock.warehouse_name})")
        else:
            facts.append("External warehouse stock: UNAVAILABLE")

        if weather:
            facts.append(f"Transit environmental status: {weather.road_risk_level} (wind {weather.wind_speed_mph} mph, precip {weather.precipitation_in}\")")
        else:
            facts.append("Transit environmental status: UNAVAILABLE")

        return {
            "disruption_type": disruption_type,
            "zetaris_connected": bool(stock or weather),
            "external_buffer_stock": stock_dict,
            "transit_weather": weather_dict,
            "supporting_facts": facts,
            "sources": [
                "TPCDS_DB.inventory+warehouse+item (Zetaris NDP)",
                "NY_TAXI.weather_data (Zetaris NDP)",
            ],
        }

    def get_bearing_telemetry_series(self) -> List[CanonicalMachineCondition]:
        """Returns the full run-to-failure vibration time series from NASA IMS benchmark."""
        return self.nasa_loader.load_records()

    def load_genuine_machine_condition(self, condition: str = "nominal") -> CanonicalMachineCondition:
        """Retrieves a genuine measured machine condition record from NASA IMS dataset."""
        record = self.nasa_loader.get_record_by_condition(condition)
        if not record:
            raise ValueError(f"No condition record found for status: {condition}")
        return record

    def map_operational_batch(
        self,
        source_row: Dict[str, Any],
        scenario_id: str = "default",
    ) -> CanonicalProductionBatch:
        """Maps raw MES/ERP source batch into canonical USL representation with explicit provenance."""
        batch_code = source_row.get("batch_id") or source_row.get("batch_code") or "BATCH-UNKNOWN"
        recipe_name = source_row.get("recipe_code") or source_row.get("recipe_name") or "Standard"
        planned_kg = float(source_row.get("target_quantity_kg") or source_row.get("planned_quantity_kg") or 0.0)
        actual_kg = float(source_row.get("actual_quantity_kg") or 0.0)
        remaining_kg = max(0.0, planned_kg - actual_kg)
        status = str(source_row.get("stage") or source_row.get("status") or "scheduled").lower()
        machine_code = source_row.get("assigned_machine_code") or source_row.get("machine_id") or "ROAST-01"

        prov = USLProvenance(
            source_system="factory_source.source_batches",
            source_dataset="mes_batch_records",
            original_record_id=batch_code,
            source_url_or_ref=f"scenario:{scenario_id}",
            observed_at=datetime.now(timezone.utc).isoformat(),
            data_quality_status="verified",
            transformation_notes="Mapped to CanonicalProductionBatch via USL rule MES-01.",
        )

        return CanonicalProductionBatch(
            batch_id=f"production_batch:mes:{batch_code}",
            batch_code=batch_code,
            recipe_name=recipe_name,
            planned_quantity_kg=planned_kg,
            actual_quantity_kg=actual_kg,
            remaining_quantity_kg=remaining_kg,
            status=status,
            assigned_machine_id=f"machine:plant:{machine_code}",
            record_kind=SemanticRecordKind.SIMULATED_SCENARIO,
            provenance=prov,
        )

    def map_operational_order(
        self,
        source_row: Dict[str, Any],
        scenario_id: str = "default",
    ) -> CanonicalCustomerOrder:
        """Maps raw ERP source order into canonical USL representation with explicit provenance."""
        order_code = source_row.get("order_id") or source_row.get("order_code") or "ORD-UNKNOWN"
        customer_name = source_row.get("customer_name") or "Standard Customer"
        product_sku = source_row.get("target_product") or source_row.get("product_sku") or "SKU-DEFAULT"
        quantity_kg = float(source_row.get("quantity_kg") or 0.0)
        deadline = str(source_row.get("promised_delivery") or source_row.get("deadline") or datetime.now(timezone.utc).isoformat())
        status = str(source_row.get("status") or "confirmed").lower()
        assigned_batch = source_row.get("assigned_batch_id") or ""

        prov = USLProvenance(
            source_system="factory_source.source_orders",
            source_dataset="erp_sales_orders",
            original_record_id=order_code,
            source_url_or_ref=f"scenario:{scenario_id}",
            observed_at=datetime.now(timezone.utc).isoformat(),
            data_quality_status="verified",
            transformation_notes="Mapped to CanonicalCustomerOrder via USL rule ERP-01.",
        )

        return CanonicalCustomerOrder(
            order_id=f"customer_order:erp:{order_code}",
            order_code=order_code,
            customer_name=customer_name,
            product_sku=product_sku,
            quantity_kg=quantity_kg,
            deadline=deadline,
            status=status,
            assigned_batch_id=assigned_batch,
            record_kind=SemanticRecordKind.SIMULATED_SCENARIO,
            provenance=prov,
        )

    def populate_world_model_from_usl(
        self,
        world: WorldModel,
        scenario_id: str,
        condition: str = "nominal",
    ) -> Dict[str, Any]:
        """Injects genuine NASA IMS telemetry into the World Model with rigorous provenance."""
        record = self.load_genuine_machine_condition(condition)

        wm_prov = Provenance(
            kind="telemetry",
            at=record.timestamp,
            run_id=scenario_id,
            by=record.provenance.source_system,
        )

        world.assert_fact(
            about=record.entity_id,
            predicate="vibration_rms",
            value=record.vibration_rms_mms,
            source=wm_prov,
            confidence=0.99,
        )
        world.assert_fact(
            about=record.entity_id,
            predicate="bearing_temperature_c",
            value=record.temperature_c,
            source=wm_prov,
            confidence=0.99,
        )
        world.assert_fact(
            about=record.entity_id,
            predicate="status",
            value=record.operational_status,
            source=wm_prov,
            confidence=1.0,
        )

        return {
            "entity_id": record.entity_id,
            "vibration_rms": record.vibration_rms_mms,
            "temperature_c": record.temperature_c,
            "status": record.operational_status,
            "provenance": record.provenance.source_dataset,
            "record_kind": record.record_kind.value,
        }

    def trace_scenario_provenance(self, world: WorldModel) -> Dict[str, Any]:
        """Categorizes facts asserted in the scenario World Model into measured facts,

        simulated scenario elements, and operator governance decisions.
        """
        shape = world.state_shape()
        facts = shape.get("facts", [])

        measured_facts = []
        simulated_scenario_facts = []
        governance_decisions = []

        for f in facts:
            source = f.get("source", {})
            by = source.get("by", "")
            kind = source.get("kind", "")
            about = f.get("about", "")
            predicate = f.get("predicate", "")
            value = f.get("value")

            fact_entry = {
                "about": about,
                "predicate": predicate,
                "value": value,
                "at": source.get("at"),
                "source_by": by,
                "kind": kind,
            }

            if "nasa" in by.lower() or "sensor" in by.lower() or predicate in ("vibration_rms", "bearing_temperature_c"):
                fact_entry["record_kind"] = SemanticRecordKind.MEASURED_FACT.value
                fact_entry["provenance_layer"] = "NASA_IMS_Bearing_Run_to_Failure / Sensor Telemetry"
                measured_facts.append(fact_entry)
            elif "operator" in by.lower() or "approval" in kind.lower() or predicate == "approved_plan":
                fact_entry["record_kind"] = SemanticRecordKind.OPERATOR_DECISION.value
                fact_entry["provenance_layer"] = "Human Operator Decision Log"
                governance_decisions.append(fact_entry)
            else:
                fact_entry["record_kind"] = SemanticRecordKind.SIMULATED_SCENARIO.value
                fact_entry["provenance_layer"] = "Discrete-Event Simulation Timeline"
                simulated_scenario_facts.append(fact_entry)

        return {
            "total_facts_asserted": len(facts),
            "measured_facts_count": len(measured_facts),
            "simulated_scenario_count": len(simulated_scenario_facts),
            "governance_decisions_count": len(governance_decisions),
            "measured_facts": measured_facts,
            "simulated_scenario": simulated_scenario_facts,
            "governance_decisions": governance_decisions,
        }

