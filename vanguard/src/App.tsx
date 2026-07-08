import { ErrorBoundary } from './components/ErrorBoundary'
import { MemberGate } from './components/MemberGate'
import { VanguardSite } from './pages/VanguardSite'
import { ClientSite } from './pages/ClientSite'
import { CockpitPage } from './pages/CockpitPage'

export default function App() {
  // Routage minimal par chemin (pas de dépendance routeur) :
  //  /reports → page produit publique (rapports d'analyse), ouverte à tous
  //  /cockpit → centre de commandement (pouls public + dossier gaté opérateur)
  //  /        → vitrine ZHC (gated pour les zones membres)
  const path = typeof window !== 'undefined' ? window.location.pathname : '/'
  if (path === '/reports' || path.startsWith('/reports/')) {
    return (
      <ErrorBoundary>
        <ClientSite />
      </ErrorBoundary>
    )
  }
  if (path === '/cockpit' || path.startsWith('/cockpit/')) {
    return (
      <ErrorBoundary>
        <CockpitPage />
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