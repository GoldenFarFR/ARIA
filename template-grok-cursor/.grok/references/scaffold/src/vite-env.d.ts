/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEXPULSE_API_URL: string
  readonly VITE_PRIVY_APP_ID: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}