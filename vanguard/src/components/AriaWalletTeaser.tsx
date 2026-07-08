import { useCallback, useEffect, useRef, useState } from 'react'
import { getIdentityToken, useIdentityToken, useLogin, usePrivy, useUser } from '@privy-io/react-auth'
import { getTrackRecord, type TrackRecord } from '../api'
import { PRIVY_LOGIN_METHODS } from '../lib/privy-config'
import { exchangePrivyForAriaSession } from '../lib/privy-session'

/**
 * Teaser « Portefeuille suivi ARIA » pour la page d'accueil (FOMO honnête).
 * Design validé (aperçu). Styles scopés sous .aw-scope pour ne pas heurter la home.
 * Facts-only : chiffres lus en direct depuis /api/aria/track-record. Tant que le
 * backend ne renvoie rien (ou 0 position), on affiche un état « en préparation »
 * sobre, jamais un chiffre gonflé.
 */

const CSS = `
.aw-scope{--gold:#c9a227;--gold-lite:#e6c463;--gold-deep:#8a6a13;--emerald:#2aa189;--emerald-lite:#37d39a;--ivory:#f6f2e9;--muted:#8b8f9a;--faint:#5c606b;--rose:#d98a8a;--line:rgba(230,196,99,.16);--serif:Georgia,'Cormorant Garamond','Times New Roman',serif;--mono:ui-monospace,'JetBrains Mono',Menlo,monospace}
.aw-card{position:relative;width:min(680px,100%);margin:0 auto;background:linear-gradient(165deg,#15171f,#0d0f15);border:1px solid var(--line);border-radius:18px;overflow:hidden;box-shadow:0 40px 90px -40px rgba(0,0,0,.8),0 1px 0 rgba(255,255,255,.04) inset}
.aw-card::before{content:"";position:absolute;inset:0;height:3px;background:linear-gradient(90deg,var(--emerald),var(--gold-lite) 55%,var(--gold-deep))}
.aw-pad{padding:30px 34px}
@media(max-width:560px){.aw-pad{padding:24px 22px}}
.aw-head{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.aw-eyebrow{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:.66rem;letter-spacing:.16em;text-transform:uppercase;color:var(--gold-lite)}
.aw-eyebrow .aw-dot{width:7px;height:7px;border-radius:50%;background:var(--emerald-lite);animation:aw-pulse 2.4s infinite}
@keyframes aw-pulse{0%{box-shadow:0 0 0 0 rgba(55,211,154,.5)}70%{box-shadow:0 0 0 7px rgba(55,211,154,0)}100%{box-shadow:0 0 0 0 rgba(55,211,154,0)}}
.aw-verified{display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:.6rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.aw-verified svg{width:13px;height:13px;stroke:var(--emerald-lite)}
.aw-big{margin:18px 0 2px;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.aw-ret{font-family:var(--serif);font-size:clamp(3rem,11vw,4.6rem);line-height:.9;font-weight:600;color:var(--ivory);letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.aw-ret .aw-sign{color:var(--emerald-lite)}
.aw-idx{font-family:var(--mono);font-size:.8rem;color:var(--muted);letter-spacing:.04em}
.aw-idx b{color:var(--gold-lite);font-weight:600}
.aw-since{font-family:inherit;font-size:.82rem;color:var(--muted);margin-top:4px}
.aw-spark{margin:16px -4px 6px;width:calc(100% + 8px)}
.aw-spark canvas{display:block;width:100%;height:90px}
.aw-sleeves{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
.aw-sleeve{border:1px solid var(--line);border-radius:11px;padding:13px 15px;background:rgba(255,255,255,.015)}
.aw-sleeve .aw-k{font-family:var(--mono);font-size:.6rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.aw-sleeve .aw-v{font-family:var(--serif);font-size:1.5rem;margin-top:4px;font-variant-numeric:tabular-nums;color:var(--emerald-lite)}
.aw-sleeve .aw-sub{font-size:.72rem;color:var(--faint);margin-top:2px}
.aw-trust{display:flex;flex-wrap:wrap;gap:9px 10px;margin-top:16px}
.aw-chip{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:.7rem;color:#cdd0d6;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:7px 11px;font-variant-numeric:tabular-nums}
.aw-chip svg{width:13px;height:13px;stroke:var(--gold-lite);flex:none}
.aw-chip b{color:var(--ivory);font-weight:600}
.aw-chip.aw-no b{color:var(--rose)}
.aw-cta{margin-top:20px;display:flex;align-items:center;gap:16px 20px;flex-wrap:wrap}
.aw-btn{display:inline-flex;align-items:center;gap:9px;padding:14px 26px;border-radius:8px;border:0;cursor:pointer;font-weight:700;font-size:.9rem;color:#161512;background:linear-gradient(120deg,var(--gold-lite),var(--gold) 58%,var(--emerald));background-size:160% 100%;transition:background-position .5s,transform .25s;text-decoration:none}
.aw-btn:hover{background-position:100% 0;transform:translateY(-1px)}
.aw-btn svg{width:15px;height:15px;stroke:#161512}
.aw-locknote{font-size:.8rem;color:var(--muted);max-width:300px}
.aw-locknote svg{width:12px;height:12px;stroke:var(--muted);vertical-align:-1px;margin-right:4px}
.aw-disc{margin-top:18px;padding-top:15px;border-top:1px solid rgba(255,255,255,.06);font-size:.72rem;font-style:italic;color:var(--faint);line-height:1.6}
.aw-prep{font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;color:var(--muted);margin:14px 0 4px}
@media(prefers-reduced-motion:reduce){.aw-scope *{animation:none!important;transition:none!important}}
`

function Icon({ d, extra }: { d: string; extra?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
      {extra ? <path d={extra} /> : null}
    </svg>
  )
}

export function AriaWalletTeaser() {
  const [data, setData] = useState<TrackRecord | null>(null)
  const [loaded, setLoaded] = useState(false)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const { getAccessToken } = usePrivy()
  const { refreshUser } = useUser()
  const { identityToken: hookIdentityToken } = useIdentityToken()
  const { login } = useLogin({
    onComplete: () => {
      void exchangePrivyForAriaSession(getAccessToken, getIdentityToken, refreshUser, hookIdentityToken)
    },
  })
  const joinWaitlist = useCallback(() => {
    login({ loginMethods: [...PRIVY_LOGIN_METHODS] })
  }, [login])

  useEffect(() => {
    let alive = true
    getTrackRecord()
      .then((d) => { if (alive) setData(d) })
      .catch(() => { /* backend absent -> état en préparation */ })
      .finally(() => { if (alive) setLoaded(true) })
    return () => { alive = false }
  }, [])

  // Le portefeuille n'a de contenu que si des positions sont valorisées.
  const hasData = !!data && (data.positions > 0 || data.verdicts_total > 0)
  const ret = data?.wallet_return_pct ?? 0
  const idx = data?.wallet_index ?? 100

  // Courbe : dessinée seulement si un historique réel existe (jamais inventée).
  useEffect(() => {
    const history = (data as (TrackRecord & { history?: number[] }) | null)?.history
    const cv = canvasRef.current
    if (!cv || !history || history.length < 2) return
    const cx = cv.getContext('2d')
    if (!cx) return
    const W = cv.width, H = cv.height, pad = 10
    const lo = Math.min(...history), hi = Math.max(...history), rng = hi - lo || 1
    const X = (i: number) => pad + (W - 2 * pad) * i / (history.length - 1)
    const Y = (v: number) => H - pad - (H - 2 * pad) * (v - lo) / rng
    const grad = cx.createLinearGradient(0, 0, 0, H)
    grad.addColorStop(0, 'rgba(230,196,99,.28)'); grad.addColorStop(1, 'rgba(230,196,99,0)')
    cx.beginPath(); cx.moveTo(X(0), Y(history[0]))
    history.forEach((v, i) => cx.lineTo(X(i), Y(v)))
    cx.lineTo(X(history.length - 1), H - pad); cx.lineTo(X(0), H - pad); cx.closePath()
    cx.fillStyle = grad; cx.fill()
    cx.beginPath(); cx.moveTo(X(0), Y(history[0]))
    history.forEach((v, i) => cx.lineTo(X(i), Y(v)))
    cx.strokeStyle = '#e6c463'; cx.lineWidth = 2.5; cx.lineJoin = 'round'; cx.stroke()
  }, [data])

  const hasHistory = !!(data as (TrackRecord & { history?: number[] }) | null)?.history?.length

  return (
    <div className="aw-scope">
      <style>{CSS}</style>
      <div className="aw-card">
        <div className="aw-pad">
          <div className="aw-head">
            <span className="aw-eyebrow"><span className="aw-dot" />ARIA &middot; Portefeuille suivi en direct</span>
            <span className="aw-verified">
              <Icon d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z" extra="m9 12 2 2 4-4" />
              Vérifiable on-chain
            </span>
          </div>

          {hasData ? (
            <>
              <div className="aw-big">
                <span className="aw-ret"><span className="aw-sign">{ret >= 0 ? '+' : ''}</span>{ret.toFixed(1)}%</span>
                <span className="aw-idx">indice <b>{idx.toFixed(1)}</b></span>
              </div>
              <div className="aw-since">Performance du suivi depuis le lancement &middot; valorisée aux prix on-chain réels</div>
              {hasHistory ? (
                <div className="aw-spark"><canvas ref={canvasRef} width={1200} height={180} aria-hidden /></div>
              ) : null}
              <div className="aw-sleeves">
                <div className="aw-sleeve">
                  <div className="aw-k">VC &middot; moyen / long terme (85%)</div>
                  <div className="aw-v">{(data!.vc_return_pct >= 0 ? '+' : '') + data!.vc_return_pct.toFixed(1)}%</div>
                  <div className="aw-sub">bâtisseurs cachés, thèse tenue</div>
                </div>
                <div className="aw-sleeve">
                  <div className="aw-k">Spéculation small-cap (15%)</div>
                  <div className="aw-v">{(data!.spec_return_pct >= 0 ? '+' : '') + data!.spec_return_pct.toFixed(1)}%</div>
                  <div className="aw-sub">asymétrie filtrée, taille serrée</div>
                </div>
              </div>
              <div className="aw-trust">
                <span className="aw-chip"><Icon d="M3 3v18h18" extra="m19 9-5 5-4-4-4 4" /><b>{data!.verdicts_total}</b>&nbsp;verdicts datés</span>
                {data!.hit_rate != null ? (
                  <span className="aw-chip"><Icon d="M20 6 9 17l-5-5" />hit-rate BUY <b>{Math.round(data!.hit_rate * 100)}%</b></span>
                ) : null}
                <span className="aw-chip aw-no"><Icon d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" extra="m5.6 5.6 12.8 12.8" /><b>{data!.avoid_count}</b>&nbsp;AVOID (Wall of NO)</span>
                <span className="aw-chip"><Icon d="M4 4h16v16H4z" extra="M4 10h16M10 4v16" /><b>{data!.pool_active}</b>&nbsp;tokens screenés</span>
              </div>
            </>
          ) : (
            <div className="aw-prep">
              {loaded
                ? 'Track record en préparation : les premiers verdicts datés arrivent, chaque chiffre sera vérifiable on-chain.'
                : 'Chargement du track record…'}
            </div>
          )}

          <div className="aw-cta">
            <button type="button" className="aw-btn" onClick={joinWaitlist}>Rejoindre la liste d'attente
              <Icon d="M5 12h14" extra="m13 6 6 6-6 6" /></button>
            <span className="aw-locknote">
              <Icon d="M4 10h16v10H4z" extra="M8 10V7a4 4 0 0 1 8 0v3" />
              Détail des positions et preuves on-chain (hashes) réservés aux abonnés.
            </span>
          </div>

          <div className="aw-disc">Suivi (paper) valorisé aux prix on-chain réels, aucune position réelle. Informationnel, ne constitue pas un conseil en investissement. Aucun rendement garanti ; les performances passées ne préjugent pas des résultats futurs.</div>
        </div>
      </div>
    </div>
  )
}
