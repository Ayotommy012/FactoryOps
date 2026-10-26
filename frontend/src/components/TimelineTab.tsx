// frontend/src/components/TimelineTab.tsx
import { useState, type FC } from 'react'
import {
  ListOrdered,
  Filter,
  ChevronDown,
  ChevronRight,
  Tag,
} from 'lucide-react'
import type { OperationalTimelineEvent } from '../lib/types'

interface TimelineTabProps {
  timeline: OperationalTimelineEvent[]
}

export const TimelineTab: FC<TimelineTabProps> = ({ timeline }) => {
  const [filterType, setFilterType] = useState<string>('all')
  const [searchTerm, setSearchTerm] = useState('')
  const [expandedEventIds, setExpandedEventIds] = useState<Record<string, boolean>>({})

  // Collect unique event types
  const eventTypes = Array.from(new Set(timeline.map((e) => e.event_type)))

  const filteredEvents = timeline.filter((e) => {
    const matchesType = filterType === 'all' || e.event_type === filterType
    const matchesSearch =
      searchTerm === '' ||
      e.event_type.toLowerCase().includes(searchTerm.toLowerCase()) ||
      e.event_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      JSON.stringify(e.payload).toLowerCase().includes(searchTerm.toLowerCase())
    return matchesType && matchesSearch
  })

  const toggleExpand = (id: string) => {
    setExpandedEventIds((prev) => ({
      ...prev,
      [id]: !prev[id],
    }))
  }

  const getEventTypeColor = (type: string) => {
    if (type.includes('anomaly') || type.includes('failure') || type.includes('alert')) {
      return '#ef4444'
    }
    if (type.includes('recovery') || type.includes('decision') || type.includes('plan')) {
      return '#38bdf8'
    }
    if (type.includes('step') || type.includes('init')) {
      return '#22c55e'
    }
    return '#94a3b8'
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="panel-card">
        <div className="panel-header">
          <div className="panel-title panel-title-cyan">
            <ListOrdered size={18} />
            Append-Only Verified Operational Event Log ({timeline.length} Events)
          </div>

          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Filter size={14} color="#94a3b8" />
              <select
                className="select-input"
                style={{ width: '180px', padding: '5px 8px', fontSize: '0.78rem' }}
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
              >
                <option value="all">All Event Types ({timeline.length})</option>
                {eventTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ width: '220px' }}>
              <input
                type="text"
                className="input-text"
                placeholder="Search events..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>
        </div>

        {filteredEvents.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', color: '#94a3b8' }}>
            No operational events match current filter.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {filteredEvents.map((e) => {
              const isExpanded = !!expandedEventIds[e.event_id]
              const typeColor = getEventTypeColor(e.event_type)

              return (
                <div
                  key={e.event_id}
                  style={{
                    background: '#090d16',
                    border: '1px solid #1e293b',
                    borderRadius: '6px',
                    padding: '10px 14px',
                    transition: 'border-color 0.15s ease',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      cursor: 'pointer',
                    }}
                    onClick={() => toggleExpand(e.event_id)}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ color: '#64748b' }}>
                        {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                      </span>

                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '0.78rem',
                          color: '#94a3b8',
                          minWidth: '150px',
                        }}
                      >
                        {e.simulation_time.replace('T', ' ').substring(0, 19)}
                      </span>

                      <span
                        style={{
                          fontWeight: 700,
                          fontSize: '0.85rem',
                          color: typeColor,
                          minWidth: '180px',
                        }}
                      >
                        {e.event_type}
                      </span>

                      <span style={{ fontSize: '0.72rem', color: '#64748b', fontFamily: 'var(--font-mono)' }}>
                        {e.event_id}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {e.entity_refs && e.entity_refs.length > 0 && (
                        <span className="badge badge-neutral" style={{ fontSize: '0.65rem' }}>
                          <Tag size={10} />
                          {e.entity_refs.length} refs
                        </span>
                      )}
                      <span style={{ fontSize: '0.72rem', color: '#64748b' }}>
                        {isExpanded ? 'Hide Payload' : 'View Payload'}
                      </span>
                    </div>
                  </div>

                  {isExpanded && (
                    <div
                      style={{
                        marginTop: '10px',
                        paddingTop: '10px',
                        borderTop: '1px solid #1e293b',
                      }}
                    >
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginBottom: '4px' }}>
                        Payload Details & Source Lineage:
                      </div>
                      <pre className="code-box" style={{ maxHeight: '250px' }}>
                        {JSON.stringify(
                          {
                            recorded_at: e.recorded_at,
                            source_record_id: e.source_record_id,
                            entity_refs: e.entity_refs,
                            payload: e.payload,
                          },
                          null,
                          2
                        )}
                      </pre>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
