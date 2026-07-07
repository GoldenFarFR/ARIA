import { ErrorBoundary } from './components/ErrorBoundary'
import { MemberGate } from './components/MemberGate'
import { VanguardSite } from './pages/VanguardSite'
import { ClientSite } from './pages/ClientSite'

export default function App() {
  // Routage minimal par chemin (pas de dépendance routeur) :
  //  /reports → page produit publique (rapports d'analyse), ouverte à tous
  //  /        → vitrine ZHC (gated pour les zones membres)
  const path = typeof window !== 'undefined' ? window.location.pathname : '/'
  if (path === '/reports' || path.startsWith('/reports/')) {
    return (
      <ErrorBoundary>
        <ClientSite />
      </ErrorBoundary>
    )
  }
  return (
    <ErrorBoundary>
      <MemberGate>
        <VanguardSite />
      </MemberGate>
    </ErrorBoundary>
  )
}