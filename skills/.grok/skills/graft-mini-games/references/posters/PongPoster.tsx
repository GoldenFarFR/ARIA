import type { PosterProps } from '../types'

export function PongPoster({ className = '' }: PosterProps) {
  return (
    <svg viewBox="0 0 160 120" className={className} aria-hidden>
      <rect width="160" height="120" fill="#0f0f14" />
      <line x1="80" y1="8" x2="80" y2="112" stroke="#2a2a3a" strokeWidth="1" strokeDasharray="4 6" />
      <rect x="12" y="44" width="6" height="32" rx="2" fill="#e5e5e5" />
      <rect x="142" y="52" width="6" height="32" rx="2" fill="#e5e5e5" />
      <circle cx="80" cy="60" r="6" fill="#fbbf24" />
    </svg>
  )
}