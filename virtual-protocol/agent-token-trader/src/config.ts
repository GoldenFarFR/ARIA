import "dotenv/config";

export interface RuntimeConfig {
  apiBaseUrl: string;
  adminSecret: string;
  maxTradeUsdc: number;
  maxSlippageBps: number;
  pollIntervalMs: number;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Variable d'environnement manquante : ${name}`);
  }
  return value;
}

export function loadConfig(): RuntimeConfig {
  return {
    apiBaseUrl: requireEnv("ARIA_API_BASE_URL"),
    adminSecret: requireEnv("ARIA_ADMIN_SECRET"),
    maxTradeUsdc: Number(process.env.ARIA_AGENT_TOKEN_MAX_TRADE_USDC ?? "10"),
    // 1000 bps = 10% — plafond absolu, jamais dépassé même si ARIA_AGENT_TOKEN_MAX_SLIPPAGE_BPS
    // est mal configuré à une valeur plus haute (cf. règle CLAUDE.md issue de la faille HL Perps).
    maxSlippageBps: Math.min(
      Number(process.env.ARIA_AGENT_TOKEN_MAX_SLIPPAGE_BPS ?? "500"),
      1000,
    ),
    pollIntervalMs: Number(process.env.ARIA_AGENT_TOKEN_POLL_INTERVAL_MS ?? "60000"),
  };
}

/**
 * Kill-switch : relu DEPUIS L'ENVIRONNEMENT à chaque appel, jamais mis en cache.
 * Couper `ARIA_AGENT_TOKEN_TRADER_ENABLED` doit interrompre le prochain cycle du
 * poller sans redémarrage du process — un flag lu une seule fois au démarrage
 * n'est pas un vrai kill-switch.
 */
export function isTraderEnabled(): boolean {
  return (process.env.ARIA_AGENT_TOKEN_TRADER_ENABLED ?? "false").toLowerCase() === "true";
}
