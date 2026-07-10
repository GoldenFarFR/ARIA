import { useEffect, useState } from 'react'
import { getMarketCycle, type MarketCycle } from '../api'

const PHASES = [
  { key: 'accumulation', label: 'Accumulation', color: '#7fb88f' },
  { key: 'hausse (markup)', label: 'Hausse', color: '#e8c468' },
  { key: 'distribution', label: 'Distribution', color: '#e0a15a' },
  { key: 'baisse (markdown)', label: 'Baisse', color: '#d98a8a' },
]

function relativeDays(since: string): string {
  const then = new Date(since).getTime()
  if (Number.isNaN(then)) return since
  const days = Math.max(0, Math.round((Date.now() - then) / 86_400_000))
  return `depuis ${days} j`
}

function PhaseTrack({ activeLabel }: { activeLabel: string }) {
  return (
    <div className="flex gap-1.5 mb-4" role="img" aria-label={`Phase actuelle du cycle : ${activeLabel}`}>
      {PHASES.map((p) => {
        const active = p.key === activeLabel
        return (
          <div key={p.key} className="flex-1">
            <div
              className="h-1.5 rounded-full transition-opacity"
              style={{ backgroundColor: p.color, opacity: active ? 0.9 : 0.15 }}
            />
            <p
              className="text-[10px] uppercase tracking-[0.1em] mt-1.5 truncate"
              style={{ color: active ? p.color : '#5c5f66', fontWeight: active ? 600 : 400 }}
            >
              {p.label}
            </p>
          </div>
        )
      })}
    </div>
  )
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
  const activePhase = PHASES.find((p) => p.key === phase.label)
  const color = activePhase?.color ?? '#8b8f9a'

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <p className="section-label mb-4">Cycle Bitcoin (halving à halving)</p>

      <PhaseTrack activeLabel={phase.label} />

      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="text-lg text-[#f4efe6] font-medium capitalize leading-tight">
            {activePhase?.label ?? phase.label}
          </p>
          <p className="text-xs text-[#8b8f9a] mt-0.5">
            {phase.cycle_name} · {relativeDays(phase.since)}
          </p>
        </div>
        <p className="text-3xl font-mono tabular-nums" style={{ color }}>
          {phase.change_pct >= 0 ? '+' : ''}
          {phase.change_pct.toFixed(0)}%
        </p>
      </div>

      <p className="text-[11px] text-[#8b8f9a] mt-4 leading-relaxed">{cycle.disclaimer}</p>
    </div>
  )
}
