// frontend/src/lib/api.ts
// Typed API Client for FactoryOps FastAPI Backend

import type {
  AutonomousHistoryResponse,
  DisruptionCatalogueItem,
  FederatedQueryResult,
  HealthStatus,
  ImpactAnalysis,
  MeterlessStatusResponse,
  NASABearingRecord,
  OperationalTimelineEvent,
  ProductionLineResponse,
  RecoveryDecisionRequest,
  RecoveryDecisionResult,
  RecoveryPlan,
  Scenario,
  StepResponse,
  SupplyChainForecast,
  TimingStatusResponse,
  TriggerDisruptionResponse,
  USLMapping,
  WorldModelResponse,
  ZetarisStatusResponse,
} from './types'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!res.ok) {
    let errorDetail = `HTTP ${res.status}: ${res.statusText}`
    try {
      const errJson = await res.json()
      if (errJson.detail) {
        errorDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail)
      }
    } catch {
      // ignore
    }
    throw new Error(errorDetail)
  }

  return res.json() as Promise<T>
}

export const api = {
  // System Health
  getHealth: () => request<HealthStatus>('/health'),
  getDbHealth: () => request<HealthStatus>('/health/db'),

  // Simulations
  listSimulations: (status?: string) =>
    request<Scenario[]>(status ? `/simulations?status=${encodeURIComponent(status)}` : '/simulations'),

  createSimulation: (params: {
    name: string
    disruption_type?: string
    random_seed?: number
    step_size_minutes?: number
    scenario_id?: string
    autonomous_recovery?: boolean
  }) =>
    request<Scenario>('/simulations', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  getSimulation: (scenarioId: string) =>
    request<Scenario>(`/simulations/${encodeURIComponent(scenarioId)}`),

  stepSimulation: (scenarioId: string, steps: number = 1, idempotencyKey?: string, targetStep?: number) =>
    request<StepResponse>(`/simulations/${encodeURIComponent(scenarioId)}/step`, {
      method: 'POST',
      body: JSON.stringify({
        steps,
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
        ...(targetStep !== undefined ? { target_step: targetStep } : {}),
      }),
    }),

  pauseSimulation: (scenarioId: string) =>
    request<{ scenario_id: string; auto_advance: boolean; status: string }>(
      `/simulations/${encodeURIComponent(scenarioId)}/pause`,
      { method: 'POST' }
    ),

  resumeSimulation: (scenarioId: string) =>
    request<{ scenario_id: string; auto_advance: boolean; status: string }>(
      `/simulations/${encodeURIComponent(scenarioId)}/resume`,
      { method: 'POST' }
    ),

  getTimingStatus: (scenarioId: string) =>
    request<TimingStatusResponse>(
      `/simulations/${encodeURIComponent(scenarioId)}/timing`
    ),

  triggerAutoTick: (scenarioId: string) =>
    request<{ ticked: boolean; reason?: string; current_step: number; simulation_time?: string; scenario_status?: string }>(
      `/simulations/${encodeURIComponent(scenarioId)}/auto-tick`,
      { method: 'POST' }
    ),

  getTimeline: (scenarioId: string) =>
    request<{ scenario_id: string; event_count: number; timeline: OperationalTimelineEvent[] }>(
      `/simulations/${encodeURIComponent(scenarioId)}/timeline`
    ),

  getWorldModel: (scenarioId: string) =>
    request<WorldModelResponse>(`/simulations/${encodeURIComponent(scenarioId)}/world-model`),

  // Recovery & Disruption
  getRecoveryImpact: (scenarioId: string) =>
    request<ImpactAnalysis>(`/simulations/${encodeURIComponent(scenarioId)}/recovery/impact`),

  getRecoveryPlans: (scenarioId: string) =>
    request<{ scenario_id: string; candidate_count: number; plans: RecoveryPlan[] }>(
      `/simulations/${encodeURIComponent(scenarioId)}/recovery/plans`,
      { method: 'POST' }
    ),

  submitRecoveryDecision: (scenarioId: string, decision: RecoveryDecisionRequest) =>
    request<RecoveryDecisionResult>(`/simulations/${encodeURIComponent(scenarioId)}/recovery/decide`, {
      method: 'POST',
      body: JSON.stringify(decision),
    }),

  runAutonomousRecovery: (scenarioId: string) =>
    request<{ final_status: string; selected_plan_id: string | null; summary: string }>(
      `/simulations/${encodeURIComponent(scenarioId)}/autonomous-recovery/run`,
      { method: 'POST', body: JSON.stringify({ auto_advance_steps: false }) }
    ),

  getAutonomousHistory: (scenarioId: string) =>
    request<AutonomousHistoryResponse>(
      `/simulations/${encodeURIComponent(scenarioId)}/autonomous-recovery/history`
    ),

  // Unified Semantic Layer & Data Federation
  getUSLMappings: () =>
    request<{ layer: string; mapping_count: number; mappings: USLMapping[] }>('/usl/mappings'),

  getNASABearingTimeline: () =>
    request<{ dataset: string; progression_count: number; timeline: NASABearingRecord[] }>(
      '/usl/nasa-bearing/timeline'
    ),

  getZetarisStatus: () =>
    request<ZetarisStatusResponse>('/usl/zetaris/status'),

  executeFederatedQuery: (sql: string, allowFallback: boolean = true, channel: 'auto' | 'jdbc' | 'rest' = 'auto') =>
    request<FederatedQueryResult>('/usl/federated-query', {
      method: 'POST',
      body: JSON.stringify({ sql, allow_fallback: allowFallback, channel }),
    }),

  // AI Providers
  getMeterlessStatus: () =>
    request<MeterlessStatusResponse>('/providers/meterless/status'),

  // Disruptions Simulator & Production Line
  getDisruptionsCatalogue: (scenarioId: string) =>
    request<{ scenario_id: string; disruptions: DisruptionCatalogueItem[] }>(
      `/simulations/${encodeURIComponent(scenarioId)}/disruptions/catalogue`
    ),

  triggerDisruption: (scenarioId: string, disruptionType: string, parameters?: Record<string, any>) =>
    request<TriggerDisruptionResponse>(
      `/simulations/${encodeURIComponent(scenarioId)}/disruptions/trigger`,
      {
        method: 'POST',
        body: JSON.stringify({ disruption_type: disruptionType, parameters }),
      }
    ),

  getProductionLine: (scenarioId: string) =>
    request<ProductionLineResponse>(
      `/simulations/${encodeURIComponent(scenarioId)}/production-line`
    ),

  getSupplyChainForecast: (scenarioId: string) =>
    request<SupplyChainForecast>(
      `/simulations/${encodeURIComponent(scenarioId)}/supply-chain`
    ),
}
