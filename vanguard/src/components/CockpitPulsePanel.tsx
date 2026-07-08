import { useCallback, useEffect, useState } from 'react'
import {
  getExamStatus,
  getPulse,
  getSepoliaStatus,
  getTrackRecord,
  type ExamStatus,
  type Pulse,
  type SepoliaStatus,
  type TrackRecord,
} from '../api'

const POLL_MS = 30_000

const CYCLE_LABELS: Record<string, string> = {
  vc_crawl: 'Découverte de candidats',
  vc_weekly_forecast: 'Prévision hebdomadaire',
  vc_radar_x: 'Radar X (écoute sociale)',
  vc_thesis_review: 'Revue de thèse',
  paper_trade_cycle: 'Cycle paper-trading',
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'jamais'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'inconnu'
  const mins = Math.round((Date.now() - then) / 60_000)
  if (mins < 1) return "à l'instant"
  if (mins < 60) return `il y a ${mins} min`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `il y a ${hours} h`
  return `il y a ${Math.round(hours / 24)} j`
}

export function CockpitPulsePanel() {
  const [pulse, setPulse] = useState<Pulse | null>(null)
  const [track, setTrack] = useState<TrackRecord | null>(null)
  const [exam, setExam] = useState<ExamStatus | null>(null)
  const [sepolia, setSepolia] = useState<SepoliaStatus | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    getPulse()
      .then((p) => {
        setPulse(p)
        setFailed(false)
      })
      .catch(() => setFailed(true))
    getTrackRecord()
      .then(setTrack)
      .catch(() => {
        /* le tri fraude/légitime reste optionnel — le pouls reste utile sans */
      })
    getExamStatus()
      .then(setExam)
      .catch(() => {
        /* l'examen reste optionnel — le pouls reste utile sans */
      })
    getSepoliaStatus()
      .then(setSepolia)
      .catch(() => {
        /* le rehearsal Sepolia reste optionnel — le pouls reste utile sans */
      })
  }, [])

  useEffect(() => {
    load()
    const id = window.setInterval(load, POLL_MS)
    return () => window.clearInterval(id)
  }, [load])

  if (failed && !pulse) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <p className="text-sm text-[#8b8f9a]">Pouls indisponible pour le moment — nouvelle tentative automatique.</p>
      </div>
    )
  }

  if (!pulse) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <div className="skeleton h-6 w-40 rounded-sm mb-5" />
        <div className="grid sm:grid-cols-2 gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-14 rounded-sm" />
          ))}
        </div>
      </div>
    )
  }

  const cycles = Object.entries(pulse.heartbeat.cycles)
  const alive = pulse.heartbeat.alive

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <div className="flex items-center justify-between flex-wrap gap-3 mb-5">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2.5 w-2.5">
            {alive ? (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
            ) : null}
            <span
              className={`relative inline-flex rounded-full h-2.5 w-2.5 ${alive ? 'bg-emerald-400' : 'bg-[#d98a8a]'}`}
            />
          </span>
          <span className="text-sm text-[#f4efe6] font-medium">
            {alive ? 'ARIA est en vie' : 'Aucun battement récent'}
          </span>
          <span className="text-xs text-[#8b8f9a] font-mono">— {relativeTime(pulse.heartbeat.last_tick)}</span>
        </div>
        <span className="text-[10px] uppercase tracking-[0.2em] text-[#8a7344] font-mono">
          build {pulse.commit}
        </span>
      </div>

      {cycles.length === 0 ? (
        <p className="text-sm text-[#8b8f9a] mb-5">Aucun cycle enregistré pour l'instant.</p>
      ) : (
        <div className="grid sm:grid-cols-2 gap-3 mb-5">
          {cycles.map(([key, ts]) => (
            <div key={key} className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">
                {CYCLE_LABELS[key] ?? key}
              </p>
              <p className="text-sm text-[#d4d0c8] font-mono tabular-nums">{relativeTime(ts)}</p>
            </div>
          ))}
        </div>
      )}

      {track ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <div className="minimal-card px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Contrats gardés</p>
            <p className="text-lg text-[#c9a962] font-mono tabular-nums">{track.pool_active}</p>
          </div>
          <div className="minimal-card px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Contrats rejetés</p>
            <p className="text-lg text-[#d98a8a] font-mono tabular-nums">{track.pool_rejected}</p>
          </div>
          <div className="minimal-card px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Pronostics</p>
            <p className="text-lg text-[#d4d0c8] font-mono tabular-nums">{track.verdicts_total}</p>
          </div>
          <div className="minimal-card px-4 py-3">
            <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Hit-rate</p>
            <p className="text-lg text-[#d4d0c8] font-mono tabular-nums">
              {track.hit_rate != null ? `${Math.round(track.hit_rate * 100)}%` : '—'}
            </p>
          </div>
        </div>
      ) : null}

      {exam?.enabled ? (
        <div className="mb-5">
          <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-2">
            Examen trading — jour {exam.current_day}/{exam.program_days}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Score du jour</p>
              <p className="text-lg text-[#d4d0c8] font-mono tabular-nums">
                {exam.today.avg_score != null ? `${exam.today.avg_score}/10` : '—'}
              </p>
            </div>
            <div className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Score cumulé</p>
              <p className="text-lg text-[#c9a962] font-mono tabular-nums">
                {exam.cumulative.avg_score != null ? `${exam.cumulative.avg_score}/10` : '—'}
              </p>
            </div>
            <div className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Questions répondues</p>
              <p className="text-lg text-[#d4d0c8] font-mono tabular-nums">{exam.cumulative.answered}</p>
            </div>
          </div>
        </div>
      ) : null}

      {sepolia?.enabled ? (
        <div className="mb-5">
          <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
            <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344]">
              Rehearsal Sepolia autonome (testnet, aucune valeur réelle)
            </p>
            {sepolia.circuit_breaker_open ? (
              <span className="text-[10px] uppercase tracking-[0.12em] text-[#d98a8a] font-mono">
                coupe-circuit armé
              </span>
            ) : null}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-2">
            <div className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Cycles</p>
              <p className="text-lg text-[#d4d0c8] font-mono tabular-nums">{sepolia.cycles_total}</p>
            </div>
            <div className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Tx envoyées</p>
              <p className="text-lg text-[#c9a962] font-mono tabular-nums">{sepolia.tx_count}</p>
            </div>
            <div className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Erreurs</p>
              <p className="text-lg text-[#d98a8a] font-mono tabular-nums">{sepolia.error_count}</p>
            </div>
            <div className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">Hésitations</p>
              <p className="text-lg text-[#d4d0c8] font-mono tabular-nums">{sepolia.hesitation_count}</p>
            </div>
          </div>
          {sepolia.last ? (
            <p className="text-xs text-[#8b8f9a] font-mono">
              Dernière décision : {sepolia.last.symbol || '—'} · {sepolia.last.decision} (
              {sepolia.last.outcome}) — {relativeTime(sepolia.last.at)}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <span className="luxury-badge">exécution réelle : {pulse.real_execution ? 'ON' : 'OFF (garde-fou)'}</span>
        <span className="luxury-badge">paper-trading : {pulse.paper_trading ? 'actif' : 'inactif'}</span>
        <span className="luxury-badge">
          ancrage onchain : {pulse.onchain.anchor_ready ? 'prêt' : 'non armé'}
        </span>
      </div>
    </div>
  )
}
