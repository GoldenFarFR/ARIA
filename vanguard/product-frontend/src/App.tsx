import { lazy, Suspense } from 'react'
import { MemberGate } from './components/MemberGate'

const MarketApp = lazy(() =>
  import('./pages/MarketApp').then((m) => ({ default: m.MarketApp })),
)
const OperatorDashboard = lazy(() =>
  import('./pages/OperatorDashboard').then((m) => ({ default: m.OperatorDashboard })),
)

const spinner = (
  <div className="min-h-screen pixel-canvas flex items-center justify-center">
    <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
  </div>
)

function App() {
  // 06/08 -- operator-only trading dashboard: deliberately OUTSIDE MemberGate
  // (Privy member auth grants zero operator rights). Its own screen gates on
  // the operator TOTP login (see OperatorDashboard/operator-auth.ts) before
  // showing anything.
  if (window.location.pathname.startsWith('/ops')) {
    return (
      <Suspense fallback={spinner}>
        <OperatorDashboard />
      </Suspense>
    )
  }

  return (
    <MemberGate>
      <Suspense fallback={spinner}>
        <MarketApp />
      </Suspense>
    </MemberGate>
  )
}

export default App