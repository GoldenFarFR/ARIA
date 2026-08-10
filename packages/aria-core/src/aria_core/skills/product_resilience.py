"""Product-resilience signal -- does the token's real on-chain activity hold
up while the broader market is falling? (10/08, operator request: "capter sa
qualité alors que le marché decline est un sacré atout"). Same doctrine as
sell_distribution.py/bundle_launch.py: a JUDGED qualitative signal that FEEDS
ARIA's VC reasoning -- never a hard veto, never a mechanical delta on
TokenScanContext.security_score (see acp_onchain_scan.py's own doctrine,
lines ~575-581: "no new hard veto" for any signal added after the base score).

Activity proxy: transfer count + unique counterparties on the token's own
contract, RECENT window (7d) vs the PRIOR window (7-14d) -- both derived from
a single Blockscout call (services/blockscout.py::get_token_transfers_for_token),
no new historical storage needed (a token's transfer history is Blockscout's
own record, not something ARIA needs to journal itself).

Market proxy: BTC drawdown over the same 14-day window, reusing
market_sentiment._fetch_recent_closes + classify_sentiment (never a second,
diverging drawdown calculation) -- deliberately NOT market_sentiment.
resolve_meta_regime() (a single-row upsert of the CURRENT regime only, no
historical window available -- see that module's own "no expiration, every
heartbeat cycle recomputes and overwrites" doctrine)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Below this BTC drawdown, the market isn't meaningfully "declining" -- no
# resilience claim is meaningful without a real downturn to resist against.
# Same order of magnitude as market_sentiment.py's own topping/capitulation
# thresholds.
_MARKET_DECLINE_THRESHOLD_PCT = -15.0
# Activity is judged "holding" at or above this change -- never required to
# GROW, just not collapse alongside the market. A true collapse (a token
# nobody uses anymore) reads very differently from ordinary week-to-week
# noise on a small sample.
_ACTIVITY_HOLDING_FLOOR_PCT = -10.0
# Minimum transfers in the PRIOR window before a resilience claim is
# meaningful at all -- same spirit as sell_distribution's
# _MIN_SELLERS_FOR_JUDGMENT (a near-empty market has no real activity trend
# to measure).
_MIN_PRIOR_TRANSFERS_FOR_JUDGMENT = 10


@dataclass(frozen=True)
class ProductResilienceFacts:
    """On-chain activity facts for a token, recent vs prior 7-day windows,
    plus the market drawdown over the same 14-day span."""

    recent_transfer_count: int = 0
    prior_transfer_count: int = 0
    recent_unique_addresses: int = 0
    market_drawdown_pct: float | None = None
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ProductResilienceVerdict:
    signal: str  # resilient / neutral / declining / unknown
    points: list[str] = field(default_factory=list)


def judge_product_resilience(facts: ProductResilienceFacts) -> ProductResilienceVerdict:
    """Weighted judgment, never a hard cutoff -- same doctrine as
    judge_sell_distribution/judge_bundle_launch."""
    if not facts.available:
        return ProductResilienceVerdict(
            signal="unknown", points=[facts.error or "activité on-chain récente non analysable"],
        )
    if facts.prior_transfer_count < _MIN_PRIOR_TRANSFERS_FOR_JUDGMENT:
        return ProductResilienceVerdict(
            signal="unknown",
            points=[
                f"seulement {facts.prior_transfer_count} transfert(s) sur la fenêtre de "
                "référence -- échantillon trop faible pour juger"
            ],
        )
    if facts.market_drawdown_pct is None:
        return ProductResilienceVerdict(
            signal="unknown", points=["régime de marché de référence (BTC) indisponible"],
        )

    activity_change_pct = ((facts.recent_transfer_count / facts.prior_transfer_count) - 1.0) * 100.0
    market_declining = facts.market_drawdown_pct <= _MARKET_DECLINE_THRESHOLD_PCT
    activity_holding = activity_change_pct >= _ACTIVITY_HOLDING_FLOOR_PCT

    if market_declining and activity_holding:
        return ProductResilienceVerdict(
            signal="resilient",
            points=[
                f"activité on-chain du token {activity_change_pct:+.0f}% sur 7j "
                f"({facts.recent_transfer_count} vs {facts.prior_transfer_count} transferts, "
                f"{facts.recent_unique_addresses} adresses distinctes) alors que le marché "
                f"(BTC) recule de {facts.market_drawdown_pct:.0f}% -- traction réelle "
                "indépendante du cycle, signal RARE à peser fortement"
            ],
        )
    if market_declining and not activity_holding:
        return ProductResilienceVerdict(
            signal="declining",
            points=[
                f"activité on-chain du token {activity_change_pct:+.0f}% sur 7j alors que le "
                f"marché (BTC) recule déjà de {facts.market_drawdown_pct:.0f}% -- l'usage suit "
                "le marché à la baisse, pas de signe de traction indépendante"
            ],
        )
    return ProductResilienceVerdict(
        signal="neutral",
        points=[
            f"marché stable/haussier (BTC {facts.market_drawdown_pct:+.0f}%) -- pas de contexte "
            "baissier pour juger une vraie résilience produit cette fois-ci"
        ],
    )


async def gather_product_resilience_facts(
    contract: str, *, blockscout_client=None, market_sentiment_module=None,
) -> ProductResilienceFacts:
    """Best-effort collection (defensive, same shape as gather_sell_distribution_facts).
    ``blockscout_client``/``market_sentiment_module`` are injectable for tests only."""
    if blockscout_client is None:
        from aria_core.services.blockscout import blockscout_client as _default_client

        blockscout_client = _default_client
    if market_sentiment_module is None:
        from aria_core.skills import market_sentiment as _default_module

        market_sentiment_module = _default_module

    try:
        result = await blockscout_client.get_token_transfers_for_token(contract, limit=500, max_pages=10)
    except Exception as exc:  # noqa: BLE001 -- never blocking
        return ProductResilienceFacts(available=False, error=f"Blockscout indisponible ({exc})")

    if not result.available:
        return ProductResilienceFacts(available=False, error=result.error or "Blockscout indisponible")
    if not result.transfers:
        return ProductResilienceFacts(available=False, error="aucun transfert récent trouvé")

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=7)
    prior_cutoff = now - timedelta(days=14)

    recent_count = 0
    prior_count = 0
    recent_addresses: set[str] = set()

    for transfer in result.transfers:
        if not transfer.timestamp:
            continue
        try:
            ts = datetime.fromisoformat(transfer.timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= recent_cutoff:
            recent_count += 1
            recent_addresses.add(transfer.from_address)
            recent_addresses.add(transfer.to_address)
        elif ts >= prior_cutoff:
            prior_count += 1

    drawdown: float | None = None
    try:
        closes = await market_sentiment_module._fetch_recent_closes("bitcoin", days=14)
        if closes:
            reading = market_sentiment_module.classify_sentiment(closes)
            drawdown = reading.drawdown_from_high_pct
    except Exception:  # noqa: BLE001 -- never blocking, market context stays optional
        drawdown = None

    return ProductResilienceFacts(
        recent_transfer_count=recent_count,
        prior_transfer_count=prior_count,
        recent_unique_addresses=len(recent_addresses),
        market_drawdown_pct=drawdown,
        available=True,
    )
