"""Recurring extraction of Blockscout Pro holders (x402) -- coordinates the
growth of ``token_holder_intel`` (147 tokens as of 21/07, extracted in a
manual one-shot) with smart-money candidate discovery
(``smart_money_leaderboard.discover_and_enqueue_candidates``, which reads the
SAME table). Answers an operator request (21/07): "the token scans / the
number of holders absorbed and to process toward smart money need to be
coordinated".

Depth per market-cap tier (explicit operator decision, 21/07): top 500
holders for >=1000M$ mcap, top 300 for >=500M$, top 200 for >=100M$, top 100
for everything else (including unknown mcap -- never inventing a higher tier
for lack of data). Reuses ``coingecko.coingecko_client`` (already built) for
the market cap, ``blockscout_x402.get_token_holders_x402_paginated`` (already
built, one payment per page of 50) for the extraction itself.

Bounded real cost: a low ``MAX_TOKENS_PER_CYCLE`` + the SHARED weekly cap
(``x402_budget.py``, 5$/week, already fail-closed) bound the worst case --
no extra dedicated cap here, consistent with the rest of the x402 consumers
(twit.sh/cybercentry/ottoai).

Token selection (21/07, replaces the old ``screened_token`` source -- see
``token_candidate_screening.screen_and_select_candidates``): continuous
DexScreener/GeckoTerminal discovery (``momentum_entry.
discover_momentum_candidates``), filtered by GoPlus honeypot + liquidity
≥50,000$ + 24h volume ≥1,000$, deduplicated against ``token_holder_intel``
(never recounted) and against a dedicated permanent blacklist (confirmed
honeypot -> never retested). HONEST LIMITATION: re-extracting already-covered
tokens (staleness -- holders change over time) is NOT built here -- with
>1300 tokens never yet touched as of 21/07, that leaves a wide margin before
this becomes relevant."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Sobriety -- bulk extraction costs real money (x402), unlike the other
# smart-money discovery/scoring cycles. Low by default, see
# MAX_WALLETS_PER_CYCLE=1 (wallet_scan_queue.py) as a precedent of caution.
MAX_TOKENS_PER_CYCLE = 2

# Depth tiers by market cap (operator decision, 21/07) -- descending order,
# the first threshold crossed wins. Unknown market cap (CoinGecko
# unavailable/token not listed) -> never a made-up higher tier, falls back
# to the floor (100).
_TIERS = (
    (1_000_000_000.0, 500),
    (500_000_000.0, 300),
    (100_000_000.0, 200),
)
_DEFAULT_TARGET_COUNT = 100


def token_holder_extraction_enabled() -> bool:
    return os.environ.get("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def target_holder_count(market_cap_usd: float | None) -> int:
    if market_cap_usd is None:
        return _DEFAULT_TARGET_COUNT
    for threshold, count in _TIERS:
        if market_cap_usd >= threshold:
            return count
    return _DEFAULT_TARGET_COUNT


async def run_token_holder_extraction_cycle(notifier=None) -> dict:
    """One pass: selects up to ``MAX_TOKENS_PER_CYCLE`` tokens never yet
    extracted, determines their target depth via market cap (CoinGecko,
    free), extracts their holders (Blockscout x402, paid, paginated) and
    stores them -- smart-money discovery (separate cycle,
    ``smart_money_leaderboard_discovery_cycle``) will then read this same
    table on its next pass, no explicit coordination needed beyond sharing
    the same table."""
    if not token_holder_extraction_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    from aria_core.token_candidate_screening import screen_and_select_candidates

    candidates = await screen_and_select_candidates(MAX_TOKENS_PER_CYCLE)
    if not candidates:
        return {"outcome": "no_candidate"}

    from aria_core import token_holder_intel
    from aria_core.services import blockscout_x402
    from aria_core.services.coingecko import coingecko_client

    processed: list[dict] = []
    for contract, symbol in candidates:
        fundamentals = await coingecko_client.get_token_fundamentals(contract, platform_id="base")
        market_cap = fundamentals.market_cap_usd if fundamentals.available else None
        target = target_holder_count(market_cap)
        try:
            holders = await blockscout_x402.get_token_holders_x402_paginated(
                contract, chain="base", target_count=target, token_symbol=symbol,
            )
        except Exception as exc:  # noqa: BLE001 -- never blocking, move to next token
            logger.warning("token_holder_extraction: failed for %s (%s)", contract, exc)
            holders = []
        written = await token_holder_intel.store_holders(contract, "base", holders) if holders else 0
        processed.append({
            "contract": contract, "symbol": symbol,
            "market_cap_usd": market_cap, "target_count": target, "holders_stored": written,
        })

    total_stored = sum(p["holders_stored"] for p in processed)
    if notifier is not None and total_stored:
        detail = ", ".join(
            f"{p['symbol'] or p['contract'][:10]} ({p['holders_stored']}/{p['target_count']})"
            for p in processed
        )
        await notifier(
            f"🧬 Extraction holders -- {total_stored} holder(s) stocké(s) sur "
            f"{len(processed)} token(s) : {detail}."
        )

    return {"outcome": "ok", "tokens_processed": processed}
