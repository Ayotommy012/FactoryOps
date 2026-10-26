// frontend/src/components/WorldModelTab.tsx
import { useState, type FC } from 'react'
import {
  Network,
  Database,
  FileCheck2,
} from 'lucide-react'
import type { WorldModelResponse, SimulationSnapshot } from '../lib/types'

interface WorldModelTabProps {
  worldModel: WorldModelResponse | null
  snapshot: SimulationSnapshot | null
  scenarioId: string
}

export const WorldModelTab: FC<WorldModelTabProps> = ({
  worldModel,
  snapshot,
  scenarioId,
}) => {
  const [activeSubView, setActiveSubView] = useState<'graph' | 'state_shape' | 'audit'>('graph')
  const [searchTerm, setSearchTerm] = useState('')

  if (!worldModel) {
    return (
      <div style={{ padding: '32px', textAlign: 'center', color: '#94a3b8' }}>
        Loading World Model canonical state...
      </div>
    )
  }

  const shape = worldModel.canonical_shape || {}
  const auditEntries = worldModel.audit_log || []

  // Filtered audit log
  const filteredAudit = auditEntries.filter(
    (a) =>
      a.summary?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      a.kind?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      a.actor?.toLowerCase().includes(searchTerm.toLowerCase())
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Overview Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '14px' }}>
        <div className="panel-card" style={{ padding: '14px 18px' }}>
          <div style={{ fontSize: '0.72rem', color: '#94a3b8', textTransform: 'uppercase' }}>World Model Namespace</div>
          <div style={{ fontSize: '1rem', fontWeight: 700, color: '#38bdf8', fontFamily: 'var(--font-mono)', marginTop: '2px' }}>
            {worldModel.scenario_id || scenarioId}
          </div>
          <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '4px' }}>Meterless State Engine</div>
        </div>

        <div className="panel-card" style={{ padding: '14px 18px' }}>
          <div style={{ fontSize: '0.72rem', color: '#94a3b8', textTransform: 'uppercase' }}>Active Machines</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 800, color: '#f8fafc', fontFamily: 'var(--font-mono)' }}>
            {Object.keys(snapshot?.machines || {}).length} Units
          </div>
          <div style={{ fontSize: '0.7rem', color: '#22c55e', marginTop: '2px' }}>Tracked via SCADA feeds</div>
        </div>

        <div className="panel-card" style={{ padding: '14px 18px' }}>
          <div style={{ fontSize: '0.72rem', color: '#94a3b8', textTransform: 'uppercase' }}>Tracked Entities & Lots</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 800, color: '#f8fafc', fontFamily: 'var(--font-mono)' }}>
            {Object.keys(snapshot?.inventory || {}).length + Object.keys(snapshot?.batches || {}).length} Entities
          </div>
          <div style={{ fontSize: '0.7rem', color: '#38bdf8', marginTop: '2px' }}>MES batches & WMS inventory</div>
        </div>

        <div className="panel-card" style={{ padding: '14px 18px' }}>
          <div style={{ fontSize: '0.72rem', color: '#94a3b8', textTransform: 'uppercase' }}>Audit Sequence Steps</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 800, color: '#cbd5e1', fontFamily: 'var(--font-mono)' }}>
            #{auditEntries.length} Events
          </div>
          <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '2px' }}>Append-only bitemporal facts</div>
        </div>
      </div>

      {/* View Switcher Header */}
      <div className="panel-card">
        <div className="panel-header" style={{ marginBottom: '16px' }}>
          <div className="panel-title panel-title-cyan">
            <Network size={18} />
            Meterless World Model Canonical Graph & State Engine
          </div>

          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              className={`btn ${activeSubView === 'graph' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveSubView('graph')}
            >
              <Network size={14} />
              Entity Hierarchy Graph
            </button>
            <button
              className={`btn ${activeSubView === 'state_shape' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveSubView('state_shape')}
            >
              <Database size={14} />
              Canonical State Shape
            </button>
            <button
              className={`btn ${activeSubView === 'audit' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveSubView('audit')}
            >
              <FileCheck2 size={14} />
              State Mutation Audit Log ({auditEntries.length})
            </button>
          </div>
        </div>

        {/* SubView 1: Entity Relationship Hierarchy Graph */}
        {activeSubView === 'graph' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              The World Model maintains an integrated semantic graph connecting physical plant machines, operational batches, storage inventories, and customer delivery orders:
            </div>

            <div
              style={{
                background: '#060911',
                border: '1px solid #1e293b',
                borderRadius: '8px',
                padding: '20px',
                display: 'flex',
                flexDirection: 'column',
                gap: '20px',
              }}
            >
              {/* Plant Root Node */}
              <div style={{ display: 'flex', justifyContent: 'center' }}>
                <div
                  style={{
                    background: '#131c2e',
                    border: '2px solid #0284c7',
                    borderRadius: '8px',
                    padding: '10px 24px',
                    textAlign: 'center',
                    boxShadow: '0 0 15px rgba(2, 132, 199, 0.3)',
                  }}
                >
                  <div style={{ fontSize: '0.7rem', color: '#38bdf8', textTransform: 'uppercase', fontWeight: 700 }}>
                    Root Plant Facility
                  </div>
                  <div style={{ fontSize: '1rem', fontWeight: 800, color: '#f8fafc' }}>
                    plant:fac:Cocoa-Processing-01
                  </div>
                </div>
              </div>

              {/* Connecting Line */}
              <div style={{ height: '20px', width: '2px', background: '#334155', margin: '0 auto' }}></div>

              {/* Middle Layer: Machines */}
              <div>
                <div
                  style={{
                    fontSize: '0.72rem',
                    color: '#94a3b8',
                    textTransform: 'uppercase',
                    textAlign: 'center',
                    marginBottom: '10px',
                    fontWeight: 600,
                  }}
                >
                  Physical Asset Entities (SCADA Lineage)
                </div>

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                    gap: '12px',
                  }}
                >
                  {Object.values(snapshot?.machines || {}).map((m) => {
                    const isDegraded = m.status === 'degraded' || m.status === 'failed'
                    const machineKey = m.id || m.code || m.machine_id || 'machine'

                    return (
                      <div
                        key={machineKey}
                        style={{
                          background: isDegraded ? 'rgba(239, 68, 68, 0.1)' : '#0f172a',
                          border: `1px solid ${isDegraded ? '#ef4444' : '#1e293b'}`,
                          borderRadius: '6px',
                          padding: '10px 12px',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#f8fafc' }}>
                            {m.name || m.code || m.id || m.machine_id}
                          </span>
                          <span
                            className={`badge ${
                              isDegraded ? 'badge-danger' : 'badge-nominal'
                            }`}
                            style={{ fontSize: '0.62rem', padding: '1px 5px' }}
                          >
                            {m.status}
                          </span>
                        </div>
                        <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '4px' }}>
                          {m.station || m.code || 'Processing Unit'}
                        </div>
                        <div
                          style={{
                            fontSize: '0.75rem',
                            fontFamily: 'var(--font-mono)',
                            color: isDegraded ? '#f87171' : '#38bdf8',
                            marginTop: '4px',
                          }}
                        >
                          {m.temperature_c.toFixed(1)} °C • {m.vibration_rms.toFixed(1)} mm/s
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Connecting Line */}
              <div style={{ height: '20px', width: '2px', background: '#334155', margin: '0 auto' }}></div>

              {/* Bottom Layer: Batches & Orders */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
                {/* Batches Node */}
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '6px', padding: '12px 16px' }}>
                  <div style={{ fontSize: '0.72rem', color: '#38bdf8', textTransform: 'uppercase', fontWeight: 700, marginBottom: '6px' }}>
                    Production Batch Entities (MES Lineage)
                  </div>
                  {Object.values(snapshot?.batches || {}).map((b) => (
                    <div key={b.id || b.batch_id || b.batch_code} style={{ fontSize: '0.8rem', color: '#cbd5e1', padding: '4px 0' }}>
                      <strong>{b.batch_code || b.id}</strong>: {b.recipe_name} ({b.planned_quantity_kg} kg)
                    </div>
                  ))}
                </div>

                {/* Orders Node */}
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '6px', padding: '12px 16px' }}>
                  <div style={{ fontSize: '0.72rem', color: '#fbbf24', textTransform: 'uppercase', fontWeight: 700, marginBottom: '6px' }}>
                    Customer Order Commitments (ERP Lineage)
                  </div>
                  {Object.values(snapshot?.orders || {}).map((o) => (
                    <div key={o.id || o.order_id || o.order_code} style={{ fontSize: '0.8rem', color: '#cbd5e1', padding: '4px 0' }}>
                      <strong>{o.order_code || o.id}</strong>: {o.customer_name} ({(o.quantity_kg ?? o.ordered_quantity_kg ?? 0).toLocaleString()} kg)
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* SubView 2: Canonical State Shape JSON */}
        {activeSubView === 'state_shape' && (
          <div>
            <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: '10px' }}>
              Meterless World Model canonical state representation returned by <code>wm.state_shape()</code>:
            </div>
            <pre className="code-box">{JSON.stringify(shape, null, 2)}</pre>
          </div>
        )}

        {/* SubView 3: Audit Log Table */}
        {activeSubView === 'audit' && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
                Append-only mutation history recorded by the World Model state machine:
              </div>

              <div style={{ width: '280px', position: 'relative' }}>
                <input
                  type="text"
                  className="input-text"
                  placeholder="Search audit log..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                />
              </div>
            </div>

            <div className="data-table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Seq</th>
                    <th>Time</th>
                    <th>Action Kind</th>
                    <th>Actor / Provider</th>
                    <th>Audit Summary</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAudit.map((item) => (
                    <tr key={item.seq}>
                      <td style={{ color: '#38bdf8', fontWeight: 600 }}>#{item.seq}</td>
                      <td>{item.at ? item.at.replace('T', ' ').substring(0, 19) : ''}</td>
                      <td>
                        <span className="badge badge-neutral">{item.kind}</span>
                      </td>
                      <td style={{ color: '#cbd5e1' }}>{item.actor}</td>
                      <td style={{ color: '#f8fafc', whiteSpace: 'normal', maxWidth: '400px' }}>
                        {item.summary}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
