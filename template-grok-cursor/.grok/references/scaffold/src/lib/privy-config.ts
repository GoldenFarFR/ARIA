export const PRIVY_APP_ID = (import.meta.env.VITE_PRIVY_APP_ID ?? '').trim()

export const PRIVY_LOGIN_METHODS = ['email', 'twitter', 'discord'] as const

export const privyProviderConfig = {
  loginMethods: [...PRIVY_LOGIN_METHODS],
  appearance: {
    theme: 'dark' as const,
    accentColor: '#6366f1' as `#${string}`,
    showWalletLoginFirst: false,
    landingHeader: 'Sign in to __SITE_NAME__',
    loginMessage: 'Use email or social login.',
  },
  embeddedWallets: {
    ethereum: { createOnLogin: 'off' as const },
  },
}