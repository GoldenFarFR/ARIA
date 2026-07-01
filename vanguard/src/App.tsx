import { ProductFrame } from './components/ProductFrame'
import { ProductLaunchHint } from './components/ProductLaunchHint'
import { ErrorBoundary } from './components/ErrorBoundary'
import { MemberGate } from './components/MemberGate'
import { MemberWelcome } from './components/MemberWelcome'
import { VanguardSite } from './pages/VanguardSite'

export default function App() {
  return (
    <ErrorBoundary>
      <MemberGate>
        <VanguardSite />
        <MemberWelcome />
        <ProductLaunchHint />
        <ProductFrame />
      </MemberGate>
    </ErrorBoundary>
  )
}