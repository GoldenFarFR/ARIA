"""Absorber dedicated to the bonding niche (15%) — the counterpart to ``token_absorber.py``.

Discovers candidates STILL on the bonding curve (``services/launchpad_discovery``,
``"bonding"`` category adapters), scans them (``scan_base_token``, which already
resolves ``ctx.bonding_phase``/``ctx.mint_authority``/``ctx.dev_signal``), filters
them (``bonding_screen.bonding_safety_screen``, NOT the standard filter that would
wrongly require a DEX pair) and files them into ``screened_pool`` under
``network="base-bonding"`` — **never** ``network="base"`` (the 85% VC pool), so as
to never contaminate the weekly draw (``weekly_training.draw_lottery``).

Same doctrine as ``token_absorber.py``:
  - a **confirmed** rejection (``hard_fail``) is final (``status='rejected'``, not
    re-scanned without an explicit resurrection);
  - a **soft** failure (unavailable data: bonding status not yet confirmed,
    mint authority undeterminable...) leaves a trace (``status='pending'``) and
    will be retried on the next cycle, never wrongly banned.

No on-chain write, no signature: read + a journal.
"""

from __future__ import annotations

import logging
import os

from aria_core import screened_pool
from aria_core.skills.acp_onchain_scan import scan_base_token
from aria_core.skills.bonding_screen import bonding_safety_screen

logger = logging.getLogger(__name__)

BONDING_NETWORK = "base-bonding"


async def absorb_bonding_candidate(
    contract: str, *, scanner=None, force: bool = False,
    known_age_days: float | None = None, **screen_kwargs
) -> str:
    """Scans a bonding candidate and files it: 'kept' / 'rejected' / 'skip_*'.

    Symmetric to ``token_absorber.absorb`` but on the ``base-bonding`` pool and
    via ``bonding_safety_screen`` (no DEX pair requirement). ``force=True``
    bypasses the known-status short-circuit (resurrection / refresh).
    ``known_age_days`` (accepted and IGNORED, Volet C 12/07):
    ``base_crawler.retry_stale_pending`` now passes it to EVERY ``absorber``
    unconditionally (same-day fix — otherwise the pre-filter would never
    trigger in prod, see ``token_absorber``) — a bonding token has no DEX pair
    nor an equivalent Blockscout pre-filter, so this parameter is meaningless
    here. Explicitly in the signature (not absorbed by ``**screen_kwargs``) so
    it does NOT leak into ``bonding_safety_screen``, which doesn't expect it
    and would raise an exception.
    """
    scan = scanner or scan_base_token
    if not force:
        status = await screened_pool.get_status(contract)
        if status == "rejected":
            return "skip_rejected"
        if status == "active":
            return "skip_active"

    ctx = await scan(contract, include_dev_behavior=True)
    result = bonding_safety_screen(ctx, **screen_kwargs)

    if result.passed:
        await screened_pool.upsert_screened(
            contract=contract,
            symbol="",
            liquidity_usd=0.0,  # no DEX liquidity in bonding, by construction
            security_score=result.security_score,
            verdict=result.verdict,
            network=BONDING_NETWORK,
            screen_reason=(
                f"bonding {result.bonding_progress:.0%} vers graduation"
                if result.bonding_progress is not None
                else "bonding (progression inconnue)"
            ),
        )
        return "kept"

    if not result.hard_fail:
        reason = "; ".join(result.reasons) if result.reasons else "raison indisponible"
        logger.info("bonding_absorb %s: soft failure (%s) — not banned, to be retried", contract, reason)
        await screened_pool.record_pending(
            contract=contract, reason=reason, network=BONDING_NETWORK,
            security_score=result.security_score, verdict=result.verdict,
        )
        return "skip_incomplete"

    await screened_pool.record_rejected(
        contract=contract, reason="; ".join(result.reasons), network=BONDING_NETWORK,
        security_score=result.security_score, verdict=result.verdict,
    )
    return "rejected"


async def discover_and_absorb_bonding(*, discover=None, absorber=None, limit_per_launchpad: int = 50) -> dict:
    """Discovers then absorbs bonding candidates from ALL active launchpads.

    Returns the count per verdict, aggregated across all launchpads (same
    shape as ``base_crawler.crawl_and_absorb``, for a symmetric heartbeat wiring).
    """
    if discover is None:
        from aria_core.services.launchpad_discovery import discover_bonding_candidates as discover
    absorb = absorber or absorb_bonding_candidate

    by_launchpad = await discover(limit_per_launchpad=limit_per_launchpad)
    counts: dict[str, int] = {}
    for _launchpad_key, addresses in (by_launchpad or {}).items():
        for contract in addresses:
            try:
                verdict = await absorb(contract)
            except Exception as exc:  # noqa: BLE001 — one failing candidate doesn't stop the others
                logger.info("bonding_absorb %s: unexpected failure (%s)", contract, exc)
                verdict = "error"
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


async def absorb_direct_candidate(contract: str, *, scanner=None) -> str:
    """Absorbs a freshly discovered DEX-direct candidate (Clanker, including via
    Bankr which deploys on it -- recognizable vanity addresses, verified 10/07).

    Simple relay to ``token_absorber.absorb`` (same judgment as the standard
    pipeline, 85% VC pool). Missing DEX pair/insufficient liquidity/not yet
    verified contract is NO LONGER a definitive rejection since the 10/07 fix
    to ``safety_screen.hard_fail`` (operator decision: only a CONFIRMED
    malicious mechanism in the contract justifies a definitive ban --
    liquidity/verification/pair are investment aspects that evolve with the
    project's maturity, "like all other tokens"). A just-deployed token
    therefore correctly lands in ``pending`` (retry) instead of ``rejected``
    with no dedicated logic here -- single scan, reused via ``ctx=``.
    """
    from aria_core.token_absorber import absorb as absorb_standard

    scan = scanner or scan_base_token
    status = await screened_pool.get_status(contract)
    if status == "rejected":
        return "skip_rejected"
    if status == "active":
        return "skip_active"

    ctx = await scan(contract, include_honeypot=True)
    return await absorb_standard(contract, scanner=scanner, ctx=ctx, source="bonding_direct")


def bonding_discovery_enabled() -> bool:
    """Seam gated OFF by default. The multi-launchpad discovery heartbeat cycle
    only runs once this flag is enabled by the operator (new network calls)."""
    return os.environ.get("ARIA_BONDING_DISCOVERY_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def run_bonding_discovery_cycle(*, limit_per_launchpad: int = 50) -> dict:
    """A full cycle: discovery + absorption across ALL active launchpads.

    Two INDEPENDENT parts (a failure in one never erases the success of the
    other):
      - ``bonding``: candidates still on the curve (15% niche, ``network="base-bonding"``);
      - ``direct``: candidates with real DEX liquidity (Clanker, graduated
        Virtuals) — go through ``absorb_direct_candidate`` ("not yet a pair"
        grace period before joining the STANDARD ``token_absorber.absorb``
        pipeline, 85% pool).
    """
    bonding_counts = await discover_and_absorb_bonding(limit_per_launchpad=limit_per_launchpad)

    direct_counts: dict[str, int] = {}
    try:
        from aria_core.services.launchpad_discovery import discover_direct_candidates

        by_launchpad = await discover_direct_candidates(limit_per_launchpad=limit_per_launchpad)
        for _launchpad_key, addresses in (by_launchpad or {}).items():
            for contract in addresses:
                try:
                    verdict = await absorb_direct_candidate(contract)
                except Exception as exc:  # noqa: BLE001
                    logger.info("bonding_discovery_cycle: direct absorb %s failed (%s)", contract, exc)
                    verdict = "error"
                direct_counts[verdict] = direct_counts.get(verdict, 0) + 1
    except Exception as exc:  # noqa: BLE001 — the direct part must never break the bonding part
        logger.info("bonding_discovery_cycle: direct part failed (%s)", exc)

    return {"bonding": bonding_counts, "direct": direct_counts}


async def retry_stale_bonding_pending(
    *, limit: int = 20, older_than_hours: int = 24, max_retries: int = 5, max_age_days: int = 7,
) -> dict:
    """Retries bonding ``pending`` candidates left aside — the bonding counterpart
    of ``base_crawler.retry_stale_pending`` (#105, anti-loop cap #108), the same
    gap identified for the ``base-bonding`` pool: ``discover_and_absorb_bonding``
    only revisits a ``pending`` candidate if it happens to reappear in a later
    discovery, nothing retries it deliberately if the launchpad doesn't put it
    back in front of the crawl.

    Duplicates NOTHING: delegates entirely to ``base_crawler.retry_stale_pending``
    (loop, counting, anti-infinite-loop cap via ``abandon_stale_pending`` --
    network-agnostic, primary key = ``contract``) with only a ``lister`` scoped
    to the ``base-bonding`` network and the bonding ``absorber`` in place of
    the standard one. Same verdict semantics ('kept'/'rejected'/'skip_incomplete'/'abandoned').
    """
    from aria_core import screened_pool
    from aria_core.base_crawler import retry_stale_pending

    async def lister():
        return await screened_pool.list_stale_pending(
            older_than_hours=older_than_hours, limit=limit, network=BONDING_NETWORK
        )

    return await retry_stale_pending(
        limit=limit,
        older_than_hours=older_than_hours,
        max_retries=max_retries,
        max_age_days=max_age_days,
        lister=lister,
        absorber=absorb_bonding_candidate,
    )
