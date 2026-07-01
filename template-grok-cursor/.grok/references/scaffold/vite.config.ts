import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const peerStub = fileURLToPath(new URL('./src/shims/privy-vite-peer.ts', import.meta.url))

/** Alias peer deps optionnelles Privy (sites sans wallet crypto). */
const privyPeerAliases: Record<string, string> = {
  '@solana/kit': peerStub,
  '@solana-program/system': peerStub,
  '@solana-program/token': peerStub,
  '@solana-program/token-2022': peerStub,
  '@solana-program/compute-budget': peerStub,
  '@solana/transaction-confirmation': peerStub,
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: privyPeerAliases },
  server: {
    port: 5175, // changer par site
  },
})