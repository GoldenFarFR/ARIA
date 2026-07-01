import { useEffect, useState } from 'react'

const HINT_KEY = 'aria:show-product-hint'

export function ProductLaunchHint() {
  const [show, setShow] = useState(false)

  useEffect(() => {
    try {
      if (sessionStorage.getItem(HINT_KEY) === '1') {
        setShow(true)
        sessionStorage.removeItem(HINT_KEY)
      }
    } catch {
      /* ignore */
    }
  }, [])

  if (!show) return null

  return (
    <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-6 md:max-w-sm z-50 glass-vanguard rounded-sm p-4 border border-[#c9a962]/25 shadow-lg">
      <p className="text-sm text-[#e8d5a8] leading-relaxed">
        Connecte-toi (Sign in), puis clique <strong className="text-[#f4efe6]">Open Aria Market</strong> dans la
        navigation.
      </p>
      <button
        type="button"
        onClick={() => setShow(false)}
        className="mt-3 text-xs uppercase tracking-widest text-[#8a7344] hover:text-[#c9a962]"
      >
        OK
      </button>
    </div>
  )
}