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
  const [scenariosList, setScenariosList] = useState<Scenario[]>([])
  const activeScenarioIdRef = useRef<string | null>(null)
  const refreshSeqRef = useRef(0)
  const isPollingRef = useRef(false)
  const lastKnownStepRef = useRef<Record<string, number>>({})

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

  const setActiveScenarioAndUrl = useCallback((sc: Scenario) => {
    activeScenarioIdRef.current = sc.id
    setScenario(sc)
    const url = new URL(window.location.href)
    if (url.searchParams.get('scenario') !== sc.id) {
      url.searchParams.set('scenario', sc.id)
      window.history.pushState({}, '', url.toString())
    }
  }, [])

  const refreshScenarioList = useCallback(async () => {
    try {
      const list = await api.listSimulations()
      setScenariosList(list)
      return list
    } catch (e) {
      console.warn('Failed to list scenarios:', e)
      return []
    }
  }, [])

  // Load requested scenario, most relevant existing scenario, or create default demo run
  const loadOrCreateScenario = useCallback(async (list: Scenario[]) => {
    try {
      // 1. Check URL query parameter first
      const params = new URLSearchParams(window.location.search)
      const urlScenarioId = params.get('scenario')
      if (urlScenarioId) {
        try {
          const specific = await api.getSimulation(urlScenarioId)
          setActiveScenarioAndUrl(specific)
          return specific.id
        } catch {
          console.warn(`Scenario ${urlScenarioId} from URL not found, falling back.`)
        }
      }

      // 2. If list has active/recent scenarios, pick the latest active one or the latest one
      if (list.length > 0) {
        const active = list.find((s) => s.status === 'running' || s.status === 'degraded' || s.status === 'recovering') || list[0]
        setActiveScenarioAndUrl(active)
        return active.id
      }

      // 3. Fall back to demo runner or creating new baseline
      const demoId = 'scn_demo_runner'
      try {
        const existing = await api.getSimulation(demoId)
        setActiveScenarioAndUrl(existing)
        return existing.id
      } catch {
        const created = await api.createSimulation({
          name: 'Demo Roaster Thermal Disruption Run',
          disruption_type: 'roaster_bearing_overheat',
          random_seed: 42,
          step_size_minutes: 15,
          scenario_id: demoId,
        })
        setActiveScenarioAndUrl(created)
        return created.id
      }
    } catch (e) {
      console.error('Failed to initialize scenario:', e)
      return null
    }
  }, [setActiveScenarioAndUrl])

  // Refresh active scenario data with sequence-order gating and monotonic step protection
  const refreshScenarioData = useCallback(async (scenarioId: string, options?: { full?: boolean }) => {
    if (!scenarioId) return
    const thisSeq = ++refreshSeqRef.current

    try {
      // 1. Fetch core scenario state and timing status
      const [scn, timing] = await Promise.all([
        api.getSimulation(scenarioId),
        api.getTimingStatus(scenarioId).catch(() => null),
      ])

      // Stale response checks:
      // a) User switched active scenario while request was in-flight
      // b) A newer refresh request was already initiated
      if (activeScenarioIdRef.current && activeScenarioIdRef.current !== scenarioId) {
        return
      }
      if (thisSeq < refreshSeqRef.current) {
        return
      }

      const prevStep = lastKnownStepRef.current[scenarioId] ?? -1
      const stepChanged = scn.current_step !== prevStep
      lastKnownStepRef.current[scenarioId] = scn.current_step

      // Monotonic step update guard: prevent out-of-order polling responses from rolling back the clock
      setScenario((prev) => {
        if (!prev || prev.id !== scenarioId) return scn
        if (prev.current_step > scn.current_step) {
          return {
            ...scn,
            current_step: prev.current_step,
            simulation_time: prev.simulation_time,
          }
        }
        return scn
      })

      if (timing) setTimingStatus(timing)

      // 2. Fetch heavy auxiliary data (timeline, worldModel, impact) only when full refresh
      // is requested, or on step change / initial load.
      if (options?.full || stepChanged || prevStep === -1) {
        const [tl, wm, imp] = await Promise.allSettled([
          api.getTimeline(scenarioId),
          api.getWorldModel(scenarioId),
          api.getRecoveryImpact(scenarioId),
        ])

        if (activeScenarioIdRef.current && activeScenarioIdRef.current !== scenarioId) return
        if (thisSeq < refreshSeqRef.current) return

        if (tl.status === 'fulfilled') setTimeline(tl.value.timeline || [])
        if (wm.status === 'fulfilled') setWorldModel(wm.value)
        if (imp.status === 'fulfilled') setImpact(imp.value)
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
        // Load system statuses and scenario list in parallel
        const [h, dbH, meterless, zetaris, maps, nasa, scnList] = await Promise.allSettled([
          api.getHealth(),
          api.getDbHealth(),
          api.getMeterlessStatus(),
          api.getZetarisStatus(),
          api.getUSLMappings(),
          api.getNASABearingTimeline(),
          api.listSimulations().catch(() => []),
        ])

        if (h.status === 'fulfilled') setHealth(h.value)
        if (dbH.status === 'fulfilled') setDbHealth(dbH.value)
        if (meterless.status === 'fulfilled') setMeterlessStatus(meterless.value)
        if (zetaris.status === 'fulfilled') setZetarisStatus(zetaris.value)
        if (maps.status === 'fulfilled') setMappings(maps.value.mappings || [])
        if (nasa.status === 'fulfilled') setNasaTimeline(nasa.value.timeline || [])
        const currentList = scnList.status === 'fulfilled' && Array.isArray(scnList.value) ? scnList.value : []
        setScenariosList(currentList)

        // Initialize or load requested/most recent scenario
        const scnId = await loadOrCreateScenario(currentList)
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
    const currentScenarioId = scenario.id
    setLoading(true)
    try {
      const stepKey = `step_${currentScenarioId}_${scenario.current_step + 1}_${Date.now()}`
      const targetStep = scenario.current_step + steps
      const res = await api.stepSimulation(currentScenarioId, steps, stepKey, targetStep)
      if (activeScenarioIdRef.current && activeScenarioIdRef.current !== currentScenarioId) {
        return
      }
      setScenario((prev) =>
        prev && prev.id === currentScenarioId
          ? {
              ...prev,
              current_step: res.current_step,
              simulation_time: res.simulation_time,
              status: res.status,
              snapshot: res.snapshot,
            }
          : prev
      )

      showToast(`Advanced simulation clock by ${steps} step(s) to step ${res.current_step}.`)
      await refreshScenarioData(currentScenarioId, { full: true })
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
    const currentScenarioId = scenario.id
    plansInFlight.current = true
    setLoadingPlans(true)
    try {
      const [imp, pl] = await Promise.all([
        api.getRecoveryImpact(currentScenarioId),
        api.getRecoveryPlans(currentScenarioId),
      ])
      if (activeScenarioIdRef.current && activeScenarioIdRef.current !== currentScenarioId) {
        return
      }
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
    const currentScenarioId = scenario.id
    setLoading(true)
    try {
      const res = await api.submitRecoveryDecision(currentScenarioId, decision)
      if (activeScenarioIdRef.current && activeScenarioIdRef.current !== currentScenarioId) {
        return
      }
      showToast(
        `Decision applied: ${res.decision.toUpperCase()} for ${res.plan_id}. Status: ${res.updated_scenario_status}`
      )
      await refreshScenarioData(currentScenarioId, { full: true })
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
      const created = await api.createSimulation(params)
      setActiveScenarioAndUrl(created)
      setImpact(null)
      setPlans([])
      showToast(`Created new simulation scenario: ${created.name} (${created.id})`)
      refreshScenarioList()
      await refreshScenarioData(created.id, { full: true })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast(`Failed to create scenario: ${msg}`)
      throw e
    } finally {
      setLoading(false)
    }
  }

  // Explicitly select scenario from list
  const handleSelectScenario = async (selectedId: string) => {
    if (!selectedId || selectedId === scenario?.id) return
    const prevScenario = scenario
    const prevId = activeScenarioIdRef.current
    setLoading(true)
    try {
      activeScenarioIdRef.current = selectedId
      const target = scenariosList.find((s) => s.id === selectedId)
      if (target) {
        setActiveScenarioAndUrl(target)
      } else {
        const loaded = await api.getSimulation(selectedId)
        if (activeScenarioIdRef.current !== selectedId) return
        setActiveScenarioAndUrl(loaded)
      }
      setImpact(null)
      setPlans([])
      await refreshScenarioData(selectedId, { full: true })
    } catch (e: unknown) {
      activeScenarioIdRef.current = prevId
      if (prevScenario) {
        setActiveScenarioAndUrl(prevScenario)
      }
      const msg = e instanceof Error ? e.message : String(e)
      showToast(`Failed to switch scenario to ${selectedId}: ${msg}`)
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

  // Periodic background polling to sync state with backend auto-runner (in-flight guarded)
  useEffect(() => {
    if (!scenario?.id) return
    const currentId = scenario.id
    let isCancelled = false

    const poll = async () => {
      if (isCancelled || activeScenarioIdRef.current !== currentId) return
      if (isPollingRef.current) return
      isPollingRef.current = true
      try {
        await refreshScenarioData(currentId)
      } finally {
        isPollingRef.current = false
      }
    }

    const timer = setInterval(poll, 2500)
    return () => {
      isCancelled = true
      isPollingRef.current = false
      clearInterval(timer)
    }
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
        scenarios={scenariosList}
        onSelectScenario={handleSelectScenario}
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
        onRefresh={() => scenario && refreshScenarioData(scenario.id, { full: true })}
      />

      {/* Navigation Tabs */}
      <Tabs
        activeTab={activeTab}
        onSelectTab={(tab) => {
          setActiveTab(tab)
          if (scenario?.id) {
            refreshScenarioData(scenario.id, { full: true })
          }
        }}
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
