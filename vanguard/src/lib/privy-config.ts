/** Privy dashboard: https://dashboard.privy.io — see aria-vanguard/operator/README.md */
export const PRIVY_APP_ID = (import.meta.env.VITE_PRIVY_APP_ID ?? '').trim()

// Google requiert l'activation du provider Google OAuth dans le tableau de bord Privy
// (Authentication > Login methods > Google) avec les identifiants OAuth Google. Le code
// ci-dessous ajoute juste le bouton ; sans l'activation dashboard, Privy l'ignore.
export const PRIVY_LOGIN_METHODS = ['email', 'google', 'twitter', 'discord'] as const

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