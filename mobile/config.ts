// Real production domain (api.ariavanguardzhc.com, separate from the vitrine's
// own domain) -- confirmed live against /api/aria/ops/version before writing this.
export const API_BASE_URL = "https://api.ariavanguardzhc.com";

// Kept independent of backend_version (see operator_mobile.py) -- bumped only
// when this app's own request/response contract changes.
export const MOBILE_API_VERSION = 1;
