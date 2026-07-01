import type { GemId, SpecialKind } from '../game/types'

const PALETTE: Record<GemId, { base: string; mid: string; dark: string; shine: string }> = {
  0: { base: '#ff8a9a', mid: '#e02040', dark: '#8a1028', shine: '#ffd0d8' },
  1: { base: '#7ec8ff', mid: '#1e6fd4', dark: '#0d3a6e', shine: '#d0ecff' },
  2: { base: '#7dffb0', mid: '#1faa55', dark: '#0d5c30', shine: '#d0ffe8' },
  3: { base: '#ffe07a', mid: '#e6a817', dark: '#8a5f08', shine: '#fff4c8' },
  4: { base: '#d4a8ff', mid: '#8b3fd4', dark: '#4a1f7a', shine: '#f0d8ff' },
  5: { base: '#f0f8ff', mid: '#b8d4e8', dark: '#3d5a6e', shine: '#ffffff' },
}

export interface GemSpriteProps {
  gem: GemId
  special: SpecialKind
}

export function GemSprite({ gem, special }: GemSpriteProps) {
  const c = PALETTE[gem]
  return (
    <svg
      className="gem-crush__sprite"
      viewBox="0 0 64 64"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <radialGradient id={`gem-body-${gem}`} cx="38%" cy="28%" r="68%">
          <stop offset="0%" stopColor={c.shine} />
          <stop offset="45%" stopColor={c.base} />
          <stop offset="100%" stopColor={c.dark} />
        </radialGradient>
        <linearGradient id={`gem-rim-${gem}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={c.shine} stopOpacity="0.9" />
          <stop offset="100%" stopColor={c.mid} stopOpacity="0.4" />
        </linearGradient>
      </defs>
      <ellipse cx="32" cy="36" rx="24" ry="22" fill={`url(#gem-body-${gem})`} />
      <ellipse cx="32" cy="36" rx="24" ry="22" fill="none" stroke={`url(#gem-rim-${gem})`} strokeWidth="2" />
      <ellipse cx="24" cy="26" rx="9" ry="6" fill={c.shine} opacity="0.75" />
      <ellipse cx="40" cy="42" rx="5" ry="3" fill={c.mid} opacity="0.35" />
      {special === 'line-h' && (
        <rect x="10" y="30" width="44" height="5" rx="2" fill="rgba(255,255,255,0.92)" />
      )}
      {special === 'line-v' && (
        <rect x="30" y="10" width="5" height="44" rx="2" fill="rgba(255,255,255,0.92)" />
      )}
      {special === 'bomb' && (
        <>
          <circle cx="32" cy="34" r="14" fill="rgba(255,200,80,0.35)" />
          <text x="32" y="40" textAnchor="middle" fontSize="18" fill="#fff8e0" fontWeight="700">
            ★
          </text>
        </>
      )}
    </svg>
  )
}