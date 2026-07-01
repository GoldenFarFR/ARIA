import { useEffect, useState } from 'react'

/** True on phones/tablets where keyboard is usually unavailable. */
export function useTouchControls(): boolean {
  const [touch, setTouch] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia('(hover: none), (pointer: coarse)')
    const update = () => setTouch(mq.matches)
    update()
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])

  return touch
}