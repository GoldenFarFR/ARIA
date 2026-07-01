import { useEffect, useRef, type PointerEvent, type ReactNode, type RefObject } from 'react'

export type GameOverlay = {
  title: string
  subtitle?: ReactNode
  action?: ReactNode
}

type Props = {
  shellRef?: RefObject<HTMLDivElement | null>
  ariaLabel: string
  onShellClick?: () => void
  onShellPointerDown?: (e: PointerEvent<HTMLDivElement>) => void
  hudLeft?: ReactNode
  hudCenter?: ReactNode
  hudRight?: ReactNode
  ready?: GameOverlay | null
  hint?: ReactNode
  end?: GameOverlay | null
  touchControls?: ReactNode
  desktopHint?: string
  children: ReactNode
}

export function GameShell({
  shellRef: externalRef,
  ariaLabel,
  onShellClick,
  onShellPointerDown,
  hudLeft,
  hudCenter,
  hudRight,
  ready,
  hint,
  end,
  touchControls,
  desktopHint,
  children,
}: Props) {
  const internalRef = useRef<HTMLDivElement>(null)
  const shellRef = externalRef ?? internalRef

  useEffect(() => {
    shellRef.current?.focus()
  }, [shellRef])

  return (
    <div
      ref={shellRef}
      tabIndex={0}
      role="application"
      aria-label={ariaLabel}
      onClick={onShellClick}
      onPointerDown={onShellPointerDown}
      className="game-shell relative w-full h-full"
    >
      {children}

      {(hudLeft || hudCenter || hudRight) && (
        <div className="game-hud absolute top-0 left-0 right-0 flex items-start justify-between gap-4 px-4 pt-3 pointer-events-none">
          <div className="game-hud__slot min-w-0">{hudLeft}</div>
          <div className="game-hud__slot">{hudCenter}</div>
          <div className="game-hud__slot text-right min-w-0">{hudRight}</div>
        </div>
      )}

      {ready && (
        <div className="game-overlay game-overlay--ready absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <p className="game-overlay__title">{ready.title}</p>
          {ready.subtitle && <div className="game-overlay__subtitle">{ready.subtitle}</div>}
        </div>
      )}

      {hint && (
        <div className="game-hint absolute bottom-4 left-0 right-0 text-center pointer-events-none">{hint}</div>
      )}

      {end && (
        <div className="game-overlay game-overlay--end absolute inset-0 flex flex-col items-center justify-center">
          <p className="game-overlay__title text-xl mb-1">{end.title}</p>
          {end.subtitle && <div className="game-overlay__subtitle mb-4">{end.subtitle}</div>}
          {end.action}
        </div>
      )}

      {touchControls}

      {desktopHint && (
        <p className="game-desktop-hint absolute bottom-3 right-4 pointer-events-none hidden sm:block">
          {desktopHint}
        </p>
      )}
    </div>
  )
}