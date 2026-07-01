import { PrivyProvider } from '@privy-io/react-auth'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { PRIVY_APP_ID, privyProviderConfig } from './lib/privy-config'

const root = createRoot(document.getElementById('root')!)

if (!PRIVY_APP_ID) {
  root.render(
    <div className="min-h-screen site-mesh flex items-center justify-center p-4">
      <p className="text-sm text-neutral-500">VITE_PRIVY_APP_ID is not configured.</p>
    </div>,
  )
} else {
  root.render(
    <PrivyProvider appId={PRIVY_APP_ID} config={privyProviderConfig}>
      <App />
    </PrivyProvider>,
  )
}