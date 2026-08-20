"""Continuous integrity checks on the shadow pockets' own data (20/08).

**Why this exists.** Three separate measurement bugs were found by hand on
20/08, each only because someone looked at a number that felt wrong:
  - a pocket priced its ENTRY through REST and its EXIT through the RPC, so
    every PnL compared two different sources (surfaced as a 53% reserve drop
    reporting a 79% price drop -- impossible on a constant-product curve);
  - the avoided-PnL counterfactual ran on a 180-minute window while claiming
    to match a 60-minute one;
  - `shadow_snapshot_archive` was never wired on the websocket branch, so most
    checks left no trace at all.
Each was invisible until a human happened to look. These checks make the same
class of defect surface on its own.

**What it deliberately does NOT do.** No verdict on strategy, no threshold
tuning, no closing of positions. It answers one question only -- "is this data
trustworthy?" -- because a wrong measurement is worse than no measurement: it
gets acted on. Findings go to `system_issues` (the project's existing registry,
surfaced at every session start) rather than a new channel.

Read-only against the shadow DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from .paths import shadow_db_path

# A bonding curve's price IS quote/token, so price and reserve must move
# together. This is the invariant that exposed the two-source bug.
#
# RELATIVE, not absolute. An absolute tolerance was tried first and calibrated
# against the real bug it exists to catch -- it FAILED: the actual case
# (price ratio 0.217 vs reserve ratio 0.468) is an absolute gap of only 0.251,
# under any tolerance loose enough to avoid false positives. In relative terms
# the same case is a 54% divergence, unmistakable. A check that cannot catch
# the bug that motivated it is worse than no check, so this was verified
# against the real numbers before being kept.
PRICE_RESERVE_TOLERANCE = 0.40

# Beyond this, a multiplier is far more likely to be a corrupted price than a
# real move. Matches the dome's existing PEAK_PRICE_SANITY_MULTIPLE doctrine.
IMPLAUSIBLE_MULTIPLIER = 50.0

# An open position untouched for this long means the exit loop is not reaching
# it -- the exact failure that made liquidity_collapse catches land 32-116s
# late while the nominal cadence was 10s.
STALE_OPEN_MINUTES = 45.0

POCKET_TABLES = {
    "late_bonding": "solana_late_bonding_shadow_log",
    "ws_exit": "solana_fresh_launch_ws_exit_shadow_log",
    "fast_discovery": "solana_fresh_launch_fast_discovery_shadow_log",
}


@dataclass
class IntegrityFinding:
    pocket: str
    check: str
    count: int
    detail: str

    @property
    def dedup_key(self) -> str:
        """Stable per (pocket, check) so a persistent problem opens ONE issue
        rather than one per pass."""
        return f"shadow_integrity:{self.pocket}:{self.check}"


async def _rows(db, sql: str, params: tuple) -> list[dict]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


async def check_pocket(pocket: str, *, since: str | None = None, db_path: str | None = None) -> list[IntegrityFinding]:
    """Runs every check against one pocket. Returns findings, never raises --
    an integrity checker that can crash the caller is worse than none."""
    table = POCKET_TABLES.get(pocket)
    if table is None:
        raise ValueError(f"unknown pocket -- expected one of {sorted(POCKET_TABLES)}")

    since = since or (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    findings: list[IntegrityFinding] = []
    try:
        async with aiosqlite.connect(db_path or str(shadow_db_path())) as db:
            # 1. price and reserve must move together on a bonding curve.
            rows = await _rows(
                db,
                # BONDING CURVES ONLY. The invariant is price == quote/token,
                # which holds on a bonding curve and NOT on a migrated AMM pool,
                # where price depends on both sides independently of the USD
                # reserve figure. First run of this check flagged 51/107 and
                # 19/48 on the two older pockets purely because they also trade
                # migrated tokens -- a checker that cries wolf gets ignored,
                # which is worse than not having it.
                f"SELECT entry_price, reserve_usd, last_price, last_reserve_usd FROM {table} "
                f"WHERE exit_reason IS NOT NULL AND detected_at >= ? "
                f"AND entry_price > 0 AND reserve_usd > 0 "
                f"AND last_price IS NOT NULL AND last_reserve_usd IS NOT NULL "
                f"AND (exit_price_source = 'pumpfun' OR dex_id = 'pumpfun')",
                (since,),
            )
            mismatched = []
            for r in rows:
                price_ratio = r["last_price"] / r["entry_price"]
                reserve_ratio = r["last_reserve_usd"] / r["reserve_usd"]
                if reserve_ratio <= 0:
                    continue
                if abs(price_ratio - reserve_ratio) / reserve_ratio > PRICE_RESERVE_TOLERANCE:
                    mismatched.append(r)
            if mismatched:
                findings.append(IntegrityFinding(
                    pocket, "price_reserve_divergence", len(mismatched),
                    f"{len(mismatched)}/{len(rows)} closures where price and reserve moved "
                    f"inconsistently -- the signature of two different price sources",
                ))

            # 2. implausible multipliers = corrupted price, not a real move.
            rows = await _rows(
                db,
                f"SELECT COUNT(*) AS n FROM {table} WHERE detected_at >= ? "
                f"AND COALESCE(realistic_final_multiplier, final_multiplier) > ?",
                (since, IMPLAUSIBLE_MULTIPLIER),
            )
            if rows and rows[0]["n"]:
                findings.append(IntegrityFinding(
                    pocket, "implausible_multiplier", rows[0]["n"],
                    f"{rows[0]['n']} closures above x{IMPLAUSIBLE_MULTIPLIER:.0f} -- far more "
                    f"likely a corrupted price than a real move",
                ))

            # 3. open positions the exit loop is not reaching.
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_OPEN_MINUTES)).isoformat()
            rows = await _rows(
                db,
                f"SELECT COUNT(*) AS n FROM {table} WHERE exit_reason IS NULL "
                f"AND COALESCE(last_checked_at, detected_at) < ?",
                (cutoff,),
            )
            if rows and rows[0]["n"]:
                findings.append(IntegrityFinding(
                    pocket, "stale_open_positions", rows[0]["n"],
                    f"{rows[0]['n']} open positions unchecked for over "
                    f"{STALE_OPEN_MINUTES:.0f}min -- the exit loop is not reaching them",
                ))

            # 4. a position recorded without a usable entry price can never
            #    produce a meaningful PnL, so it silently poisons every average.
            #    NO time window here, unlike the checks above: a row like this
            #    gets STUCK, so it is old by definition. Real case found 20/08 --
            #    two rows with entry_price=0 sat open for 8 hours holding the
            #    head of the exit queue, and the windowed version of this very
            #    check could not see them.
            rows = await _rows(
                db,
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE (entry_price IS NULL OR entry_price <= 0) AND exit_reason IS NULL",
                (),
            )
            if rows and rows[0]["n"]:
                findings.append(IntegrityFinding(
                    pocket, "unusable_entry_price", rows[0]["n"],
                    f"{rows[0]['n']} OPEN rows with a null or non-positive entry price -- "
                    f"no PnL is computable for them and they hold the exit queue's head",
                ))
    except aiosqlite.OperationalError:
        return []  # a pocket whose table does not exist yet is not a finding
    return findings


async def run_all(*, db_path: str | None = None, open_issues: bool = True) -> dict:
    """Checks every pocket and, by default, records findings in `system_issues`
    -- the project's existing registry, already surfaced at session start, so
    this needs no new notification channel."""
    all_findings: list[IntegrityFinding] = []
    for pocket in POCKET_TABLES:
        try:
            all_findings.extend(await check_pocket(pocket, db_path=db_path))
        except Exception:  # noqa: BLE001 -- one pocket's failure never hides the others
            continue

    if open_issues and all_findings:
        try:
            from . import system_issues

            for f in all_findings:
                await system_issues.open_issue(
                    "shadow_data_integrity",
                    f"Shadow data integrity: {f.check} ({f.pocket})",
                    detail=f.detail, severity="warning", dedup_key=f.dedup_key,
                )
        except Exception:  # noqa: BLE001 -- reporting must never break the check
            pass

    return {
        "checked_pockets": len(POCKET_TABLES),
        "findings": [{"pocket": f.pocket, "check": f.check, "count": f.count, "detail": f.detail}
                     for f in all_findings],
        "clean": not all_findings,
    }
