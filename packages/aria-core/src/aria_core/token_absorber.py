"""Token absorber — ARIA's "talent scout".

Scans a contract and decides, unforgivingly:
  - **real value** (passes the security filter) → **kept** in the database
    (`screened_pool`, status active);
  - **nothing** → **rejected "for good"** (status rejected): it's never
    re-scanned again (efficiency), only the reason is kept.

**Resurrection**: if noise reappears (radar / activity spike),
``reconsider_on_signal`` is called — the noise **wakes up** a rejected
candidate, then the re-scan **re-evaluates on the on-chain facts**. Noise
filters/wakes, it never decides (dome: a social signal never triggers an
action, it triggers a re-analysis).

No on-chain write, no signing: this is reading + a journal.
"""
from __future__ import annotations

import logging
import time

from aria_core import screened_pool
from aria_core.services.blockscout import blockscout_client
from aria_core.skills.acp_onchain_scan import scan_base_token
from aria_core.skills.liquidity_stability import record_and_check_liquidity_stability
from aria_core.skills.safety_screen import safety_screen

logger = logging.getLogger(__name__)

# Discovery pre-filter (Part C, 12/07): below this threshold, a candidate is
# treated as "not yet mature" (contract not yet verified, holders not yet
# indexed by Blockscout) rather than "structurally blocked" — anti-false-
# negative guard rail for just-deployed tokens (see ``_prefilter_reason``).
_PREFILTER_MIN_AGE_DAYS = 2.0

_PREFILTER_REASON_PREFIX = "pré-filtre découverte (Blockscout)"


def _prefilter_reason(info) -> str | None:
    """``None`` if the candidate must go through the full scan, otherwise the reason to log.

    Only decides on available Blockscout facts (``info.available``) — any
    missing data (429, timeout, address not found) falls through to the full
    scan (fail-open, never a rejection on missing data, see the
    ``blockscout.py`` policy).
    """
    if info is None or not info.available:
        return None
    unverified = info.is_verified is False
    holders_unknown = info.holders_count is None or info.holders_count == 0
    if not (unverified or holders_unknown):
        return None
    bits = []
    if unverified:
        bits.append("contrat non vérifié")
    if holders_unknown:
        bits.append("holders non indexés")
    return f"{_PREFILTER_REASON_PREFIX} : {' et '.join(bits)} — écarté avant scan complet"


async def absorb(
    contract: str,
    *,
    scanner=None,
    force: bool = False,
    max_age_days: int | None = None,
    known_age_days: float | None = None,
    ctx=None,
    source: str = "",
    **screen_kwargs,
) -> str:
    """Scans a contract and sorts it: 'kept' / 'rejected' / 'skip_*'.

    Without ``force``: a contract already 'rejected' ('thrown out for good')
    or already 'active' is NOT re-scanned (returns 'skip_rejected' /
    'skip_active'). ``force=True`` (resurrection or refresh) bypasses this
    short-circuit and re-evaluates. ``scanner`` is injectable (offline
    tests). ``screen_kwargs`` are passed to ``safety_screen`` (adjustable
    thresholds). ``max_age_days`` (optional): out of scope (not fraud/
    legitimate — 'skip_too_old') if the pair is older; checked before the
    security filter to save the honeypot scan. ``ctx`` (optional): already
    scanned context (avoids a second network scan if the caller already had
    to look at ``ctx.best_pair`` before deciding to call ``absorb`` — see
    ``bonding_absorber.absorb_direct_candidate``). ``source`` (optional,
    e.g. ``'top_pools'``/``'radar_x'``): originating discovery pipeline,
    passed through as-is to ``screened_pool`` — pure traceability, doesn't
    affect any filtering decision (following diversification audit #77,
    12/07). ``known_age_days`` (optional, Part C 12/07): on-chain age
    already known by the caller (e.g. ``first_screened_at`` on the
    ``retry_stale_pending`` side) — if ``>= _PREFILTER_MIN_AGE_DAYS`` AND no
    ``ctx`` is already supplied, a lightweight Blockscout call
    (``get_address_info``) decides BEFORE the full scan: contract still
    unverified and/or holders never indexed after this delay ->
    ``'skip_prefiltered'`` (soft failure, retraced as ``pending``, never
    ``rejected`` — a candidate can always mature later). ``None`` (default)
    or a value under the threshold: unchanged behavior, systematic full
    scan — never reject on missing data or a candidate that's still too fresh.
    """
    scan = scanner or scan_base_token
    if not force:
        status = await screened_pool.get_status(contract)
        if status == "rejected":
            return "skip_rejected"
        if status == "active":
            return "skip_active"

    if ctx is None and known_age_days is not None and known_age_days >= _PREFILTER_MIN_AGE_DAYS:
        info = await blockscout_client.get_address_info(contract)
        reason = _prefilter_reason(info)
        if reason is not None:
            logger.info("absorb %s: pre-filtered (%s) — full scan avoided", contract, reason)
            await screened_pool.record_pending(
                contract=contract,
                reason=reason,
                source=source,
            )
            return "skip_prefiltered"

    # Honeypot check ACTIVE at the entry filter: a honeypot token / with an
    # extractive tax / reversible ownership must not enter the pool, not
    # merely be flagged for analysis.
    if ctx is None:
        ctx = await scan(contract, include_honeypot=True)

    if max_age_days is not None:
        created_ms = ctx.best_pair.pair_created_at if ctx.best_pair else None
        if created_ms:
            age_days = (time.time() * 1000 - created_ms) / 86_400_000
            if age_days > max_age_days:
                return "skip_too_old"

    # 22/07 -- item #19 (stress-test): time-stability confirmation on
    # liquidity before the screen's judgment. Manipulation synchronized to
    # THIS scan's window (liquidity pumped then withdrawn) would never be
    # detected by a single reading -- compared against the last known scan
    # of this same contract (recent window), never a rejection on a first
    # scan with no history.
    liquidity_stability = None
    if ctx.best_pair is not None and ctx.best_pair.liquidity_usd:
        stability_result = await record_and_check_liquidity_stability(
            contract, "base", ctx.best_pair.liquidity_usd, ctx.best_pair.volume_24h_usd,
        )
        liquidity_stability = stability_result.confirmed

    result = safety_screen(ctx, liquidity_stability_confirmed=liquidity_stability, **screen_kwargs)

    if result.passed:
        best = ctx.best_pair
        await screened_pool.upsert_screened(
            contract=contract,
            symbol=(best.base_symbol if best else ""),
            liquidity_usd=result.liquidity_usd,
            security_score=result.security_score,
            top_holder_pct=ctx.top_holder_pct,
            verdict=result.verdict,
            pool_address=(best.pair_address if best else ""),
            screen_reason=result.reasons[0] if result.reasons else "",
            source=source,
        )
        return "kept"

    # SOFT failure (unavailable data: 429/timeout, holders not returned): we
    # do NOT ban "for good" — a later re-scan can decide. Otherwise a good
    # token scanned during an outage spike would be lost for good.
    if not result.hard_fail:
        # Transparency required: if the token is PROMISING but OPAQUE, ARIA
        # surfaces a recalibration request to the operator instead of
        # deciding in the dark.
        try:
            from aria_core.recalibration import maybe_escalate

            await maybe_escalate(ctx, symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""))
        except Exception as exc:  # noqa: BLE001 — the escalation must never break the absorption
            logger.info("absorb %s: recalibration escalation failed (%s)", contract, exc)
        reason = "; ".join(result.reasons) if result.reasons else "raison indisponible"
        logger.info("absorb %s: soft failure (%s) — not banned, will retry", contract, reason)
        # Consultable trace (status='pending', doesn't short-circuit the
        # re-scan): before this fix, a soft failure left NO data anywhere
        # (audit #77). liquidity_usd/security_score/verdict passed through
        # (15/07): the full scan already ran here (unlike the Part C
        # pre-filter above), don't leave a promising pending candidate
        # indistinguishable from one with no signal at all.
        await screened_pool.record_pending(
            contract=contract,
            reason=reason,
            symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""),
            source=source,
            liquidity_usd=result.liquidity_usd,
            security_score=result.security_score,
            verdict=result.verdict,
            top_holder_pct=ctx.top_holder_pct,
        )
        return "skip_incomplete"

    # Same fix (15/07): a hard rejection also has a full scan in hand, don't
    # leave it indistinguishable from a rejection with no signal at all.
    await screened_pool.record_rejected(
        contract=contract,
        reason="; ".join(result.reasons),
        symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""),
        source=source,
        liquidity_usd=result.liquidity_usd,
        security_score=result.security_score,
        verdict=result.verdict,
        top_holder_pct=ctx.top_holder_pct,
    )
    return "rejected"


async def reconsider_on_signal(
    contract: str, *, scanner=None, source: str = "", **screen_kwargs
) -> str:
    """Noise reappeared: resurrects a rejected candidate and re-evaluates it on the on-chain facts.

    The signal decides nothing — it just reopens the door, the re-scan
    decides. Returns the new verdict ('kept' / 'rejected'). ``source``: same
    parameter as ``absorb`` (the waking signal IS the originating pipeline
    here, e.g. 'radar_x')."""
    await screened_pool.reconsider(contract)
    return await absorb(contract, scanner=scanner, force=True, source=source, **screen_kwargs)
