import { CHAPTERS, chapterForLevel } from '../game/chapters'

export interface LevelMapProps {
  level: number
}

export function LevelMap({ level }: LevelMapProps) {
  const current = chapterForLevel(level)
  return (
    <nav className="gem-crush__map gem-crush__map--world map-world" aria-label="Carte des chapitres">
      <p className="gem-crush__map-title">Carte ARIA</p>
      <ol className="gem-crush__map-track">
        {CHAPTERS.map((ch) => {
          const state =
            ch.id < current.id ? 'done' : ch.id === current.id ? 'active' : 'locked'
          return (
            <li
              key={ch.id}
              className={`gem-crush__map-node gem-crush__map-node--${state}`}
              style={{ '--ch-accent': ch.accent } as React.CSSProperties}
            >
              <span className="gem-crush__map-dot" aria-hidden="true" />
              <span className="gem-crush__map-label">{ch.name}</span>
            </li>
          )
        })}
      </ol>
      <p className="gem-crush__map-sub">{current.subtitle}</p>
    </nav>
  )
}