import { Bell, Radio } from 'lucide-react'
import type { Alert } from '../types'

interface AlertsPanelProps {
  alerts: Alert[]
  connected: boolean
  compact?: boolean
}

function signalBadge(type: string) {
  if (type === 'buy') return 'bg-buy/15 text-buy border-buy/30'
  if (type === 'sell') return 'bg-sell/15 text-sell border-sell/30'
  return 'bg-watch/15 text-watch border-watch/30'
}

export function AlertsPanel({ alerts, connected, compact }: AlertsPanelProps) {
  return (
    <div className="pixel-panel overflow-hidden flex flex-col">
      <div className="px-3 py-2 border-b-2 border-border-bright bg-panel-elevated">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Bell className="w-4 h-4 text-accent shrink-0" />
            <h3 className="pixel-label">Live alerts</h3>
          </div>
          <div className="flex items-center gap-1.5 text-xs font-terminal shrink-0">
            <Radio className={`w-3 h-3 ${connected ? 'text-buy' : 'text-sell'}`} />
            <span className={connected ? 'text-buy' : 'text-sell'}>
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        </div>
        <p className="text-xs text-terminal/45 font-terminal mt-1.5 leading-snug">
          ARIA scans your favorites every ~30s. Strong buy/sell signals on watched tokens appear here instantly.
        </p>
      </div>

      <div className={`overflow-y-auto p-2 space-y-1.5 ${compact ? 'max-h-40' : 'max-h-52'}`}>
        {alerts.length === 0 ? (
          <p className="text-xs text-terminal/50 text-center py-4 font-terminal leading-relaxed">
            Add tokens to favorites — alerts show up when price action triggers a signal.
          </p>
        ) : (
          alerts.map((alert) => (
            <div key={alert.id} className="pixel-panel-inset p-2">
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-sm font-terminal font-medium text-terminal">{alert.symbol}</span>
                <span
                  className={`text-xs px-1.5 py-0.5 border-2 uppercase font-terminal ${signalBadge(alert.signal_type)}`}
                >
                  {alert.signal_type}
                </span>
              </div>
              <p className="text-xs text-terminal/60 font-terminal line-clamp-2">{alert.message}</p>
            </div>
          ))
        )}
      </div>
    </div>
  )
}