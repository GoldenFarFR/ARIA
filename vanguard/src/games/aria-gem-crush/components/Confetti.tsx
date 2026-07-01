import { useEffect, useRef } from 'react'

const COLORS = ['#c9a962', '#e8d5a8', '#ff8a9a', '#7ec8ff', '#7dffb0', '#d4a8ff']

export function Confetti({ active }: { active: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!active) return
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const w = (canvas.width = canvas.offsetWidth * 2)
    const h = (canvas.height = canvas.offsetHeight * 2)
    ctx.scale(2, 2)
    const cw = w / 2
    const ch = h / 2

    const pieces = Array.from({ length: 48 }, () => ({
      x: Math.random() * cw,
      y: -10 - Math.random() * 40,
      vx: (Math.random() - 0.5) * 4,
      vy: 2 + Math.random() * 3,
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.2,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      size: 4 + Math.random() * 5,
    }))

    let frame = 0
    let raf = 0
    const draw = () => {
      ctx.clearRect(0, 0, cw, ch)
      for (const p of pieces) {
        p.x += p.vx
        p.y += p.vy
        p.vy += 0.06
        p.rot += p.vr
        ctx.save()
        ctx.translate(p.x, p.y)
        ctx.rotate(p.rot)
        ctx.fillStyle = p.color
        ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6)
        ctx.restore()
      }
      frame += 1
      if (frame < 120) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [active])

  if (!active) return null
  return <canvas ref={ref} className="gem-crush__confetti" aria-hidden="true" />
}