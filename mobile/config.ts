// Real production domain (api.ariavanguardzhc.com, separate from the vitrine's
// own domain) -- confirmed live against /api/aria/ops/version before writing this.
export const API_BASE_URL = "https://api.ariavanguardzhc.com";

// Kept independent of backend_version (see operator_mobile.py) -- bumped only
// when this app's own request/response contract changes.
export const MOBILE_API_VERSION = 1;

// 08/07 -- Privy auth redesign. Same PRIVY_APP_ID as the web member site
// (vanguard/src/lib/privy-config.ts, same Privy dashboard project) -- no
// reason to split into a second Privy app just for this channel. clientId is
// DIFFERENT: a mobile app needs its own Privy "client" registered in the
// dashboard (Settings -> Clients -> New client, platform "Expo"/React
// Native) with the app's URL scheme (see app.json's "scheme") declared as an
// allowed redirect target for OAuth to work.
//
// OPERATOR ACTION REQUIRED before the next build: set both via
// `eas secret:create` (or eas.json env per profile) --
//   EXPO_PUBLIC_PRIVY_APP_ID       (same value as VITE_PRIVY_APP_ID)
//   EXPO_PUBLIC_PRIVY_CLIENT_ID    (new, from the dashboard step above)
export const PRIVY_APP_ID = process.env.EXPO_PUBLIC_PRIVY_APP_ID ?? "";
export const PRIVY_CLIENT_ID = process.env.EXPO_PUBLIC_PRIVY_CLIENT_ID ?? "";
