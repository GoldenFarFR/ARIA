import { useEffect, useRef } from 'react'
import type { PreviewProps } from '../types'

export function PongPreview({ active, className = '' }: PreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let raf = 0
    let ball = { x: 80, y: 60, vx: 2.2, vy: 1.4 }
    const paddleH = 28
    let leftY = 44
    let rightY = 52

    const draw = () => {
      if (!active) return

      ball.x += ball.vx
      ball.y += ball.vy
      if (ball.y < 10 || ball.y > 110) ball.vy *= -1
      if (ball.x < 22) {
        ball.vx = Math.abs(ball.vx)
        leftY = Math.max(10, Math.min(82, ball.y - paddleH / 2))
      }
      if (ball.x > 138) {
        ball.vx = -Math.abs(ball.vx)
        rightY = Math.max(10, Math.min(82, ball.y - paddleH / 2))
      }

      ctx.fillStyle = '#0f0f14'
      ctx.fillRect(0, 0, 160, 120)
      ctx.setLineDash([4, 6])
      ctx.strokeStyle = '#2a2a3a'
      ctx.beginPath()
      ctx.moveTo(80, 8)
      ctx.lineTo(80, 112)
      ctx.stroke()
      ctx.setLineDash([])

      ctx.fillStyle = '#e5e5e5'
      ctx.fillRect(12, leftY, 6, paddleH)
      ctx.fillRect(142, rightY, 6, paddleH)
      ctx.fillStyle = '#fbbf24'
      ctx.beginPath()
      ctx.arc(ball.x, ball.y, 6, 0, Math.PI * 2)
      ctx.fill()

      raf = requestAnimationFrame(draw)
    }

    if (active) raf = requestAnimationFrame(draw)
    else {
      ctx.fillStyle = '#0f0f14'
      ctx.fillRect(0, 0, 160, 120)
    }

    return () => cancelAnimationFrame(raf)
  }, [active])

  return (
    <canvas
      ref={canvasRef}
      width={160}
      height={120}
      className={`block w-full h-full object-contain ${className}`}
      aria-hidden
    />
  )
}