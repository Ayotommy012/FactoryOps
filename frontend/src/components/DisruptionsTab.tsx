// frontend/src/components/DisruptionsTab.tsx
// Industrial Disruption Simulator Catalogue and Execution Console

import { useEffect, useState, type FC } from 'react'
import { api } from '../lib/api'
import type { DisruptionCatalogueItem, Scenario } from '../lib/types'

interface DisruptionsTabProps {
  scenario: Scenario
  onRefresh: () => void
  onNavigateToRecovery?: () => void
}

export const DisruptionsTab: FC<DisruptionsTabProps> = ({
  scenario,
  onRefresh,
  onNavigateToRecovery,
}) => {
  const [catalogue, setCatalogue] = useState<DisruptionCatalogueItem[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedCategory, setSelectedCategory] = useState<string>('all')
  const [triggeringType, setTriggeringType] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [customParams, setCustomParams] = useState<Record<string, Record<string, any>>>({})

  const fetchCatalogue = async () => {
    try {
      setLoading(true)
      const res = await api.getDisruptionsCatalogue(scenario.id)
      setCatalogue(res.disruptions)
      // Initialize default parameters
      const initial: Record<string, Record<string, any>> = {}
      res.disruptions.forEach((d) => {
        initial[d.type_id] = { ...d.default_parameters }
      })
      setCustomParams(initial)
      setError(null)
    } catch (err: any) {
      setError(err.message || 'Failed to load disruptions catalogue.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchCatalogue()
  }, [scenario.id, scenario.current_step, scenario.status])

  const handleParamChange = (typeId: string, paramName: string, value: any) => {
    setCustomParams((prev) => ({
      ...prev,
      [typeId]: {
        ...(prev[typeId] || {}),
        [paramName]: value,
      },
    }))
  }

  const handleTrigger = async (typeId: string) => {
    try {
      setTriggeringType(typeId)
      setActionMessage(null)
      const params = customParams[typeId] || {}
      const res = await api.triggerDisruption(scenario.id, typeId, params)
      setActionMessage({
        type: 'success',
        text: `Triggered '${res.disruption_name || res.disruption_type}' successfully at Step ${res.current_step}. Affected: ${res.affected_entities.join(', ')}`,
      })
      onRefresh()
      fetchCatalogue()
    } catch (err: any) {
      setActionMessage({
        type: 'error',
        text: err.message || `Failed to trigger disruption '${typeId}'.`,
      })
    } finally {
      setTriggeringType(null)
    }
  }

  const handleRandomTrigger = async () => {
    try {
      setTriggeringType('random')
      setActionMessage(null)
      const res = await api.triggerDisruption(scenario.id, 'random')
      setActionMessage({
        type: 'success',
        text: `🎲 Random selection triggered: '${res.disruption_name || res.disruption_type}' at Step ${res.current_step}.`,
      })
      onRefresh()
      fetchCatalogue()
    } catch (err: any) {
      setActionMessage({
        type: 'error',
        text: err.message || 'Failed to trigger random disruption.',
      })
    } finally {
      setTriggeringType(null)
    }
  }

  const categories = ['all', 'Supply Chain', 'Machinery & Equipment', 'Inventory & Quality']
  const filteredDisruptions = catalogue.filter((d) =>
    selectedCategory === 'all' ? true : d.category === selectedCategory
  )

  const activeIncidents = scenario.parameters?.active_incidents || scenario.parameters?.snapshot?.active_incidents || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Top Banner & Random Trigger */}
      <div className="panel" style={{ background: 'var(--bg-canvas)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <h2 style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-main)', marginBottom: '4px' }}>
              Industrial Disruption Simulator & Stress Testing
            </h2>
            <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
              Inject physical, supply chain, and biochemical anomalies to test factory resilience, World Model telemetry tracing, and automated recovery planning.
            </p>
          </div>

          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              className="btn btn-primary"
              onClick={handleRandomTrigger}
              disabled={loading || triggeringType !== null}
              style={{ padding: '8px 18px', fontSize: '0.82rem' }}
            >
              {triggeringType === 'random' ? 'Selecting...' : '🎲 Trigger Random Disruption'}
            </button>
            <button
              className="btn"
              onClick={fetchCatalogue}
              disabled={loading}
              title="Refresh catalogue eligibility"
            >
              ↻ Refresh
            </button>
          </div>
        </div>

        {/* Feedback Alert */}
        {actionMessage && (
          <div
            style={{
              marginTop: '14px',
              padding: '10px 14px',
              borderRadius: '2px',
              fontSize: '0.8rem',
              fontFamily: 'var(--font-mono)',
              background: actionMessage.type === 'success' ? 'var(--status-nominal-bg)' : 'var(--status-danger-bg)',
              borderColor: actionMessage.type === 'success' ? 'var(--status-nominal-border)' : 'var(--status-danger-border)',
              borderWidth: '1px',
              borderStyle: 'solid',
              color: actionMessage.type === 'success' ? '#B5C4AC' : '#DFA29D',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span>{actionMessage.text}</span>
            {actionMessage.type === 'success' && onNavigateToRecovery && (
              <button
                className="btn btn-primary"
                onClick={onNavigateToRecovery}
                style={{ padding: '3px 8px', fontSize: '0.72rem' }}
              >
                View Recovery Intelligence →
              </button>
            )}
          </div>
        )}
      </div>

      {/* Active Incidents Banner if present */}
      {activeIncidents.length > 0 && (
        <div className="panel" style={{ borderLeft: '3px solid var(--accent-orange)' }}>
          <div className="panel-header">
            <span className="panel-title" style={{ color: 'var(--accent-orange)' }}>
              ⚠ Active Disruption Incidents ({activeIncidents.length})
            </span>
          </div>
          <table className="scada-table">
            <thead>
              <tr>
                <th>Incident ID</th>
                <th>Disruption Type</th>
                <th>Severity</th>
                <th>Triggered Step</th>
                <th>Status</th>
                <th>Affected System</th>
              </tr>
            </thead>
            <tbody>
              {activeIncidents.map((inc: any, idx: number) => (
                <tr key={idx}>
                  <td>{inc.incident_id || `INC-${idx + 1}`}</td>
                  <td style={{ fontWeight: 600, color: 'var(--accent-orange)' }}>{inc.type}</td>
                  <td>
                    <span
                      className={`badge ${
                        inc.severity === 'critical'
                          ? 'badge-danger'
                          : inc.severity === 'high'
                          ? 'badge-warning'
                          : inc.severity === 'medium'
                          ? 'badge-info'
                          : 'badge-nominal'
                      }`}
                    >
                      {inc.severity ? inc.severity.toUpperCase() : 'UNCLASSIFIED'}
                    </span>
                  </td>
                  <td>Step {inc.triggered_at_step ?? inc.step}</td>
                  <td>
                    <span className="badge badge-warning">{inc.status || 'active'}</span>
                  </td>
                  <td>{inc.entity_id || 'Factory Plant Core'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Category Filter Pills */}
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <span style={{ fontSize: '0.75rem', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          Category Filter:
        </span>
        {categories.map((cat) => (
          <button
            key={cat}
            className={`btn ${selectedCategory === cat ? 'btn-primary' : ''}`}
            onClick={() => setSelectedCategory(cat)}
            style={{
              padding: '4px 10px',
              fontSize: '0.72rem',
              borderColor: selectedCategory === cat ? 'var(--accent-cocoa-light)' : 'var(--border-subtle)',
            }}
          >
            {cat === 'all' ? 'All Disruptions (6)' : cat}
          </button>
        ))}
      </div>

      {/* Catalogue Cards Grid */}
      {loading ? (
        <div className="panel" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
          Loading disruption catalogue and eligibility matrix...
        </div>
      ) : error ? (
        <div className="panel" style={{ color: 'var(--status-danger)' }}>
          {error}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: '16px' }}>
          {filteredDisruptions.map((d) => {
            const params = customParams[d.type_id] || d.default_parameters || {}
            const isTriggering = triggeringType === d.type_id

            return (
              <div
                key={d.type_id}
                className="panel"
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  borderTop: d.eligible ? '3px solid var(--accent-cocoa)' : '3px solid var(--border-subtle)',
                  opacity: d.eligible ? 1.0 : 0.75,
                }}
              >
                <div>
                  {/* Card Header: Category & Eligibility */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                    <span
                      style={{
                        fontSize: '0.68rem',
                        fontWeight: 700,
                        fontFamily: 'var(--font-mono)',
                        textTransform: 'uppercase',
                        color:
                          d.category === 'Supply Chain'
                            ? '#C66B3D'
                            : d.category === 'Machinery & Equipment'
                            ? '#7D8B72'
                            : '#8B634E',
                      }}
                    >
                      {d.category}
                    </span>

                    <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                      <span
                        className={`badge ${
                          d.severity === 'critical'
                            ? 'badge-danger'
                            : d.severity === 'high'
                            ? 'badge-warning'
                            : d.severity === 'medium'
                            ? 'badge-info'
                            : 'badge-nominal'
                        }`}
                        style={{ fontSize: '0.65rem' }}
                        title="Typical severity at default parameters; a triggered incident is re-classified from live evidence"
                      >
                        {d.severity ? `TYPICAL ${d.severity.toUpperCase()}` : 'UNCLASSIFIED'}
                      </span>
                      {d.eligible ? (
                        <span className="badge badge-nominal">Eligible</span>
                      ) : (
                        <span className="badge badge-info" title={d.ineligibility_reason || 'Prerequisites not met'}>
                          Ineligible
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Title */}
                  <h3 style={{ fontSize: '0.95rem', fontWeight: 700, color: 'var(--text-main)', marginBottom: '8px', lineHeight: 1.3 }}>
                    {d.name}
                  </h3>

                  {/* Explanation */}
                  <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: '12px', lineHeight: 1.4 }}>
                    {d.explanation}
                  </p>

                  {/* Prerequisites */}
                  <div
                    style={{
                      padding: '8px 10px',
                      background: 'var(--bg-canvas)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '2px',
                      fontSize: '0.72rem',
                      fontFamily: 'var(--font-mono)',
                      color: 'var(--text-muted)',
                      marginBottom: '14px',
                    }}
                  >
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Pre-condition: </span>
                    {d.prerequisites}
                    {!d.eligible && d.ineligibility_reason && (
                      <div style={{ marginTop: '4px', color: 'var(--accent-orange)' }}>
                        ✗ Currently blocked: {d.ineligibility_reason}
                      </div>
                    )}
                  </div>

                  {/* Parameter Tuning Inputs */}
                  {Object.keys(d.parameter_schema).length > 0 && (
                    <div style={{ marginBottom: '16px' }}>
                      <span style={{ fontSize: '0.70rem', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>
                        Parameters Tuning:
                      </span>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {Object.entries(d.parameter_schema).map(([pName, schema]: [string, any]) => (
                          <div key={pName} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <label style={{ fontSize: '0.74rem', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                              {pName.replace(/_/g, ' ')} ({schema.unit || ''}):
                            </label>
                            <input
                              type="number"
                              min={schema.min}
                              max={schema.max}
                              step={schema.type === 'float' ? '0.1' : '1'}
                              value={params[pName] ?? schema.default}
                              onChange={(e) =>
                                handleParamChange(
                                  d.type_id,
                                  pName,
                                  schema.type === 'float' ? parseFloat(e.target.value) : parseInt(e.target.value, 10)
                                )
                              }
                              disabled={!d.eligible || isTriggering}
                              style={{
                                width: '90px',
                                background: 'var(--bg-canvas)',
                                border: '1px solid var(--border-color)',
                                color: 'var(--text-main)',
                                padding: '4px 8px',
                                fontSize: '0.76rem',
                                fontFamily: 'var(--font-mono)',
                                borderRadius: '2px',
                                textAlign: 'right',
                              }}
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Trigger Button */}
                <button
                  className="btn btn-primary"
                  onClick={() => handleTrigger(d.type_id)}
                  disabled={!d.eligible || isTriggering}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: d.eligible ? 'var(--accent-cocoa)' : 'var(--bg-canvas)',
                    borderColor: d.eligible ? 'var(--accent-cocoa-light)' : 'var(--border-subtle)',
                  }}
                >
                  {isTriggering ? 'Simulating Impact...' : `Trigger ${d.name.split(' ')[0]}`}
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
