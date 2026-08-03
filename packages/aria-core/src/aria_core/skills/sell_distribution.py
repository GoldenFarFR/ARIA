"""Sell-concentration signal -- distinguishes a distributed community taking
profit from one whale dumping (03/08, real due-diligence session, C-MEM
case). A token falling off a price peak looks identical from mcap/liquidity
alone in both scenarios; the difference only shows up in WHO sold and how
concentrated that selling was.

Detected via `services/dune.py::get_token_sell_distribution` (the `dex.trades`
SELL side, `token_sold_address` -- mirror of the BUY-side query used by
`get_token_early_buyers`). Pure, deterministic JUDGE, same doctrine as
`insider_wallets.py`/`sybil_cluster.py`: produces a weighted signal
(concern/neutral/unknown) that FEEDS ARIA's reasoning -- never an automatic
rejection."""
from __future__ import annotations

from dataclasses import dataclass, field

# Share of the total sold volume held by the single largest seller, above
# which selling reads as "one actor dumping" rather than a distributed
# community taking profit. Rough calibration from the C-MEM case (100 sellers
# examined, largest realized PNL ~$1,091 out of a long tail down to cents --
# no single wallet anywhere near dominant) -- to adjust with more real cases.
_DOMINANT_SELLER_SHARE = 0.40
# Minimum number of distinct sellers examined before judging concentration at
# all -- a handful of sellers ranked "top" on a near-empty market isn't a
# real distribution to measure (same spirit as sybil_cluster's minimum
# cluster size).
_MIN_SELLERS_FOR_JUDGMENT = 5


@dataclass(frozen=True)
class SellDistributionFacts:
    """On-chain facts about who sold a token over the lookback window."""

    sellers_examined: int = 0
    total_sold_usd: float = 0.0
    top_seller_share: float | None = None  # 0..1, None if total_sold_usd == 0
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class SellDistributionVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_sell_distribution(facts: SellDistributionFacts) -> SellDistributionVerdict:
    """Weighted judgment, never a hard cutoff -- same doctrine as
    judge_insider_wallets/judge_sybil_cluster."""
    if not facts.available:
        return SellDistributionVerdict(
            signal="unknown", points=[facts.error or "historique de vente non analysable"],
        )
    if facts.sellers_examined < _MIN_SELLERS_FOR_JUDGMENT or facts.top_seller_share is None:
        return SellDistributionVerdict(
            signal="neutral",
            points=[f"seulement {facts.sellers_examined} vendeur(s) distinct(s) sur la fenêtre -- échantillon trop faible pour juger"],
        )
    if facts.top_seller_share >= _DOMINANT_SELLER_SHARE:
        return SellDistributionVerdict(
            signal="concern",
            points=[
                f"le plus gros vendeur représente {facts.top_seller_share * 100:.0f}% du volume vendu "
                f"({facts.sellers_examined} vendeurs distincts) -- signature d'un acteur unique qui liquide, "
                "pas d'une communauté qui prend ses profits"
            ],
        )
    return SellDistributionVerdict(
        signal="neutral",
        points=[
            f"vente étalée sur {facts.sellers_examined} vendeurs distincts, le plus gros ne représente que "
            f"{facts.top_seller_share * 100:.0f}% du volume -- cohérent avec une prise de profit distribuée"
        ],
    )


async def gather_sell_distribution_facts(
    contract: str, *, lookback_days: int = 90, blockchain: str = "base", dune_module=None,
) -> SellDistributionFacts:
    """Best-effort collection of facts about sell concentration (defensive,
    never blocking). ``dune_module`` injectable for offline tests (default:
    `services.dune`)."""
    if dune_module is None:
        from aria_core.services import dune as dune_module

    result = await dune_module.get_token_sell_distribution(
        contract, blockchain=blockchain, lookback_days=lookback_days,
    )
    if not result.available:
        return SellDistributionFacts(available=False, error=result.error)

    sellers = [s for s in result.sellers if s.total_sold_usd > 0]
    if not sellers:
        return SellDistributionFacts(sellers_examined=0, total_sold_usd=0.0, available=True)

    total = sum(s.total_sold_usd for s in sellers)
    top_share = (sellers[0].total_sold_usd / total) if total > 0 else None

    return SellDistributionFacts(
        sellers_examined=len(sellers),
        total_sold_usd=total,
        top_seller_share=top_share,
        available=True,
    )
