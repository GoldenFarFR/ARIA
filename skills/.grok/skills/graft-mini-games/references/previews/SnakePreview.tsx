import { useEffect, useRef } from 'react'
import type { PreviewProps } from '../types'

const CELL = 8
const COLS = 18
const ROWS = 13

/** Serpent qui avance en boucle — preview uniquement si active */
export function SnakePreview({ active, className = '' }: PreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const frameRef = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let raf = 0
    const segments = [
      { x: 4, y: 6 },
      { x: 3, y: 6 },
      { x: 2, y: 6 },
      { x: 2, y: 5 },
      { x: 2, y: 4 },
    ]
    const apple = { x: 12, y: 4 }
    let dir = { x: 1, y: 0 }
    let tick = 0

    const draw = () => {
      if (!active) return
      tick += 1
      if (tick % 8 === 0) {
        const head = segments[0]
        const next = { x: head.x + dir.x, y: head.y + dir.y }
        if (next.x >= COLS - 1 || next.y <= 0 || next.y >= ROWS - 1) {
          dir = { x: -1, y: 0 }
        } else if (next.x <= 1) {
          dir = { x: 0, y: 1 }
        }
        const nh = { x: head.x + dir.x, y: head.y + dir.y }
        segments.unshift(nh)
        if (nh.x === apple.x && nh.y === apple.y) {
          apple.x = 10 + (frameRef.current % 5)
          apple.y = 3 + (frameRef.current % 4)
        } else {
          segments.pop()
        }
        frameRef.current += 1
      }

      ctx.fillStyle = '#0f0f14'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.strokeStyle = '#2a2a3a'
      ctx.strokeRect(CELL, CELL, (COLS - 2) * CELL, (ROWS - 2) * CELL)

      ctx.fillStyle = '#22c55e'
      ctx.beginPath()
      ctx.arc((apple.x + 0.5) * CELL, (apple.y + 0.5) * CELL, CELL * 0.35, 0, Math.PI * 2)
      ctx.fill()

      segments.forEach((s, i) => {
        ctx.fillStyle = i === 0 ? '#818cf8' : '#6366f1'
        ctx.fillRect(s.x * CELL, s.y * CELL, CELL - 1, CELL - 1)
      })

      raf = requestAnimationFrame(draw)
    }

    if (active) {
      raf = requestAnimationFrame(draw)
    } else {
      ctx.fillStyle = '#0f0f14'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
    }

    return () => cancelAnimationFrame(raf)
  }, [active])

  return (
    <canvas
      ref={canvasRef}
      width={COLS * CELL}
      height={ROWS * CELL}
      className={`block w-full h-full object-contain ${className}`}
      aria-hidden
    />
  )
}