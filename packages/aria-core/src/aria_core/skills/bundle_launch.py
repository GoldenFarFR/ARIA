"""Bundle/sniper-at-launch signal -- spots one actor splitting a single buy
across many wallets inside the launch block (backlog #258, promoted from the
research watch 07/08).

Why no existing guardrail covers this: `safety_screen`/`mint_authority` judge
what the CONTRACT can do (mint, blacklist, transfer disabling), `sybil_cluster`
counts holders, `insider_wallets` tracks wallets funded directly by the
deployer. None of them looks at WHEN the earliest buys landed relative to each
other -- yet a bundled launch is precisely a timing signature: many distinct
takers buying in the SAME block, which no organic market produces. The point of
bundling is that the holder map then looks healthy and distributed while the
supply is really under one person's control, ready for a coordinated dump.

Facts come from `services/dune.py::get_token_bundle_launch` (one query, no
per-wallet network call). Pure, deterministic JUDGE, same doctrine as
`sell_distribution.py`/`insider_wallets.py`/`sybil_cluster.py`: produces a
weighted signal (concern/neutral/unknown) that FEEDS ARIA's reasoning -- never
an automatic rejection.

Deliberate limit, stated rather than hidden: same-block co-buying is evidence of
COORDINATION, not proof of a single owner. Independent snipers racing the same
launch also land in the first block. Distinguishing the two needs the funding
graph (are those wallets fed by one source?), a future extension -- which is
exactly why this is a consultative signal and not a hard gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Distinct buyers inside ONE block, at or above which the block reads as
# coordinated rather than organic. Deliberately conservative: an organic launch
# does produce several buyers in the first block (that is the sniper race), so a
# low threshold would flag almost every launch. Starting value, NOT empirically
# calibrated on ARIA's own data yet -- documented as such rather than presented
# as validated (same honesty rule as sell_distribution's _DOMINANT_SELLER_SHARE).
_BUNDLE_BUYERS_PER_BLOCK = 5
# How many of the earliest blocks are considered "the launch window". Bundling
# happens in the creation block or the one or two immediately after; beyond that
# the buys are just early trading.
_LAUNCH_WINDOW_BLOCKS = 3


@dataclass(frozen=True)
class BundleLaunchFacts:
    """On-chain facts about how the token's earliest blocks were bought."""

    blocks_examined: int = 0
    max_buyers_in_one_block: int = 0
    bundled_block_number: int | None = None
    bundled_block_usd: float = 0.0
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class BundleLaunchVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_bundle_launch(facts: BundleLaunchFacts) -> BundleLaunchVerdict:
    """Weighted judgment, never a hard cutoff -- same doctrine as
    judge_sell_distribution/judge_insider_wallets/judge_sybil_cluster."""
    if not facts.available:
        return BundleLaunchVerdict(
            signal="unknown", points=[facts.error or "blocs de lancement non analysables"],
        )
    if facts.blocks_examined <= 0:
        return BundleLaunchVerdict(
            signal="unknown",
            points=["aucun bloc d'achat trouvé sur la fenêtre -- rien à juger"],
        )
    if facts.max_buyers_in_one_block >= _BUNDLE_BUYERS_PER_BLOCK:
        where = (
            f" (bloc {facts.bundled_block_number})" if facts.bundled_block_number is not None else ""
        )
        usd = f", {facts.bundled_block_usd:,.0f}$ achetés dans ce seul bloc" if facts.bundled_block_usd else ""
        return BundleLaunchVerdict(
            signal="concern",
            points=[
                f"{facts.max_buyers_in_one_block} acheteurs distincts dans un même bloc au lancement"
                f"{where}{usd} -- signature d'un achat groupé coordonné (un acteur réparti sur "
                "plusieurs wallets, ou une meute de snipers), risque de dump coordonné ; "
                "à confirmer par le financement commun de ces wallets, non vérifié ici"
            ],
        )
    return BundleLaunchVerdict(
        signal="neutral",
        points=[
            f"au plus {facts.max_buyers_in_one_block} acheteur(s) distinct(s) par bloc sur les "
            f"{facts.blocks_examined} premiers blocs -- pas de signature d'achat groupé"
        ],
    )


async def gather_bundle_launch_facts(
    contract: str, *, lookback_days: int = 90, blockchain: str = "base", dune_module=None,
) -> BundleLaunchFacts:
    """Best-effort collection of launch-block facts (defensive, never
    blocking). ``dune_module`` injectable for offline tests (default:
    `services.dune`) -- same shape as ``gather_sell_distribution_facts``.

    Only the first ``_LAUNCH_WINDOW_BLOCKS`` blocks are considered: the query
    returns the earliest blocks ordered ascending, and bundling by definition
    happens at launch, not during later trading."""
    if dune_module is None:
        from aria_core.services import dune as dune_module

    result = await dune_module.get_token_bundle_launch(
        contract, blockchain=blockchain, lookback_days=lookback_days,
    )
    if not result.available:
        return BundleLaunchFacts(available=False, error=result.error)
    window = list(result.blocks)[:_LAUNCH_WINDOW_BLOCKS]
    if not window:
        return BundleLaunchFacts(available=True, blocks_examined=0)
    worst = max(window, key=lambda b: b.distinct_buyers)
    return BundleLaunchFacts(
        blocks_examined=len(window),
        max_buyers_in_one_block=worst.distinct_buyers,
        bundled_block_number=worst.block_number,
        bundled_block_usd=worst.bought_usd,
        available=True,
    )
