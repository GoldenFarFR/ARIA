import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { importVanguardSession, listenForVanguardSessionPush } from './lib/session-handoff'

importVanguardSession()
listenForVanguardSessionPush()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)