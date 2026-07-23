"""X Radar — social sourcing filtered by on-chain arbitration (Vault 4).

Listens to social noise (``services/x_social``), keeps candidates noisy
enough (anti-astroturf threshold), then has them **arbitrated by on-chain
data** via the absorber:
  - unknown / active contract → ``absorb`` (the scan decides: kept or
    rejected);
  - already **rejected** contract → ``reconsider_on_signal`` (the noise WAKES
    UP a rejected candidate, the re-scan re-evaluates on the facts).

RED LINE (dome): social signals NEVER trigger a buy/sell. They only **source**
new candidates and **reopen the door** to rejected ones. The decision always
belongs to on-chain analysis. ARIA is never the megaphone of a cabal: a
social consensus is not worth a thesis.

Everything is injectable -> testable offline. Read-only, no signing.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Anti-astroturf thresholds: below these values, the noise isn't credible
# enough to warrant a scan (a single author spamming isn't a signal). Adjustable.
_MIN_MENTIONS = 2
_MIN_DISTINCT_AUTHORS = 2


async def run_radar(
    *,
    social_client=None,
    query: str = "base token 0x",
    absorber=None,
    resonator=None,
    pool_status=None,
    min_mentions: int = _MIN_MENTIONS,
    min_distinct_authors: int = _MIN_DISTINCT_AUTHORS,
    limit: int = 50,
) -> dict:
    """Runs one social-radar round -> on-chain arbitration. Returns a count report.

    Injectables (prod defaults in parentheses):
      - ``social_client`` (``x_social.x_social_client``): source of the noise;
      - ``absorber(contract)`` (``token_absorber.absorb``): scans a new candidate;
      - ``resonator(contract)`` (``token_absorber.reconsider_on_signal``): wakes up a rejected one;
      - ``pool_status(contract)`` (``screened_pool.get_status``): known status ('rejected'/'active'/None).

    Report: ``{sourced, above_threshold, kept, rejected, resurrected, skipped, error}``.
    """
    if social_client is None:
        from aria_core.services.x_social import x_social_client as social_client
    if absorber is None:
        from aria_core.token_absorber import absorb as absorber
    if resonator is None:
        from aria_core.token_absorber import reconsider_on_signal as resonator
    if pool_status is None:
        from aria_core.screened_pool import get_status as pool_status

    signals = await social_client.scan_mentions(query, limit=limit * 4)
    report = {
        "sourced": len(signals),
        "above_threshold": 0,
        "kept": 0,
        "rejected": 0,
        "resurrected": 0,
        "skipped": 0,
        "error": 0,
    }

    processed = 0
    for sig in signals:
        if processed >= limit:
            break
        # Noise filter: the social signal must be credible enough to warrant a scan.
        if sig.mentions < min_mentions or sig.distinct_authors < min_distinct_authors:
            continue
        report["above_threshold"] += 1
        processed += 1

        try:
            status = await pool_status(sig.contract)
            if status == "rejected":
                # The noise wakes up a rejected candidate; the on-chain re-scan decides.
                verdict = await resonator(sig.contract)
                if verdict == "kept":
                    report["resurrected"] += 1
                else:
                    report["rejected"] += 1
            elif status == "active":
                report["skipped"] += 1
            else:
                verdict = await absorber(sig.contract)
                if verdict == "kept":
                    report["kept"] += 1
                elif verdict == "rejected":
                    report["rejected"] += 1
                else:
                    report["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — a crashing candidate doesn't stop the radar
            logger.info("radar_x: processing %s failed (%s)", sig.contract, exc)
            report["error"] += 1

    logger.info("radar_x: round complete %s", report)
    return report
