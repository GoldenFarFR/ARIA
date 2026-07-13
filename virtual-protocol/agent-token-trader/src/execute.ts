import type { FreshQuote } from "./quote.js";

/**
 * Surface des 5 fonctions exposées par la librairie npm `bondv5-trader`
 * (github:Virtual-Protocol/bondv5-trader). Aucun wrapper officiel n'existe — cette
 * interface documente le contrat attendu, à ajuster une fois la lib réellement
 * installée si sa signature diverge.
 */
export interface BondV5Trader {
  balanceOf(wallet: string, token: string): Promise<bigint>;
  ensureApproval(wallet: string, token: string, spender: string, amountWei: bigint): Promise<void>;
  usdcToVirtualSwap(wallet: string, amountInWei: bigint, minOutWei: bigint): Promise<{ txHash: string }>;
  virtualToUsdcSwap(wallet: string, amountInWei: bigint, minOutWei: bigint): Promise<{ txHash: string }>;
  bondingV5Trade(
    wallet: string,
    contract: string,
    amountInWei: bigint,
    minOutWei: bigint,
    isBuy: boolean,
  ): Promise<{ txHash: string }>;
}

/**
 * Seul point d'entrée d'exécution : refuse tout `minOutWei` qui ne vienne pas d'un
 * `FreshQuote` calculé juste avant l'appel (corrige la faille `minOutWei=1` par
 * défaut de la librairie — cf. quote.ts). N'accepte jamais un minOutWei littéral.
 */
export async function executeBondingTrade(
  trader: BondV5Trader,
  wallet: string,
  contract: string,
  quote: FreshQuote,
  isBuy: boolean,
): Promise<{ txHash: string }> {
  if (quote.minOutWei <= 0n) {
    throw new Error("minOutWei calculé <= 0 — devis invalide, trade refusé");
  }
  return trader.bondingV5Trade(wallet, contract, quote.amountIn, quote.minOutWei, isBuy);
}
