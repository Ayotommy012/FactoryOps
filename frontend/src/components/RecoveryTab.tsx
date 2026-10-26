// frontend/src/components/RecoveryTab.tsx
import { useState, type FC } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Clock,
  Sparkles,
  ShieldCheck,
  UserCheck,
  RotateCcw,
} from 'lucide-react'
import type {
  ImpactAnalysis,
  RecoveryPlan,
  RecoveryDecisionRequest,
  Scenario,
  TimingStatusResponse,
} from '../lib/types'
import { AutonomousRecoveryPanel } from './AutonomousRecoveryPanel'

interface RecoveryTabProps {
  scenarioId: string
  impact: ImpactAnalysis | null
  plans: RecoveryPlan[]
  loadingPlans: boolean
  onFetchImpactAndPlans: () => void
  onDecide: (decision: RecoveryDecisionRequest) => Promise<void>
  scenario?: Scenario | null
  timing?: TimingStatusResponse | null
}

export const RecoveryTab: FC<RecoveryTabProps> = ({
  impact,
  plans,
  loadingPlans,
  onFetchImpactAndPlans,
  onDecide,
  scenario = null,
  timing = null,
}) => {
  const [selectedPlanForDecision, setSelectedPlanForDecision] = useState<{
    plan: RecoveryPlan
    decision: 'approved' | 'rejected'
  } | null>(null)
  const [operatorId, setOperatorId] = useState('OP-LEAD-CHIEF')
  const [rationale, setRationale] = useState(
    'Operator verified SLA buffer compliance and lowest deterministic cost.'
  )
  const [submittingDecision, setSubmittingDecision] = useState(false)

  const handleConfirmDecision = async () => {
    if (!selectedPlanForDecision) return
    setSubmittingDecision(true)
    try {
      await onDecide({
        plan_id: selectedPlanForDecision.plan.plan_id,
        decision: selectedPlanForDecision.decision,
        operator_id: operatorId,
        rationale: rationale,
      })
      setSelectedPlanForDecision(null)
    } finally {
      setSubmittingDecision(false)
    }
  }

  const severity: string = impact?.severity || 'unclassified'
  const severityThemes: Record<string, {
    gradient: string; borderColor: string; iconBg: string; titleColor: string
    badgeClass: string; badgeText: string; boxShadow: string
  }> = {
    critical: {
      gradient: 'linear-gradient(135deg, rgba(239, 68, 68, 0.15) 0%, rgba(15, 23, 42, 0.9) 100%)',
      borderColor: '#ef4444',
      iconBg: '#ef4444',
      titleColor: '#fca5a5',
      badgeClass: 'badge-danger',
      badgeText: 'CRITICAL INTERVENTION REQUIRED',
      boxShadow: '0 0 20px rgba(239, 68, 68, 0.2)',
    },
    high: {
      gradient: 'linear-gradient(135deg, rgba(245, 158, 11, 0.15) 0%, rgba(15, 23, 42, 0.9) 100%)',
      borderColor: '#f59e0b',
      iconBg: '#f59e0b',
      titleColor: '#fcd34d',
      badgeClass: 'badge-warning',
      badgeText: 'HIGH SEVERITY INTERVENTION REQUIRED',
      boxShadow: '0 0 20px rgba(245, 158, 11, 0.2)',
    },
    medium: {
      gradient: 'linear-gradient(135deg, rgba(59, 130, 246, 0.15) 0%, rgba(15, 23, 42, 0.9) 100%)',
      borderColor: '#3b82f6',
      iconBg: '#3b82f6',
      titleColor: '#93c5fd',
      badgeClass: 'badge-info',
      badgeText: 'OPERATIONAL INTERVENTION REQUIRED',
      boxShadow: '0 0 20px rgba(59, 130, 246, 0.2)',
    },
    low: {
      gradient: 'linear-gradient(135deg, rgba(16, 185, 129, 0.15) 0%, rgba(15, 23, 42, 0.9) 100%)',
      borderColor: '#10b981',
      iconBg: '#10b981',
      titleColor: '#6ee7b7',
      badgeClass: 'badge-nominal',
      badgeText: 'LOW SEVERITY NOTICE',
      boxShadow: '0 0 20px rgba(16, 185, 129, 0.2)',
    },
  }
  const severityTheme = severityThemes[severity] || {
    gradient: 'linear-gradient(135deg, rgba(148, 163, 184, 0.15) 0%, rgba(15, 23, 42, 0.9) 100%)',
    borderColor: '#94a3b8',
    iconBg: '#64748b',
    titleColor: '#cbd5e1',
    badgeClass: 'badge-info',
    badgeText: 'SEVERITY NOT YET CLASSIFIED',
    boxShadow: 'none',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <AutonomousRecoveryPanel scenario={scenario} timing={timing} />

      {/* Top Banner: Anomaly Detection & Financial SLA Impact */}
      {impact && impact.evidence.length > 0 ? (
        <div
          style={{
            background: severityTheme.gradient,
            border: `1px solid ${severityTheme.borderColor}`,
            borderRadius: '8px',
            padding: '20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            boxShadow: severityTheme.boxShadow,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div
                style={{
                  background: severityTheme.iconBg,
                  width: '36px',
                  height: '36px',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <AlertTriangle size={20} color="#fff" />
              </div>
              <div>
                <h3 style={{ fontSize: '1.1rem', fontWeight: 700, color: severityTheme.titleColor }}>
                  ACTIVE MANUFACTURING ANOMALY DETECTED
                </h3>
                <div style={{ fontSize: '0.78rem', color: '#cbd5e1' }}>
                  SCADA Telemetry Violation on Asset: <strong style={{ color: '#fff' }}>{impact.disrupted_machine_id}</strong>
                </div>
              </div>
            </div>
            <span className={`badge ${severityTheme.badgeClass}`} style={{ fontSize: '0.8rem', padding: '4px 10px' }}>
              {severityTheme.badgeText}
            </span>
          </div>

          {/* Evidence Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px' }}>
            {impact.evidence.map((ev, i) => {
              const evSeverityColor =
                ev.severity === 'critical'
                  ? '#f87171'
                  : ev.severity === 'high'
                  ? '#fbbf24'
                  : ev.severity === 'medium'
                  ? '#60a5fa'
                  : '#4ade80'
              const evBorderColor =
                ev.severity === 'critical'
                  ? 'rgba(239, 68, 68, 0.4)'
                  : ev.severity === 'high'
                  ? 'rgba(245, 158, 11, 0.4)'
                  : ev.severity === 'medium'
                  ? 'rgba(59, 130, 246, 0.4)'
                  : 'rgba(16, 185, 129, 0.4)'

              return (
                <div
                  key={i}
                  style={{
                    background: 'rgba(0, 0, 0, 0.4)',
                    border: `1px solid ${evBorderColor}`,
                    borderRadius: '6px',
                    padding: '10px 14px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: '#94a3b8' }}>
                    <span>{ev.metric.toUpperCase()}</span>
                    <span style={{ color: evSeverityColor, fontWeight: 600 }}>{ev.severity.toUpperCase()}</span>
                  </div>
                  <div style={{ fontSize: '1.15rem', fontWeight: 700, color: evSeverityColor, margin: '4px 0', fontFamily: 'var(--font-mono)' }}>
                    {ev.observed_value.toFixed(1)} {ev.unit}{' '}
                    <span style={{ fontSize: '0.78rem', color: '#94a3b8', fontWeight: 400 }}>
                      (Threshold: {ev.threshold_value} {ev.unit})
                    </span>
                  </div>
                  <div style={{ fontSize: '0.75rem', color: '#e2e8f0' }}>{ev.message}</div>
                  {ev.provenance_source && (
                    <div style={{ fontSize: '0.68rem', color: '#38bdf8', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>
                      Historical Benchmark Grounding: {ev.provenance_source} (Empirical ISO 10816 baseline, not live factory sensor)
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Cascading Consequence & SLA Risk */}
          <div
            style={{
              background: 'rgba(15, 23, 42, 0.8)',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '14px',
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: '12px',
            }}
          >
            <div>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase' }}>Affected Customer & Order</div>
              <div style={{ fontWeight: 600, color: '#f8fafc', fontSize: '0.85rem' }}>
                {impact.affected_customer || 'Artisan Chocolatier'}
              </div>
              <div style={{ fontSize: '0.75rem', color: '#38bdf8', fontFamily: 'var(--font-mono)' }}>
                {impact.affected_order_id} ({impact.affected_batch_id})
              </div>
            </div>

            <div>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase' }}>Projected Unintervened Finish</div>
              <div style={{ fontWeight: 600, color: '#f87171', fontSize: '0.85rem', fontFamily: 'var(--font-mono)' }}>
                {impact.unintervened_completion ? impact.unintervened_completion.replace('T', ' ').substring(0, 19) + ' UTC' : 'N/A'}
              </div>
              <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                Delay: +{impact.delay_hours.toFixed(1)} hours
              </div>
            </div>

            <div>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase' }}>Contractual SLA Status</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '2px' }}>
                {impact.sla_breached ? (
                  <span className="badge badge-danger">
                    <XCircle size={12} /> SLA BREACHED
                  </span>
                ) : (
                  <span className="badge badge-nominal">
                    <CheckCircle2 size={12} /> ON SCHEDULE
                  </span>
                )}
              </div>
            </div>

            <div>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase' }}>Estimated Delay Penalty</div>
              <div
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '1.2rem',
                  fontWeight: 800,
                  color: impact.potential_penalty_usd > 0 ? '#f87171' : '#22c55e',
                }}
              >
                ${impact.potential_penalty_usd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="panel-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div className="panel-title panel-title-cyan">
              <ShieldCheck size={18} />
              Autonomous Health & Incident Monitoring
            </div>
            <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginTop: '4px' }}>
              No active manufacturing disruptions currently flagged. Advance simulation steps to trigger an anomaly.
            </div>
          </div>
          <button className="btn btn-secondary" onClick={onFetchImpactAndPlans} disabled={loadingPlans}>
            <RotateCcw size={14} />
            Check Active Incidents
          </button>
        </div>
      )}

      {/* Ranked Candidate Recovery Plans */}
      <div className="panel-card">
        <div className="panel-header">
          <div>
            <div className="panel-title">
              <Sparkles size={18} color="#38bdf8" />
              Recovery Intelligence: Deterministic Hard Constraints & AI Explanations
            </div>
            <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '4px' }}>
              Candidate plans generated via graph traversal, ranked by total cost and delivery deadline feasibility.
            </div>
          </div>
          <button
            className="btn btn-primary"
            onClick={onFetchImpactAndPlans}
            disabled={loadingPlans}
          >
            <Sparkles size={14} />
            {loadingPlans ? 'Analyzing...' : 'Generate Ranked Plans'}
          </button>
        </div>

        {plans.length === 0 ? (
          <div
            style={{
              padding: '36px',
              textAlign: 'center',
              color: '#94a3b8',
              background: '#090d16',
              borderRadius: '6px',
              border: '1px dashed #334155',
            }}
          >
            <Clock size={32} color="#64748b" style={{ margin: '0 auto 10px' }} />
            <div style={{ fontWeight: 600, color: '#f8fafc', marginBottom: '4px' }}>No Recovery Plans Generated Yet</div>
            <div style={{ fontSize: '0.82rem', maxWidth: '480px', margin: '0 auto' }}>
              Step the simulation forward to trigger the roaster bearing anomaly, then click "Generate Ranked Plans" to review deterministic candidate options.
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {plans.map((p) => {
              const isRank1 = p.rank === 1

              return (
                <div
                  key={p.plan_id}
                  style={{
                    background: isRank1 ? 'rgba(2, 132, 199, 0.06)' : '#090d16',
                    border: `1px solid ${isRank1 ? '#0284c7' : '#1e293b'}`,
                    borderRadius: '8px',
                    padding: '18px 20px',
                    boxShadow: isRank1 ? '0 0 15px rgba(2, 132, 199, 0.15)' : 'none',
                  }}
                >
                  {/* Plan Header */}
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      gap: '12px',
                      marginBottom: '12px',
                      borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
                      paddingBottom: '10px',
                    }}
                  >
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span
                          className={`badge ${isRank1 ? 'badge-nominal' : 'badge-neutral'}`}
                          style={{ fontSize: '0.78rem', padding: '3px 8px' }}
                        >
                          Rank #{p.rank} {isRank1 ? '• RECOMMENDED' : ''}
                        </span>
                        <h4 style={{ fontSize: '1rem', fontWeight: 700, color: '#f8fafc' }}>{p.title}</h4>
                      </div>
                      <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '2px' }}>
                        Strategy: <span style={{ color: '#38bdf8', textTransform: 'capitalize' }}>{p.strategy.replace('_', ' ')}</span> •
                        Estimated Downtime: <strong style={{ color: '#f8fafc' }}>{p.estimated_downtime_hours}h</strong>
                      </div>
                    </div>

                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase' }}>Total Plan Cost</div>
                      <div
                        style={{
                          fontSize: '1.25rem',
                          fontFamily: 'var(--font-mono)',
                          fontWeight: 800,
                          color: isRank1 ? '#38bdf8' : '#f8fafc',
                        }}
                      >
                        ${p.total_cost_usd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </div>
                    </div>
                  </div>

                  {/* Badges: SLA Compliance, Feasibility */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '14px' }}>
                    <span className={`badge ${p.meets_deadline ? 'badge-nominal' : 'badge-danger'}`}>
                      {p.meets_deadline ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                      {p.meets_deadline ? `Meets Delivery SLA (+${p.buffer_hours.toFixed(1)}h Buffer)` : 'Breaches Customer SLA'}
                    </span>

                    <span className={`badge ${p.feasibility_verdict === 'feasible' ? 'badge-nominal' : 'badge-danger'}`}>
                      Feasibility: {p.feasibility_verdict.toUpperCase()}
                    </span>

                    <span className="badge badge-neutral">
                      Projected Finish: {p.projected_completion.replace('T', ' ').substring(0, 19)} UTC
                    </span>

                    {p.authorization_outcome && (
                      <span
                        className={`badge ${
                          p.authorization_outcome === 'AUTO_EXECUTE'
                            ? 'badge-nominal'
                            : p.authorization_outcome === 'REQUIRES_APPROVAL'
                            ? 'badge-warning'
                            : 'badge-danger'
                        }`}
                        title={p.authorization_reason}
                        style={{ fontWeight: 600 }}
                      >
                        {p.authorization_outcome === 'AUTO_EXECUTE'
                          ? '⚡ Policy: Auto-Executable'
                          : p.authorization_outcome === 'REQUIRES_APPROVAL'
                          ? '⚠️ Policy: Requires Operator Approval'
                          : '🚫 Policy: BLOCKED (Hard Constraint)'}
                      </span>
                    )}
                  </div>

                  {/* Explanation from Meterless / NVIDIA Nemotron / Deterministic Fallback */}
                  {p.explanation && (() => {
                    const isLiveMeterless = p.explanation.startsWith('[Meterless Runtime AI]')
                    const isLiveNemotron = p.explanation.startsWith('[NVIDIA Nemotron]')
                    const isLiveAI = isLiveMeterless || isLiveNemotron

                    return (
                      <div
                        style={{
                          background: isLiveAI ? 'rgba(6, 182, 212, 0.08)' : '#131c2e',
                          border: `1px solid ${isLiveAI ? '#0284c7' : '#1e293b'}`,
                          borderRadius: '6px',
                          padding: '12px 14px',
                          marginBottom: '14px',
                          fontSize: '0.82rem',
                          color: '#cbd5e1',
                          lineHeight: 1.5,
                        }}
                      >
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            marginBottom: '6px',
                          }}
                        >
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '6px',
                              fontSize: '0.72rem',
                              fontWeight: 700,
                              color: isLiveAI ? '#38bdf8' : '#94a3b8',
                              textTransform: 'uppercase',
                              letterSpacing: '0.04em',
                            }}
                          >
                            <Sparkles size={13} />
                            {isLiveAI ? 'Live AI Operational Reasoning' : 'Deterministic Fallback Rationale'}
                          </div>

                          <span
                            className={`badge ${isLiveAI ? 'badge-nominal' : 'badge-neutral'}`}
                            style={{ fontSize: '0.65rem', padding: '1px 6px' }}
                          >
                            {isLiveMeterless
                              ? 'Meterless Runtime AI'
                              : isLiveNemotron
                              ? 'NVIDIA Nemotron'
                              : 'Deterministic (Rule-Based)'}
                          </span>
                        </div>
                        <div style={{ whiteSpace: 'pre-wrap' }}>{p.explanation}</div>
                      </div>
                    )
                  })()}

                  {/* Cost Breakdown & Action Steps */}
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                      gap: '14px',
                      marginBottom: '14px',
                    }}
                  >
                    {/* Cost Breakdown Table */}
                    <div style={{ background: '#090d16', padding: '10px 12px', borderRadius: '6px', border: '1px solid #1e293b' }}>
                      <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase', marginBottom: '6px', fontWeight: 600 }}>
                        Deterministic Cost Itemization
                      </div>
                      <div className="metric-row">
                        <span className="metric-label">Equipment / Parts</span>
                        <span className="metric-value">${(p.cost_breakdown.equipment_cost_usd || 0).toFixed(2)}</span>
                      </div>
                      <div className="metric-row">
                        <span className="metric-label">Labor</span>
                        <span className="metric-value">${(p.cost_breakdown.labor_cost_usd || 0).toFixed(2)}</span>
                      </div>
                      <div className="metric-row">
                        <span className="metric-label">SLA Delay Penalties</span>
                        <span className="metric-value" style={{ color: (p.cost_breakdown.penalty_cost_usd || 0) > 0 ? '#f87171' : '#22c55e' }}>
                          ${(p.cost_breakdown.penalty_cost_usd || 0).toFixed(2)}
                        </span>
                      </div>
                      <div className="metric-row" style={{ borderBottom: 'none' }}>
                        <span className="metric-label">Scrap & Waste</span>
                        <span className="metric-value">${(p.cost_breakdown.raw_material_scrap_usd || 0).toFixed(2)}</span>
                      </div>
                    </div>

                    {/* Action Execution Steps */}
                    <div style={{ background: '#090d16', padding: '10px 12px', borderRadius: '6px', border: '1px solid #1e293b' }}>
                      <div style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase', marginBottom: '6px', fontWeight: 600 }}>
                        Operator Execution Steps
                      </div>
                      <ul style={{ paddingLeft: '18px', fontSize: '0.78rem', color: '#cbd5e1', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        {p.action_steps.map((st, sidx) => (
                          <li key={sidx}>{st}</li>
                        ))}
                      </ul>
                    </div>
                  </div>

                  {/* Operator Approval / Decision Actions */}
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'flex-end',
                      gap: '10px',
                      borderTop: '1px solid rgba(255, 255, 255, 0.06)',
                      paddingTop: '12px',
                    }}
                  >
                    {p.authorization_outcome === 'BLOCKED' && (
                      <span style={{ fontSize: '0.74rem', color: '#f87171', alignSelf: 'center', marginRight: 'auto' }}>
                        Blocked by safety policy: {p.authorization_reason}
                      </span>
                    )}

                    <button
                      className="btn btn-danger"
                      onClick={() =>
                        setSelectedPlanForDecision({
                          plan: p,
                          decision: 'rejected',
                        })
                      }
                    >
                      <XCircle size={14} />
                      Reject Plan #{p.rank}
                    </button>

                    <button
                      className="btn btn-success"
                      disabled={p.authorization_outcome === 'BLOCKED'}
                      style={p.authorization_outcome === 'BLOCKED' ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
                      title={p.authorization_outcome === 'BLOCKED' ? `Blocked by safety policy: ${p.authorization_reason}` : undefined}
                      onClick={() =>
                        setSelectedPlanForDecision({
                          plan: p,
                          decision: 'approved',
                        })
                      }
                    >
                      <UserCheck size={14} />
                      Approve & Execute Plan #{p.rank}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Human-in-the-Loop Confirmation Modal */}
      {selectedPlanForDecision && (
        <div
          className="modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget && !submittingDecision) {
              setSelectedPlanForDecision(null)
            }
          }}
          role="dialog"
          aria-modal="true"
        >
          <div className="modal-box">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <UserCheck size={20} color="#38bdf8" />
                <h3 style={{ fontSize: '1.05rem', fontWeight: 700, color: '#f8fafc' }}>
                  Human-in-the-Loop Operator Verification
                </h3>
              </div>
              <button
                style={{ background: 'transparent', border: 'none', color: '#94a3b8', cursor: 'pointer' }}
                onClick={() => setSelectedPlanForDecision(null)}
              >
                ✕
              </button>
            </div>

            <div style={{ marginBottom: '16px', fontSize: '0.85rem', color: '#cbd5e1' }}>
              You are about to record an authoritative operator decision for{' '}
              <strong style={{ color: '#f8fafc' }}>{selectedPlanForDecision.plan.title}</strong>:
              <div style={{ marginTop: '8px' }}>
                Decision:{' '}
                <span
                  className={`badge ${
                    selectedPlanForDecision.decision === 'approved' ? 'badge-nominal' : 'badge-danger'
                  }`}
                  style={{ textTransform: 'uppercase' }}
                >
                  {selectedPlanForDecision.decision}
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '20px' }}>
              <div>
                <label style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                  Authorizing Operator ID
                </label>
                <input
                  type="text"
                  className="input-text"
                  value={operatorId}
                  onChange={(e) => setOperatorId(e.target.value)}
                />
              </div>

              <div>
                <label style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
                  Operator Justification / Audit Rationale
                </label>
                <textarea
                  className="input-text"
                  rows={3}
                  value={rationale}
                  onChange={(e) => setRationale(e.target.value)}
                />
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button
                className="btn btn-secondary"
                onClick={() => setSelectedPlanForDecision(null)}
                disabled={submittingDecision}
              >
                Cancel
              </button>

              <button
                className={`btn ${selectedPlanForDecision.decision === 'approved' ? 'btn-success' : 'btn-danger'}`}
                onClick={handleConfirmDecision}
                disabled={submittingDecision || !operatorId.trim()}
              >
                {submittingDecision ? 'Submitting...' : `Confirm ${selectedPlanForDecision.decision.toUpperCase()}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
