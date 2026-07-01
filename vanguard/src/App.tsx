import { ErrorBoundary } from './components/ErrorBoundary'
import { MemberGate } from './components/MemberGate'
import { VanguardSite } from './pages/VanguardSite'

export default function App() {
  return (
    <ErrorBoundary>
      <MemberGate>
        <VanguardSite />
      </MemberGate>
    </ErrorBoundary>
  )
}