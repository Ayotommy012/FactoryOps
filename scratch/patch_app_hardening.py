with open('frontend/src/App.tsx', 'rb') as f:
    raw = f.read()

content = raw.replace(b'\r\r\n', b'\n').replace(b'\r\n', b'\n').decode('utf-8')

# 1. Update handleStep to pass { full: true }
target_step = """      showToast(`Advanced simulation clock by ${steps} step(s) to step ${res.current_step}.`)
      await refreshScenarioData(currentScenarioId)"""

replacement_step = """      showToast(`Advanced simulation clock by ${steps} step(s) to step ${res.current_step}.`)
      await refreshScenarioData(currentScenarioId, { full: true })"""

assert target_step in content, "Target step not found in App.tsx"
content = content.replace(target_step, replacement_step, 1)

# 2. Update handleDecide to pass { full: true }
target_decide = """      showToast(
        `Decision applied: ${res.decision.toUpperCase()} for ${res.plan_id}. Status: ${res.updated_scenario_status}`
      )
      await refreshScenarioData(currentScenarioId)"""

replacement_decide = """      showToast(
        `Decision applied: ${res.decision.toUpperCase()} for ${res.plan_id}. Status: ${res.updated_scenario_status}`
      )
      await refreshScenarioData(currentScenarioId, { full: true })"""

assert target_decide in content, "Target decide not found in App.tsx"
content = content.replace(target_decide, replacement_decide, 1)

# 3. Update handleCreateScenario to pass { full: true }
target_create = """      showToast(`Created new simulation scenario: ${created.name} (${created.id})`)
      refreshScenarioList()
      await refreshScenarioData(created.id)"""

replacement_create = """      showToast(`Created new simulation scenario: ${created.name} (${created.id})`)
      refreshScenarioList()
      await refreshScenarioData(created.id, { full: true })"""

assert target_create in content, "Target create not found in App.tsx"
content = content.replace(target_create, replacement_create, 1)

# 4. Update handleSelectScenario to pass { full: true }
target_select = """      setImpact(null)
      setPlans([])
      await refreshScenarioData(selectedId)"""

replacement_select = """      setImpact(null)
      setPlans([])
      await refreshScenarioData(selectedId, { full: true })"""

assert target_select in content, "Target select not found in App.tsx"
content = content.replace(target_select, replacement_select, 1)

# 5. In polling cleanup, ensure isPollingRef is reset
target_poll_cleanup = """    return () => {
      isCancelled = true
      clearInterval(timer)
    }"""

replacement_poll_cleanup = """    return () => {
      isCancelled = true
      isPollingRef.current = false
      clearInterval(timer)
    }"""

assert target_poll_cleanup in content, "Target poll cleanup not found in App.tsx"
content = content.replace(target_poll_cleanup, replacement_poll_cleanup, 1)

# 6. Add tab switch handler to refresh data on tab change
target_tabs = """      {/* Navigation Tabs */}
      <Tabs
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        hasActiveAnomaly={isRoasterDegraded}
        eventCount={timeline.length}
      />"""

replacement_tabs = """      {/* Navigation Tabs */}
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
      />"""

assert target_tabs in content, "Target tabs not found in App.tsx"
content = content.replace(target_tabs, replacement_tabs, 1)

# 7. Update onRefresh in Header
target_header_refresh = """onRefresh={() => scenario && refreshScenarioData(scenario.id)}"""
replacement_header_refresh = """onRefresh={() => scenario && refreshScenarioData(scenario.id, { full: true })}"""
assert target_header_refresh in content, "Target header refresh not found in App.tsx"
content = content.replace(target_header_refresh, replacement_header_refresh, 1)

with open('frontend/src/App.tsx', 'wb') as f:
    f.write(content.replace('\n', '\r\n').encode('utf-8'))

print("Successfully updated App.tsx")
