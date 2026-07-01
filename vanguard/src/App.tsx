import { ErrorBoundary } from './components/ErrorBoundary'
import { VanguardSite } from './pages/VanguardSite'

export default function App() {
  return (
    <ErrorBoundary>
      <VanguardSite />
    </ErrorBoundary>
  )
}