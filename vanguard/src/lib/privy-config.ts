/** Privy dashboard: https://dashboard.privy.io — see aria-vanguard/operator/README.md */
export const PRIVY_APP_ID = (import.meta.env.VITE_PRIVY_APP_ID ?? '').trim()

export const PRIVY_LOGIN_METHODS = ['email', 'twitter', 'discord'] as const

export const privyProviderConfig = {
  loginMethods: [...PRIVY_LOGIN_METHODS],
  appearance: {
    theme: 'dark' as const,
    accentColor: '#c9a962' as `#${string}`,
    showWalletLoginFirst: false,
    landingHeader: 'Sign in to ZHC Institute',
    loginMessage: 'Use email or social login. Link X to access member tools.',
  },
  embeddedWallets: {
    ethereum: { createOnLogin: 'off' as const },
  },
}