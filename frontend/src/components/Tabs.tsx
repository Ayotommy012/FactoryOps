// frontend/src/components/Tabs.tsx
import type { FC } from 'react'
import {
  Factory,
  Zap,
  AlertOctagon,
  Network,
  DatabaseZap,
  ListOrdered,
} from 'lucide-react'

export type TabKey = 'operations' | 'disruptions' | 'recovery' | 'world_model' | 'federation' | 'timeline'

interface TabsProps {
  activeTab: TabKey
  onSelectTab: (tab: TabKey) => void
  hasActiveAnomaly?: boolean
  eventCount?: number
}

export const Tabs: FC<TabsProps> = ({
  activeTab,
  onSelectTab,
  hasActiveAnomaly,
  eventCount,
}) => {
  const tabs = [
    {
      key: 'operations' as TabKey,
      label: 'Operations & Process Line',
      icon: Factory,
      badge: null,
    },
    {
      key: 'disruptions' as TabKey,
      label: 'Disruption Simulator',
      icon: Zap,
      badge: 'STRESS',
      badgeColor: 'badge-warning',
    },
    {
      key: 'recovery' as TabKey,
      label: 'Recovery Intelligence',
      icon: AlertOctagon,
      badge: hasActiveAnomaly ? 'ANOMALY' : null,
      badgeColor: 'badge-danger',
    },
    {
      key: 'world_model' as TabKey,
      label: 'World Model & Graph',
      icon: Network,
      badge: null,
    },
    {
      key: 'federation' as TabKey,
      label: 'USL & Zetaris Federation',
      icon: DatabaseZap,
      badge: 'JDBC',
      badgeColor: 'badge-info',
    },
    {
      key: 'timeline' as TabKey,
      label: 'Event Audit Log',
      icon: ListOrdered,
      badge: eventCount ? `${eventCount}` : null,
      badgeColor: 'badge-info',
    },
  ]

  return (
    <nav
      style={{
        background: 'var(--bg-canvas)',
        borderBottom: '1px solid var(--border-color)',
        padding: '0 24px',
        display: 'flex',
        gap: '4px',
        overflowX: 'auto',
      }}
    >
      {tabs.map((tab) => {
        const Icon = tab.icon
        const isActive = activeTab === tab.key
        return (
          <button
            key={tab.key}
            onClick={() => onSelectTab(tab.key)}
            style={{
              background: 'transparent',
              border: 'none',
              borderBottom: isActive ? '2px solid var(--accent-green)' : '2px solid transparent',
              color: isActive ? 'var(--text-main)' : 'var(--text-muted)',
              padding: '12px 16px',
              fontSize: '0.82rem',
              fontWeight: isActive ? 600 : 500,
              fontFamily: 'var(--font-mono)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              transition: 'all 0.15s ease',
              whiteSpace: 'nowrap',
            }}
          >
            <Icon size={15} color={isActive ? 'var(--accent-green-bright)' : 'var(--text-dim)'} />
            <span>{tab.label}</span>
            {tab.badge && (
              <span
                className={`badge ${tab.badgeColor || 'badge-info'}`}
                style={{ fontSize: '0.62rem', padding: '1px 5px' }}
              >
                {tab.badge}
              </span>
            )}
          </button>
        )
      })}
    </nav>
  )
}
