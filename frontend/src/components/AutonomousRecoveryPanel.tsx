// frontend/src/components/AutonomousRecoveryPanel.tsx
import { useEffect, useState, type FC } from 'react'
import { Bot, Loader2, ShieldAlert, UserCheck, CheckCircle2, Clock } from 'lucide-react'
import { api } from '../lib/api'
import type { AutonomousHistoryResponse, Scenario, TimingStatusResponse } from '../lib/types'

interface AutonomousRecoveryPanelProps {
  scenario: Scenario | null
  timing: TimingStatusResponse | null
}

const STATUS_VIEW: Record<string, { label: string; badge: string; icon: FC<{ size?: number }> }> = {
  investigating: { label: 'Investigating — Meterless selecting plan', badge: 'badge-info', icon: Loader2 },
  in_progress: { label: 'Executing autonomously', badge: 'badge-warning', icon: Bot },
  completed: { label: 'Recovery completed', badge: 'badge-nominal', icon: CheckCircle2 },
  pending_approval: { label: 'Awaiting operator approval', badge: 'badge-warning', icon: UserCheck },
  blocked: { label: 'Blocked by safety policy', badge: 'badge-danger', icon: ShieldAlert },
  awaiting_decision: { label: 'Awaiting autonomous evaluation', badge: 'badge-info', icon: Clock },
  no_action_required: { label: 'No action required', badge: 'badge-nominal', icon: CheckCircle2 },
}

const SOURCE_LABEL: Record<string, string> = {
  meterless_runtime: 'Meterless Runtime (live inference)',
  meterless_nightshift: 'Meterless Nightshift job',
  nvidia_nemotron: 'NVIDIA Nemotron',
  deterministic_fallback: 'Deterministic fallback',
}

export const AutonomousRecoveryPanel: FC<AutonomousRecoveryPanelProps> = ({ scenario, timing }) => {
  const [history, setHistory] = useState<AutonomousHistoryResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const recovery = scenario?.parameters?.recovery || {}
  const summary = scenario?.parameters?.autonomous_recovery_history
  const enabled = scenario?.parameters?.autonomous_recovery?.enabled === true
  const lifecycleKey = `${summary?.lifecycle_id || ''}|${recovery.status || ''}`

  // Refetch audit events only when the lifecycle or recovery status changes.
  useEffect(() => {
    if (!scenario?.id) return
    api.getAutonomousHistory(scenario.id).then(setHistory).catch(() => setHistory(null))
  }, [scenario?.id, lifecycleKey])

  if (!scenario) return null

  const status = recovery.status || (recovery.decision === 'approved' ? 'in_progress' : undefined)
  const view = status ? STATUS_VIEW[status] : undefined
  const Icon = view?.icon || Bot
  const isAutonomousExecution = recovery.autonomous === true

  const handleRunNow = async () => {
    setRunning(true)
    setMessage(null)
    try {
      const res = await api.runAutonomousRecovery(scenario.id)
      setMessage(`Lifecycle finished: ${res.final_status} (${res.selected_plan_id || 'no plan'})`)
    } catch (e: unknown) {
      setMessage(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  const recentEvents = (history?.audit_events || []).slice(0, 6)

  return (
    <div className="panel" style={{ padding: '18px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Bot size={20} />
          <h3 style={{ fontSize: '1rem', fontWeight: 700 }}>Autonomous Recovery</h3>
          <span className={`badge ${enabled ? 'badge-nominal' : 'badge-info'}`}>
            {enabled ? 'AUTONOMOUS MODE ON' : 'MANUAL (legacy scenario)'}
          </span>
        </div>
        <button className="btn btn-primary" onClick={handleRunNow} disabled={running || status === 'investigating'}>
          {running ? 'Running…' : 'Run lifecycle now'}
        </button>
      </div>

      {view ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <Icon size={16} />
          <span className={`badge ${view.badge}`}>{view.label}</span>
          {recovery.selected_plan && <code>{recovery.selected_plan}</code>}
          {status === 'in_progress' && recovery.remaining_steps != null && (
            <span style={{ color: 'var(--text-secondary)' }}>{recovery.remaining_steps} step(s) remaining</span>
          )}
          {status === 'completed' && (
            <span style={{ color: 'var(--text-secondary)' }}>
              {isAutonomousExecution ? 'executed without manual approval' : `approved by ${recovery.operator_id}`}
              {recovery.finished_at_step != null ? ` · finished at step ${recovery.finished_at_step}` : ''}
            </span>
          )}
        </div>
      ) : (
        <div style={{ color: 'var(--text-secondary)' }}>No recovery activity for the current incident.</div>
      )}

      {isAutonomousExecution && recovery.decision_source && (
        <div style={{ fontSize: '0.85rem' }}>
          <strong>Plan selected by:</strong> {SOURCE_LABEL[recovery.decision_source] || recovery.decision_source}
          {recovery.resolved_model ? ` · ${recovery.resolved_model}` : ''}
          {recovery.fallback_reason && (
            <div style={{ color: 'var(--accent-orange)', marginTop: '4px' }}>Fallback reason: {recovery.fallback_reason}</div>
          )}
          {recovery.rationale && <div style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>{recovery.rationale}</div>}
        </div>
      )}

      {(status === 'pending_approval' || status === 'blocked') && (
        <div style={{ fontSize: '0.85rem', color: 'var(--accent-orange)' }}>
          {recovery.escalation_reason || recovery.authorization?.reason}
        </div>
      )}
      {status === 'awaiting_decision' && recovery.next_attempt_at && (
        <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          Retry scheduled at {new Date(recovery.next_attempt_at).toLocaleTimeString()} after: {recovery.last_error}
        </div>
      )}
      {timing && !timing.requires_human_decision && timing.ineligibility_reason && (
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Clock: {timing.ineligibility_reason}</div>
      )}
      {summary && (
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Last lifecycle: {summary.summary}</div>
      )}
      {message && <div style={{ fontSize: '0.8rem' }}>{message}</div>}

      {recentEvents.length > 0 && (
        <table style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr>
              <th>Event</th>
              <th>Plan</th>
              <th>Recorded</th>
            </tr>
          </thead>
          <tbody>
            {recentEvents.map((e) => (
              <tr key={e.event_id}>
                <td>{e.event_type}</td>
                <td>{e.payload?.plan_id || e.payload?.winning_plan_id || '—'}</td>
                <td>{new Date(e.recorded_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
