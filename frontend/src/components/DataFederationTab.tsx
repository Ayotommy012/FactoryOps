// frontend/src/components/DataFederationTab.tsx
import { useState, type FC } from 'react'
import {
  DatabaseZap,
  Play,
  Layers,
  Activity,
  Info,
} from 'lucide-react'
import type {
  FederatedQueryResult,
  NASABearingRecord,
  USLMapping,
  ZetarisStatusResponse,
} from '../lib/types'
import { api } from '../lib/api'

interface DataFederationTabProps {
  zetarisStatus: ZetarisStatusResponse | null
  mappings: USLMapping[]
  nasaTimeline: NASABearingRecord[]
}

export const DataFederationTab: FC<DataFederationTabProps> = ({
  zetarisStatus,
  mappings,
  nasaTimeline,
}) => {
  const [activeSubTab, setActiveSubTab] = useState<'sql' | 'mappings' | 'nasa'>('sql')
  const [sqlQuery, setSqlQuery] = useState('SELECT * FROM factoryops.machines LIMIT 5')
  const [queryChannel, setQueryChannel] = useState<'auto' | 'jdbc' | 'rest'>('auto')
  const [executing, setExecuting] = useState(false)
  const [queryResult, setQueryResult] = useState<FederatedQueryResult | null>(null)
  const [queryError, setQueryError] = useState<string | null>(null)

  const presets = [
    { label: 'Factory Machines', sql: 'SELECT * FROM factoryops.machines LIMIT 5' },
    { label: 'Factory Batches', sql: 'SELECT * FROM factoryops.batches LIMIT 5' },
    { label: 'Zetaris TPCDS Warehouse', sql: 'SELECT * FROM TPCDS_DB.warehouse LIMIT 5' },
    { label: 'Zetaris NY Taxi Zones', sql: 'SELECT * FROM NY_TAXI.taxi_zones LIMIT 5' },
    { label: 'Raw SCADA Source Telemetry', sql: 'SELECT * FROM factory_source.source_machine_telemetry LIMIT 5' },
  ]

  const handleRunQuery = async () => {
    setExecuting(true)
    setQueryError(null)
    try {
      const res = await api.executeFederatedQuery(sqlQuery, true, queryChannel)
      setQueryResult(res)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setQueryError(msg)
    } finally {
      setExecuting(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* Zetaris Federation Environment Status */}
      <div className="panel-card">
        <div className="panel-header">
          <div className="panel-title panel-title-cyan">
            <DatabaseZap size={18} />
            Zetaris Networked Data Platform (NDP) & Unified Semantic Layer
          </div>
          <span className={`badge ${zetarisStatus?.connected ? 'badge-nominal' : 'badge-warning'}`}>
            {zetarisStatus?.connected ? 'REST:8888 Online' : 'Fallback Active'}
          </span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '14px' }}>
          <div style={{ background: '#090d16', padding: '10px 14px', borderRadius: '6px', border: '1px solid #1e293b' }}>
            <div style={{ fontSize: '0.68rem', color: '#94a3b8', textTransform: 'uppercase' }}>REST Query Interface</div>
            <div style={{ fontSize: '0.88rem', fontFamily: 'var(--font-mono)', fontWeight: 600, color: '#38bdf8', marginTop: '2px' }}>
              {zetarisStatus?.base_url || 'http://127.0.0.1:8888'}
            </div>
            <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '4px' }}>
              Spark SQL without JVM dependency
            </div>
          </div>

          <div style={{ background: '#090d16', padding: '10px 14px', borderRadius: '6px', border: '1px solid #1e293b' }}>
            <div style={{ fontSize: '0.68rem', color: '#94a3b8', textTransform: 'uppercase' }}>Host JDBC Driver Status</div>
            <div style={{ fontSize: '0.85rem', fontFamily: 'var(--font-mono)', fontWeight: 600, color: '#f8fafc', marginTop: '2px', wordBreak: 'break-all' }}>
              {zetarisStatus?.jdbc_environment.driver_jar_filename || 'ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar'}
            </div>
            <div
              style={{
                fontSize: '0.7rem',
                color: zetarisStatus?.jdbc_environment.live_session_verified
                  ? '#22c55e'
                  : zetarisStatus?.jdbc_environment.driver_jar_exists
                  ? '#38bdf8'
                  : '#f59e0b',
                marginTop: '4px',
              }}
            >
              {zetarisStatus?.jdbc_environment.live_session_verified
                ? 'Verified on host • Port 10000 Live JDBC ready'
                : zetarisStatus?.jdbc_environment.driver_jar_exists && zetarisStatus?.jdbc_environment.jdbc_port_open
                ? 'JAR verified on disk • Port 10000 open'
                : zetarisStatus?.jdbc_environment.driver_jar_exists
                ? 'JAR verified on disk • Port 10000 offline'
                : 'Driver JAR not found at configured path'}
            </div>
          </div>

          <div style={{ background: '#090d16', padding: '10px 14px', borderRadius: '6px', border: '1px solid #1e293b' }}>
            <div style={{ fontSize: '0.68rem', color: '#94a3b8', textTransform: 'uppercase' }}>Discovered Lightning DBs</div>
            <div style={{ fontSize: '0.85rem', fontFamily: 'var(--font-mono)', fontWeight: 600, color: '#f8fafc', marginTop: '2px' }}>
              {(zetarisStatus?.jdbc_environment.lightning_databases || ['NY_TAXI', 'TPCDS_DB']).join(', ')}
            </div>
            <div style={{ fontSize: '0.7rem', color: '#38bdf8', marginTop: '4px' }}>
              Zetaris Lightning metastore catalog
            </div>
          </div>

          <div style={{ background: '#090d16', padding: '10px 14px', borderRadius: '6px', border: '1px solid #1e293b' }}>
            <div style={{ fontSize: '0.68rem', color: '#94a3b8', textTransform: 'uppercase' }}>Transparent Failover Policy</div>
            <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#86efac', marginTop: '2px' }}>
              Direct PostgreSQL Fallback
            </div>
            <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '4px' }}>
              Zero simulation interruption guarantee
            </div>
          </div>
        </div>

        {zetarisStatus?.boundary_summary && (
          <div
            style={{
              marginTop: '12px',
              padding: '8px 12px',
              background: '#131c2e',
              border: '1px solid #1e293b',
              borderRadius: '6px',
              fontSize: '0.75rem',
              color: '#cbd5e1',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <Info size={14} color="#38bdf8" />
            <span>{zetarisStatus.boundary_summary}</span>
          </div>
        )}

        {zetarisStatus?.jdbc_environment?.next_operator_action && (
          <div
            style={{
              marginTop: '8px',
              padding: '8px 12px',
              background: '#0e1726',
              border: '1px solid #1e3a5f',
              borderRadius: '6px',
              fontSize: '0.74rem',
              color: '#94a3b8',
              lineHeight: 1.4,
            }}
          >
            <strong style={{ color: '#38bdf8' }}>Integration Status & Operator Action: </strong>
            {zetarisStatus.jdbc_environment.next_operator_action}
          </div>
        )}
      </div>

      {/* Sub-Tabs: Live SQL Console | Mapping Manifesto | NASA IMS Benchmark */}
      <div className="panel-card">
        <div className="panel-header" style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              className={`btn ${activeSubTab === 'sql' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveSubTab('sql')}
            >
              <DatabaseZap size={14} />
              Federated SQL Query Runner
            </button>

            <button
              className={`btn ${activeSubTab === 'mappings' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveSubTab('mappings')}
            >
              <Layers size={14} />
              Semantic Mapping Manifesto ({mappings.length})
            </button>

            <button
              className={`btn ${activeSubTab === 'nasa' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setActiveSubTab('nasa')}
            >
              <Activity size={14} />
              NASA IMS Bearing Run-to-Failure Benchmark
            </button>
          </div>
        </div>

        {/* Sub-Tab 1: Live SQL Runner */}
        {activeSubTab === 'sql' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {/* Presets */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
              <span style={{ fontSize: '0.75rem', color: '#94a3b8', marginRight: '4px' }}>Sample Queries:</span>
              {presets.map((p, idx) => (
                <button
                  key={idx}
                  className="btn btn-secondary"
                  style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                  onClick={() => setSqlQuery(p.sql)}
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* SQL Input Box & Channel */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <textarea
                className="input-text"
                rows={3}
                style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem' }}
                value={sqlQuery}
                onChange={(e) => setSqlQuery(e.target.value)}
                placeholder="Enter SQL statement to execute through USL..."
              />

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>Execution Channel:</span>
                  <select
                    className="select-input"
                    style={{ width: '130px', padding: '4px 8px', fontSize: '0.78rem' }}
                    value={queryChannel}
                    onChange={(e) => setQueryChannel(e.target.value as 'auto' | 'jdbc' | 'rest')}
                  >
                    <option value="auto">Auto (JDBC → REST → Postgres)</option>
                    <option value="jdbc">JDBC Only</option>
                    <option value="rest">REST Only</option>
                  </select>
                </div>

                <button
                  className="btn btn-primary"
                  onClick={handleRunQuery}
                  disabled={executing || !sqlQuery.trim()}
                >
                  <Play size={14} />
                  {executing ? 'Executing Query...' : 'Execute SQL'}
                </button>
              </div>
            </div>

            {/* Error Message if any */}
            {queryError && (
              <div
                style={{
                  background: 'rgba(239, 68, 68, 0.15)',
                  border: '1px solid #ef4444',
                  borderRadius: '6px',
                  padding: '12px',
                  fontSize: '0.82rem',
                  color: '#fca5a5',
                }}
              >
                <strong>Query Error:</strong> {queryError}
              </div>
            )}

            {/* Query Result View */}
            {queryResult && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '8px' }}>
                {/* Provenance & Source Layer Banner */}
                <div
                  style={{
                    background: queryResult.source_layer === 'zetaris_federation' ? 'rgba(2, 132, 199, 0.12)' : '#131c2e',
                    border: `1px solid ${queryResult.source_layer === 'zetaris_federation' ? '#0284c7' : '#334155'}`,
                    borderRadius: '6px',
                    padding: '12px 16px',
                    display: 'flex',
                    flexWrap: 'wrap',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: '12px',
                  }}
                >
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span
                        className={`badge ${
                          queryResult.source_layer === 'zetaris_federation' ? 'badge-nominal' : 'badge-warning'
                        }`}
                        style={{ fontSize: '0.75rem' }}
                      >
                        Source: {queryResult.source_layer}
                      </span>
                      <span style={{ fontSize: '0.82rem', fontWeight: 600, color: '#f8fafc' }}>
                        {queryResult.records.length} rows returned ({queryResult.execution_time_ms.toFixed(1)} ms)
                      </span>
                    </div>
                    <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginTop: '4px' }}>
                      {queryResult.boundary_notes}
                    </div>
                  </div>

                  <div style={{ textAlign: 'right', fontSize: '0.72rem', color: '#94a3b8', fontFamily: 'var(--font-mono)' }}>
                    <div>System: {queryResult.provenance?.source_system || 'N/A'}</div>
                    <div>Quality: {queryResult.provenance?.data_quality_status || 'verified'}</div>
                  </div>
                </div>

                {/* Table of Records */}
                {queryResult.records.length > 0 ? (
                  <div className="data-table-container">
                    <table className="data-table">
                      <thead>
                        <tr>
                          {Object.keys(queryResult.records[0]).map((col) => (
                            <th key={col}>{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {queryResult.records.map((row, rIdx) => (
                          <tr key={rIdx}>
                            {Object.values(row).map((val, cIdx) => (
                              <td key={cIdx}>
                                {val !== null && typeof val === 'object'
                                  ? JSON.stringify(val)
                                  : String(val ?? '')}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div style={{ padding: '20px', textAlign: 'center', color: '#94a3b8', background: '#090d16', borderRadius: '6px' }}>
                    Query executed successfully with 0 rows returned.
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Sub-Tab 2: Mapping Manifesto */}
        {activeSubTab === 'mappings' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              The Unified Semantic Layer (USL) aligns heterogeneous operational telemetry (SCADA, MES, ERP, and NASA IMS benchmarks) into unified canonical schemas:
            </div>

            <div className="data-table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Source Entity</th>
                    <th>Canonical Entity</th>
                    <th>Unit Conversions</th>
                    <th>Missing Value Policy</th>
                    <th>Record Kind</th>
                  </tr>
                </thead>
                <tbody>
                  {mappings.map((m, idx) => (
                    <tr key={idx}>
                      <td style={{ color: '#38bdf8', fontWeight: 600 }}>{m.source_entity}</td>
                      <td style={{ color: '#86efac' }}>{m.canonical_entity}</td>
                      <td>{JSON.stringify(m.unit_conversions)}</td>
                      <td style={{ color: '#cbd5e1' }}>{m.missing_value_policy}</td>
                      <td>
                        <span className="badge badge-neutral">{m.record_kind}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Sub-Tab 3: NASA IMS Benchmark */}
        {activeSubTab === 'nasa' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            <div
              style={{
                background: 'rgba(2, 132, 199, 0.08)',
                border: '1px solid #0284c7',
                borderRadius: '6px',
                padding: '12px 16px',
                fontSize: '0.82rem',
                color: '#cbd5e1',
                lineHeight: 1.5,
              }}
            >
              <div style={{ fontWeight: 700, color: '#38bdf8', marginBottom: '4px' }}>
                NASA / UC Intelligent Maintenance Systems (IMS) Bearing Run-to-Failure Dataset
              </div>
              <div>
                Physical benchmark run-to-failure observations (outer race defect test on Bearing 1, Channel 1 & 2) providing empirical grounding for ROAST-01 vibration and thermal runaway progression in FactoryOps.
              </div>
            </div>

            <div className="data-table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Benchmark Timestamp</th>
                    <th>Bearing ID</th>
                    <th>Vibration RMS (mm/s)</th>
                    <th>Baseline Ratio</th>
                    <th>Temperature (°C)</th>
                    <th>ISO 10816 Condition</th>
                    <th>Raw Accel Ch1 (g)</th>
                  </tr>
                </thead>
                <tbody>
                  {nasaTimeline.map((rec, idx) => {
                    const isSevere = rec.operational_status === 'critical_failure' || rec.operational_status === 'degraded'
                    return (
                      <tr key={idx}>
                        <td style={{ fontFamily: 'var(--font-mono)' }}>{rec.timestamp}</td>
                        <td style={{ color: '#38bdf8', fontWeight: 600 }}>{rec.source_bearing_id}</td>
                        <td style={{ fontWeight: 700, color: isSevere ? '#f87171' : '#f8fafc' }}>
                          {rec.vibration_rms_mms.toFixed(2)} mm/s
                        </td>
                        <td style={{ color: (rec.vibration_ratio_vs_baseline || 1) > 2 ? '#f87171' : '#cbd5e1' }}>
                          {rec.vibration_ratio_vs_baseline ? `${rec.vibration_ratio_vs_baseline.toFixed(1)}x` : '1.0x'}
                        </td>
                        <td>{rec.temperature_c.toFixed(1)} °C</td>
                        <td>
                          <span
                            className={`badge ${
                              rec.operational_status === 'nominal'
                                ? 'badge-nominal'
                                : rec.operational_status === 'warning'
                                ? 'badge-warning'
                                : 'badge-danger'
                            }`}
                          >
                            {rec.operational_status}
                          </span>
                        </td>
                        <td style={{ color: '#94a3b8' }}>{rec.raw_ch1_g?.toFixed(3) ?? '-'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
