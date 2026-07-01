import type { ReactNode } from 'react'

type Dir = 'up' | 'down' | 'left' | 'right'

const DIR_LABEL: Record<Dir, string> = {
  up: '↑',
  down: '↓',
  left: '←',
  right: '→',
}

const DIR_ARIA: Record<Dir, string> = {
  up: 'Haut',
  down: 'Bas',
  left: 'Gauche',
  right: 'Droite',
}

function TouchBtn({
  dir,
  onPress,
}: {
  dir: Dir
  onPress: () => void
}) {
  return (
    <button
      type="button"
      aria-label={DIR_ARIA[dir]}
      className="game-touch-btn"
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => {
        e.preventDefault()
        onPress()
      }}
    >
      {DIR_LABEL[dir]}
    </button>
  )
}

export function TouchDirPad({ onDir }: { onDir: (dir: Dir) => void }) {
  return (
    <div className="absolute bottom-3 left-1/2 -translate-x-1/2 grid grid-cols-3 gap-1.5 pointer-events-auto">
      <span />
      <TouchBtn dir="up" onPress={() => onDir('up')} />
      <span />
      <TouchBtn dir="left" onPress={() => onDir('left')} />
      <TouchBtn dir="down" onPress={() => onDir('down')} />
      <TouchBtn dir="right" onPress={() => onDir('right')} />
    </div>
  )
}

export function TouchVerticalPad({
  onUp,
  onDown,
}: {
  onUp: (active: boolean) => void
  onDown: (active: boolean) => void
}) {
  return (
    <div className="absolute left-3 top-1/2 -translate-y-1/2 flex flex-col gap-2 pointer-events-auto">
      <HoldBtn label="Monter" glyph="↑" onHold={onUp} />
      <HoldBtn label="Descendre" glyph="↓" onHold={onDown} />
    </div>
  )
}

function HoldBtn({
  label,
  glyph,
  onHold,
}: {
  label: string
  glyph: ReactNode
  onHold: (active: boolean) => void
}) {
  return (
    <button
      type="button"
      aria-label={label}
      className="game-touch-btn game-touch-btn--lg"
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => {
        e.preventDefault()
        e.stopPropagation()
        onHold(true)
      }}
      onPointerUp={() => onHold(false)}
      onPointerLeave={() => onHold(false)}
    >
      {glyph}
    </button>
  )
}