import { useCallback, useState } from 'react'
import type { MiniGameDefinition } from './types'

type Props = {
  game: MiniGameDefinition
}

export function MiniGameCard({ game }: Props) {
  const { Poster, Preview, title, description, href } = game
  const [active, setActive] = useState(false)

  const onEnter = useCallback(() => setActive(true), [])
  const onLeave = useCallback(() => setActive(false), [])

  const inner = (
    <>
      <div className="mini-game-card__viewport relative w-full aspect-[4/3] overflow-hidden rounded-xl border border-white/10 bg-[#12121a]">
        <div
          className={`absolute inset-0 transition-opacity duration-150 ${active ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
          aria-hidden={active}
        >
          <Poster className="w-full h-full object-cover" />
        </div>
        <div
          className={`absolute inset-0 transition-opacity duration-150 ${active ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
          aria-hidden={!active}
        >
          <Preview active={active} className="w-full h-full" />
        </div>
      </div>
      <h3 className="mt-3 text-sm font-medium text-white">{title}</h3>
      <p className="mt-1 text-xs text-neutral-500 line-clamp-2">{description}</p>
    </>
  )

  const className =
    'mini-game-card group block rounded-2xl p-3 hover:bg-white/[0.03] focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500/60 transition-colors'

  if (href) {
    return (
      <a href={href} className={className} onMouseEnter={onEnter} onMouseLeave={onLeave} onFocus={onEnter} onBlur={onLeave}>
        {inner}
      </a>
    )
  }

  return (
    <article
      className={className}
      tabIndex={0}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
    >
      {inner}
    </article>
  )
}