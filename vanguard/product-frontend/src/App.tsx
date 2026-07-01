import { lazy, Suspense } from 'react'
import { MemberGate } from './components/MemberGate'

const MarketApp = lazy(() =>
  import('./pages/MarketApp').then((m) => ({ default: m.MarketApp })),
)

function App() {
  return (
    <MemberGate>
      <Suspense
        fallback={
          <div className="min-h-screen pixel-canvas flex items-center justify-center">
            <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
          </div>
        }
      >
        <MarketApp />
      </Suspense>
    </MemberGate>
  )
}

export default App