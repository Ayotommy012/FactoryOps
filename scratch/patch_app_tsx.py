with open('frontend/src/App.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

target1 = """  const [scenariosList, setScenariosList] = useState<Scenario[]>([])
  const activeScenarioIdRef = useRef<string | null>(null)"""

replacement1 = """  const [scenariosList, setScenariosList] = useState<Scenario[]>([])
  const activeScenarioIdRef = useRef<string | null>(null)
  const refreshSeqRef = useRef(0)
  const isPollingRef = useRef(false)
  const lastKnownStepRef = useRef<Record<string, number>>({})"""

target2 = """  // Refresh active scenario data
  const refreshScenarioData = useCallback(async (scenarioId: string) => {
    try {
      const [scn, tl, wm, timing] = await Promise.all([
        api.getSimulation(scenarioId),
        api.getTimeline(scenarioId).catch(() => ({ timeline: [] })),
        api.getWorldModel(scenarioId).catch(() => null),
        api.getTimingStatus(scenarioId).catch(() => null),
      ])
      if (activeScenarioIdRef.current && activeScenarioIdRef.current !== scenarioId) {
        return
      }
      setScenario(scn)
      setTimeline(tl.timeline || [])
      if (wm) setWorldModel(wm)
      if (timing) setTimingStatus(timing)

      try {
        const imp = await api.getRecoveryImpact(scenarioId)
        if (activeScenarioIdRef.current && activeScenarioIdRef.current !== scenarioId) return
        setImpact(imp)
      } catch {
        // ignore
      }
    } catch (e) {
      console.error('Failed to refresh scenario data:', e)
    }
  }, [])"""

replacement2 = """  // Refresh active scenario data with sequence-order gating and monotonic step protection
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
  }, [])"""

target3 = """  // Periodic background polling to sync state with backend auto-runner
  useEffect(() => {
    if (!scenario?.id) return
    const currentId = scenario.id
    const timer = setInterval(() => {
      if (activeScenarioIdRef.current === currentId) {
        refreshScenarioData(currentId)
      }
    }, 2500)
    return () => clearInterval(timer)
  }, [scenario?.id, refreshScenarioData])"""

replacement3 = """  // Periodic background polling to sync state with backend auto-runner (in-flight guarded)
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
      clearInterval(timer)
    }
  }, [scenario?.id, refreshScenarioData])"""

content_norm = content.replace('\r\n', '\n')
target1_norm = target1.replace('\r\n', '\n')
replacement1_norm = replacement1.replace('\r\n', '\n')
target2_norm = target2.replace('\r\n', '\n')
replacement2_norm = replacement2.replace('\r\n', '\n')
target3_norm = target3.replace('\r\n', '\n')
replacement3_norm = replacement3.replace('\r\n', '\n')

assert target1_norm in content_norm, "Target 1 not found"
content_norm = content_norm.replace(target1_norm, replacement1_norm, 1)

assert target2_norm in content_norm, "Target 2 not found"
content_norm = content_norm.replace(target2_norm, replacement2_norm, 1)

assert target3_norm in content_norm, "Target 3 not found"
content_norm = content_norm.replace(target3_norm, replacement3_norm, 1)

with open('frontend/src/App.tsx', 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(content_norm.replace('\n', '\r\n'))

print("Successfully patched App.tsx")
