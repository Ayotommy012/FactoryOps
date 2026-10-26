// frontend/src/lib/types.ts
// FactoryOps Unified TypeScript Domain Models

export interface HealthStatus {
  status: string
  app?: string
  environment?: string
  database?: string
}

export interface MachineSnapshot {
  id?: string
  machine_id?: string
  code?: string
  name?: string
  station?: string
  status: 'nominal' | 'running' | 'degraded' | 'failed' | 'maintenance' | 'offline' | string
  temperature_c: number
  vibration_rms: number
  speed_rpm?: number
  power_kw?: number
  rated_capacity_kgh?: number
  current_throughput_kgh?: number
  rated_capacity_kg_h?: number
  current_throughput_kg_h?: number
  rated_capacity_kg_per_hour?: number
  current_throughput_kg_per_hour?: number
  last_maintenance_date?: string
  oil_level_pct?: number
}

export interface BatchSnapshot {
  id?: string
  batch_id?: string
  batch_code: string
  recipe_name: string
  status: 'scheduled' | 'in_progress' | 'completed' | 'quarantined' | 'diverted' | 'delayed' | string
  current_stage?: string
  planned_quantity_kg: number
  actual_quantity_kg: number
  yield_pct?: number
  start_time?: string
  target_completion?: string
}

export interface InventorySnapshot {
  id?: string
  lot_id?: string
  lot_code?: string
  material_type: string
  quantity_kg?: number
  current_quantity_kg?: number
  storage_location?: string
  quality_grade?: string
  quality_status?: string
}

export interface OrderSnapshot {
  id?: string
  order_id?: string
  order_code: string
  customer_name: string
  product_name?: string
  product_sku?: string
  quantity_kg?: number
  ordered_quantity_kg?: number
  deadline?: string
  delivery_deadline?: string
  status: 'open' | 'in_production' | 'fulfilled' | 'breached' | string
  penalty_per_day_late?: number
  penalty_usd_per_hour?: number
}

export interface SimulationSnapshot {
  simulation_time: string
  current_step: number
  machines: Record<string, MachineSnapshot>
  batches: Record<string, BatchSnapshot>
  inventory: Record<string, InventorySnapshot>
  orders: Record<string, OrderSnapshot>
  production_cycle?: ProductionCycleInfo
}

export interface Scenario {
  id: string
  name: string
  disruption_type: string
  status: 'initialized' | 'running' | 'degraded' | 'recovering' | 'completed' | string
  random_seed: number
  simulation_time: string
  current_step: number
  step_size_minutes: number
  snapshot: SimulationSnapshot
  parameters?: {
    auto_advance?: boolean
    recovery?: {
      decision?: 'approved' | 'rejected' | string
      selected_plan?: string
      operator_id?: string
      rationale?: string
      applied_at?: string
      start_step?: number
      duration_hours?: number
      duration_steps?: number
      remaining_steps?: number
      completed_step?: number
      status?: 'in_progress' | 'completed' | 'rejected' | string
      autonomous?: boolean
      decision_source?: string
      incident_key?: string
      lifecycle_id?: string
      escalation_reason?: string
      last_error?: string
      next_attempt_at?: string
      failures?: number
      fallback_reason?: string | null
      resolved_model?: string | null
      severity?: Severity
      disruption_type?: string
      finished_at_step?: number
      authorization?: { outcome?: string; reason?: string }
    }
    autonomous_recovery?: { enabled?: boolean }
    autonomous_recovery_history?: AutonomousRecoverySummary
    [key: string]: any
  }
}

export type Severity = 'low' | 'medium' | 'high' | 'critical'

export interface AutonomousHistoryResponse {
  scenario_id: string
  has_autonomous_history: boolean
  autonomous_recovery_enabled: boolean
  recovery: Record<string, any> | null
  summary: AutonomousRecoverySummary | null
  audit_events: { event_id: string; event_type: string; recorded_at: string; payload: Record<string, any> }[]
}

export interface AutonomousRecoverySummary {
  lifecycle_id: string
  final_status: string
  rounds_count: number
  winning_plan_id: string | null
  total_cost_usd: number
  summary: string
}

export interface TimingStatusResponse {
  scenario_id: string
  auto_advance: boolean
  interval_seconds: number
  current_step: number
  simulation_time: string
  scenario_status: string
  is_awaiting_decision: boolean
  requires_human_decision?: boolean
  autonomous_recovery_enabled?: boolean
  is_eligible_for_auto_tick: boolean
  ineligibility_reason: string | null
  recovery?: {
    decision?: string
    selected_plan?: string
    status?: string
    autonomous?: boolean
    decision_source?: string
    escalation_reason?: string
    start_step?: number
    duration_steps?: number
    remaining_steps?: number
    completed_step?: number
  }
}

export interface StepResponse {
  scenario_id: string
  current_step: number
  simulation_time: string
  status: string
  disruption_type: string
  events_emitted_count: number
  snapshot: SimulationSnapshot
}

export interface DisruptionEvidence {
  metric: string
  observed_value: number
  threshold_value: number
  unit: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  message: string
  record_kind?: string
  provenance_source?: string
}

export interface ImpactAnalysis {
  scenario_id: string
  disrupted_machine_id: string
  affected_batch_id: string
  affected_order_id: string
  affected_customer: string
  order_deadline: string
  unintervened_completion: string
  delay_hours: number
  sla_breached: boolean
  potential_penalty_usd: number
  remaining_quantity_kg: number
  evidence: DisruptionEvidence[]
  provenance_summary?: Record<string, unknown>
  severity?: 'low' | 'medium' | 'high' | 'critical'
  disruption_type?: string
}

export interface RecoveryPlan {
  plan_id: string
  title: string
  strategy: 'maintenance' | 'reroute' | 'split_expedite' | string
  rank: number
  feasibility_verdict: 'feasible' | 'infeasible'
  hard_constraints_met: string[]
  estimated_downtime_hours: number
  projected_completion: string
  meets_deadline: boolean
  buffer_hours: number
  total_cost_usd: number
  cost_breakdown: {
    equipment_cost_usd?: number
    labor_cost_usd?: number
    penalty_cost_usd?: number
    raw_material_scrap_usd?: number
    [key: string]: number | undefined
  }
  action_steps: string[]
  explanation?: string
  authorization_outcome?: 'AUTO_EXECUTE' | 'REQUIRES_APPROVAL' | 'BLOCKED'
  authorization_reason?: string
}

export interface RecoveryDecisionRequest {
  plan_id: string
  decision: 'approved' | 'rejected'
  operator_id: string
  rationale: string
}

export interface RecoveryDecisionResult {
  scenario_id: string
  plan_id: string
  decision: 'approved' | 'rejected'
  operator_id: string
  rationale: string
  applied_at: string
  event_id: string
  updated_scenario_status: string
}

export interface OperationalTimelineEvent {
  id: number
  event_id: string
  event_type: string
  simulation_time: string
  recorded_at: string
  source_record_id?: string
  entity_refs: string[]
  payload: Record<string, unknown>
}

export interface WorldModelAuditEntry {
  seq: number
  at: string
  kind: string
  actor: string
  summary: string
}

export interface WorldModelResponse {
  scenario_id: string
  canonical_shape: Record<string, unknown>
  audit_log: WorldModelAuditEntry[]
}

export interface USLMapping {
  source_entity: string
  canonical_entity: string
  key_mapping: Record<string, string>
  unit_conversions: Record<string, string>
  missing_value_policy: string
  timestamp_semantics: string
  provenance_rule: string
  record_kind: string
}

export interface NASABearingRecord {
  source_bearing_id: string
  timestamp: string
  vibration_rms_mms: number
  temperature_c: number
  operational_status: string
  vibration_ratio_vs_baseline?: number
  raw_ch1_g?: number
  raw_ch2_g?: number
  source_provenance?: string
}

export interface ZetarisStatusResponse {
  connected: boolean
  base_url: string
  jdbc_environment: {
    jdbc_url: string
    driver_jar_path: string
    driver_jar_filename?: string
    driver_jar_exists: boolean
    driver_version?: string
    driver_class: string
    host_java_available?: boolean
    host_java_version?: string
    jdbc_port_open?: boolean
    live_session_verified?: boolean
    lightning_databases: string[]
    table_counts?: Record<string, number>
    factory_schema_registered?: boolean
    connection_status: 'connected' | 'live_session_ready' | 'host_unreachable' | 'driver_jar_missing' | 'host_java_missing' | 'connection_failed' | 'not_configured' | 'error' | string
    next_operator_action?: string
    error_message?: string
  }
  boundary_summary: string
}

export interface FederatedQueryResult {
  success: boolean
  source_layer: 'zetaris_federation' | 'direct_postgres_fallback'
  query: string
  records: Record<string, unknown>[]
  total_count: number
  execution_time_ms: number
  boundary_notes: string
  provenance: {
    source_system: string
    source_dataset: string
    original_record_id: string
    source_url_or_ref: string
    observed_at: string
    data_quality_status: string
  }
}

export interface MeterlessStatusResponse {
  provider_id: string
  display_name: string
  base_url: string
  status: 'online' | 'authorized' | 'unauthorized' | 'offline' | string
  message: string
  available_models: string[]
  tested_at?: string
}

export interface DisruptionCatalogueItem {
  type_id: string
  name: string
  category: 'Supply Chain' | 'Machinery & Equipment' | 'Inventory & Quality' | string
  explanation: string
  prerequisites: string
  default_parameters: Record<string, any>
  parameter_schema: Record<string, any>
  eligible: boolean
  ineligibility_reason?: string | null
  severity?: 'low' | 'medium' | 'high' | 'critical'
}

export interface TriggerDisruptionResponse {
  success: boolean
  status: string
  scenario_id: string
  disruption_type: string
  disruption_name?: string
  current_step: number
  parameters: Record<string, any>
  is_random?: boolean
  random_selection?: boolean
  random_selection_meta?: Record<string, any>
  affected_entities: string[]
}

export interface ProductionCycleInfo {
  cycle_number: number
  active_stage_num: number
  stage_step_counter: number
  completed_cycles: number
}

export interface ProductionLineResponse {
  scenario_id: string
  current_step: number
  simulation_time: string
  production_cycle?: ProductionCycleInfo
  stages: ProductionLineStage[]
}

export interface ProductionLineStage {
  num: number
  id: string
  name: string
  machine_code: string
  machine_id: string
  station: string
  status: 'running' | 'at_risk' | 'blocked' | 'offline' | 'recovering' | 'queued' | 'completed' | 'pending' | string
  throughput_kg_h: number
  rated_capacity_kg_h: number
  health_score: number
  temperature_c: number
  vibration_rms: number
  input_material: string
  input_available: boolean
  input_quantity_kg: number
  output_product: string
  progress_pct: number
  operational_reason: string
  upstream_stage_num: number | null
  downstream_stage_num: number | null
  cycle_number?: number
  completed_cycles?: number
  is_active?: boolean
}

export interface TrajectoryPoint {
  step: number
  simulation_time: string
  inventory_kg: number
  is_replenishment_step: boolean
  is_stockout: boolean
}

export interface SupplyChainForecast {
  scenario_id?: string
  current_step?: number
  simulation_time?: string
  usable_inventory_kg: number
  is_quarantined: boolean
  active_burn_rate_kg_h: number
  active_burn_rate_kg_step: number
  steps_until_stockout: number | null
  projected_stockout_step: number | null
  projected_stockout_time: string | null
  shipment: {
    id: string
    po_number: string
    supplier_name: string
    quantity_kg: number
    status: string
    eta_step: number
    eta_time: string
    disruption_reason?: string | null
  }
  is_stockout_before_replenishment: boolean
  starvation_gap_steps: number
  starvation_gap_hours: number
  affected_stages: string[]
  affected_order_code: string
  affected_customer: string
  trajectory: TrajectoryPoint[]
}
