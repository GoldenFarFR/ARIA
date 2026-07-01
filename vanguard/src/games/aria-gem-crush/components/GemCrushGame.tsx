import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { levelConfig } from '../game/constants'
import {
  createInitialBoard,
  processSwap,
  type CascadeStep,
} from '../game/engine'
import { findHintMove, hasValidMove } from '../game/hints'
import { shuffleBoard } from '../game/shuffle'
import { useGemAudio } from '../hooks/useGemAudio'
import type { Board } from '../game/types'
import { chapterForLevel } from '../game/chapters'
import { Confetti } from './Confetti'
import { GemSprite } from './GemSprite'
import { LevelMap } from './LevelMap'
import { GEM_CRUSH_RELEASE_TITLE, GEM_CRUSH_UPDATED_AT, GEM_CRUSH_VERSION } from '../version'

type Pos = [number, number]

const TUTORIAL_KEY = 'aria-gem-crush-tutorial-v1'

function comboLabelFor(combo: number): string {
  if (combo >= 4) return `Sugar Rush ×${combo} !`  // aria-gem-crush-v34
  if (combo === 3) return 'BLAST ×3 !'  // aria-gem-crush-v37
  if (combo > 1) return `Délicieux ×${combo} !`
  return 'Sweet !'  // aria-gem-crush-v34
}

export interface GemCrushGameProps {
  compact?: boolean
  onScoreChange?: (score: number) => void
}

export function GemCrushGame({ compact = false, onScoreChange }: GemCrushGameProps) {
  const { play } = useGemAudio()
  const [board, setBoard] = useState<Board>(() => createInitialBoard())
  const [selected, setSelected] = useState<Pos | null>(null)
  const [score, setScore] = useState(0)
  const [level, setLevel] = useState(1)
  const [movesLeft, setMovesLeft] = useState(() => levelConfig(1).moves)
  const [target, setTarget] = useState(() => levelConfig(1).target)
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState<Set<string>>(new Set())
  const [comboLabel, setComboLabel] = useState<string | null>(null)
  const [won, setWon] = useState(false)
  const [gameOver, setGameOver] = useState(false)
  const [shake, setShake] = useState(false)
  const [invalidFlash, setInvalidFlash] = useState(false)
  const [scorePops, setScorePops] = useState<{ id: number; value: number }[]>([])
  const [swapAnim, setSwapAnim] = useState<{ a: Pos; b: Pos } | null>(null)
  const [hint, setHint] = useState<[Pos, Pos] | null>(null)
  const [showTutorial, setShowTutorial] = useState(false)
  const [shuffling, setShuffling] = useState(false)
  const [celebrate, setCelebrate] = useState(false)
  const touchRef = useRef<{ x: number; y: number; r: number; c: number } | null>(null)
  const idleRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const posByIdRef = useRef<Map<string, [number, number]>>(new Map())
  const [fallRows, setFallRows] = useState<Map<string, number>>(new Map())

  const progress = useMemo(() => Math.min(100, (score / target) * 100), [score, target])
  const nearTarget = progress >= 70  // aria-gem-crush-v38

  useEffect(() => {
    onScoreChange?.(score)
  }, [score, onScoreChange])

  useEffect(() => {
    const prev = posByIdRef.current
    const next = new Map<string, [number, number]>()
    const falls = new Map<string, number>()
    board.forEach((row, r) => {
      row.forEach((cell, c) => {
        next.set(cell.id, [r, c])
        const old = prev.get(cell.id)
        if (old && old[1] === c && r > old[0]) falls.set(cell.id, r - old[0])
      })
    })
    posByIdRef.current = next
    if (falls.size > 0) {
      setFallRows(falls)
      const t = window.setTimeout(() => setFallRows(new Map()), 380)
      return () => window.clearTimeout(t)
    }
  }, [board])

  useEffect(() => {
    if (typeof localStorage !== 'undefined' && !localStorage.getItem(TUTORIAL_KEY)) {
      setShowTutorial(true)
    }
  }, [])

  const dismissTutorial = () => {
    localStorage.setItem(TUTORIAL_KEY, '1')
    setShowTutorial(false)
  }

  const scheduleHint = useCallback(() => {
    if (idleRef.current) clearTimeout(idleRef.current)
    idleRef.current = setTimeout(() => {
      if (!busy && !gameOver) setHint(findHintMove(board))
    }, 6000)  // aria-gem-crush-v35
  }, [board, busy, gameOver])

  useEffect(() => {
    scheduleHint()
    return () => {
      if (idleRef.current) clearTimeout(idleRef.current)
    }
  }, [scheduleHint])

  const pushScorePop = useCallback((value: number) => {
    const id = Date.now() + Math.random()
    setScorePops((p) => [...p.slice(-4), { id, value }])
    window.setTimeout(() => setScorePops((p) => p.filter((x) => x.id !== id)), 900)
  }, [])

  const ensurePlayable = useCallback(
    (nextBoard: Board) => {
      if (hasValidMove(nextBoard)) return nextBoard
      setShuffling(true)
      play('shuffle')
      setComboLabel('ARIA réorganise…')  // aria-gem-crush-v36
      const shuffled = shuffleBoard(nextBoard)
      window.setTimeout(() => {
        setShuffling(false)
        setComboLabel(null)
      }, 700)
      return shuffled
    },
    [play],
  )

  const advanceLevel = useCallback(() => {
    const next = level + 1
    const cfg = levelConfig(next)
    setLevel(next)
    setTarget(cfg.target)
    setMovesLeft(cfg.moves)
    setBoard(ensurePlayable(createInitialBoard()))
    setWon(false)
    setCelebrate(false)
    setComboLabel(`Trophée ${next} débloqué !`)  // aria-gem-crush-v35
    window.setTimeout(() => setComboLabel(null), 1800)
  }, [level, ensurePlayable])

  const runCascade = useCallback(
    async (steps: CascadeStep[]) => {
      for (const step of steps) {
        setFlash(step.cleared)
        setComboLabel(comboLabelFor(step.combo))
        play(step.combo > 1 ? 'combo' : 'match', step.combo)
        pushScorePop(step.scoreGain)
        await new Promise((r) => setTimeout(r, 220))  // aria-gem-crush-v39
        setBoard(step.board)
        setScore((s) => s + step.scoreGain)
        await new Promise((r) => setTimeout(r, 160))  // aria-gem-crush-v34
        setFlash(new Set())
      }
      window.setTimeout(() => setComboLabel(null), 700)
    },
    [play, pushScorePop],
  )

  const trySwap = useCallback(
    async (a: Pos, b: Pos) => {
      if (busy || gameOver || shuffling) return
      setHint(null)
      setBusy(true)
      setSwapAnim({ a, b })
      play('swap')
      await new Promise((r) => setTimeout(r, 200))
      setSwapAnim(null)

      const result = processSwap(board, a, b)
      if (!result.ok) {
        setShake(true)
        setInvalidFlash(true)
        play('invalid')
        window.setTimeout(() => {
          setShake(false)
          setInvalidFlash(false)
        }, 420)
        setBusy(false)
        scheduleHint()
        return
      }
      setMovesLeft((m) => m - 1)
      await runCascade(result.steps)
      const final = ensurePlayable(result.final)
      setBoard(final)
      setBusy(false)
      scheduleHint()
    },
    [board, busy, gameOver, shuffling, runCascade, ensurePlayable, play, scheduleHint],
  )

  useEffect(() => {
    if (score >= target && !won) {
      setWon(true)
      setCelebrate(true)
      play('win')
      setComboLabel('Délicieux ! ★★★')  // aria-gem-crush-v39
      window.setTimeout(() => advanceLevel(), 2400)  // aria-gem-crush-v31
    }
  }, [score, target, won, advanceLevel, play])

  useEffect(() => {
    if (movesLeft <= 0 && score < target && !won) setGameOver(true)
  }, [movesLeft, score, target, won])

  const onCellClick = (r: number, c: number) => {
    if (busy || gameOver || shuffling) return
    const pos: Pos = [r, c]
    if (!selected) {
      setSelected(pos)
      setHint(null)
      return
    }
    if (selected[0] === r && selected[1] === c) {
      setSelected(null)
      return
    }
    void trySwap(selected, pos)
    setSelected(null)
  }

  const showHintNow = () => {
    const h = findHintMove(board)
    setHint(h)
    if (h) setSelected(h[0])
  }

  const restart = () => {
    const cfg = levelConfig(1)
    setBoard(ensurePlayable(createInitialBoard()))
    setScore(0)
    setLevel(1)
    setMovesLeft(cfg.moves)
    setTarget(cfg.target)
    setGameOver(false)
    setWon(false)
    setSelected(null)
    setCelebrate(false)
  }

  const isHintCell = (r: number, c: number) =>
    hint?.some(([hr, hc]) => hr === r && hc === c) ?? false

  const cellClass = (r: number, c: number, isSelected: boolean, isFlash: boolean) => {
    const cell = board[r][c]
    const classes = [
      'gem-crush__cell',
      `gem-crush__gem-${cell.gem}`,
      cell.special !== 'none' ? `gem-crush__special-${cell.special}` : '',
      isSelected ? 'gem-crush__cell--selected' : '',
      isFlash ? 'gem-crush__cell--pop' : '',
      isHintCell(r, c) ? 'gem-crush__cell--hint' : '',
      shuffling ? 'gem-crush__cell--shuffle' : '',
    ]
    if (swapAnim) {
      const { a, b } = swapAnim
      if (a[0] === r && a[1] === c) classes.push('gem-crush__cell--swap-a')
      if (b[0] === r && b[1] === c) classes.push('gem-crush__cell--swap-b')
    }
    return classes.filter(Boolean).join(' ')
  }

  const onTouchStart = (r: number, c: number, e: React.TouchEvent) => {
    const t = e.touches[0]
    touchRef.current = { x: t.clientX, y: t.clientY, r, c }
  }

  const onTouchEnd = (e: React.TouchEvent) => {
    const start = touchRef.current
    touchRef.current = null
    if (!start) return
    const t = e.changedTouches[0]
    const dx = t.clientX - start.x
    const dy = t.clientY - start.y
    const dist = Math.hypot(dx, dy)
    if (dist < 18) {
      onCellClick(start.r, start.c)
      return
    }
    let nr = start.r
    let nc = start.c
    if (Math.abs(dx) > Math.abs(dy)) nc += dx > 0 ? 1 : -1
    else nr += dy > 0 ? 1 : -1
    if (nr >= 0 && nr < 8 && nc >= 0 && nc < 8) void trySwap([start.r, start.c], [nr, nc])
  }

  const mascotLine =
    comboLabel || (nearTarget ? 'Objectif proche !' : 'Aligne 3 gemmes pour gagner')
  const chapter = chapterForLevel(level)

  return (
    <div
      className={`gem-crush ${compact ? 'gem-crush--compact' : ''} ${shake ? 'gem-crush--shake' : ''} ${invalidFlash ? 'gem-crush--invalid-flash' : ''} ${nearTarget ? 'gem-crush--near-win' : ''}`}  // aria-gem-crush-v30
      style={{ position: 'relative' }}
    >
      <Confetti active={celebrate} />

      <div
        className="gem-crush__version-badge"
        title={`${GEM_CRUSH_RELEASE_TITLE} — ${GEM_CRUSH_UPDATED_AT}`}
        aria-label={`Version ${GEM_CRUSH_VERSION}`}
      >
        v{GEM_CRUSH_VERSION}
      </div>

      <div className="gem-crush__mascot" aria-hidden="true">
        <span className="gem-crush__mascot-face">A</span>
        <span className="gem-crush__mascot-bubble">{mascotLine}</span>
      </div>

      <LevelMap level={level} />

      <header className="gem-crush__hud">
        <div className="gem-crush__stat">
          <span className="gem-crush__label">Score</span>
          <strong>{score.toLocaleString('fr-FR')}</strong>
        </div>
        <div className="gem-crush__stat">
          <span className="gem-crush__label">Niveau</span>
          <strong>{level}</strong>
          <span className="gem-crush__chapter">{chapter.name} · {chapter.subtitle}</span>
        </div>
        <div className="gem-crush__stat">
          <span className="gem-crush__label">Coups</span>
          <strong className={movesLeft <= 5 ? 'gem-crush__warn' : ''}>{movesLeft}</strong>
        </div>
        <div className="gem-crush__progress-wrap">
          <span className="gem-crush__label">Objectif {target.toLocaleString('fr-FR')}</span>
          <div className="gem-crush__progress" data-near={nearTarget ? '1' : '0'}>
            <div className="gem-crush__progress-bar" style={{ width: `${progress}%` }} />
          </div>
        </div>
      </header>

      {comboLabel && <div className="gem-crush__combo">{comboLabel}</div>}

      <div className="gem-crush__board-wrap" data-combo={comboLabel ? '1' : undefined} data-ice={level > 2 ? '1' : undefined}>
        <div className="gem-crush__sparkles" aria-hidden="true" />
        <div className="gem-crush__score-pops" aria-hidden="true">
          {scorePops.map((p) => (
            <span key={p.id} className="gem-crush__score-pop">
              +{p.value}
            </span>
          ))}
        </div>
        <div className="gem-crush__board" role="grid" aria-label="Plateau match-3">
          {board.map((row, r) =>
            row.map((cell, c) => {
              const k = `${r},${c}`
              const isSelected = selected?.[0] === r && selected?.[1] === c
              const isFlash = flash.has(k)
              const fall = fallRows.get(cell.id)
              return (
                <button
                  key={cell.id}
                  type="button"
                  className={`${cellClass(r, c, isSelected, isFlash)} gem-crush__cell--sprite${fall ? ' gem-crush__cell--falling' : ''}`}
                  style={
                    fall
                      ? ({ '--fall-rows': fall } as React.CSSProperties)
                      : undefined
                  }
                  onClick={() => onCellClick(r, c)}
                  onTouchStart={(e) => onTouchStart(r, c, e)}
                  onTouchEnd={onTouchEnd}
                  aria-label={`Gemme ligne ${r + 1} colonne ${c + 1}`}
                >
                  <GemSprite gem={cell.gem} special={cell.special} />
                </button>
              )
            }),
          )}
        </div>
      </div>

      <div className="gem-crush__actions">
        <button
          type="button"
          className="gem-crush__btn gem-crush__btn--ghost"
          data-hint={hint ? '1' : undefined}
          onClick={showHintNow}
        >
          Indice
        </button>
      </div>

      {showTutorial && (
        <div className="gem-crush__tutorial gem-crush__tutorial--animated">
          <p className="gem-crush__tutorial-title">Bienvenue sur Gem Crush</p>
          <p>Échange deux gemmes voisines — atteins l&apos;objectif avant la fin des coups.</p>
          <p>Sur mobile : glisse pour échanger.</p>
          <button type="button" className="gem-crush__btn" onClick={dismissTutorial}>
            Jouer
          </button>
        </div>
      )}

      {(gameOver || won) && !showTutorial && (
        <div className="gem-crush__overlay">
          <p className="gem-crush__overlay-title">
            {won ? 'Saison ARIA — 3 étoiles débloquées' : 'Plus de coups'}
          </p>
          <p className="gem-crush__overlay-sub">
            {won
              ? 'Coffre débloqué — niveau suivant…'
              : `Score ${score.toLocaleString('fr-FR')} — retente ta chance !`}
          </p>
          {!won && (
            <button type="button" className="gem-crush__btn" onClick={restart}>
              Nouvelle tentative
            </button>
          )}
        </div>
      )}

      <p className="gem-crush__hint">
        Glisse ou touche · 3+ alignées · 4 = rayure · 5 = bombe
      </p>
      <p className="gem-crush__aria-credit">
        Aria Vanguard ZHC · Gem Crush · v{GEM_CRUSH_VERSION}
      </p>
    </div>
  )
}