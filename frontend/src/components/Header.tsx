// frontend/src/components/Header.tsx
import type { FC } from 'react'
import {
  Activity,
  Database,
  Cpu,
  Layers,
  Play,
  Pause,
  FastForward,
  PlusCircle,
  RefreshCw,
  Clock,
  Zap,
} from 'lucide-react'
import type { Scenario, HealthStatus, MeterlessStatusResponse, ZetarisStatusResponse } from '../lib/types'

interface HeaderProps {
  scenario: Scenario | null
  scenarios?: Scenario[]
  onSelectScenario?: (scenarioId: string) => void
  health: HealthStatus | null
  dbHealth: HealthStatus | null
  meterlessStatus: MeterlessStatusResponse | null
  zetarisStatus: ZetarisStatusResponse | null
  loading: boolean
  isAwaitingDecision?: boolean
  onStep: (steps: number) => void
  onToggleAuto?: () => void
  onOpenRunModal: () => void
  onOpenNewModal: () => void
  onRefresh: () => void
}

export const Header: FC<HeaderProps> = ({
  scenario,
  scenarios = [],
  onSelectScenario,
  health,
  dbHealth,
  meterlessStatus,
  zetarisStatus,
  loading,
  isAwaitingDecision = false,
  onStep,
  onToggleAuto,
  onOpenRunModal,
  onOpenNewModal,
  onRefresh,
}) => {
  const formatTime = (timeStr?: string) => {
    if (!timeStr) return '--:--:--'
    return timeStr.replace('T', ' ').substring(0, 19) + ' UTC'
  }

  const getStatusBadgeClass = (status?: string) => {
    switch (status?.toLowerCase()) {
      case 'nominal':
      case 'running':
      case 'healthy':
        return 'badge-nominal'
      case 'degraded':
        return 'badge-warning'
      case 'failed':
      case 'critical':
      case 'breached':
        return 'badge-danger'
      case 'recovering':
        return 'badge-info'
      default:
        return 'badge-neutral'
    }
  }

  return (
    <header
      style={{
        background: 'var(--bg-canvas)',
        borderBottom: '1px solid var(--border-color)',
        padding: '12px 24px',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '16px',
        position: 'sticky',
        top: 0,
        zIndex: 50,
      }}
    >
      {/* Brand & Active Scenario */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: '3px',
              background: 'var(--accent-cocoa)',
              border: '1px solid var(--accent-cocoa-light)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 2px 4px rgba(0, 0, 0, 0.4)',
            }}
          >
            <Activity size={20} color="#F3EFE5" />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '1.25rem', fontWeight: 800, letterSpacing: '-0.02em', color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>
                Factory<span style={{ color: 'var(--accent-orange)' }}>Ops</span>
              </span>
              <span
                style={{
                  fontSize: '0.65rem',
                  padding: '2px 6px',
                  borderRadius: '2px',
                  background: 'var(--bg-card)',
                  color: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                  fontWeight: 600,
                  border: '1px solid var(--border-subtle)',
                }}
              >
                v0.2.0 • Cocoa SCADA
              </span>
            </div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              Virtual Cocoa Processing & Automated Recovery Operations
            </div>
          </div>
        </div>

        {scenario && (
          <div
            style={{
              borderLeft: '1px solid #1e293b',
              paddingLeft: '16px',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
            }}
          >
            <div>
              <div style={{ fontSize: '0.72rem', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Active Scenario: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-orange)' }}>{scenario.id}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {scenarios && scenarios.length > 0 ? (
                  <select
                    className="select-input"
                    style={{ fontSize: '0.85rem', padding: '2px 8px', maxWidth: '280px', background: 'var(--bg-card)', color: 'var(--text-main)' }}
                    value={scenario.id}
                    onChange={(e) => onSelectScenario?.(e.target.value)}
                    title="Switch active scenario"
                  >
                    {scenarios.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.id}: {s.name} ({s.status}, step {s.current_step})
                      </option>
                    ))}
                  </select>
                ) : (
                  <span style={{ fontWeight: 600, fontSize: '0.9rem', color: '#e2e8f0' }}>{scenario.name}</span>
                )}
                <span className={`badge ${getStatusBadgeClass(scenario.status)}`}>{scenario.status}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Simulation Clock & Step Information */}
      {scenario && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '20px',
            background: '#0f172a',
            border: '1px solid #1e293b',
            padding: '6px 16px',
            borderRadius: '6px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Clock size={16} color="#38bdf8" />
            <div>
              <div style={{ fontSize: '0.68rem', color: '#64748b', textTransform: 'uppercase' }}>Simulation Clock</div>
              <div style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)', fontWeight: 600, color: '#f8fafc' }}>
                {formatTime(scenario.simulation_time)}
              </div>
            </div>
          </div>

          <div style={{ borderLeft: '1px solid #334155', height: '24px' }}></div>

          <div>
            <div style={{ fontSize: '0.68rem', color: '#64748b', textTransform: 'uppercase' }}>Step / Disruption</div>
            <div style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)', fontWeight: 600, color: '#f8fafc' }}>
              Step {scenario.current_step} •{' '}
              <span style={{ color: scenario.disruption_type !== 'none' ? '#fbbf24' : '#94a3b8' }}>
                {scenario.disruption_type}
              </span>
            </div>
          </div>

          <div style={{ borderLeft: '1px solid #334155', height: '24px' }}></div>

          <div>
            <div style={{ fontSize: '0.68rem', color: '#64748b', textTransform: 'uppercase' }}>Progression Mode</div>
            <div>
              {scenario.status === 'completed' ? (
                <span className="badge badge-nominal" style={{ fontSize: '0.70rem' }} title="Simulation completed all batches">
                  🏁 Completed
                </span>
              ) : scenario.status === 'failed' ? (
                <span className="badge badge-danger" style={{ fontSize: '0.70rem' }} title="Simulation halted due to failure">
                  🛑 Halted (Failed)
                </span>
              ) : scenario.status === 'cancelled' ? (
                <span className="badge badge-neutral" style={{ fontSize: '0.70rem' }} title="Simulation cancelled">
                  ⏹️ Cancelled
                </span>
              ) : isAwaitingDecision ? (
                <span className="badge badge-warning" style={{ fontSize: '0.70rem' }} title="Automatic progression paused awaiting operator recovery decision">
                  ⚠️ Awaiting Decision
                </span>
              ) : scenario.status === 'recovering' ? (
                <span className="badge badge-info" style={{ fontSize: '0.70rem' }} title="Maintenance recovery in progress">
                  🔧 Recovering ({scenario.parameters?.recovery?.remaining_steps ?? 0} steps left)
                </span>
              ) : scenario.parameters?.auto_advance === false ? (
                <span className="badge badge-neutral" style={{ fontSize: '0.70rem' }} title="Automatic progression paused by operator">
                  ⏸️ Auto Paused
                </span>
              ) : (
                <span className="badge badge-nominal" style={{ fontSize: '0.70rem' }} title="Automatic 5-second simulation progression active">
                  🟢 Auto (1 step / 5s)
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Integration Badges & Actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        {/* System Health Badges */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }} title="System Integration Health Status">
          <div
            className={`badge ${health?.status === 'healthy' ? 'badge-nominal' : 'badge-danger'}`}
            style={{ fontSize: '0.68rem' }}
          >
            <Activity size={12} />
            FastAPI
          </div>

          <div
            className={`badge ${dbHealth?.database === 'connected' ? 'badge-nominal' : 'badge-danger'}`}
            style={{ fontSize: '0.68rem' }}
          >
            <Database size={12} />
            PostgreSQL:5433
          </div>

          <div
            className={`badge ${meterlessStatus?.status === 'online' || meterlessStatus?.status === 'authorized' ? 'badge-nominal' : 'badge-neutral'}`}
            style={{ fontSize: '0.68rem' }}
            title={`Meterless AI Provider: ${meterlessStatus?.status || 'checking'} (${meterlessStatus?.message || ''})`}
          >
            <Cpu size={12} />
            Meterless WM
          </div>

          <div
            className={`badge ${zetarisStatus?.connected ? 'badge-nominal' : 'badge-warning'}`}
            style={{ fontSize: '0.68rem' }}
            title={`Zetaris Federation: ${zetarisStatus?.connected ? 'REST:8888 Online' : 'Fallback active'}`}
          >
            <Layers size={12} />
            Zetaris USL
          </div>
        </div>

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            className="btn btn-primary"
            onClick={onOpenRunModal}
            disabled={loading || !scenario}
            title="Open Disruption Simulator to execute catalogue disruption or random stress-test"
            style={{ fontWeight: 700 }}
          >
            <Zap size={14} />
            Run
          </button>

          {onToggleAuto && (
            <button
              className={`btn ${scenario?.parameters?.auto_advance === false ? 'btn-success' : 'btn-secondary'}`}
              onClick={onToggleAuto}
              disabled={loading || !scenario}
              title={
                scenario?.parameters?.auto_advance === false
                  ? 'Resume automatic 5-second simulation progression'
                  : 'Pause automatic simulation progression'
              }
            >
              {scenario?.parameters?.auto_advance === false ? <Play size={14} /> : <Pause size={14} />}
              {scenario?.parameters?.auto_advance === false ? 'Resume Auto' : 'Pause Auto'}
            </button>
          )}

          <button
            className="btn btn-primary"
            onClick={() => onStep(1)}
            disabled={loading || !scenario || scenario.status === 'completed' || scenario.status === 'failed' || scenario.status === 'cancelled'}
            title="Advance factory simulation clock by 1 discrete step (15 mins)"
          >
            <Play size={14} />
            Step +1
          </button>

          <button
            className="btn btn-warning"
            onClick={() => onStep(3)}
            disabled={loading || !scenario || scenario.status === 'completed' || scenario.status === 'failed' || scenario.status === 'cancelled'}
            title="Advance simulation by 3 steps directly to trigger roaster bearing thermal runaway"
          >
            <FastForward size={14} />
            Step to Anomaly (+3)
          </button>

          <button
            className="btn btn-secondary"
            onClick={onOpenNewModal}
            disabled={loading}
            title="Create a new isolated simulation run"
          >
            <PlusCircle size={14} />
            New Run
          </button>

          <button
            className="btn btn-secondary"
            onClick={onRefresh}
            disabled={loading}
            title="Refresh current state"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>
    </header>
  )
}
