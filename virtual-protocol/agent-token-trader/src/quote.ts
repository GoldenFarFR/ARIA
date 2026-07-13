/**
 * Calcul de devis frais + garde-fou anti-slippage — corrige la faille confirmée
 * `bondv5-trader` (`minOutWei` par défaut = 1, aucune protection réelle sauf calcul
 * explicite ici). Aucun chemin de ce module n'autorise un `minOutWei` non dérivé
 * d'un devis calculé immédiatement avant l'appel.
 */

export interface CurveReserves {
  reserveIn: bigint;
  reserveOut: bigint;
}

/** Constant-product AMM (x*y=k) : sortie brute attendue avant tolérance de slippage. */
export function expectedAmountOut(amountIn: bigint, reserves: CurveReserves): bigint {
  if (amountIn <= 0n) {
    throw new Error("amountIn doit être positif");
  }
  const { reserveIn, reserveOut } = reserves;
  if (reserveIn <= 0n || reserveOut <= 0n) {
    throw new Error("Réserves de courbe invalides — devis impossible");
  }
  return (reserveOut * amountIn) / (reserveIn + amountIn);
}

/**
 * `minOutWei` = sortie attendue réduite par la tolérance de slippage (en points de
 * base). Plafonné en amont à 1000 bps (10%) par `config.maxSlippageBps` — cette
 * fonction applique juste la tolérance fournie, elle ne la valide pas elle-même.
 */
export function computeMinOutWei(amountIn: bigint, reserves: CurveReserves, slippageBps: number): bigint {
  const expected = expectedAmountOut(amountIn, reserves);
  const bps = BigInt(Math.round(slippageBps));
  return (expected * (10_000n - bps)) / 10_000n;
}

export interface FreshQuote {
  amountIn: bigint;
  expectedOut: bigint;
  minOutWei: bigint;
  slippageBps: number;
}

/**
 * Fournisseur de réserves de courbe branché sur la lecture on-chain réelle (RPC Base,
 * adresse du contrat bonding). Injecté plutôt que codé en dur ici : ce module ne doit
 * jamais fabriquer un devis sans lecture on-chain fraîche.
 */
export type CurveReserveProvider = (contract: string) => Promise<CurveReserves>;

export async function getFreshQuote(
  contract: string,
  amountIn: bigint,
  slippageBps: number,
  readReserves: CurveReserveProvider,
  maxSlippageBps: number,
): Promise<FreshQuote> {
  if (slippageBps > maxSlippageBps) {
    throw new Error(
      `slippage demandé ${slippageBps}bps > plafond absolu ${maxSlippageBps}bps — trade refusé`,
    );
  }
  const reserves = await readReserves(contract);
  const expectedOut = expectedAmountOut(amountIn, reserves);
  const minOutWei = computeMinOutWei(amountIn, reserves, slippageBps);
  return { amountIn, expectedOut, minOutWei, slippageBps };
}
