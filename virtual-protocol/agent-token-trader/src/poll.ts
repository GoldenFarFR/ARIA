import { loadConfig, isTraderEnabled } from "./config.js";
import { PoolClient } from "./poolClient.js";
import { getFreshQuote, type CurveReserveProvider } from "./quote.js";
import { executeBondingTrade, type BondV5Trader } from "./execute.js";

/**
 * Boucle principale. `isTraderEnabled()` est relu à CHAQUE itération — jamais une
 * seule fois au démarrage — pour que couper `ARIA_AGENT_TOKEN_TRADER_ENABLED`
 * interrompe le prochain cycle sans redémarrer le process.
 */
export async function runPollLoop(
  trader: BondV5Trader,
  wallet: string,
  readReserves: CurveReserveProvider,
): Promise<void> {
  const config = loadConfig();
  const pool = new PoolClient(config);

  // eslint-disable-next-line no-constant-condition
  while (true) {
    if (!isTraderEnabled()) {
      await sleep(config.pollIntervalMs);
      continue;
    }

    const candidates = await pool.fetchBondingCandidates("active");
    for (const candidate of candidates) {
      // Kill-switch revérifié entre chaque candidat, pas seulement au début du cycle :
      // une coupure en cours de traitement d'un lot doit arrêter les trades restants.
      if (!isTraderEnabled()) {
        break;
      }
      await tryExecute(trader, wallet, candidate.contract, candidate.symbol, config, pool, readReserves);
    }

    await sleep(config.pollIntervalMs);
  }
}

async function tryExecute(
  trader: BondV5Trader,
  wallet: string,
  contract: string,
  symbol: string,
  config: ReturnType<typeof loadConfig>,
  pool: PoolClient,
  readReserves: CurveReserveProvider,
): Promise<void> {
  const amountInWei = usdcToWei(config.maxTradeUsdc);
  try {
    const quote = await getFreshQuote(
      contract,
      amountInWei,
      config.maxSlippageBps,
      readReserves,
      config.maxSlippageBps,
    );
    const result = await executeBondingTrade(trader, wallet, contract, quote, true);
    await pool.logTrade({
      contract,
      symbol,
      side: "buy",
      amount_usdc: config.maxTradeUsdc,
      min_out_wei: quote.minOutWei.toString(),
      slippage_bps: quote.slippageBps,
      tx_hash: result.txHash,
      status: "ok",
    });
  } catch (err) {
    await pool.logTrade({
      contract,
      symbol,
      side: "buy",
      amount_usdc: config.maxTradeUsdc,
      status: "blocked",
      reason: err instanceof Error ? err.message : String(err),
    });
  }
}

function usdcToWei(amountUsdc: number): bigint {
  return BigInt(Math.round(amountUsdc * 1_000_000)); // USDC = 6 décimales
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
