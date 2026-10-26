// frontend/src/App.tsx
import { useState, useEffect, useCallback, useRef } from 'react'
import { Header } from './components/Header'
import { Tabs, type TabKey } from './components/Tabs'
import { OperationsTab } from './components/OperationsTab'
import { DisruptionsTab } from './components/DisruptionsTab'
import { RecoveryTab } from './components/RecoveryTab'
import { WorldModelTab } from './components/WorldModelTab'
import { DataFederationTab } from './components/DataFederationTab'
import { TimelineTab } from './components/TimelineTab'
import { NewScenarioModal } from './components/NewScenarioModal'
import { RunModal } from './components/RunModal'
import { api } from './lib/api'
import type {
  HealthStatus,
  ImpactAnalysis,
  MeterlessStatusResponse,
  NASABearingRecord,
  OperationalTimelineEvent,
  RecoveryDecisionRequest,
  RecoveryPlan,
  Scenario,
  TimingStatusResponse,
  USLMapping,
  WorldModelResponse,
  ZetarisStatusResponse,
} from './lib/types'

export function App() {
  const [activeTab, setActiveTab] = useState<TabKey>('operations')
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [dbHealth, setDbHealth] = useState<HealthStatus | null>(null)
  const [meterlessStatus, setMeterlessStatus] = useState<MeterlessStatusResponse | null>(null)
  const [zetarisStatus, setZetarisStatus] = useState<ZetarisStatusResponse | null>(null)
  const [impact, setImpact] = useState<ImpactAnalysis | null>(null)
  const [plans, setPlans] = useState<RecoveryPlan[]>([])
  const [worldModel, setWorldModel] = useState<WorldModelResponse | null>(null)
  const [mappings, setMappings] = useState<USLMapping[]>([])
  const [nasaTimeline, setNasaTimeline] = useState<NASABearingRecord[]>([])
  const [timeline, setTimeline] = useState<OperationalTimelineEvent[]>([])
  const [timingStatus, setTimingStatus] = useState<TimingStatusResponse | null>(null)

  const [loading, setLoading] = useState(false)
  const [loadingPlans, setLoadingPlans] = useState(false)
  const [isNewModalOpen, setIsNewModalOpen] = useState(false)
  const [isRunModalOpen, setIsRunModalOpen] = useState(false)
  const [toastMessage, setToastMessage] = useState<string | null>(null)

  const showToast = (msg: string) => {
    setToastMessage(msg)
    setTimeout(() => {
      setToastMessage((current) => (current === msg ? null : current))
    }, 4000)
  }

  // Load baseline scenario or create default demo run
  const loadOrCreateScenario = useCallback(async () => {
    try {
      const demoId = 'scn_demo_runner'
      try {
        const existing = await api.getSimulation(demoId)
        setScenario(existing)
        return existing.id
      } catch {
        // If not found, spawn demo
        const created = await api.createSimulation({
          name: 'Demo Roaster Thermal Disruption Run',
          disruption_type: 'roaster_bearing_overheat',
          random_seed: 42,
          step_size_minutes: 15,
          scenario_id: demoId,
        })
        setScenario(created)
        return created.id
      }
    } catch (e) {
      console.error('Failed to initialize baseline scenario:', e)
      return null
    }
  }, [])

  // Refresh active scenario data
  const refreshScenarioData = useCallback(async (scenarioId: string) => {
    try {
      const [scn, tl, wm, timing] = await Promise.all([
        api.getSimulation(scenarioId),
        api.getTimeline(scenarioId).catch(() => ({ timeline: [] })),
        api.getWorldModel(scenarioId).catch(() => null),
        api.getTimingStatus(scenarioId).catch(() => null),
      ])
      setScenario(scn)
      setTimeline(tl.timeline || [])
      if (wm) setWorldModel(wm)
      if (timing) setTimingStatus(timing)

      // Impact is deterministic and cheap (no AI call): keep it in sync for any
      // incident type so severity badges match the backend's live evidence.
      try {
        const imp = await api.getRecoveryImpact(scenarioId)
        setImpact(imp)
      } catch {
        // ignore
      }
    } catch (e) {
      console.error('Failed to refresh scenario data:', e)
    }
  }, [])

  // Initial load on mount
  useEffect(() => {
    const init = async () => {
      setLoading(true)
      try {
        // Load system statuses in parallel
        const [h, dbH, meterless, zetaris, maps, nasa] = await Promise.allSettled([
          api.getHealth(),
          api.getDbHealth(),
          api.getMeterlessStatus(),
          api.getZetarisStatus(),
          api.getUSLMappings(),
          api.getNASABearingTimeline(),
        ])

        if (h.status === 'fulfilled') setHealth(h.value)
        if (dbH.status === 'fulfilled') setDbHealth(dbH.value)
        if (meterless.status === 'fulfilled') setMeterlessStatus(meterless.value)
        if (zetaris.status === 'fulfilled') setZetarisStatus(zetaris.value)
        if (maps.status === 'fulfilled') setMappings(maps.value.mappings || [])
        if (nasa.status === 'fulfilled') setNasaTimeline(nasa.value.timeline || [])

        // Initialize or load demo scenario
        const scnId = await loadOrCreateScenario()
        if (scnId) {
          await refreshScenarioData(scnId)
        }
      } finally {
        setLoading(false)
      }
    }

    init()
  }, [loadOrCreateScenario, refreshScenarioData])

  // Step simulation
  const handleStep = async (steps: number) => {
    if (!scenario) return
    setLoading(true)
    try {
      const res = await api.stepSimulation(scenario.id, steps)
      setScenario((prev) =>
        prev
          ? {
              ...prev,
              current_step: res.current_step,
              simulation_time: res.simulation_time,
              status: res.status,
              snapshot: res.snapshot,
            }
          : null
      )

      showToast(`Advanced simulation clock by ${steps} step(s) to step ${res.current_step}.`)
      await refreshScenarioData(scenario.id)
      // Plans (which carry a Meterless explanation) are fetched on demand from the
      // Recovery tab, not on every step: per-step fetches were a 429 source.
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast(`Step failed: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  // Fetch Impact and Ranked Plans (single in-flight request at a time)
  const plansInFlight = useRef(false)
  const handleFetchImpactAndPlans = async () => {
    if (!scenario || plansInFlight.current) return
    plansInFlight.current = true
    setLoadingPlans(true)
    try {
      const [imp, pl] = await Promise.all([
        api.getRecoveryImpact(scenario.id),
        api.getRecoveryPlans(scenario.id),
      ])
      setImpact(imp)
      setPlans(pl.plans || [])
      showToast(`Evaluated ${pl.candidate_count} ranked recovery candidate plans.`)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast(`Plan generation error: ${msg}`)
    } finally {
      plansInFlight.current = false
      setLoadingPlans(false)
    }
  }

  // Human-in-the-Loop Decision submit
  const handleDecide = async (decision: RecoveryDecisionRequest) => {
    if (!scenario) return
    setLoading(true)
    try {
      const res = await api.submitRecoveryDecision(scenario.id, decision)
      showToast(
        `Decision applied: ${res.decision.toUpperCase()} for ${res.plan_id}. Status: ${res.updated_scenario_status}`
      )
      await refreshScenarioData(scenario.id)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast(`Decision submission failed: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  // Create new scenario
  const handleCreateScenario = async (params: {
    name: string
    disruption_type: string
    random_seed: number
    step_size_minutes: number
  }) => {
    setLoading(true)
    try {
      const uniqueId = 'scn_' + Math.random().toString(36).substring(2, 8)
      const created = await api.createSimulation({
        ...params,
        scenario_id: uniqueId,
      })
      setScenario(created)
      setImpact(null)
      setPlans([])
      showToast(`Created new simulation scenario: ${created.name} (${created.id})`)
      await refreshScenarioData(created.id)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast(`Failed to create scenario: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  const isRoasterDegraded =
    (impact?.evidence?.length ?? 0) > 0 ||
    scenario?.snapshot?.machines?.['machine:plant:ROAST-01']?.status === 'degraded' ||
    scenario?.status === 'degraded'

  const isAwaitingDecision =
    timingStatus != null
      ? (timingStatus.requires_human_decision ?? timingStatus.is_awaiting_decision)
      : Boolean(
          scenario &&
          (scenario.status === 'running' || scenario.status === 'degraded') &&
          scenario.parameters?.recovery?.status !== 'completed' &&
          !scenario.parameters?.recovery?.decision &&
          (scenario.snapshot?.machines?.['machine:plant:ROAST-01']?.status === 'degraded' ||
            scenario.snapshot?.machines?.['machine:plant:MILL-01']?.status === 'failed' ||
            (scenario.disruption_type !== 'none' && scenario.current_step >= 3))
        )

  // Periodic background polling to sync state with backend auto-runner
  useEffect(() => {
    if (!scenario?.id) return
    const timer = setInterval(() => {
      refreshScenarioData(scenario.id)
    }, 2500)
    return () => clearInterval(timer)
  }, [scenario?.id, refreshScenarioData])

  const handleToggleAuto = async () => {
    if (!scenario) return
    setLoading(true)
    try {
      const isCurrentlyAuto = scenario.parameters?.auto_advance !== false
      if (isCurrentlyAuto) {
        await api.pauseSimulation(scenario.id)
        showToast('Automatic simulation progression paused.')
      } else {
        await api.resumeSimulation(scenario.id)
        showToast('Automatic simulation progression resumed (1 step / 5s).')
      }
      await refreshScenarioData(scenario.id)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast(`Failed to toggle auto progression: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--bg-dark)' }}>
      {/* Toast Notification */}
      {toastMessage && (
        <div
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            background: '#0284c7',
            color: '#fff',
            padding: '10px 18px',
            borderRadius: '6px',
            boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.5)',
            zIndex: 1000,
            fontSize: '0.85rem',
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          {toastMessage}
        </div>
      )}

      {/* Header Bar */}
      <Header
        scenario={scenario}
        health={health}
        dbHealth={dbHealth}
        meterlessStatus={meterlessStatus}
        zetarisStatus={zetarisStatus}
        loading={loading}
        isAwaitingDecision={isAwaitingDecision}
        onStep={handleStep}
        onToggleAuto={handleToggleAuto}
        onOpenRunModal={() => setIsRunModalOpen(true)}
        onOpenNewModal={() => setIsNewModalOpen(true)}
        onRefresh={() => scenario && refreshScenarioData(scenario.id)}
      />

      {/* Navigation Tabs */}
      <Tabs
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        hasActiveAnomaly={isRoasterDegraded}
        eventCount={timeline.length}
      />

      {/* Tab Content Body */}
      <main style={{ padding: '24px', flex: 1, maxWidth: '1600px', width: '100%', margin: '0 auto' }}>
        {activeTab === 'operations' && (
          <OperationsTab
            scenarioId={scenario?.id}
            snapshot={scenario?.snapshot || null}
            disruptionType={scenario?.disruption_type}
          />
        )}

        {activeTab === 'disruptions' && scenario && (
          <DisruptionsTab
            scenario={scenario}
            onRefresh={() => refreshScenarioData(scenario.id)}
            onNavigateToRecovery={() => setActiveTab('recovery')}
          />
        )}

        {activeTab === 'recovery' && (
          <RecoveryTab
            scenarioId={scenario?.id || ''}
            impact={impact}
            plans={plans}
            loadingPlans={loadingPlans}
            onFetchImpactAndPlans={handleFetchImpactAndPlans}
            onDecide={handleDecide}
            scenario={scenario}
            timing={timingStatus}
          />
        )}

        {activeTab === 'world_model' && (
          <WorldModelTab
            worldModel={worldModel}
            snapshot={scenario?.snapshot || null}
            scenarioId={scenario?.id || ''}
          />
        )}

        {activeTab === 'federation' && (
          <DataFederationTab
            zetarisStatus={zetarisStatus}
            mappings={mappings}
            nasaTimeline={nasaTimeline}
          />
        )}

        {activeTab === 'timeline' && <TimelineTab timeline={timeline} />}
      </main>

      {/* Centered Disruption Run Modal */}
      <RunModal
        isOpen={isRunModalOpen}
        onClose={() => setIsRunModalOpen(false)}
        scenario={scenario}
        onSuccess={(msg) => {
          showToast(msg || 'Disruption triggered successfully.')
          if (scenario) refreshScenarioData(scenario.id)
        }}
      />

      {/* Modal to Create New Run */}
      <NewScenarioModal
        isOpen={isNewModalOpen}
        onClose={() => setIsNewModalOpen(false)}
        onCreate={handleCreateScenario}
      />
    </div>
  )
}

export default App
