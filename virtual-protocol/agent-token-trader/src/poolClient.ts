import type { RuntimeConfig } from "./config.js";

export interface BondingCandidate {
  contract: string;
  symbol: string;
  verdict: string;
  pool_address: string;
  network: string;
  status: string;
  screen_reason: string;
  retry_count: number;
  source: string;
}

export interface TradeLogEntry {
  contract: string;
  symbol?: string;
  side: "buy" | "sell";
  amount_usdc?: number;
  amount_token?: number;
  min_out_wei?: string;
  slippage_bps?: number;
  tx_hash?: string;
  status: "ok" | "failed" | "blocked";
  reason?: string;
}

/**
 * Seul point d'accès du process TypeScript aux candidats screenés : HTTP vers
 * aria-core/vanguard, jamais de lecture directe de `aria.db` (SQLite) depuis ce
 * process — deux langages ne doivent jamais taper le même fichier en continu.
 */
export class PoolClient {
  constructor(private readonly config: RuntimeConfig) {}

  async fetchBondingCandidates(status: string = "active"): Promise<BondingCandidate[]> {
    const url = new URL("/api/aria/bonding-pool", this.config.apiBaseUrl);
    url.searchParams.set("status", status);
    const res = await fetch(url, {
      headers: { "X-Admin-Secret": this.config.adminSecret },
    });
    if (!res.ok) {
      throw new Error(`bonding-pool HTTP ${res.status}`);
    }
    const body = (await res.json()) as { items: BondingCandidate[] };
    return body.items;
  }

  async logTrade(entry: TradeLogEntry): Promise<void> {
    const url = new URL("/api/aria/bonding-pool/trade-log", this.config.apiBaseUrl);
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "X-Admin-Secret": this.config.adminSecret,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(entry),
    });
    if (!res.ok) {
      throw new Error(`bonding-pool/trade-log HTTP ${res.status}`);
    }
  }
}
