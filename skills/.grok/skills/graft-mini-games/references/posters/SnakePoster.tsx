import type { PosterProps } from '../types'

/** Frame fixe : serpent en L, pomme à droite */
export function SnakePoster({ className = '' }: PosterProps) {
  return (
    <svg viewBox="0 0 160 120" className={className} aria-hidden>
      <rect width="160" height="120" fill="#0f0f14" />
      <rect x="8" y="8" width="144" height="104" rx="4" fill="none" stroke="#2a2a3a" strokeWidth="2" />
      <rect x="40" y="56" width="8" height="8" fill="#6366f1" />
      <rect x="48" y="56" width="8" height="8" fill="#6366f1" />
      <rect x="56" y="56" width="8" height="8" fill="#6366f1" />
      <rect x="56" y="48" width="8" height="8" fill="#6366f1" />
      <rect x="56" y="40" width="8" height="8" fill="#818cf8" />
      <circle cx="100" cy="44" r="5" fill="#22c55e" />
    </svg>
  )
}