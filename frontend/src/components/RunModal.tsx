// frontend/src/components/RunModal.tsx
// Centered Modal Dialog for Disruption Selection & Execution

import { useState, useEffect, useCallback, type FC } from 'react'
import {
  AlertTriangle,
  CheckCircle,
  Dices,
  RefreshCw,
  Sliders,
  X,
  Zap,
} from 'lucide-react'
import { api } from '../lib/api'
import type { DisruptionCatalogueItem, Scenario } from '../lib/types'

interface RunModalProps {
  isOpen: boolean
  onClose: () => void
  scenario: Scenario | null
  onSuccess: (message?: string) => void
}

export const RunModal: FC<RunModalProps> = ({
  isOpen,
  onClose,
  scenario,
  onSuccess,
}) => {
  const [catalogue, setCatalogue] = useState<DisruptionCatalogueItem[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedDisruption, setSelectedDisruption] = useState<string | null>(null)
  const [selectedCategory, setSelectedCategory] = useState<string>('all')
  const [customParams, setCustomParams] = useState<Record<string, Record<string, any>>>({})

  // Fetch dynamic disruption catalogue from backend
  const fetchCatalogue = useCallback(async () => {
    if (!scenario?.id) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.getDisruptionsCatalogue(scenario.id)
      setCatalogue(res.disruptions || [])
      // Initialize default parameters
      const initial: Record<string, Record<string, any>> = {}
      res.disruptions.forEach((d) => {
        initial[d.type_id] = { ...d.default_parameters }
      })
      setCustomParams(initial)
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch disruption catalogue. Ensure backend is accessible.')
    } finally {
      setLoading(false)
    }
  }, [scenario?.id])

  // Fetch when opened
  useEffect(() => {
    if (isOpen) {
      fetchCatalogue()
      setSelectedDisruption(null)
    }
  }, [isOpen, fetchCatalogue])

  // Close on Escape key press
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !submitting) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, submitting, onClose])

  if (!isOpen) return null

  // Trigger named disruption
  const handleTrigger = async (typeId: string) => {
    if (!scenario?.id || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const params = customParams[typeId] || {}
      const res = await api.triggerDisruption(scenario.id, typeId, params)
      const label = res.disruption_name || res.disruption_type || typeId
      onSuccess(`Triggered '${label}' disruption successfully at Step ${res.current_step}.`)
      onClose()
    } catch (err: any) {
      setError(err?.message || `Failed to trigger disruption '${typeId}'.`)
    } finally {
      setSubmitting(false)
    }
  }

  // Trigger random disruption
  const handleRandomTrigger = async () => {
    if (!scenario?.id || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.triggerDisruption(scenario.id, 'random')
      const label = res.disruption_name || res.disruption_type || 'Unknown'
      onSuccess(`🎲 Random Disruption '${label}' successfully injected at Step ${res.current_step}!`)
      onClose()
    } catch (err: any) {
      setError(err?.message || 'Failed to trigger random scenario disruption.')
    } finally {
      setSubmitting(false)
    }
  }

  const handleParamChange = (typeId: string, paramName: string, value: any) => {
    setCustomParams((prev) => ({
      ...prev,
      [typeId]: {
        ...(prev[typeId] || {}),
        [paramName]: value,
      },
    }))
  }

  const categories = ['all', 'Supply Chain', 'Machinery & Equipment', 'Inventory & Quality']
  const filteredDisruptions = catalogue.filter((d) =>
    selectedCategory === 'all' ? true : d.category === selectedCategory
  )

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        // Close on backdrop click (outside modal content)
        if (e.target === e.currentTarget && !submitting) {
          onClose()
        }
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="run-modal-title"
    >
      <div className="modal-box" style={{ maxWidth: '780px', width: '94%', maxHeight: '88vh' }}>
        {/* Modal Header */}
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div
              style={{
                width: '28px',
                height: '28px',
                borderRadius: '2px',
                background: 'rgba(34, 197, 94, 0.15)',
                border: '1px solid var(--accent-green)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Zap size={16} color="var(--accent-green-bright)" />
            </div>
            <div>
              <h3 id="run-modal-title" style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)', margin: 0 }}>
                Run Disruption Simulator
              </h3>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                Scenario: <span style={{ color: 'var(--accent-green-bright)' }}>{scenario?.name || scenario?.id}</span> • Step {scenario?.current_step}
              </div>
            </div>
          </div>

          <button
            onClick={onClose}
            disabled={submitting}
            className="btn-close"
            title="Close dialog (Escape)"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="modal-body" style={{ padding: '16px 20px', overflowY: 'auto' }}>
          {/* Inline Error Banner with Retry */}
          {error && (
            <div
              style={{
                background: 'rgba(239, 68, 68, 0.15)',
                border: '1px solid var(--accent-crimson)',
                borderRadius: '3px',
                padding: '10px 14px',
                marginBottom: '16px',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '12px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#FCA5A5', fontSize: '0.8rem' }}>
                <AlertTriangle size={16} />
                <span>{error}</span>
              </div>
              <button
                className="btn btn-secondary"
                onClick={fetchCatalogue}
                disabled={loading || submitting}
                style={{ fontSize: '0.72rem', padding: '4px 10px' }}
              >
                <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
                Retry
              </button>
            </div>
          )}

          {/* Prominent Random Scenario Option Card */}
          <div
            style={{
              background: 'linear-gradient(135deg, rgba(34, 197, 94, 0.08) 0%, rgba(20, 30, 20, 0.6) 100%)',
              border: '1px solid var(--accent-green)',
              borderRadius: '4px',
              padding: '14px 18px',
              marginBottom: '18px',
              display: 'flex',
              flexWrap: 'wrap',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: '12px',
            }}
          >
            <div style={{ flex: '1 1 300px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                <Dices size={18} color="var(--accent-green-bright)" />
                <span style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>
                  Random Scenario Injection
                </span>
                <span className="badge badge-nominal" style={{ fontSize: '0.65rem' }}>
                  Unbiased Stress Test
                </span>
              </div>
              <p style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', margin: 0 }}>
                Selects and triggers an eligible physical, supply chain, or quality disruption at random to challenge operator response and automated recovery plans.
              </p>
            </div>

            <button
              className="btn btn-primary"
              onClick={handleRandomTrigger}
              disabled={loading || submitting}
              style={{
                background: 'var(--accent-green-dark)',
                borderColor: 'var(--accent-green)',
                color: '#F0FDF4',
                padding: '8px 18px',
                fontWeight: 700,
                fontSize: '0.82rem',
                minWidth: '150px',
              }}
            >
              {submitting ? 'Injecting...' : '🎲 Execute Random'}
            </button>
          </div>

          {/* Category Filter Tabs */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px', flexWrap: 'wrap', gap: '8px' }}>
            <div style={{ display: 'flex', gap: '6px' }}>
              {categories.map((cat) => (
                <button
                  key={cat}
                  className={`btn ${selectedCategory === cat ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setSelectedCategory(cat)}
                  style={{ fontSize: '0.70rem', padding: '4px 10px' }}
                >
                  {cat === 'all' ? 'All Disruptions' : cat}
                </button>
              ))}
            </div>

            <button
              className="btn btn-secondary"
              onClick={fetchCatalogue}
              disabled={loading || submitting}
              style={{ fontSize: '0.70rem', padding: '4px 8px' }}
              title="Refresh catalogue eligibility"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              Sync
            </button>
          </div>

          {/* Loading Indicator */}
          {loading && catalogue.length === 0 && (
            <div style={{ textAlign: 'center', padding: '30px 0', color: 'var(--text-muted)', fontSize: '0.82rem', fontFamily: 'var(--font-mono)' }}>
              <RefreshCw size={24} className="animate-spin" style={{ margin: '0 auto 10px', color: 'var(--accent-green)' }} />
              Connecting to disruption catalogue...
            </div>
          )}

          {/* Catalogue List */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {filteredDisruptions.map((d) => {
              const isEligible = d.eligible
              const isSelected = selectedDisruption === d.type_id
              const params = customParams[d.type_id] || {}

              return (
                <div
                  key={d.type_id}
                  style={{
                    background: isSelected ? 'var(--bg-card-hover)' : 'var(--bg-canvas)',
                    border: `1px solid ${isSelected ? 'var(--accent-green)' : 'var(--border-subtle)'}`,
                    borderRadius: '3px',
                    padding: '12px 14px',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', flexWrap: 'wrap' }}>
                    <div style={{ flex: '1 1 350px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                        <span style={{ fontWeight: 700, fontSize: '0.86rem', color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>
                          {d.name}
                        </span>
                        <span className="badge badge-neutral" style={{ fontSize: '0.62rem' }}>
                          {d.category}
                        </span>
                        {isEligible ? (
                          <span className="badge badge-nominal" style={{ fontSize: '0.62rem' }}>
                            <CheckCircle size={10} /> Ready
                          </span>
                        ) : (
                          <span className="badge badge-warning" style={{ fontSize: '0.62rem' }}>
                            Ineligible
                          </span>
                        )}
                      </div>

                      <p style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', margin: '0 0 6px' }}>
                        {d.explanation}
                      </p>

                      <div style={{ fontSize: '0.70rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                        Prerequisite: {d.prerequisites}
                        {!isEligible && d.ineligibility_reason && (
                          <span style={{ color: 'var(--accent-amber)', marginLeft: '6px' }}>
                            • {d.ineligibility_reason}
                          </span>
                        )}
                      </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {Object.keys(d.default_parameters || {}).length > 0 && (
                        <button
                          className="btn btn-secondary"
                          onClick={() => setSelectedDisruption(isSelected ? null : d.type_id)}
                          style={{ fontSize: '0.70rem', padding: '5px 8px' }}
                          title="Configure custom parameters"
                        >
                          <Sliders size={12} />
                        </button>
                      )}

                      <button
                        className="btn btn-primary"
                        onClick={() => handleTrigger(d.type_id)}
                        disabled={!isEligible || submitting}
                        style={{
                          fontSize: '0.74rem',
                          padding: '6px 14px',
                          background: isEligible ? 'var(--accent-green-dark)' : undefined,
                          borderColor: isEligible ? 'var(--accent-green)' : undefined,
                        }}
                      >
                        {submitting ? 'Triggering...' : 'Trigger'}
                      </button>
                    </div>
                  </div>

                  {/* Inline Parameter Customizer if expanded */}
                  {isSelected && Object.keys(d.default_parameters || {}).length > 0 && (
                    <div
                      style={{
                        marginTop: '10px',
                        paddingTop: '10px',
                        borderTop: '1px solid var(--border-subtle)',
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                        gap: '10px',
                      }}
                    >
                      {Object.entries(d.default_parameters).map(([key, defaultVal]) => (
                        <div key={key}>
                          <label style={{ fontSize: '0.68rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '2px', fontFamily: 'var(--font-mono)' }}>
                            {key.replace(/_/g, ' ')}
                          </label>
                          <input
                            type={typeof defaultVal === 'number' ? 'number' : 'text'}
                            step={typeof defaultVal === 'number' && defaultVal % 1 !== 0 ? '0.1' : '1'}
                            value={params[key] ?? defaultVal}
                            onChange={(e) =>
                              handleParamChange(
                                d.type_id,
                                key,
                                typeof defaultVal === 'number' ? Number(e.target.value) : e.target.value
                              )
                            }
                            className="input-text"
                            style={{ width: '100%', fontSize: '0.76rem', padding: '4px 8px' }}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Modal Footer */}
        <div className="modal-footer" style={{ padding: '12px 20px', borderTop: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
          <button
            className="btn btn-secondary"
            onClick={onClose}
            disabled={submitting}
            style={{ fontSize: '0.76rem' }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
