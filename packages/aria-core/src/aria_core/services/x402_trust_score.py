"""In-house x402 provider trust score (07/24) -- "build in-house" answer to
paying an x402 provider (e.g. x402.fuchss.app's Endpoint Trust Score,
$0.005/call, found in the Bazaar scan) for exactly this metric: on-chain USDC
settlement volume + distinct payer count for a given payTo address. That
provider's own computation is fully public-chain data ARIA can already read
for free.

Cascade doctrine (22/07, explicit operator decision -- "let's relieve
Blockscout as much as possible"): tries wallet_transfers_fast (Alchemy/
Moralis) FIRST on "base", only falls back to Blockscout's token-transfers
endpoint if both fast providers are unavailable -- Blockscout's
token-transfers alone already consumes 73.6% of the whole Pro credit budget
(cf. docs/HANDOFF_BLOCKSCOUT.md), so a new consumer of this same endpoint
must never bypass the existing relief valve. Same exact cascade shape as
smart_money.py's own scan loop.

USDC address reused from agent_wallet_cdp_adapter.USDC_BASE_ADDRESS -- never
a 3rd hardcoded copy in this codebase."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée indisponible"


def _parse_timestamp(raw: str | None) -> datetime | None:
    """Same tolerant ISO parsing as smart_money.py's own helper -- kept as a
    small local copy rather than importing a private function across
    modules."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class X402TrustScoreResult:
    available: bool = True
    pay_to_address: str = ""
    volume_usd: float = 0.0
    unique_payers: int = 0
    transfer_count: int = 0
    lookback_days: int = 30
    error: str | None = None


async def compute_x402_trust_score(
    pay_to_address: str, *, chain: str = "base", lookback_days: int = 30,
) -> X402TrustScoreResult:
    """Real on-chain USDC settlement volume + distinct payer count for a
    given x402 payTo address, over the last ``lookback_days`` -- the same
    signal a paid x402 "trust score" provider would sell, computed for free
    from data ARIA already has clients for.

    Dome doctrine: any failure (no data source available, RPC/API error)
    degrades to ``available=False``, never a fabricated score."""
    from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

    if not pay_to_address or not pay_to_address.strip():
        return X402TrustScoreResult(available=False, error=f"{UNAVAILABLE} (adresse manquante)")

    address = pay_to_address.strip().lower()
    usdc_address = USDC_BASE_ADDRESS.lower()

    transfers_result = None
    if chain == "base":
        from aria_core.services import wallet_transfers_fast

        fast_result = await wallet_transfers_fast.get_fast_token_transfers(
            address, chain, limit=2000, max_pages=10,
        )
        if fast_result.available:
            transfers_result = fast_result

    if transfers_result is None:
        from aria_core.services.blockscout import get_blockscout_client

        chain_client = get_blockscout_client(chain)
        transfers_result = await chain_client.get_token_transfers(
            address, limit=2000, max_pages=10, token_type="ERC-20",
        )

    if not transfers_result.available:
        return X402TrustScoreResult(
            available=False, pay_to_address=pay_to_address, error=transfers_result.error or UNAVAILABLE,
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    volume = 0.0
    payers: set[str] = set()
    transfer_count = 0
    for t in transfers_result.transfers:
        if not t.token_address or t.token_address.lower() != usdc_address:
            continue
        if not t.to_address or t.to_address.lower() != address:
            continue
        ts = _parse_timestamp(t.timestamp)
        if ts is not None and ts < cutoff:
            continue
        if t.amount is None:
            continue
        volume += t.amount
        transfer_count += 1
        if t.from_address:
            payers.add(t.from_address.lower())

    return X402TrustScoreResult(
        available=True, pay_to_address=pay_to_address, volume_usd=volume,
        unique_payers=len(payers), transfer_count=transfer_count, lookback_days=lookback_days,
    )
