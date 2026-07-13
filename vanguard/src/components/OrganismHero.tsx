import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { agentChat, getPaperWallet, getTrendingPairs, type PaperWallet, type TrendingPair } from '../api'

/**
 * OrganismHero: the ZHC homepage hero -- an animated "living organism" (a glowing
 * blob with tapered, wobbling branches) that doubles as the primary navigation and
 * as an ambient market-data visualisation.
 *
 * Ported from an interactive HTML/Canvas prototype (extensively iterated with the
 * operator). Real deviations from that prototype, all deliberate:
 *  - no second logo/brand-frame here (VanguardNav, the persistent header, already
 *    carries the brand mark -- see VanguardSite.tsx)
 *  - styles scoped under `.aria-organism` (never document.documentElement -- the
 *    ambience slider only ever touches this component's own CSS custom properties)
 *  - background "market" particles are real crypto data (GET /pairs/trending, the
 *    only real market data source this backend has -- see backend/app/api/routes/
 *    pairs.py) plus a handful of hardcoded, verified upcoming crypto events; no
 *    other asset class (stocks/forex/commodities/indices) is fabricated
 *  - the portfolio panel shows the real paper-trading aggregate (getPaperWallet),
 *    never a fake position list
 *  - branch-tip labels route to real destinations (/cockpit, the member
 *    sign-in button, in-page modals) instead of demo-only handlers
 *  - the ask-input at the bottom (`.ao-ask`) IS the real ARIA chat: it posts
 *    to `agentChat()` (POST /aria/chat, same client the old standalone chat
 *    section used) and renders the reply in `.ao-ask-reply` -- there is no
 *    separate chat section on this page anymore, this is it
 *
 * The canvas animation engine (branch wobble, capture/feeding pseudopod mechanic,
 * finance-particle drift/collision) is imperative by nature (a physics loop, not
 * declarative UI) and lives in `createOrganismEngine`, instantiated once per mount.
 * Data-driven surfaces (portfolio stats, modals, nav labels, theme slider value)
 * are plain React state so they stay declarative and testable.
 */

// ---------------------------------------------------------------------------
// Types (kept loose for the internal canvas physics -- this is a decorative
// animation, not a data-integrity-critical path; see module docstring).
// ---------------------------------------------------------------------------

interface Point {
  x: number
  y: number
}

interface Branch {
  pts: Point[]
  thickness: number
  isNav: boolean
  seed: number
  phase: number
}

interface FinanceToken {
  t: string
  c: 'crypto' | 'event'
}

interface FinanceParticle {
  x: number
  y: number
  baseX: number
  xMin: number
  xMax: number
  token: string
  lines: string[] | null
  textW: number
  rgb: string
  size: number
  a: number
  speed: number
  minDist: number
  driftX: number
  phase: number
}

interface FeedingBranch {
  angle: number
  targetLen: number
  age: number
  state: 'growing' | 'holding' | 'retracting'
  seed: number
}

interface NavNodeDef {
  label: string
  angle: number
}

export interface NavTip {
  x: number
  y: number
  label: string
}

type MarketCategory = 'crypto' | 'event'

// ---------------------------------------------------------------------------
// Fixed, real data (not an API call): the three upcoming crypto events shown
// as drifting background particles, verified calendar facts. The dedicated
// "Événements" branch modal has its own, longer, separately-maintained list.
// ---------------------------------------------------------------------------

const EVENT_BG_TOKENS: FinanceToken[] = [
  { t: 'TOKEN2049 SINGAPORE · 7-8 OCT 2026', c: 'event' },
  { t: 'DEVCON 8 (MUMBAI) · 3-6 NOV 2026', c: 'event' },
  { t: 'TOKEN2049 DUBAI · 21-22 AVR 2027', c: 'event' },
]

const FINANCE_COLORS: Record<MarketCategory, string> = {
  crypto: '81,255,190',
  event: '195,110,225',
}

// Crypto is the only real "market" left after dropping the demo-only asset
// classes (stocks/forex/commodities/indices had no real backend source) --
// so the ambient tint is simply the crypto palette, always.
const AMBIENT = { rgb: [143, 227, 211], hot: [201, 255, 176], vg: '20,30,28' }

const NAV_NODES: NavNodeDef[] = [
  { label: 'Cockpit', angle: -62 },
  { label: 'Track record', angle: 14 },
  { label: 'Événements', angle: 53 },
  { label: 'Méthodologie', angle: 92 },
  { label: 'Accès membre', angle: 166 },
  { label: 'Telegram', angle: 234 },
]

const NORTH_LABELS = new Set(['Telegram', 'Cockpit', 'Track record', 'Accès membre'])

const CAPTURE_RADIUS = 191.25
const UI_FADE_PAD = 60

// ---------------------------------------------------------------------------
// Nav label geometry tuning for narrow (portrait mobile) viewports.
//
// Branch length is already `R * fraction` with `R = min(W, H)`, so it
// already avoids assuming desktop-width room. What it doesn't account for:
// two fixed-degree angles tuned to look good on a wide desktop canvas can
// still crowd on a narrow phone, because the absolute pixel gap between two
// tips at a fixed angular delta is proportional to R -- and R is far
// smaller on a narrow phone (R = W there) than on desktop (R = H there),
// while the nav label's own footprint (font-size, padding) never shrinks.
// Confirmed by measurement (Playwright, 375x812): Événements/Méthodologie
// (39 degrees apart, both south-facing, both rendered with the label BELOW
// the ring -- see NORTH_LABELS) collide; every other pair, either further
// apart in angle or flipped to render its label above the ring, does not.
//
// Two independent, width-scaled compensations below, both a no-op at
// desktop widths (>= NAV_NARROW_BREAKPOINT):
//  1. a small angular nudge that widens specifically the tightest gap
//  2. a small radius boost so every nav tip (not just that pair) sits
//     further from the core, buying every label a little more room
// ---------------------------------------------------------------------------

const NAV_NARROW_BREAKPOINT = 640

// Degrees to push each label away from the other, at the narrowest widths.
const NAV_ANGLE_NUDGE: Partial<Record<string, number>> = {
  'Événements': -1,
  Méthodologie: 1,
}

function navNarrowness(width: number): number {
  return Math.max(0, Math.min(1, (NAV_NARROW_BREAKPOINT - width) / NAV_NARROW_BREAKPOINT))
}

const EVENTS = [
  { name: 'WebX 2026', date: '13-14 juil. 2026', detail: 'Tokyo — la plus grande conférence Web3 d’Asie.' },
  { name: 'NFT.NYC 2026', date: '1-3 sept. 2026', detail: 'New York — conférence de référence NFT et Web3.' },
  { name: 'Korea Blockchain Week', date: '29 sept-1 oct. 2026', detail: 'Séoul — semaine blockchain avec forum institutionnel.' },
  { name: 'TOKEN2049 Singapore', date: '7-8 oct. 2026', detail: 'Marina Bay Sands — la plus grande conférence crypto d’Asie.' },
  { name: 'Devcon 8', date: '3-6 nov. 2026', detail: 'Mumbai — le rendez-vous développeurs officiel d’Ethereum.' },
  { name: 'Bitcoin Amsterdam', date: '5-6 nov. 2026', detail: 'Amsterdam — grand rendez-vous Bitcoin européen.' },
  { name: 'Solana Breakpoint', date: '15-17 nov. 2026', detail: 'Londres — conférence phare de l’écosystème Solana.' },
  { name: 'ETHDenver 2027', date: '17-21 févr. 2027', detail: 'Denver — un des plus grands rassemblements Ethereum au monde.' },
  { name: 'EthCC[10]', date: '12-15 avr. 2027', detail: 'Europe — conférence communautaire Ethereum de référence.' },
  { name: 'TOKEN2049 Dubai', date: '21-22 avr. 2027', detail: 'Édition Moyen-Orient, reprogrammée pour 2027.' },
  { name: 'Paris Blockchain Week', date: '6-7 juil. 2027', detail: 'Paris — semaine blockchain et Signal Week, régulation et institutionnels.' },
]

const METHOD_STEPS = [
  { n: '01', title: 'Sourcing continu', text: 'Des dizaines de candidats détectés chaque semaine sur Base, avant la foule.' },
  { n: '02', title: 'Filtre de sécurité', text: 'Contrat, propriété, liquidité, détenteurs vérifiés. Un doute suffit à écarter.' },
  { n: '03', title: 'Analyse quantitative', text: 'Des signaux techniques mesurés sur l’historique réel, jamais une intuition.' },
  { n: '04', title: 'Raisonnement contextualisé', text: 'Le contexte de marché pèse dans la thèse, pas seulement le graphique.' },
  { n: '05', title: 'Validation croisée', text: 'Une seconde lecture challenge la première avant toute validation.' },
  { n: '06', title: 'Suivi public', text: 'Chaque verdict est daté, tracé, puis noté à l’échéance. Rien n’est caché.' },
]

// ---------------------------------------------------------------------------
// Data helpers -- real crypto pairs -> background particle tokens.
// ---------------------------------------------------------------------------

function sanitizeSymbol(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 12)
}

function formatUsdPrice(price: number): string {
  if (!Number.isFinite(price) || price <= 0) return '0'
  if (price >= 1000) return price.toLocaleString('en-US', { maximumFractionDigits: 0 })
  if (price >= 1) return price.toFixed(2)
  if (price >= 0.01) return price.toFixed(4)
  if (price >= 0.0001) return price.toFixed(6)
  return price.toExponential(2)
}

function buildCryptoTokens(pairs: TrendingPair[]): FinanceToken[] {
  const seen = new Set<string>()
  const out: FinanceToken[] = []
  for (const p of pairs) {
    if (p.price_usd == null || !Number.isFinite(p.price_usd) || p.price_usd <= 0) continue
    const sym = sanitizeSymbol(p.base_token?.symbol ?? '')
    if (!sym || seen.has(sym)) continue
    seen.add(sym)
    out.push({ t: `${sym}  $${formatUsdPrice(p.price_usd)}`, c: 'crypto' })
  }
  return out
}

// ---------------------------------------------------------------------------
// Canvas engine -- deterministic branch geometry (mulberry32), organic wobble,
// drifting finance-text particles, and the "capture" pseudopod mechanic. Kept
// as one imperative closure (mirrors the source prototype) rather than spread
// across React state, since this is a physics loop, not declarative UI.
// ---------------------------------------------------------------------------

interface EngineOptions {
  canvas: HTMLCanvasElement
  root: HTMLElement
  portfolioElRef: React.RefObject<HTMLDivElement | null>
  reducedMotion: boolean
  onLayout: (layout: { navTips: NavTip[]; core: Point }) => void
}

interface EngineHandle {
  setActiveCategories: (cats: Set<MarketCategory>) => void
  setCryptoTokens: (tokens: FinanceToken[]) => void
  setThemeLightness: (l: number) => void
  setBranchHovered: (v: boolean) => void
  pulse: () => void
  destroy: () => void
}

function mulberry32(seed: number): () => number {
  return function rng() {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function createOrganismEngine(opts: EngineOptions): EngineHandle {
  const { canvas, root, portfolioElRef, reducedMotion, onLayout } = opts
  const ctxOrNull = canvas.getContext('2d')

  const noop: EngineHandle = {
    setActiveCategories: () => {},
    setCryptoTokens: () => {},
    setThemeLightness: () => {},
    setBranchHovered: () => {},
    pulse: () => {},
    destroy: () => {},
  }
  if (!ctxOrNull) return noop
  const ctx: CanvasRenderingContext2D = ctxOrNull

  let W = 0
  let H = 0
  let cx = 0
  let cy = 0
  let DPR = 1
  let branches: Branch[] = []
  let financeParticles: FinanceParticle[] | null = null
  let feedingBranches: FeedingBranch[] = []
  let reactPulse = 0
  let t0: number | null = null
  let lastNow: number | null = null
  let branchHovered = false
  let bgLightness = 4
  let rafId = 0
  let destroyed = false

  let activeCategories = new Set<MarketCategory>(['crypto', 'event'])
  let cryptoTokens: FinanceToken[] = []

  function currentPool(): FinanceToken[] {
    const pool: FinanceToken[] = []
    if (activeCategories.has('crypto')) pool.push(...cryptoTokens)
    if (activeCategories.has('event')) pool.push(...EVENT_BG_TOKENS)
    return pool
  }

  function buildBranchFrom(rng: () => number, origin: Point, angleDeg: number, length: number, depth: number, thickness: number) {
    const angle = (angleDeg * Math.PI) / 180
    const segCount = 4 + Math.floor(rng() * 3)
    const pts: Point[] = [{ x: origin.x, y: origin.y }]
    let a = angle
    const segLen = length / segCount
    for (let i = 1; i <= segCount; i++) {
      a += (rng() - 0.5) * 0.6
      const prev = pts[pts.length - 1]
      pts.push({ x: prev.x + Math.cos(a) * segLen, y: prev.y + Math.sin(a) * segLen * 0.72 })
    }
    const seed = Math.floor(rng() * 100000)
    branches.push({ pts, thickness, isNav: false, seed, phase: rng() * Math.PI * 2 })
    if (depth > 0 && length > 18) {
      const rngSub = mulberry32(seed + 5)
      const t = 0.5 + rngSub() * 0.4
      const idx = Math.max(1, Math.min(pts.length - 1, Math.floor(t * segCount)))
      buildBranchFrom(mulberry32(seed + 91), pts[idx], angleDeg + (rngSub() - 0.5) * 110, length * 0.5, depth - 1, thickness * 0.55)
    }
  }

  function buildBranch(
    rng: () => number,
    angleDeg: number,
    length: number,
    depth: number,
    thickness: number,
    isNav: boolean,
  ): Point {
    const angle = (angleDeg * Math.PI) / 180
    const segCount = 7 + Math.floor(rng() * 4)
    const pts: Point[] = [{ x: 0, y: 0 }]
    let a = angle
    const segLen = length / segCount
    for (let i = 1; i <= segCount; i++) {
      a += (rng() - 0.5) * 0.5
      const prev = pts[pts.length - 1]
      pts.push({ x: prev.x + Math.cos(a) * segLen, y: prev.y + Math.sin(a) * segLen * 0.72 })
    }
    const seed = Math.floor(rng() * 100000)
    branches.push({ pts, thickness, isNav, seed, phase: rng() * Math.PI * 2 })
    if (depth > 0) {
      const nSub = 2 + Math.floor(rng() * 3)
      for (let s = 0; s < nSub; s++) {
        const t = 0.35 + rng() * 0.55
        const idx = Math.max(1, Math.min(pts.length - 1, Math.floor(t * segCount)))
        const base = pts[idx]
        const subAngle = angleDeg + (rng() - 0.5) * 100
        const rngSub = mulberry32(seed + s * 77 + 13)
        buildBranchFrom(rngSub, base, subAngle, length * (0.32 + rng() * 0.22), depth - 1, thickness * 0.5)
      }
    }
    return pts[pts.length - 1]
  }

  function buildAll() {
    branches = []
    const tips: NavTip[] = []
    const R = Math.min(W, H)
    const narrowness = navNarrowness(W)
    const angleWiden = narrowness * 32 // up to 32deg extra half-gap at the narrowest widths
    const lengthBoost = 1 + narrowness * 0.6 // up to +60% tip radius at the narrowest widths
    NAV_NODES.forEach((n, i) => {
      const rng = mulberry32(1000 + i * 271)
      const angle = n.angle + (NAV_ANGLE_NUDGE[n.label] ?? 0) * angleWiden
      const len = R * (0.16 + rng() * 0.04) * 1.2 * lengthBoost
      const tip = buildBranch(rng, angle, len, 2, 3.2, true)
      tips.push({ x: cx + tip.x, y: cy + tip.y, label: n.label })
    })
    const deco = mulberry32(4242)
    for (let k = 0; k < 7; k++) {
      const ang = deco() * 360
      buildBranchFrom(mulberry32(5000 + k * 333), { x: 0, y: 0 }, ang, R * (0.07 + deco() * 0.05) * 1.2, 1, 2.0)
    }
    onLayout({ navTips: tips, core: { x: cx, y: cy } })
  }

  function resize() {
    const rect = root.getBoundingClientRect()
    DPR = Math.min(window.devicePixelRatio || 1, 2)
    W = Math.max(1, Math.round(rect.width))
    H = Math.max(1, Math.round(rect.height))
    canvas.width = W * DPR
    canvas.height = H * DPR
    canvas.style.width = `${W}px`
    canvas.style.height = `${H}px`
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0)
    cx = W / 2
    cy = H / 2
  }

  function buildFinanceParticles() {
    const rng = mulberry32(2024)
    const particles: FinanceParticle[] = []
    const pool = currentPool()
    if (pool.length) {
      const shuffled = pool.slice()
      for (let i = shuffled.length - 1; i > 0; i--) {
        const j = Math.floor(rng() * (i + 1))
        const tmp = shuffled[i]
        shuffled[i] = shuffled[j]
        shuffled[j] = tmp
      }
      const maxAttempts = 14
      const hardCap = Math.min(shuffled.length, Math.max(8, Math.round((W * H) / 20000)))
      for (let k = 0; k < shuffled.length && particles.length < hardCap; k++) {
        const entry = shuffled[k]
        const isEvent = entry.c === 'event'
        const lines = isEvent ? entry.t.split(' · ') : null
        const size = 13 + rng() * 3
        ctx.font = `${size}px 'JetBrains Mono', ui-monospace, monospace`
        const textW = isEvent
          ? Math.max(ctx.measureText(lines![0]).width, ctx.measureText(lines![1] || '').width)
          : ctx.measureText(entry.t).width
        const pad = 14
        let xMin = isEvent ? textW / 2 + pad : pad
        let xMax = isEvent ? W - textW / 2 - pad : W - textW - pad
        if (xMax < xMin) {
          xMin = 0
          xMax = W
        }
        const minDist = 58 + entry.t.length * 4.2 + (isEvent ? size * 2.2 : 0)
        let x = 0
        let y = 0
        let attempt = 0
        let ok = false
        while (attempt < maxAttempts && !ok) {
          x = xMin + rng() * Math.max(1, xMax - xMin)
          y = rng() * H
          ok = true
          for (const p of particles) {
            const dx = p.x - x
            const dy = p.y - y
            if (Math.sqrt(dx * dx + dy * dy) < Math.max(minDist, p.minDist)) {
              ok = false
              break
            }
          }
          attempt++
        }
        if (!ok) continue
        particles.push({
          x,
          y,
          baseX: x,
          xMin,
          xMax,
          token: entry.t,
          lines,
          textW,
          rgb: FINANCE_COLORS[entry.c],
          size,
          a: 0.8,
          speed: (3 + rng() * 6) * 1.8,
          minDist,
          driftX: (rng() - 0.5) * 6,
          phase: rng() * 1000,
        })
      }
    }
    financeParticles = particles
  }

  function resolveFinanceCollisions() {
    if (!financeParticles) return
    const arr = financeParticles
    const n = arr.length
    for (let pass = 0; pass < 2; pass++) {
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const a = arr[i]
          const b = arr[j]
          const dx = b.x - a.x
          const dy = b.y - a.y
          const dist = Math.sqrt(dx * dx + dy * dy) || 0.0001
          const minD = Math.max(a.minDist, b.minDist)
          if (dist < minD) {
            const overlap = (minD - dist) / 2
            const nx = dx / dist
            const ny = dy / dist
            a.x -= nx * overlap
            a.y -= ny * overlap
            b.x += nx * overlap
            b.y += ny * overlap
          }
        }
      }
    }
  }

  function updateFinanceParticles(time: number, dt: number) {
    if (!financeParticles || reducedMotion || dt <= 0) return
    financeParticles.forEach((p) => {
      p.y -= p.speed * dt
      if (p.y < -60) p.y = H + 60
      const targetX = p.baseX + Math.sin(time * 0.216 + p.phase * 0.01) * p.driftX
      p.x += (targetX - p.x) * 0.06
    })
    resolveFinanceCollisions()
    financeParticles.forEach((p) => {
      if (p.x < p.xMin) p.x = p.xMin
      else if (p.x > p.xMax) p.x = p.xMax
    })
  }

  function getParticleBox(p: FinanceParticle) {
    if (p.lines) {
      return { left: p.x - p.textW / 2, right: p.x + p.textW / 2, top: p.y - p.size * 1.3, bottom: p.y + p.size * 1.3 }
    }
    return { left: p.x, right: p.x + p.textW, top: p.y - p.size * 0.55, bottom: p.y + p.size * 0.55 }
  }

  function rectFadeMultiplier(
    box: { left: number; right: number; top: number; bottom: number },
    rect: { left: number; right: number; top: number; bottom: number } | null,
  ) {
    if (!rect) return 1
    const dx = Math.max(rect.left - box.right, box.left - rect.right, 0)
    const dy = Math.max(rect.top - box.bottom, box.top - rect.bottom, 0)
    const dist = Math.sqrt(dx * dx + dy * dy)
    return Math.max(0, Math.min(1, dist / UI_FADE_PAD))
  }

  // Portfolio panel is real React-rendered DOM living in the same wrapper, but
  // getBoundingClientRect() returns viewport coordinates while particles live
  // in the canvas' own local space (root top-left = 0,0) -- convert once.
  //
  // Cached rather than recomputed inside drawFinanceBg(): getBoundingClientRect()
  // forces a synchronous layout, and drawFinanceBg() runs every animation
  // frame (up to 60x/s) -- calling it there meant paying for a forced reflow
  // twice a frame for a box that only actually moves on resize or when the
  // panel's own content changes size (wallet data arriving). Recomputed only
  // from those two triggers instead (see the two ResizeObservers below).
  let portfolioBoxLocal: { left: number; right: number; top: number; bottom: number } | null = null

  function recomputePortfolioBox() {
    const el = portfolioElRef.current
    if (!el) {
      portfolioBoxLocal = null
      return
    }
    const rootRect = root.getBoundingClientRect()
    const elRect = el.getBoundingClientRect()
    portfolioBoxLocal = {
      left: elRect.left - rootRect.left,
      right: elRect.right - rootRect.left,
      top: elRect.top - rootRect.top,
      bottom: elRect.bottom - rootRect.top,
    }
  }

  function respawnParticleFarFromCore(p: FinanceParticle) {
    if (!financeParticles) return
    const attempts = 10
    let x = p.x
    let y = H + 70
    for (let i = 0; i < attempts; i++) {
      const tryX = p.xMin + Math.random() * Math.max(1, p.xMax - p.xMin)
      const tryY = H + 40 + Math.random() * 90
      let ok = true
      for (const q of financeParticles) {
        if (q === p) continue
        const dx = q.x - tryX
        const dy = q.y - tryY
        if (Math.sqrt(dx * dx + dy * dy) < Math.max(p.minDist, q.minDist)) {
          ok = false
          break
        }
      }
      if (ok) {
        x = tryX
        y = tryY
        break
      }
    }
    p.baseX = x
    p.x = x
    p.y = y
  }

  function captureNearbyParticles() {
    if (reducedMotion || !financeParticles) return
    financeParticles.forEach((p) => {
      const box = getParticleBox(p)
      const nx = Math.max(box.left, Math.min(cx, box.right))
      const ny = Math.max(box.top, Math.min(cy, box.bottom))
      const dx = nx - cx
      const dy = ny - cy
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < CAPTURE_RADIUS) {
        feedingBranches.push({
          angle: (Math.atan2(dy, dx) * 180) / Math.PI,
          targetLen: Math.max(28, dist),
          age: 0,
          state: 'growing',
          seed: Math.random() * 1000,
        })
        reactPulse = Math.min(1, reactPulse + 0.3)
        respawnParticleFarFromCore(p)
      }
    })
  }

  function buildFeedingPoints(fb: FeedingBranch, len: number): Point[] {
    const angle = (fb.angle * Math.PI) / 180
    const segCount = 6
    const pts: Point[] = [{ x: 0, y: 0 }]
    let a = angle
    const segLen = len / segCount
    for (let i = 1; i <= segCount; i++) {
      a += Math.sin(fb.seed + i * 1.3) * 0.1
      const prev = pts[pts.length - 1]
      pts.push({ x: prev.x + Math.cos(a) * segLen, y: prev.y + Math.sin(a) * segLen * 0.72 })
    }
    return pts
  }

  function updateFeedingBranches(dt: number) {
    if (reducedMotion) {
      feedingBranches.length = 0
      return
    }
    for (let i = feedingBranches.length - 1; i >= 0; i--) {
      const fb = feedingBranches[i]
      fb.age += dt
      if (fb.state === 'growing' && fb.age >= 0.45) {
        fb.state = 'holding'
        fb.age = 0
      } else if (fb.state === 'holding' && fb.age >= 0.22) {
        fb.state = 'retracting'
        fb.age = 0
      } else if (fb.state === 'retracting' && fb.age >= 0.5) {
        feedingBranches.splice(i, 1)
      }
    }
  }

  function feedingBranchLength(fb: FeedingBranch): number {
    if (fb.state === 'growing') {
      const p = 1 - Math.pow(1 - Math.min(1, fb.age / 0.45), 3)
      return fb.targetLen * p
    }
    if (fb.state === 'holding') return fb.targetLen
    const p2 = Math.min(1, fb.age / 0.5)
    return fb.targetLen * (1 - p2 * p2)
  }

  function strokeTaperedPath(
    points: Point[],
    thickness: number,
    rgb: number[],
    netAlpha: number,
    haloAlpha: number,
    seedForNoise: number,
    taperAmt = 0.72,
  ) {
    const [r, g, bl] = rgb
    function pass(widthMul: number, alphaMul: number, blur: number) {
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.shadowColor = `rgba(${r},${g},${bl},${Math.min(1, 0.55 + reactPulse * 0.45)})`
      ctx.shadowBlur = blur
      for (let si = 0; si < points.length - 1; si++) {
        const a0 = points[si]
        const a1 = points[si + 1]
        const reachMid = si / (points.length - 1)
        const taper = 1 - reachMid * taperAmt
        const noise = 1 + Math.sin(seedForNoise + si * 1.7) * 0.08
        ctx.lineWidth = Math.max(0.6, thickness * widthMul * taper * noise)
        const alpha = taperAmt === 0 ? alphaMul : Math.min(1, alphaMul * (0.6 + 0.4 * (1 - reachMid)))
        ctx.strokeStyle = `rgba(${r},${g},${bl},${alpha})`
        ctx.beginPath()
        if (si === 0) {
          ctx.moveTo(a0.x, a0.y)
        } else {
          const mx = (points[si - 1].x + a0.x) / 2
          const my = (points[si - 1].y + a0.y) / 2
          ctx.moveTo(mx, my)
        }
        if (si < points.length - 2) {
          const nmx = (a1.x + points[si + 2].x) / 2
          const nmy = (a1.y + points[si + 2].y) / 2
          ctx.quadraticCurveTo(a1.x, a1.y, nmx, nmy)
        } else {
          ctx.lineTo(a1.x, a1.y)
        }
        ctx.stroke()
      }
    }
    pass(2.6, haloAlpha, 20 + reactPulse * 10)
    pass(1.0, netAlpha, 5 + reactPulse * 6)
  }

  function drawFeedingBranches() {
    feedingBranches.forEach((fb) => {
      const len = feedingBranchLength(fb)
      if (len < 1) return
      const hotNow = AMBIENT.hot
      const pts = buildFeedingPoints(fb, len)
      strokeTaperedPath(pts, 3.6, hotNow, 0.95, 0.24, fb.seed)
      if (fb.state === 'holding') {
        const tip = pts[pts.length - 1]
        ctx.beginPath()
        ctx.fillStyle = `rgba(${hotNow.join(',')},${0.6 * (1 - fb.age / 0.22)})`
        ctx.arc(tip.x, tip.y, 5, 0, Math.PI * 2)
        ctx.fill()
      }
    })
  }

  function drawFinanceBg() {
    if (!financeParticles) buildFinanceParticles()
    if (!financeParticles) return
    const portfolioBox = portfolioBoxLocal
    ctx.save()
    ctx.textBaseline = 'middle'
    financeParticles.forEach((p) => {
      ctx.font = `${p.size}px 'JetBrains Mono', ui-monospace, monospace`
      const box = getParticleBox(p)
      const fade = rectFadeMultiplier(box, portfolioBox)
      const alpha = p.a * fade
      if (alpha <= 0.004) return
      ctx.fillStyle = `rgba(${p.rgb},${alpha})`
      if (p.lines) {
        ctx.textAlign = 'center'
        ctx.fillText(p.lines[0], p.x, p.y - p.size * 0.75)
        if (p.lines[1]) ctx.fillText(p.lines[1], p.x, p.y + p.size * 0.75)
        ctx.textAlign = 'start'
      } else {
        ctx.fillText(p.token, p.x, p.y)
      }
    })
    ctx.restore()
  }

  function draw(now: number) {
    if (destroyed) return
    if (t0 === null) t0 = now
    const time = reducedMotion ? 0 : (now - t0) / 1000
    const dt = lastNow === null ? 0 : Math.min(0.1, (now - lastNow) / 1000)
    lastNow = now

    ctx.clearRect(0, 0, W, H)
    ctx.fillStyle = `hsl(0,0%,${bgLightness}%)`
    ctx.fillRect(0, 0, W, H)

    if (!financeParticles) buildFinanceParticles()
    updateFinanceParticles(time, dt)
    captureNearbyParticles()
    updateFeedingBranches(dt)
    drawFinanceBg()

    const vg = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(W, H) * 0.65)
    vg.addColorStop(0, `rgba(${AMBIENT.vg},0.35)`)
    vg.addColorStop(1, 'rgba(0,0,0,0)')
    ctx.fillStyle = vg
    ctx.fillRect(0, 0, W, H)

    if (reactPulse > 0 && !reducedMotion) reactPulse = Math.max(0, reactPulse - 0.012)

    ctx.save()
    ctx.translate(cx, cy)

    branches.forEach((b) => {
      const pts = b.pts
      const freq = 0.45 + ((b.seed % 97) / 97) * 0.6
      const wp: Point[] = []
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i]
        const reach = Math.min(1, i / (pts.length - 1))
        const wobbleAmt = reducedMotion || branchHovered ? 0 : (7.5 + reactPulse * 6) * (0.25 + reach * 0.9)
        const wob = Math.sin(time * freq + b.phase + i * 0.8) * wobbleAmt
        wp.push({ x: p.x, y: p.y + wob })
      }
      const glowMix = reducedMotion
        ? 0.55
        : 0.35 + 0.5 * Math.sin(time * 1.1 + b.phase) * 0.5 + 0.5 * 0.6 + reactPulse * 0.5
      const ambientRgb = AMBIENT.rgb
      const baseCol = b.isNav ? ambientRgb.map((v) => Math.min(255, v + 7)) : ambientRgb.map((v) => Math.round(v * 0.72))
      const hotCol = AMBIENT.hot
      const mix = Math.min(1, glowMix)
      const r = Math.round(baseCol[0] + (hotCol[0] - baseCol[0]) * mix * reactPulse)
      const g = Math.round(baseCol[1] + (hotCol[1] - baseCol[1]) * mix * reactPulse)
      const bl = Math.round(baseCol[2] + (hotCol[2] - baseCol[2]) * mix * reactPulse)
      strokeTaperedPath(wp, b.thickness, [r, g, bl], 0.88, 0.15, b.seed)
    })

    drawFeedingBranches()

    const baseR = 20.6 + reactPulse * 8
    const pulseVal = reducedMotion || branchHovered ? 0 : Math.sin(time * 1.4) * 3
    const coreR = baseR + pulseVal + reactPulse * 6
    const haloR = coreR * 2.6 * 0.7
    const coreHot = AMBIENT.hot
    const coreRgb = AMBIENT.rgb
    const core = ctx.createRadialGradient(0, 0, 0, 0, 0, haloR)
    core.addColorStop(0, `rgba(${coreHot.join(',')},0.9)`)
    core.addColorStop(0.35, `rgba(${coreRgb.join(',')},0.55)`)
    core.addColorStop(1, `rgba(${coreRgb.join(',')},0)`)
    ctx.fillStyle = core
    ctx.beginPath()
    ctx.arc(0, 0, haloR, 0, Math.PI * 2)
    ctx.fill()

    ctx.fillStyle = '#eafff5'
    ctx.beginPath()
    ctx.arc(0, 0, coreR * 0.55, 0, Math.PI * 2)
    ctx.fill()

    ctx.restore()

    // Under reduced motion each trigger (init/resize/theme/pulse) draws exactly
    // one frame and stops -- no continuous rAF chain. (The source prototype's
    // "one-shot reaction, no continuous wave" comment implied this, but its
    // actual condition here would have kept re-scheduling forever once
    // reactPulse was set, since reactPulse never decays under reduced motion;
    // fixed while porting.)
    if (!reducedMotion) rafId = requestAnimationFrame(draw)
  }

  function fullRebuildLayout() {
    resize()
    buildAll()
    financeParticles = null
    recomputePortfolioBox()
  }

  const ro = new ResizeObserver(() => {
    fullRebuildLayout()
    if (reducedMotion) draw(performance.now())
  })
  ro.observe(root)

  // Separate observer: the portfolio panel's own height changes (wallet data
  // arriving turns "Chargement..." into a five-row list) without the root
  // element itself resizing -- root's observer alone would miss that move.
  const portfolioRo = new ResizeObserver(() => recomputePortfolioBox())
  if (portfolioElRef.current) portfolioRo.observe(portfolioElRef.current)

  fullRebuildLayout()
  draw(performance.now())

  return {
    setActiveCategories(cats: Set<MarketCategory>) {
      activeCategories = new Set(cats)
      financeParticles = null
      if (reducedMotion) draw(performance.now())
    },
    setCryptoTokens(tokens: FinanceToken[]) {
      cryptoTokens = tokens
      financeParticles = null
      if (reducedMotion) draw(performance.now())
    },
    setThemeLightness(L: number) {
      bgLightness = L
      const isLight = L >= 55
      root.style.setProperty('--bg', `hsl(0,0%,${L}%)`)
      root.style.setProperty('--text', isLight ? 'hsl(0,0%,9%)' : 'hsl(0,0%,96%)')
      root.style.setProperty('--text-dim', isLight ? 'hsla(0,0%,9%,0.62)' : 'hsla(0,0%,96%,0.55)')
      if (reducedMotion) draw(performance.now())
    },
    setBranchHovered(v: boolean) {
      branchHovered = v
    },
    pulse() {
      reactPulse = 1.0
      if (reducedMotion) draw(performance.now())
    },
    destroy() {
      destroyed = true
      cancelAnimationFrame(rafId)
      ro.disconnect()
      portfolioRo.disconnect()
    },
  }
}

// ---------------------------------------------------------------------------
// Scoped CSS -- every selector prefixed by `.aria-organism` (see ClientSite.tsx
// for the same precedent in this repo). No rule ever touches :root or an
// unscoped selector, so this component can never reskin the rest of the app.
// ---------------------------------------------------------------------------

const CSS = `
.aria-organism{
  --bg:#050607; --text:#e8e9ea; --text-dim:#7d838a;
  --mono: ui-monospace, 'SF Mono', 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
  position:relative; width:100%; min-height:100vh; overflow:hidden;
  background:var(--bg); color:var(--text); font-family:var(--sans);
}
/* On narrow (portrait mobile) viewports, min-height:100vh stretches the
   organism far taller than its content needs -- the core cluster stays
   vertically centered (cy = H/2) while the ask-form sits pinned near the
   very bottom (bottom:30px), so a full 100vh here reads as a large empty
   gap between them. Capping the effective height keeps desktop untouched
   (this query never matches landscape/desktop widths) while shrinking
   that dead space on tall narrow phones. dvh (not vh) so mobile browser
   toolbars don't inflate the cap further. */
@media (max-width:640px){
  .aria-organism{ min-height:min(100dvh, 640px); }
}
.aria-organism *{box-sizing:border-box;}
.aria-organism .ao-canvas{position:absolute; inset:0; display:block; width:100%; height:100%;}

.aria-organism .ao-nodes{position:absolute; inset:0; z-index:6; pointer-events:none;}
.aria-organism .ao-node{
  position:absolute; z-index:6; transform:translate(-50%,-50%); pointer-events:auto;
  display:flex; flex-direction:column; align-items:center; gap:12px;
  text-decoration:none; cursor:pointer;
}
.aria-organism .ao-node-north{ flex-direction:column-reverse; }
.aria-organism .ao-node .ao-ring{
  width:9px; height:9px; border-radius:50%;
  border:1px solid #8fe3d3; background:rgba(143,227,211,0.18);
  transition:transform .25s ease, background .25s ease;
}
.aria-organism .ao-node:hover .ao-ring{transform:scale(1.7); background:rgba(201,255,176,0.6);}
.aria-organism .ao-node .ao-lbl{
  font-family:var(--mono); font-size:10.5px; letter-spacing:0.1em; text-transform:uppercase;
  color:var(--text-dim); transition:color .25s ease; white-space:nowrap;
  padding:4px 9px; border-radius:9px;
  background:rgba(5,6,7,0.6); backdrop-filter:blur(3px);
  animation-name:aoLabelFloat; animation-timing-function:ease-in-out; animation-iteration-count:infinite;
}
.aria-organism .ao-node:hover .ao-lbl{color:var(--text);}
.aria-organism .ao-node:focus-visible .ao-ring{outline:2px solid #8fe3d3; outline-offset:3px;}
@keyframes aoLabelFloat{ 0%,100%{ transform:translateY(0); } 50%{ transform:translateY(-4px); } }
@media (prefers-reduced-motion: reduce){ .aria-organism .ao-node .ao-lbl{ animation:none; } }
.aria-organism .ao-node-event .ao-ring{ border-color:rgb(224,196,232); background:rgba(224,196,232,0.18); }
.aria-organism .ao-node-event:hover .ao-ring{ background:rgba(224,196,232,0.6); }

.aria-organism .ao-ask-wrap{
  position:absolute; bottom:30px; left:50%; transform:translateX(-50%); z-index:7;
  display:flex; flex-direction:column; gap:10px; width:min(420px, calc(100% - 48px));
}
.aria-organism .ao-ask{ display:flex; gap:8px; width:100%; }
.aria-organism .ao-ask input{
  flex:1; background:rgba(255,255,255,0.04); border:1px solid rgba(232,233,234,0.14);
  border-radius:20px; padding:11px 18px; color:var(--text); font-family:var(--sans);
  font-size:13px; backdrop-filter:blur(6px);
}
.aria-organism .ao-ask input::placeholder{color:var(--text-dim);}
.aria-organism .ao-ask input:focus{outline:none; border-color:#8fe3d3;}
.aria-organism .ao-ask button{
  background:#8fe3d3; color:#04211c; border:0; border-radius:20px; padding:0 20px;
  font-family:var(--mono); font-size:11px; letter-spacing:0.06em; text-transform:uppercase;
  cursor:pointer; font-weight:700;
}
.aria-organism .ao-ask button:disabled{ opacity:0.5; cursor:default; }
.aria-organism .ao-ask button:focus-visible{outline:2px solid var(--text); outline-offset:2px;}

.aria-organism .ao-ask-reply{
  background:rgba(5,6,7,0.78); border:1px solid rgba(232,233,234,0.14); border-radius:14px;
  padding:12px 16px; font-family:var(--sans); font-size:12.5px; line-height:1.5;
  color:var(--text); max-height:220px; overflow-y:auto; backdrop-filter:blur(8px);
  white-space:pre-wrap;
}
.aria-organism .ao-ask-reply.is-error{ color:#e0a8a8; }
.aria-organism .ao-ask-reply .ao-ask-q{
  font-family:var(--mono); font-size:10px; letter-spacing:0.04em; text-transform:uppercase;
  color:var(--text-dim); margin-bottom:6px; white-space:normal;
}

.aria-organism .ao-reduced-note{
  position:absolute; bottom:78px; left:50%; transform:translateX(-50%); z-index:5;
  font-family:var(--mono); font-size:9px; color:var(--text-dim); opacity:0.6; white-space:nowrap;
}

.aria-organism .ao-pc{
  position:absolute; top:24px; right:26px; z-index:6; width:236px;
  background:rgba(10,13,13,0.58); border:1px solid rgba(143,227,211,0.22);
  border-radius:16px; padding:16px 16px 13px; backdrop-filter:blur(10px);
  box-shadow:0 10px 34px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.02) inset;
}
.aria-organism .ao-pc-head{ display:flex; align-items:center; gap:7px; margin-bottom:12px; }
.aria-organism .ao-pc-dot{
  width:6px; height:6px; border-radius:50%; background:rgba(232,233,234,0.4);
}
.aria-organism .ao-pc-dot.is-live{
  background:#c9ffb0; box-shadow:0 0 0 0 rgba(201,255,176,0.55); animation:aoPcPulse 1.8s ease-in-out infinite;
}
@keyframes aoPcPulse{ 0%,100%{ box-shadow:0 0 0 0 rgba(201,255,176,0.5); } 50%{ box-shadow:0 0 0 5px rgba(201,255,176,0); } }
@media (prefers-reduced-motion: reduce){ .aria-organism .ao-pc-dot{ animation:none; } }
.aria-organism .ao-pc-title{
  font-family:var(--mono); font-size:9.5px; letter-spacing:0.12em; text-transform:uppercase;
  color:var(--text-dim);
}
.aria-organism .ao-pc-list{ list-style:none; margin:0 0 10px; padding:0; display:flex; flex-direction:column; gap:8px; }
.aria-organism .ao-pc-row{ display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
.aria-organism .ao-pc-k{ font-family:var(--sans); font-size:11px; color:var(--text-dim); }
.aria-organism .ao-pc-v{ font-family:var(--mono); font-size:12.5px; font-weight:700; color:var(--text); font-variant-numeric:tabular-nums; }
.aria-organism .ao-pc-v.is-pos{ color:#c9ffb0; }
.aria-organism .ao-pc-v.is-neg{ color:#e0a8a8; }
.aria-organism .ao-pc-note{
  font-family:var(--mono); font-size:8.5px; line-height:1.5; color:var(--text-dim); opacity:0.75;
  border-top:1px solid rgba(232,233,234,0.08); padding-top:9px;
}
.aria-organism .ao-pc-empty{ font-size:11px; color:var(--text-dim); padding:4px 0 8px; }
@media (max-width:640px){ .aria-organism .ao-pc{ width:min(220px, calc(100% - 52px)); top:20px; right:18px; padding:13px 14px 11px; } }

.aria-organism .ao-core-hit{
  position:absolute; z-index:6; transform:translate(-50%,-50%);
  width:64px; height:64px; border-radius:50%;
  background:transparent; border:0; padding:0; cursor:pointer;
  transition:box-shadow .2s ease;
}
.aria-organism .ao-core-hit:hover{ box-shadow:0 0 0 1px rgba(255,255,255,0.18); }
.aria-organism .ao-core-hit:focus-visible{ outline:2px solid #8fe3d3; outline-offset:4px; }

.aria-organism .ao-theme-tab{
  position:absolute; z-index:8; left:22px; bottom:22px;
  width:42px; height:42px; border-radius:50%; padding:0;
  background:rgba(255,255,255,0.05); border:1px solid rgba(232,233,234,0.16);
  cursor:pointer; display:flex; align-items:center; justify-content:center;
  transition:border-color .2s ease, background .2s ease;
}
.aria-organism .ao-theme-tab:hover{ border-color:rgba(232,233,234,0.35); background:rgba(255,255,255,0.09); }
.aria-organism .ao-theme-tab:focus-visible{ outline:2px solid #8fe3d3; outline-offset:3px; }
.aria-organism .ao-theme-tab svg{ display:block; }
.aria-organism .ao-theme-panel{
  position:absolute; z-index:8; left:22px; bottom:76px; width:230px;
  background:#0b0d0e; border:1px solid rgba(232,233,234,0.12); border-radius:14px;
  padding:15px 17px 13px; box-shadow:0 16px 46px rgba(0,0,0,0.5);
}
.aria-organism .ao-theme-label{
  font-family:var(--mono); font-size:10px; letter-spacing:0.1em; text-transform:uppercase;
  color:var(--text-dim); margin-bottom:11px;
}
.aria-organism .ao-theme-slider{
  width:100%; margin:0; height:8px; border-radius:6px; cursor:pointer; outline:none;
  -webkit-appearance:none; appearance:none;
  background:linear-gradient(to right, #050607, #ffffff);
  border:1px solid rgba(232,233,234,0.18);
}
.aria-organism .ao-theme-slider::-webkit-slider-thumb{
  -webkit-appearance:none; appearance:none; width:17px; height:17px; border-radius:50%;
  background:#fff; border:2px solid #050607; box-shadow:0 1px 4px rgba(0,0,0,0.5); cursor:pointer;
}
.aria-organism .ao-theme-slider::-moz-range-thumb{
  width:17px; height:17px; border-radius:50%; background:#fff; border:2px solid #050607;
  box-shadow:0 1px 4px rgba(0,0,0,0.5); cursor:pointer;
}
.aria-organism .ao-theme-ends{
  display:flex; justify-content:space-between; font-family:var(--mono); font-size:9px;
  letter-spacing:0.06em; text-transform:uppercase; color:var(--text-dim); margin-top:7px;
}

.aria-organism .ao-modal-backdrop{
  position:fixed; inset:0; z-index:50; background:rgba(5,6,7,0.82);
  backdrop-filter:blur(6px); display:flex; align-items:center; justify-content:center;
  padding:24px;
}
.aria-organism .ao-modal{
  width:min(460px, 100%); max-height:min(560px, 84vh); overflow-y:auto;
  background:#0b0d0e; border:1px solid rgba(232,233,234,0.12); border-radius:18px;
  padding:28px 26px 22px; position:relative; box-shadow:0 20px 60px rgba(0,0,0,0.5);
  color:var(--text); font-family:var(--sans);
}
.aria-organism .ao-modal h2{
  margin:0 0 8px; font-family:var(--mono); font-size:13px; letter-spacing:0.14em;
  text-transform:uppercase; color:var(--text);
}
.aria-organism .ao-modal-sub{ margin:0 0 12px; font-size:12.5px; line-height:1.42; color:var(--text-dim); }
.aria-organism .ao-modal-close{
  position:absolute; top:14px; right:14px; background:none; border:0; color:var(--text-dim);
  font-size:16px; cursor:pointer; line-height:1; padding:6px;
}
.aria-organism .ao-modal-close:hover{ color:var(--text); }

.aria-organism .ao-calendar-connect{
  width:100%; background:rgba(224,196,232,0.14); border:1px solid rgba(224,196,232,0.4);
  color:var(--text); border-radius:12px; padding:11px 14px; font-family:var(--sans);
  font-size:13px; font-weight:600; cursor:pointer; transition:background .2s ease, border-color .2s ease;
}
.aria-organism .ao-calendar-connect:hover{ background:rgba(224,196,232,0.22); }
.aria-organism .ao-calendar-connect.is-connected{ background:rgba(159,232,196,0.16); border-color:rgba(159,232,196,0.5); }
.aria-organism .ao-modal-demo-note{
  margin:9px 0 20px; font-family:var(--mono); font-size:10px; color:var(--text-dim);
  opacity:0.75; line-height:1.5;
}
.aria-organism .ao-event-list{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:7px; }
.aria-organism .ao-event-row{
  display:flex; align-items:center; justify-content:space-between; gap:12px;
  background:rgba(255,255,255,0.03); border:1px solid rgba(232,233,234,0.08);
  border-radius:10px; padding:8px 12px;
}
.aria-organism .ao-event-info{ display:flex; flex-direction:column; gap:1px; min-width:0; }
.aria-organism .ao-event-name{ font-size:13px; font-weight:600; color:var(--text); }
.aria-organism .ao-event-date{ font-family:var(--mono); font-size:10px; letter-spacing:0.04em; color:var(--text-dim); }
.aria-organism .ao-event-detail{ font-size:10.5px; color:var(--text-dim); line-height:1.3; }
.aria-organism .ao-event-toggle{
  flex:none; background:rgba(255,255,255,0.05); border:1px solid rgba(232,233,234,0.16);
  color:var(--text-dim); border-radius:20px; padding:7px 14px; font-family:var(--mono);
  font-size:10px; letter-spacing:0.04em; text-transform:uppercase; cursor:pointer;
  transition:all .2s ease; white-space:nowrap;
}
.aria-organism .ao-event-toggle:hover{ color:var(--text); border-color:rgba(232,233,234,0.35); }
.aria-organism .ao-event-toggle.is-saved{
  background:rgba(143,227,211,0.18); border-color:#8fe3d3; color:#c9ffb0;
}

.aria-organism .ao-market-live{
  display:flex; align-items:center; gap:8px; margin:0 0 13px;
  font-family:var(--mono); font-size:10.5px; color:var(--text-dim);
}
.aria-organism .ao-market-live .ao-live-dot{
  width:6px; height:6px; border-radius:50%; background:#c9ffb0;
  box-shadow:0 0 0 0 rgba(201,255,176,0.55); animation:aoPcPulse 1.8s ease-in-out infinite;
}
.aria-organism .ao-market-live.is-stale .ao-live-dot{ background:rgba(232,233,234,0.35); animation:none; }
.aria-organism .ao-market-list{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; }
.aria-organism .ao-market-row{
  display:flex; align-items:center; gap:11px; width:100%;
  background:rgba(255,255,255,0.03); border:1px solid rgba(232,233,234,0.08);
  border-radius:10px; padding:8px 12px; cursor:pointer; font-family:var(--sans);
  transition:border-color .2s ease, background .2s ease;
}
.aria-organism .ao-market-row:hover{ border-color:rgba(232,233,234,0.25); background:rgba(255,255,255,0.05); }
.aria-organism .ao-market-row.is-active{ border-color:#8fe3d3; background:rgba(255,255,255,0.05); }
.aria-organism .ao-market-swatch{ width:11px; height:11px; border-radius:50%; flex:none; }
.aria-organism .ao-market-name{ font-size:13px; font-weight:600; color:var(--text); flex:1; text-align:left; }
.aria-organism .ao-market-check{ font-family:var(--mono); font-size:10.5px; color:#c9ffb0; }

.aria-organism .ao-method-steps{ list-style:none; margin:0 0 4px; padding:0; display:flex; flex-direction:column; gap:15px; }
.aria-organism .ao-method-step{
  display:flex; gap:13px; align-items:flex-start;
  opacity:0; transform:translateY(8px);
  animation:aoMethodReveal .5s ease forwards;
}
.aria-organism .ao-method-num{
  font-family:var(--mono); font-size:11px; color:#c9ffb0; letter-spacing:0.06em;
  flex:none; width:20px; padding-top:2px;
}
.aria-organism .ao-method-body{ display:flex; flex-direction:column; gap:3px; min-width:0; }
.aria-organism .ao-method-title{ font-size:13.5px; font-weight:600; color:var(--text); }
.aria-organism .ao-method-text{ font-size:12px; line-height:1.5; color:var(--text-dim); }
@keyframes aoMethodReveal{ to{ opacity:1; transform:translateY(0); } }
@media (prefers-reduced-motion: reduce){ .aria-organism .ao-method-step{ animation:none; opacity:1; transform:none; } }
`

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type ModalKind = 'events' | 'market' | 'method' | 'telegram' | null

export function OrganismHero() {
  const rootRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const portfolioElRef = useRef<HTMLDivElement>(null)
  const engineRef = useRef<EngineHandle | null>(null)
  const reducedMotionRef = useRef(false)

  const [reducedMotionNote, setReducedMotionNote] = useState(false)
  const [navTips, setNavTips] = useState<NavTip[]>([])
  const [corePos, setCorePos] = useState<Point>({ x: 0, y: 0 })
  const [openModal, setOpenModal] = useState<ModalKind>(null)
  const [themeOpen, setThemeOpen] = useState(false)
  const [themeValue, setThemeValue] = useState(4)
  const [askValue, setAskValue] = useState('')
  const [askQuestion, setAskQuestion] = useState<string | null>(null)
  const [askReply, setAskReply] = useState<string | null>(null)
  const [askLoading, setAskLoading] = useState(false)
  const [askError, setAskError] = useState(false)
  const [savedEvents, setSavedEvents] = useState<Record<number, boolean>>({})
  const [calendarConnected, setCalendarConnected] = useState(false)
  const [activeCategories, setActiveCategoriesState] = useState<Set<MarketCategory>>(
    () => new Set<MarketCategory>(['crypto', 'event']),
  )

  const [wallet, setWallet] = useState<PaperWallet | null>(null)
  const [walletError, setWalletError] = useState(false)
  const [walletLoaded, setWalletLoaded] = useState(false)

  const [lastFetchAt, setLastFetchAt] = useState<Date | null>(null)
  const [liveOk, setLiveOk] = useState(false)

  // --- Engine lifecycle -----------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current
    const root = rootRef.current
    if (!canvas || !root) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    reducedMotionRef.current = reduced
    setReducedMotionNote(reduced)

    const engine = createOrganismEngine({
      canvas,
      root,
      portfolioElRef,
      reducedMotion: reduced,
      onLayout: ({ navTips: tips, core }) => {
        setNavTips(tips)
        setCorePos(core)
      },
    })
    engineRef.current = engine
    engine.setThemeLightness(4)

    return () => {
      engine.destroy()
      engineRef.current = null
    }
  }, [])

  // --- Real crypto market data (only real data source in this backend) -----
  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const res = await getTrendingPairs(30)
        if (cancelled) return
        engineRef.current?.setCryptoTokens(buildCryptoTokens(res.pairs))
        setLastFetchAt(new Date())
        setLiveOk(true)
      } catch {
        if (cancelled) return
        // Never fabricate: on failure, show no crypto particles at all.
        engineRef.current?.setCryptoTokens([])
        setLiveOk(false)
      }
    }

    void load()
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 90_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  // --- Real paper-trading wallet (replaces the mockup's fake position list) -
  useEffect(() => {
    let alive = true
    getPaperWallet()
      .then((w) => {
        if (alive) setWallet(w)
      })
      .catch(() => {
        if (alive) setWalletError(true)
      })
      .finally(() => {
        if (alive) setWalletLoaded(true)
      })
    return () => {
      alive = false
    }
  }, [])

  // --- Category filter (Market modal) ---------------------------------------
  const setActiveCategories = useCallback((next: Set<MarketCategory>) => {
    setActiveCategoriesState(next)
    engineRef.current?.setActiveCategories(next)
  }, [])

  const toggleCategory = useCallback(
    (key: MarketCategory) => {
      const next = new Set(activeCategories)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      setActiveCategories(next)
    },
    [activeCategories, setActiveCategories],
  )

  // --- Theme slider ----------------------------------------------------------
  const handleThemeChange = useCallback((value: number) => {
    setThemeValue(value)
    engineRef.current?.setThemeLightness(value)
  }, [])

  useEffect(() => {
    if (!themeOpen) return
    function onDocClick(e: MouseEvent) {
      const root = rootRef.current
      if (!root) return
      const target = e.target as Node
      const panel = root.querySelector('.ao-theme-panel')
      const tab = root.querySelector('.ao-theme-tab')
      if (panel?.contains(target) || tab?.contains(target)) return
      setThemeOpen(false)
    }
    document.addEventListener('click', onDocClick)
    return () => document.removeEventListener('click', onDocClick)
  }, [themeOpen])

  // --- Escape closes whatever is open -----------------------------------------
  useEffect(() => {
    if (!openModal && !themeOpen) return
    function onKeydown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      setOpenModal(null)
      setThemeOpen(false)
    }
    document.addEventListener('keydown', onKeydown)
    return () => document.removeEventListener('keydown', onKeydown)
  }, [openModal, themeOpen])

  // --- Nav destinations -------------------------------------------------------
  const pulseMemberSignIn = useCallback(() => {
    window.scrollTo({ top: 0, behavior: reducedMotionRef.current ? 'auto' : 'smooth' })
    window.setTimeout(
      () => {
        const btn = document.querySelector<HTMLElement>('[data-nav-target="member-signin"]')
        if (!btn) return
        if (reducedMotionRef.current || typeof btn.animate !== 'function') {
          const prevOutline = btn.style.outline
          btn.style.outline = '2px solid #c9a962'
          window.setTimeout(() => {
            btn.style.outline = prevOutline
          }, 2000)
          return
        }
        btn.animate(
          [
            { boxShadow: '0 0 0 0 rgba(201,169,98,0.9)' },
            { boxShadow: '0 0 0 10px rgba(201,169,98,0)' },
            { boxShadow: '0 0 0 0 rgba(201,169,98,0)' },
          ],
          { duration: 900, iterations: 2 },
        )
      },
      reducedMotionRef.current ? 0 : 450,
    )
  }, [])

  const handleNodeClick = useCallback(
    (label: string) => (e: React.MouseEvent) => {
      e.preventDefault()
      if (label === 'Événements') setOpenModal('events')
      else if (label === 'Méthodologie') setOpenModal('method')
      else if (label === 'Accès membre') pulseMemberSignIn()
      else if (label === 'Telegram') setOpenModal('telegram')
    },
    [pulseMemberSignIn],
  )

  // The ask-input IS the real ARIA chat now (no separate #aria section to
  // scroll to) -- same agentChat() client, reply rendered right above the
  // input. Never fabricate on failure: show an explicit unavailable message,
  // not a fake answer (same doctrine as the wallet panel above).
  const handleAskSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault()
      const trimmed = askValue.trim()
      if (!trimmed || askLoading) return
      engineRef.current?.pulse()
      setAskValue('')
      setAskQuestion(trimmed)
      setAskReply(null)
      setAskError(false)
      setAskLoading(true)
      agentChat(trimmed)
        .then((res) => setAskReply(res.reply))
        .catch(() => {
          setAskError(true)
          setAskReply('Réponse indisponible pour le moment.')
        })
        .finally(() => setAskLoading(false))
    },
    [askValue, askLoading],
  )

  const toggleEventSaved = useCallback((i: number) => {
    setSavedEvents((prev) => ({ ...prev, [i]: !prev[i] }))
  }, [])

  const liveLabel = lastFetchAt
    ? `mis à jour à ${lastFetchAt.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`
    : 'en attente du premier relevé'

  return (
    <section
      ref={rootRef}
      className="aria-organism"
      aria-label="ARIA — vue d'ensemble vivante et navigation"
    >
      <style>{CSS}</style>

      <canvas ref={canvasRef} className="ao-canvas" aria-hidden="true" />

      <div className="ao-pc" ref={portfolioElRef} role="group" aria-label="Portefeuille paper-trading ARIA">
        <div className="ao-pc-head">
          <span className={`ao-pc-dot${wallet ? ' is-live' : ''}`} aria-hidden="true" />
          <span className="ao-pc-title">Portefeuille paper-trading</span>
        </div>
        {wallet ? (
          <>
            <ul className="ao-pc-list">
              <li className="ao-pc-row">
                <span className="ao-pc-k">Capital de départ</span>
                <span className="ao-pc-v">${wallet.starting.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
              </li>
              <li className="ao-pc-row">
                <span className="ao-pc-k">Valeur actuelle</span>
                <span className="ao-pc-v">${wallet.equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
              </li>
              <li className="ao-pc-row">
                <span className="ao-pc-k">Rendement</span>
                <span className={`ao-pc-v ${wallet.return_pct >= 0 ? 'is-pos' : 'is-neg'}`}>
                  {wallet.return_pct >= 0 ? '+' : ''}
                  {wallet.return_pct.toFixed(1)}%
                </span>
              </li>
              <li className="ao-pc-row">
                <span className="ao-pc-k">Positions ouvertes</span>
                <span className="ao-pc-v">{wallet.open_positions}</span>
              </li>
              <li className="ao-pc-row">
                <span className="ao-pc-k">Taux de réussite</span>
                <span className="ao-pc-v">{wallet.win_rate != null ? `${Math.round(wallet.win_rate * 100)}%` : '—'}</span>
              </li>
            </ul>
            <div className="ao-pc-note">{wallet.disclaimer}</div>
          </>
        ) : (
          <div className="ao-pc-empty">
            {walletLoaded ? (walletError ? 'Portefeuille indisponible pour le moment.' : 'Aucune donnée pour le moment.') : 'Chargement…'}
          </div>
        )}
      </div>

      <div className="ao-nodes">
        {navTips.map((tip) => {
          const isEvent = tip.label === 'Événements'
          const isNorth = NORTH_LABELS.has(tip.label)
          const className = `ao-node${isEvent ? ' ao-node-event' : ''}${isNorth ? ' ao-node-north' : ''}`
          const content = (
            <>
              <span className="ao-ring" />
              <span className="ao-lbl">{tip.label}</span>
            </>
          )
          // Track record's own in-page section (#track-record) is gone --
          // the real data now lives on /cockpit, same destination and same
          // plain-anchor wiring as the Cockpit branch (deliberately identical
          // right now; left as two labels rather than merged pending an
          // operator call on whether to consolidate them).
          if (tip.label === 'Cockpit' || tip.label === 'Track record') {
            return (
              <a key={tip.label} href="/cockpit" className={className} style={{ left: tip.x, top: tip.y }}>
                {content}
              </a>
            )
          }
          return (
            <a
              key={tip.label}
              href="#"
              className={className}
              style={{ left: tip.x, top: tip.y }}
              onClick={handleNodeClick(tip.label)}
              onMouseEnter={() => engineRef.current?.setBranchHovered(true)}
              onMouseLeave={() => engineRef.current?.setBranchHovered(false)}
              onFocus={() => engineRef.current?.setBranchHovered(true)}
              onBlur={() => engineRef.current?.setBranchHovered(false)}
            >
              {content}
            </a>
          )
        })}
      </div>

      <button
        type="button"
        className="ao-core-hit"
        style={{ left: corePos.x, top: corePos.y }}
        onClick={() => setOpenModal('market')}
        aria-label="Changer de marché"
      />

      {reducedMotionNote && (
        <div className="ao-reduced-note">
          mouvement réduit (préférence système) — réaction en un seul temps, sans onde continue
        </div>
      )}

      <div className="ao-ask-wrap">
        {(askLoading || askReply) && (
          <div
            className={`ao-ask-reply${askError ? ' is-error' : ''}`}
            role="status"
            aria-live="polite"
          >
            {askQuestion && <div className="ao-ask-q">{askQuestion}</div>}
            <div>{askLoading ? 'ARIA réfléchit…' : askReply}</div>
          </div>
        )}
        <form className="ao-ask" onSubmit={handleAskSubmit}>
          <input
            type="text"
            value={askValue}
            onChange={(e) => setAskValue(e.target.value)}
            placeholder="Demande quelque chose à ARIA…"
            autoComplete="off"
            aria-label="Demander à ARIA"
          />
          <button type="submit" disabled={askLoading}>
            Demander
          </button>
        </form>
      </div>

      <button
        type="button"
        className="ao-theme-tab"
        aria-label="Régler l'ambiance sombre / claire"
        aria-expanded={themeOpen}
        aria-controls="ao-theme-panel"
        onClick={() => setThemeOpen((v) => !v)}
      >
        <svg viewBox="0 0 100 100" width="26" height="26" aria-hidden="true">
          <circle cx="50" cy="50" r="46" fill="#fff" stroke="rgba(232,233,234,0.35)" strokeWidth="3" />
          <path d="M50,4 A46,46 0 0,1 50,96 A23,23 0 0,1 50,50 A23,23 0 0,0 50,4 Z" fill="#050607" />
          <circle cx="50" cy="27" r="7" fill="#050607" />
          <circle cx="50" cy="73" r="7" fill="#fff" />
        </svg>
      </button>
      {themeOpen && (
        <div className="ao-theme-panel" id="ao-theme-panel">
          <div className="ao-theme-label">Ambiance</div>
          <input
            type="range"
            className="ao-theme-slider"
            min={0}
            max={100}
            value={themeValue}
            onChange={(e) => handleThemeChange(Number(e.target.value))}
            aria-label="Curseur d'ambiance, noir à gauche, blanc à droite"
          />
          <div className="ao-theme-ends">
            <span>Noir</span>
            <span>Blanc</span>
          </div>
        </div>
      )}

      {openModal === 'events' && (
        <div className="ao-modal-backdrop" onClick={(e) => e.target === e.currentTarget && setOpenModal(null)}>
          <div className="ao-modal" role="dialog" aria-modal="true" aria-labelledby="ao-events-title">
            <button type="button" className="ao-modal-close" aria-label="Fermer" onClick={() => setOpenModal(null)}>
              ✕
            </button>
            <h2 id="ao-events-title">Événements</h2>
            <p className="ao-modal-sub">Les grands rendez-vous crypto à venir dans les 12 prochains mois.</p>
            <button
              type="button"
              className={`ao-calendar-connect${calendarConnected ? ' is-connected' : ''}`}
              onClick={() => setCalendarConnected((v) => !v)}
            >
              {calendarConnected ? 'Agenda connecté (démo) ✓' : 'Connecter mon agenda'}
            </button>
            <p className="ao-modal-demo-note">
              Démo -- rien n'est envoyé ni stocké ici. La vraie connexion (Google Agenda / Apple Agenda) sera branchée
              sur le site final.
            </p>
            <ul className="ao-event-list">
              {EVENTS.map((ev, i) => {
                const saved = !!savedEvents[i]
                return (
                  <li className="ao-event-row" key={ev.name}>
                    <div className="ao-event-info">
                      <span className="ao-event-name">{ev.name}</span>
                      <span className="ao-event-date">{ev.date}</span>
                      <span className="ao-event-detail">{ev.detail}</span>
                    </div>
                    <button
                      type="button"
                      className={`ao-event-toggle${saved ? ' is-saved' : ''}`}
                      onClick={() => toggleEventSaved(i)}
                    >
                      {saved ? 'Inscrit ✓' : 'Participer'}
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      )}

      {openModal === 'market' && (
        <div className="ao-modal-backdrop" onClick={(e) => e.target === e.currentTarget && setOpenModal(null)}>
          <div className="ao-modal" role="dialog" aria-modal="true" aria-labelledby="ao-market-title">
            <button type="button" className="ao-modal-close" aria-label="Fermer" onClick={() => setOpenModal(null)}>
              ✕
            </button>
            <h2 id="ao-market-title">Marchés</h2>
            <p className="ao-modal-sub">
              Filtre les valeurs de fond par marché -- un seul coché change la couleur d'ambiance.
            </p>
            <div className={`ao-market-live${liveOk ? '' : ' is-stale'}`}>
              <span className="ao-live-dot" aria-hidden="true" />
              <span>{liveOk ? `en direct · ${liveLabel}` : liveLabel}</span>
            </div>
            <ul className="ao-market-list">
              {(['crypto', 'event'] as MarketCategory[]).map((key) => {
                const checked = activeCategories.has(key)
                const isMarket = key !== 'event'
                const label = key === 'crypto' ? 'Crypto' : 'Événements'
                const swatchColor = !isMarket
                  ? 'rgba(210,210,214,0.55)'
                  : checked
                    ? `rgb(${FINANCE_COLORS[key]})`
                    : 'rgba(140,144,150,0.35)'
                return (
                  <li key={key}>
                    <button
                      type="button"
                      className={`ao-market-row${checked ? ' is-active' : ''}`}
                      aria-pressed={checked}
                      onClick={() => toggleCategory(key)}
                    >
                      <span className="ao-market-swatch" style={{ background: swatchColor }} />
                      <span className="ao-market-name">{label}</span>
                      <span className="ao-market-check">{checked ? '✓' : ''}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      )}

      {openModal === 'method' && (
        <div className="ao-modal-backdrop" onClick={(e) => e.target === e.currentTarget && setOpenModal(null)}>
          <div className="ao-modal" role="dialog" aria-modal="true" aria-labelledby="ao-method-title">
            <button type="button" className="ao-modal-close" aria-label="Fermer" onClick={() => setOpenModal(null)}>
              ✕
            </button>
            <h2 id="ao-method-title">Méthodologie</h2>
            <p className="ao-modal-sub">La même discipline à chaque fois. Détail exact réservé, principe ci-dessous.</p>
            <ol className="ao-method-steps">
              {METHOD_STEPS.map((step, i) => (
                <li className="ao-method-step" style={{ animationDelay: `${i * 0.09}s` }} key={step.n}>
                  <span className="ao-method-num">{step.n}</span>
                  <div className="ao-method-body">
                    <span className="ao-method-title">{step.title}</span>
                    <span className="ao-method-text">{step.text}</span>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      {openModal === 'telegram' && (
        <div className="ao-modal-backdrop" onClick={(e) => e.target === e.currentTarget && setOpenModal(null)}>
          <div className="ao-modal" role="dialog" aria-modal="true" aria-labelledby="ao-telegram-title">
            <button type="button" className="ao-modal-close" aria-label="Fermer" onClick={() => setOpenModal(null)}>
              ✕
            </button>
            <h2 id="ao-telegram-title">Telegram</h2>
            <p className="ao-modal-sub">Le bot Telegram d’ARIA n’est pas encore ouvert au public.</p>
            <button type="button" className="ao-calendar-connect" disabled aria-disabled="true">
              Bientôt disponible
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
