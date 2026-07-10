import { useEffect, useState } from 'react'
import { getMarketCycle, type MarketCycle } from '../api'

const PHASE_COLORS: Record<string, string> = {
  accumulation: '#7fb88f',
  'hausse (markup)': '#c9a962',
  distribution: '#e8c468',
  'baisse (markdown)': '#d98a8a',
}

function relativeDays(since: string): string {
  const then = new Date(since).getTime()
  if (Number.isNaN(then)) return since
  const days = Math.max(0, Math.round((Date.now() - then) / 86_400_000))
  return `depuis ${days} j`
}

export function CockpitCyclePanel() {
  const [cycle, setCycle] = useState<MarketCycle | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getMarketCycle()
      .then((c) => {
        if (!cancelled) setCycle(c)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (failed) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <p className="section-label mb-3">Cycle Bitcoin</p>
        <p className="text-sm text-[#8b8f9a]">Indisponible pour le moment.</p>
      </div>
    )
  }

  if (!cycle) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <div className="skeleton h-5 w-32 rounded-sm mb-4" />
        <div className="skeleton h-14 rounded-sm" />
      </div>
    )
  }

  if (!cycle.available || !cycle.phase) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <p className="section-label mb-3">Cycle Bitcoin</p>
        <p className="text-sm text-[#8b8f9a]">
          Historique Bitcoin momentanément indisponible — jamais une phase inventée.
        </p>
      </div>
    )
  }

  const { phase } = cycle
  const color = PHASE_COLORS[phase.label] ?? '#8b8f9a'

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <p className="section-label mb-4">Cycle Bitcoin (halving à halving)</p>
      <div className="flex items-center gap-3 mb-2">
        <span className="inline-flex h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
        <span className="text-lg text-[#f4efe6] font-medium capitalize">{phase.label}</span>
      </div>
      <p className="text-sm text-[#8b8f9a] mb-1">
        {phase.cycle_name} · {relativeDays(phase.since)}
      </p>
      <p
        className="text-2xl font-mono tabular-nums mb-4"
        style={{ color: phase.change_pct >= 0 ? '#7fb88f' : '#d98a8a' }}
      >
        {phase.change_pct >= 0 ? '+' : ''}
        {phase.change_pct.toFixed(0)}%
      </p>
      <p className="text-[11px] text-[#8b8f9a] leading-relaxed">{cycle.disclaimer}</p>
    </div>
  )
}
