// frontend/src/components/OperationsTab.tsx
// Dynamic 7-Stage Process Line, Interactive Stage Inspector & Supply Chain Stockout Forecaster

import { useEffect, useState, type FC } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Boxes,
  CheckCircle,
  Clock,
  Layers,
  TrendingDown,
} from 'lucide-react'
import { api } from '../lib/api'
import type {
  ProductionCycleInfo,
  ProductionLineStage,
  SimulationSnapshot,
  SupplyChainForecast,
} from '../lib/types'

interface OperationsTabProps {
  scenarioId?: string
  snapshot: SimulationSnapshot | null
  disruptionType?: string
}

export const OperationsTab: FC<OperationsTabProps> = ({
  scenarioId,
  snapshot,
  disruptionType,
}) => {
  const [stages, setStages] = useState<ProductionLineStage[]>([])
  const [prodCycle, setProdCycle] = useState<ProductionCycleInfo | null>(null)
  const [supplyChain, setSupplyChain] = useState<SupplyChainForecast | null>(null)
  const [selectedStageNum, setSelectedStageNum] = useState<number>(2) // default to Roaster Stage 2

  // Fetch production line and supply chain data
  const fetchData = async () => {
    if (!scenarioId) return
    try {
      const [lineRes, scRes] = await Promise.allSettled([
        api.getProductionLine(scenarioId),
        api.getSupplyChainForecast(scenarioId),
      ])
      if (lineRes.status === 'fulfilled') {
        setStages(lineRes.value.stages)
        if (lineRes.value.production_cycle) {
          setProdCycle(lineRes.value.production_cycle)
        }
      }
      if (scRes.status === 'fulfilled') {
        setSupplyChain(scRes.value)
      }
    } catch {
      // fallback handled gracefully
    }
  }

  useEffect(() => {
    fetchData()
  }, [scenarioId, snapshot?.current_step, disruptionType])

  if (!snapshot) {
    return (
      <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
        No operational snapshot available. Initialize or select a simulation scenario.
      </div>
    )
  }

  const machines = Object.values(snapshot.machines || {})
  const batches = Object.values(snapshot.batches || {})
  const orders = Object.values(snapshot.orders || {})

  // Fallback stages if API hasn't loaded yet
  const fallbackStages: ProductionLineStage[] = [
    {
      num: 1,
      id: 'stage_01_cleaning',
      name: 'Cleaning & Destoning',
      machine_code: 'CLEAN-01',
      machine_id: 'machine:plant:CLEAN-01',
      station: 'cleaning',
      status: 'running',
      throughput_kg_h: 1200,
      rated_capacity_kg_h: 1500,
      health_score: 1.0,
      temperature_c: 24.0,
      vibration_rms: 0.8,
      input_material: 'Raw Cocoa Beans (Silo 1)',
      input_available: true,
      input_quantity_kg: 3600,
      output_product: 'Clean De-stoned Cocoa Beans',
      progress_pct: 100,
      operational_reason: 'Processing raw bean lot from Silo 1',
      upstream_stage_num: null,
      downstream_stage_num: 2,
    },
    {
      num: 2,
      id: 'stage_02_roasting',
      name: 'Drum Roasting',
      machine_code: 'ROAST-01',
      machine_id: 'machine:plant:ROAST-01',
      station: 'roasting',
      status: 'running',
      throughput_kg_h: 1200,
      rated_capacity_kg_h: 1200,
      health_score: 0.98,
      temperature_c: 115.0,
      vibration_rms: 1.6,
      input_material: 'Clean De-stoned Beans',
      input_available: true,
      input_quantity_kg: 2500,
      output_product: 'Whole Roasted Cocoa Beans',
      progress_pct: 25.8,
      operational_reason: 'Continuous batch roasting for BATCH-101',
      upstream_stage_num: 1,
      downstream_stage_num: 3,
    },
    {
      num: 3,
      id: 'stage_03_winnowing',
      name: 'Winnowing',
      machine_code: 'WINNOW-01',
      machine_id: 'machine:plant:WINNOW-01',
      station: 'winnowing',
      status: 'running',
      throughput_kg_h: 1000,
      rated_capacity_kg_h: 1000,
      health_score: 1.0,
      temperature_c: 32.0,
      vibration_rms: 1.1,
      input_material: 'Whole Roasted Beans',
      input_available: true,
      input_quantity_kg: 2200,
      output_product: 'Clean Cocoa Nibs & Husk',
      progress_pct: 10,
      operational_reason: 'Vibratory destoning and husk separation',
      upstream_stage_num: 2,
      downstream_stage_num: 4,
    },
    {
      num: 4,
      id: 'stage_04_milling',
      name: 'Liquor Grinding',
      machine_code: 'MILL-01',
      machine_id: 'machine:plant:MILL-01',
      station: 'milling',
      status: 'running',
      throughput_kg_h: 800,
      rated_capacity_kg_h: 800,
      health_score: 1.0,
      temperature_c: 45.0,
      vibration_rms: 1.6,
      input_material: 'Clean Cocoa Nibs',
      input_available: true,
      input_quantity_kg: 2000,
      output_product: 'Pure Cocoa Liquor Paste',
      progress_pct: 0,
      operational_reason: 'Triple-roll milling to cocoa liquor',
      upstream_stage_num: 3,
      downstream_stage_num: 5,
    },
    {
      num: 5,
      id: 'stage_05_pressing',
      name: 'Hydraulic Pressing',
      machine_code: 'PRESS-01',
      machine_id: 'machine:plant:PRESS-01',
      station: 'pressing',
      status: 'queued',
      throughput_kg_h: 600,
      rated_capacity_kg_h: 600,
      health_score: 1.0,
      temperature_c: 65.0,
      vibration_rms: 2.1,
      input_material: 'Pure Cocoa Liquor Paste',
      input_available: true,
      input_quantity_kg: 1800,
      output_product: 'Cocoa Butter & Press Cake',
      progress_pct: 0,
      operational_reason: 'Waiting for upstream liquor buffer',
      upstream_stage_num: 4,
      downstream_stage_num: 6,
    },
    {
      num: 6,
      id: 'stage_06_pulverizing',
      name: 'Cake Pulverizing',
      machine_code: 'PULV-01',
      machine_id: 'machine:plant:PULV-01',
      station: 'pulverizing',
      status: 'queued',
      throughput_kg_h: 500,
      rated_capacity_kg_h: 500,
      health_score: 1.0,
      temperature_c: 30.0,
      vibration_rms: 1.4,
      input_material: 'Press Cake Blocks',
      input_available: true,
      input_quantity_kg: 1200,
      output_product: 'Fine Defatted Cocoa Powder',
      progress_pct: 0,
      operational_reason: 'Standby for press cake discharge',
      upstream_stage_num: 5,
      downstream_stage_num: 7,
    },
    {
      num: 7,
      id: 'stage_07_packaging',
      name: 'Packing & Fulfillment',
      machine_code: 'PACK-01',
      machine_id: 'machine:plant:PACK-01',
      station: 'packaging',
      status: 'queued',
      throughput_kg_h: 1500,
      rated_capacity_kg_h: 1500,
      health_score: 1.0,
      temperature_c: 21.0,
      vibration_rms: 0.5,
      input_material: 'Finished Cocoa Powder & Butter',
      input_available: true,
      input_quantity_kg: 2500,
      output_product: 'Export Pallets & Cartons',
      progress_pct: 0,
      operational_reason: 'Final packaging queue',
      upstream_stage_num: 6,
      downstream_stage_num: null,
    },
  ]

  const displayStages = stages.length > 0 ? stages : fallbackStages
  const selectedStage = displayStages.find((s) => s.num === selectedStageNum) || displayStages[0]

  const cycleInfo = prodCycle || snapshot?.production_cycle || {
    cycle_number: 1,
    active_stage_num: 1,
    stage_step_counter: 0,
    completed_cycles: 0,
  }

  const getStatusBadge = (status: string) => {
    const s = status.toLowerCase()
    switch (s) {
      case 'running':
      case 'nominal':
        return (
          <span
            className="badge badge-nominal"
            style={{
              background: 'rgba(34, 197, 94, 0.18)',
              color: '#4ADE80',
              borderColor: 'var(--accent-green)',
            }}
          >
            <Activity size={10} className="animate-pulse" /> Running
          </span>
        )
      case 'completed':
        return (
          <span className="badge badge-completed">
            <CheckCircle size={10} /> Completed
          </span>
        )
      case 'pending':
      case 'queued':
        return (
          <span className="badge badge-neutral">
            <Clock size={10} /> Pending
          </span>
        )
      case 'at_risk':
      case 'degraded':
        return (
          <span className="badge badge-warning">
            <AlertTriangle size={10} /> At Risk
          </span>
        )
      case 'blocked':
        return (
          <span className="badge badge-danger">
            <AlertTriangle size={10} /> Blocked
          </span>
        )
      case 'offline':
      case 'failed':
        return <span className="badge badge-danger">Offline</span>
      case 'recovering':
        return <span className="badge badge-info">Recovering</span>
      default:
        return <span className="badge badge-neutral">{status}</span>
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
      {/* ---------------------------------------------------------------------
          1. Dynamic 7-Stage Process Line Flow with Sequential Lifecycle
      --------------------------------------------------------------------- */}
      <div className="panel">
        <div className="panel-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <div className="panel-title">
              <Layers size={16} color="var(--accent-green-bright)" />
              Dynamic 7-Stage Cocoa Manufacturing Process Line
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span className="badge badge-nominal" style={{ fontSize: '0.68rem', padding: '3px 8px' }}>
                Cycle #{cycleInfo.cycle_number}
              </span>
              <span className="badge badge-neutral" style={{ fontSize: '0.68rem', padding: '3px 8px' }}>
                {cycleInfo.completed_cycles} Completed Cycles
              </span>
              <span className="badge badge-info" style={{ fontSize: '0.68rem', padding: '3px 8px' }}>
                Active: Stage 0{cycleInfo.active_stage_num}/07
              </span>
            </div>
          </div>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            Plant Status: {snapshot.simulation_time} • Click stage for live SCADA telemetry
          </span>
        </div>

        {/* 7 Interactive Stage Cards */}
        <div className="process-line-grid">
          {displayStages.map((st) => {
            const isSelected = selectedStageNum === st.num
            const isOfflineOrBlocked = st.status === 'blocked' || st.status === 'offline'
            const isAtRisk = st.status === 'at_risk' || st.status === 'degraded'

            return (
              <div
                key={st.num}
                className={`stage-card ${isSelected ? 'selected' : ''}`}
                onClick={() => setSelectedStageNum(st.num)}
                style={{
                  borderLeft: isOfflineOrBlocked
                    ? '3px solid var(--accent-crimson)'
                    : isAtRisk
                    ? '3px solid var(--accent-orange)'
                    : st.status === 'running'
                    ? '3px solid var(--accent-green)'
                    : st.status === 'completed'
                    ? '3px solid var(--accent-green-dark)'
                    : '3px solid var(--border-subtle)',
                  background: isSelected
                    ? 'var(--bg-card-hover)'
                    : st.status === 'running'
                    ? 'rgba(34, 197, 94, 0.05)'
                    : 'var(--bg-canvas)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '3px' }}>
                  <span className="stage-card-num">STAGE 0{st.num}</span>
                  {getStatusBadge(st.status)}
                </div>

                <div className="stage-card-name" title={st.name}>
                  {st.name}
                </div>
                <div className="stage-card-machine">
                  {st.machine_code}
                </div>

                {/* Progress Mini Bar */}
                <div style={{ width: '100%', height: '4px', background: 'var(--border-subtle)', borderRadius: '1px', overflow: 'hidden', marginBottom: '6px' }}>
                  <div
                    style={{
                      width: `${st.progress_pct}%`,
                      height: '100%',
                      background: isOfflineOrBlocked
                        ? 'var(--accent-crimson)'
                        : isAtRisk
                        ? 'var(--accent-orange)'
                        : st.status === 'completed'
                        ? 'var(--accent-green-dark)'
                        : 'var(--accent-green)',
                    }}
                  />
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.68rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                  <span>{st.status === 'pending' ? '--' : `${st.throughput_kg_h.toFixed(0)} kg/h`}</span>
                  <span>{(st.health_score * 100).toFixed(0)}% hlth</span>
                </div>
              </div>
            )
          })}
        </div>

        {/* Selected Stage Detail Inspector */}
        {selectedStage && (
          <div
            style={{
              background: 'var(--bg-canvas)',
              border: '1px solid var(--border-color)',
              borderLeft: '4px solid var(--accent-orange)',
              borderRadius: '2px',
              padding: '16px',
              marginTop: '4px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px', marginBottom: '14px', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '10px' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>
                    Stage 0{selectedStage.num}: {selectedStage.name} ({selectedStage.machine_code})
                  </span>
                  {getStatusBadge(selectedStage.status)}
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginTop: '3px' }}>
                  Operational State: <span style={{ fontFamily: 'var(--font-mono)', color: selectedStage.status === 'running' ? 'var(--accent-sage)' : 'var(--accent-orange)' }}>{selectedStage.operational_reason}</span>
                </div>
              </div>

              {/* Upstream/Downstream Lineage Breadcrumb */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.72rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                <span>Upstream: {selectedStage.upstream_stage_num ? `Stage 0${selectedStage.upstream_stage_num}` : 'Silo 1 (Raw Material)'}</span>
                <ArrowRight size={12} />
                <span style={{ color: 'var(--text-main)', fontWeight: 600 }}>Stage 0{selectedStage.num}</span>
                <ArrowRight size={12} />
                <span>Downstream: {selectedStage.downstream_stage_num ? `Stage 0${selectedStage.downstream_stage_num}` : 'Customer Dispatch'}</span>
              </div>
            </div>

            {/* SCADA Telemetry 4-Gauge Matrix */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px', marginBottom: '16px' }}>
              <div className="metric-box">
                <div className="metric-label">Operating Throughput</div>
                <div className="metric-value">
                  {selectedStage.throughput_kg_h.toFixed(0)}
                  <span className="metric-unit">kg/h</span>
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  Rated: {selectedStage.rated_capacity_kg_h} kg/h ({((selectedStage.throughput_kg_h / Math.max(1, selectedStage.rated_capacity_kg_h)) * 100).toFixed(0)}% load)
                </div>
              </div>

              <div className="metric-box">
                <div className="metric-label">Asset Health Score</div>
                <div className="metric-value" style={{ color: selectedStage.health_score > 0.8 ? 'var(--accent-sage)' : selectedStage.health_score > 0.4 ? 'var(--accent-orange)' : 'var(--accent-crimson)' }}>
                  {(selectedStage.health_score * 100).toFixed(1)}%
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  ISO 10816 Diagnostic Condition
                </div>
              </div>

              <div className="metric-box">
                <div className="metric-label">Bearing Temperature</div>
                <div className="metric-value" style={{ color: selectedStage.temperature_c >= 120 ? 'var(--accent-crimson)' : 'var(--text-main)' }}>
                  {selectedStage.temperature_c.toFixed(1)}°C
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  Limit: 120.0°C {selectedStage.temperature_c >= 120 ? '(CRITICAL)' : '(Normal)'}
                </div>
              </div>

              <div className="metric-box">
                <div className="metric-label">Vibration RMS</div>
                <div className="metric-value" style={{ color: selectedStage.vibration_rms >= 2.5 ? 'var(--accent-orange)' : 'var(--text-main)' }}>
                  {selectedStage.vibration_rms.toFixed(2)}
                  <span className="metric-unit">mm/s</span>
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  Limit: 2.5 mm/s {selectedStage.vibration_rms >= 2.5 ? '(UNBALANCE)' : '(Stable)'}
                </div>
              </div>
            </div>

            {/* In/Out Material Stream Information */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '12px' }}>
              <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', padding: '10px 12px', borderRadius: '2px' }}>
                <span style={{ fontSize: '0.68rem', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>
                  Feed Input Material
                </span>
                <div style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-main)', marginBottom: '2px' }}>
                  {selectedStage.input_material}
                </div>
                <div style={{ fontSize: '0.72rem', fontFamily: 'var(--font-mono)', color: selectedStage.input_available ? 'var(--accent-sage)' : 'var(--accent-crimson)' }}>
                  {selectedStage.input_available ? `✓ Infeed stock available (${selectedStage.input_quantity_kg.toFixed(0)} kg)` : '✗ Infeed starving / upstream blocked'}
                </div>
              </div>

              <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', padding: '10px 12px', borderRadius: '2px' }}>
                <span style={{ fontSize: '0.68rem', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>
                  Stage Discharge Product
                </span>
                <div style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-main)', marginBottom: '2px' }}>
                  {selectedStage.output_product}
                </div>
                <div style={{ fontSize: '0.72rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                  Transfers directly to Stage 0{selectedStage.downstream_stage_num || '—'}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------------------
          2. Supply Chain Stockout Forecasting & Lookahead Depletion Engine
      --------------------------------------------------------------------- */}
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">
            <TrendingDown size={16} color="var(--accent-cocoa-light)" />
            Supply Chain Inventory Depletion & Lookahead Stockout Risk
          </div>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            Lookahead Horizon: 25 Simulation Steps (6.25h) • T_stockout = Q_usable / R_burn
          </span>
        </div>

        {/* Starvation Gap Warning Callout if at risk */}
        {supplyChain?.is_stockout_before_replenishment && (
          <div
            style={{
              padding: '12px 16px',
              borderRadius: '2px',
              background: 'var(--status-danger-bg)',
              border: '1px solid var(--status-danger-border)',
              borderLeft: '4px solid var(--accent-crimson)',
              marginBottom: '16px',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '12px',
            }}
          >
            <AlertTriangle size={20} color="var(--accent-crimson)" style={{ marginTop: '2px', flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: 700, fontSize: '0.85rem', color: '#DFA29D', fontFamily: 'var(--font-mono)', textTransform: 'uppercase' }}>
                Critical Inbound Starvation Window ({supplyChain.starvation_gap_hours} Hours Gap)
              </div>
              <div style={{ fontSize: '0.78rem', color: 'var(--text-main)', marginTop: '4px', lineHeight: 1.4 }}>
                Silo 1 runs dry at <strong>Step {supplyChain.projected_stockout_step}</strong>, but Inbound Shipment <strong>{supplyChain.shipment.id}</strong> (PO {supplyChain.shipment.po_number}) is delayed by customs inspection hold until <strong>Step {supplyChain.shipment.eta_step}</strong>.
                Stage 1 (Cleaning) and Stage 2 (Roasting) will enter starvation halt, placing <strong>Customer Order {supplyChain.affected_order_code}</strong> ({supplyChain.affected_customer}) in immediate SLA default ($350/day penalty).
              </div>
            </div>
          </div>
        )}

        {/* 4 KPIs: Usable Stock, Burn Rate, Time to Stockout, Inbound ETA */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px', marginBottom: '18px' }}>
          <div className="metric-box">
            <div className="metric-label">Raw Bean Working Stock (Silo 1)</div>
            <div className="metric-value" style={{ color: (supplyChain?.usable_inventory_kg || 0) < 1500 ? 'var(--accent-orange)' : 'var(--text-main)' }}>
              {supplyChain ? supplyChain.usable_inventory_kg.toLocaleString() : '3,600'}
              <span className="metric-unit">kg</span>
            </div>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              Lot LOT-2026-001 {supplyChain?.is_quarantined ? '(QUARANTINED)' : '(Approved)'}
            </div>
          </div>

          <div className="metric-box">
            <div className="metric-label">Roaster Burn Rate</div>
            <div className="metric-value">
              {supplyChain ? supplyChain.active_burn_rate_kg_h : 1200}
              <span className="metric-unit">kg/h</span>
            </div>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              {supplyChain ? supplyChain.active_burn_rate_kg_step : 300} kg per 15-min step
            </div>
          </div>

          <div className="metric-box">
            <div className="metric-label">Projected Silo Stockout</div>
            <div className="metric-value" style={{ color: supplyChain?.is_stockout_before_replenishment ? 'var(--accent-crimson)' : 'var(--accent-sage)' }}>
              {supplyChain?.steps_until_stockout != null ? `${supplyChain.steps_until_stockout} steps` : 'Safe'}
            </div>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              {supplyChain?.projected_stockout_step != null ? `Step ${supplyChain.projected_stockout_step} (${supplyChain.steps_until_stockout ? (supplyChain.steps_until_stockout * 0.25).toFixed(1) : '0'}h remaining)` : 'No stockout forecast'}
            </div>
          </div>

          <div className="metric-box">
            <div className="metric-label">Inbound Shipment Replenishment</div>
            <div className="metric-value">
              {supplyChain?.shipment.quantity_kg.toLocaleString() || '15,000'}
              <span className="metric-unit">kg</span>
            </div>
            <div style={{ fontSize: '0.68rem', color: supplyChain?.shipment.status === 'customs_hold' ? 'var(--accent-orange)' : 'var(--accent-sage)', fontFamily: 'var(--font-mono)' }}>
              ETA Step {supplyChain?.shipment.eta_step || 8} ({supplyChain?.shipment.status || 'in_transit'})
            </div>
          </div>
        </div>

        {/* Lookahead Trajectory Chart / Table */}
        {supplyChain?.trajectory && supplyChain.trajectory.length > 0 && (
          <div>
            <div style={{ fontSize: '0.72rem', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px' }}>
              Depletion Trajectory Simulation:
            </div>
            <div className="table-container" style={{ maxHeight: '200px', overflowY: 'auto' }}>
              <table className="scada-table">
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Sim Time</th>
                    <th>Projected Silo Stock (kg)</th>
                    <th>Depletion Visual</th>
                    <th>Replenishment Event</th>
                    <th>Stockout State</th>
                  </tr>
                </thead>
                <tbody>
                  {supplyChain.trajectory.slice(0, 16).map((pt) => {
                    const maxStock = 20000.0
                    const barPct = Math.min(100, Math.max(0, (pt.inventory_kg / maxStock) * 100))

                    return (
                      <tr key={pt.step} style={{ background: pt.is_stockout ? 'rgba(167, 71, 61, 0.08)' : 'transparent' }}>
                        <td>Step {pt.step}</td>
                        <td>{pt.simulation_time}</td>
                        <td style={{ fontWeight: 600, color: pt.is_stockout ? 'var(--accent-crimson)' : 'var(--text-main)' }}>
                          {pt.inventory_kg.toLocaleString()} kg
                        </td>
                        <td style={{ width: '180px' }}>
                          <div style={{ width: '100%', height: '8px', background: 'var(--border-subtle)', borderRadius: '1px', overflow: 'hidden' }}>
                            <div
                              className={`trajectory-bar ${pt.is_stockout ? 'stockout' : pt.is_replenishment_step ? 'replenishment' : ''}`}
                              style={{ width: `${barPct}%` }}
                            />
                          </div>
                        </td>
                        <td>
                          {pt.is_replenishment_step ? (
                            <span className="badge badge-nominal">+15,000 kg Arrived</span>
                          ) : (
                            <span style={{ color: 'var(--text-dim)' }}>—</span>
                          )}
                        </td>
                        <td>
                          {pt.is_stockout ? (
                            <span className="badge badge-danger">STARVED</span>
                          ) : (
                            <span className="badge badge-nominal">OK</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------------------
          3. Machine Fleet Telemetry & Active Production Batches
      --------------------------------------------------------------------- */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: '20px' }}>
        {/* Machine Fleet Telemetry Table */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <Activity size={16} color="var(--accent-sage)" />
              Machine Asset Fleet Telemetry ({machines.length} Units)
            </div>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>SCADA Real-time</span>
          </div>

          <div className="table-container">
            <table className="scada-table">
              <thead>
                <tr>
                  <th>Machine</th>
                  <th>Station</th>
                  <th>Status</th>
                  <th>Throughput</th>
                  <th>Temp</th>
                  <th>Vib RMS</th>
                  <th>Health</th>
                </tr>
              </thead>
              <tbody>
                {machines.map((m) => {
                  const isOverheat = m.temperature_c >= 120.0
                  const isHighVib = m.vibration_rms >= 2.5
                  const mKey = m.code || m.id || 'mach'

                  return (
                    <tr key={mKey}>
                      <td style={{ fontWeight: 600 }}>{m.code || m.name}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{m.station}</td>
                      <td>{getStatusBadge(m.status)}</td>
                      <td>{(m.current_throughput_kg_h || m.current_throughput_kgh || 0).toFixed(0)} kg/h</td>
                      <td style={{ color: isOverheat ? 'var(--accent-crimson)' : 'inherit', fontWeight: isOverheat ? 700 : 400 }}>
                        {m.temperature_c.toFixed(1)}°C
                      </td>
                      <td style={{ color: isHighVib ? 'var(--accent-orange)' : 'inherit', fontWeight: isHighVib ? 700 : 400 }}>
                        {m.vibration_rms.toFixed(2)}
                      </td>
                      <td>{((m as any).health_score ? ((m as any).health_score * 100).toFixed(0) : '98')}%</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Active Production Batches & Customer Orders */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <Boxes size={16} color="var(--accent-cocoa-light)" />
              Active Production Batches & SLA Commitments
            </div>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>ERP & MES Sync</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {batches.map((b) => {
              const pct = Math.min(100, Math.round(((b.actual_quantity_kg || 0) / Math.max(1, b.planned_quantity_kg)) * 100))
              const isDelayed = b.status === 'delayed'

              return (
                <div
                  key={b.batch_code}
                  style={{
                    background: 'var(--bg-canvas)',
                    border: '1px solid var(--border-subtle)',
                    borderLeft: isDelayed ? '3px solid var(--accent-orange)' : '3px solid var(--accent-sage)',
                    padding: '10px 12px',
                    borderRadius: '2px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <div>
                      <span style={{ fontWeight: 700, fontSize: '0.82rem', fontFamily: 'var(--font-mono)', color: 'var(--text-main)' }}>
                        {b.batch_code}
                      </span>
                      <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginLeft: '8px' }}>
                        {b.recipe_name}
                      </span>
                    </div>
                    {getStatusBadge(b.status)}
                  </div>

                  {/* Progress bar */}
                  <div style={{ width: '100%', height: '5px', background: 'var(--border-subtle)', borderRadius: '1px', overflow: 'hidden', marginBottom: '6px' }}>
                    <div style={{ width: `${pct}%`, height: '100%', background: isDelayed ? 'var(--accent-orange)' : 'var(--accent-cocoa-light)' }} />
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.70rem', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                    <span>Actual: {b.actual_quantity_kg.toFixed(0)} / {b.planned_quantity_kg.toFixed(0)} kg ({pct}%)</span>
                    <span>Delay: {(b as any).delay_hours ? `${(b as any).delay_hours}h` : '0h'}</span>
                  </div>
                </div>
              )
            })}

            {/* Customer Orders */}
            <div style={{ marginTop: '4px' }}>
              <div style={{ fontSize: '0.70rem', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '6px' }}>
                Customer Orders & Financial Penalties:
              </div>
              {orders.map((o) => (
                <div
                  key={o.order_code}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '6px 8px',
                    borderBottom: '1px solid var(--border-subtle)',
                    fontSize: '0.75rem',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  <div>
                    <span style={{ fontWeight: 600, color: 'var(--text-main)' }}>{o.order_code}</span>
                    <span style={{ color: 'var(--text-muted)', marginLeft: '6px' }}>({o.customer_name})</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ color: 'var(--accent-orange)' }}>${(o.penalty_per_day_late || 350).toFixed(0)}/day SLA</span>
                    {getStatusBadge(o.status)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
