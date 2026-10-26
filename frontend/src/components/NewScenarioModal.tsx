// frontend/src/components/NewScenarioModal.tsx
import { useState, useEffect, type FC, type FormEvent } from 'react'
import { PlusCircle, Sparkles } from 'lucide-react'

interface NewScenarioModalProps {
  isOpen: boolean
  onClose: () => void
  onCreate: (params: {
    name: string
    disruption_type: string
    random_seed: number
    step_size_minutes: number
  }) => Promise<void>
}

export const NewScenarioModal: FC<NewScenarioModalProps> = ({
  isOpen,
  onClose,
  onCreate,
}) => {
  const [name, setName] = useState('Cocoa Roasting Disruption Run')
  const [disruptionType, setDisruptionType] = useState('roaster_bearing_overheat')
  const [randomSeed, setRandomSeed] = useState(42)
  const [stepSizeMinutes, setStepSizeMinutes] = useState(15)
  const [creating, setCreating] = useState(false)

  // Close on Escape key press
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !creating) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, creating, onClose])

  if (!isOpen) return null

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setCreating(true)
    try {
      await onCreate({
        name,
        disruption_type: disruptionType,
        random_seed: randomSeed,
        step_size_minutes: stepSizeMinutes,
      })
      onClose()
    } finally {
      setCreating(false)
    }
  }

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && !creating) {
          onClose()
        }
      }}
      role="dialog"
      aria-modal="true"
    >
      <div className="modal-box" style={{ maxWidth: '640px', width: '92%' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '16px',
            borderBottom: '1px solid var(--border-color)',
            paddingBottom: '12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <PlusCircle size={20} color="var(--accent-green-bright)" />
            <h3 style={{ fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-main)', margin: 0 }}>
              Initialize New Simulation Scenario
            </h3>
          </div>
          <button
            style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '1.1rem' }}
            onClick={onClose}
            disabled={creating}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div>
            <label style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px', fontFamily: 'var(--font-mono)' }}>
              Scenario Name
            </label>
            <input
              type="text"
              className="input-text"
              style={{ width: '100%' }}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div>
            <label style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px', fontFamily: 'var(--font-mono)' }}>
              Disruption Profile
            </label>
            <select
              className="select-input"
              style={{ width: '100%' }}
              value={disruptionType}
              onChange={(e) => setDisruptionType(e.target.value)}
            >
              <option value="roaster_bearing_overheat">
                Roaster Bearing Overheat (Thermal Runaway + NASA IMS grounding)
              </option>
              <option value="supplier_customs_delay">
                Raw Cocoa Bean Customs Inspection Delay (+6h delivery hold)
              </option>
              <option value="supplier_shortfall">
                Inbound Shipment Volume Shortfall (-5,000 kg shortage)
              </option>
              <option value="equipment_failure">
                Impact Grinding Mill Drive Motor Failure (Catastrophic stop)
              </option>
              <option value="reduced_machine_capacity">
                Hydraulic Press Line Pressure Degradation (-60% capacity)
              </option>
              <option value="quality_contamination">
                High-Moisture Raw Bean Lot Quarantine Hold (QA lock)
              </option>
              <option value="none">
                Nominal Baseline Run (No intentional disruption)
              </option>
            </select>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <div>
              <label style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px', fontFamily: 'var(--font-mono)' }}>
                Reproducible Random Seed
              </label>
              <input
                type="number"
                className="input-text"
                style={{ width: '100%' }}
                value={randomSeed}
                onChange={(e) => setRandomSeed(Number(e.target.value))}
              />
            </div>

            <div>
              <label style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px', fontFamily: 'var(--font-mono)' }}>
                Step Size (Minutes)
              </label>
              <input
                type="number"
                className="input-text"
                style={{ width: '100%' }}
                value={stepSizeMinutes}
                onChange={(e) => setStepSizeMinutes(Number(e.target.value))}
              />
            </div>
          </div>

          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: '10px',
              marginTop: '10px',
              borderTop: '1px solid var(--border-subtle)',
              paddingTop: '14px',
            }}
          >
            <button type="button" className="btn btn-secondary" onClick={onClose} disabled={creating}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={creating || !name.trim()}>
              <Sparkles size={14} />
              {creating ? 'Spawning Scenario...' : 'Create & Launch Simulation'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
