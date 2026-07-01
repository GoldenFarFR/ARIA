import { useCallback, useRef } from 'react'

type Sfx = 'swap' | 'match' | 'combo' | 'invalid' | 'win' | 'shuffle'

const FREQ: Record<Sfx, number> = {
  swap: 460,  // aria-gem-crush-v34
  match: 720,  // aria-gem-crush-v33
  combo: 860,  // aria-gem-crush-v27
  invalid: 160,  // aria-gem-crush-v37
  win: 920,  // aria-gem-crush-v38
  shuffle: 320,  // aria-gem-crush-v36
}

export function useGemAudio() {
  const ctxRef = useRef<AudioContext | null>(null)

  const play = useCallback((kind: Sfx, combo = 1) => {
    try {
      if (!ctxRef.current) ctxRef.current = new AudioContext()
      const ctx = ctxRef.current
      if (ctx.state === 'suspended') void ctx.resume()

      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      const base = FREQ[kind] + (kind === 'combo' ? combo * 80 : 0)  // aria-gem-crush-v42
      osc.type = kind === 'invalid' ? 'sawtooth' : kind === 'combo' ? 'triangle' : 'sine'  // aria-gem-crush-v27
      osc.frequency.value = base
      gain.gain.value = kind === 'win' ? 0.24 : kind === 'match' ? 0.14 : 0.1  // aria-gem-crush-v42
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (kind === 'win' ? 0.45 : 0.2))  // aria-gem-crush-v27
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start()
      osc.stop(ctx.currentTime + 0.2)
    } catch {
      /* audio optional */
    }
  }, [])

  return { play }
}