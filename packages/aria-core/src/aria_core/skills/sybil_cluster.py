"""Sybil cluster detection among a token's holders — beyond the pairwise
convergence already in place.

Found by the stress test (Codex Part 11): a fake distribution (e.g. 40
wallets funded by the same source, each below the individual concentration
threshold) returns a POSITIVE signal today — `_holder_concentration`
(acp_onchain_scan.py) only looks at the individual TOP HOLDER (`top_holder_pct`)
and the cumulative top 10 (`top10_holder_pct`), never whether these holders
share a common origin (same deposit, same funding wallet). 40 wallets at
2% each clear every individual barrier while 78% of the supply is actually
concentrated in the hands of a single actor disguised as a community.

Distinct from `smart_money._pairwise_convergence` (scoped to the 1-3 wallets
submitted TOGETHER to `/walletscore`, never the full pool of a token's
holders) — this module groups ALL of ONE token's top holders by common
funding source (an already-proven heuristic: `smart_money._funding_source`,
reused as-is, never duplicated).

Real network cost: one bounded Blockscout call (`get_transactions_bounded`)
PER verified holder, capped at `max_holders_checked` — significantly more
expensive than the other advisory signals in this project
(insider_wallets/deployer_history only reuse data already on hand).
Deliberately NOT wired into the default `/vc` path (see
`acp_onchain_scan.py::include_sybil_check`, off) — the automatic VC pocket
remains dormant (0% of capital, 15/07 decision), the network cost of this
signal isn't justified until it's reactivated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Cluster size (holders sharing the same funding source) above which a
# Sybil distribution is suspected rather than a coincidence (two early
# buyers funded by the same exchange is nothing unusual).
_MIN_CLUSTER_SIZE_FOR_SUSPICION = 5
# Cumulative share of supply (%) held by this cluster above which the
# signal becomes significant -- a small dust cluster has no value.
_MIN_CLUSTER_CUMULATIVE_PCT_FOR_SUSPICION = 20.0
# Maximum holders verified (network cost: 1 bounded Blockscout call each) --
# the TOP of the distribution, never all holders (often hundreds).
_DEFAULT_MAX_HOLDERS_CHECKED = 15


@dataclass(frozen=True)
class SybilClusterFacts:
    holders_checked: int = 0
    largest_cluster_size: int = 0
    largest_cluster_cumulative_pct: float = 0.0
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class SybilClusterVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_sybil_cluster(facts: SybilClusterFacts) -> SybilClusterVerdict:
    """Pure, deterministic judgment — same doctrine as the other advisory
    signals in this project (insider_wallets/deployer_history)."""
    if not facts.available:
        return SybilClusterVerdict(signal="unknown", points=[facts.error or "clustering non analysable"])
    if (
        facts.largest_cluster_size >= _MIN_CLUSTER_SIZE_FOR_SUSPICION
        and facts.largest_cluster_cumulative_pct >= _MIN_CLUSTER_CUMULATIVE_PCT_FOR_SUSPICION
    ):
        return SybilClusterVerdict(
            signal="concern",
            points=[
                f"cluster Sybil suspecté : {facts.largest_cluster_size} holders (sur "
                f"{facts.holders_checked} vérifiés) partagent la même source de financement, "
                f"cumulent {facts.largest_cluster_cumulative_pct:.0f}% de l'offre -- distribution "
                "possiblement déguisée en communauté, pas un cluster confirmé (heuristique de "
                "financement, jamais un rejet automatique)"
            ],
        )
    return SybilClusterVerdict(
        signal="neutral",
        points=[
            f"{facts.holders_checked} holder(s) vérifié(s), plus gros regroupement par source de "
            f"financement commune : {facts.largest_cluster_size} holder(s) ({facts.largest_cluster_cumulative_pct:.0f}% de l'offre)"
        ],
    )


async def gather_sybil_cluster_facts(
    holders: list,
    *,
    exclude_addresses: set[str] | None = None,
    max_holders_checked: int = _DEFAULT_MAX_HOLDERS_CHECKED,
    client=None,
    funding_source_fn=None,
) -> SybilClusterFacts:
    """Groups top holders by common funding source. ``holders``
    (list of `TokenHolder`, already fetched by the scan -- zero re-fetch of
    the holders themselves); ``exclude_addresses``: LP pool/burn addresses
    (same exclusion as `_holder_concentration`, never counted as Sybils).
    Best-effort, never blocking. ``client``/``funding_source_fn`` injectable
    for offline tests (default: blockscout_client / smart_money._funding_source)."""
    if not holders:
        return SybilClusterFacts(available=True, holders_checked=0)
    if client is None:
        from aria_core.services.blockscout import blockscout_client as client
    if funding_source_fn is None:
        from aria_core.services.smart_money import _funding_source as funding_source_fn

    excl = {a.lower() for a in (exclude_addresses or set())}
    candidates = [
        h for h in holders
        if (getattr(h, "address", "") or "").lower() not in excl
    ][:max_holders_checked]

    if not candidates:
        return SybilClusterFacts(available=True, holders_checked=0)

    clusters: dict[str, list[float]] = {}
    checked = 0
    for holder in candidates:
        try:
            source, _truncated = await funding_source_fn(client, holder.address)
        except Exception:  # noqa: BLE001 — an isolated failure doesn't invalidate the other holders
            continue
        checked += 1
        if not source:
            continue
        pct = float(getattr(holder, "percentage", None) or 0.0)
        clusters.setdefault(source, []).append(pct)

    if not clusters:
        return SybilClusterFacts(available=True, holders_checked=checked)

    largest_source = max(clusters, key=lambda s: len(clusters[s]))
    largest_pcts = clusters[largest_source]
    return SybilClusterFacts(
        holders_checked=checked,
        largest_cluster_size=len(largest_pcts),
        largest_cluster_cumulative_pct=sum(largest_pcts),
        available=True,
    )
