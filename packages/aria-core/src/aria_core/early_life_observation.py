"""Post-qualification Early-Life observation window (04/09, operator go --
"Chantier A", strictly observation-only: "Il peut mesurer et conserver,
sans declencher de trade... il ne doit pas decider retroactivement qu'un
token etait bon ou mauvais").

**The gap this closes.** `onchain_pool_discovery.OnChainPoolDiscoveryFeed`
already records a raw activity snapshot (`onchain_activity_observation`)
every cycle -- but only for candidates still in `self._candidates`, i.e.
still PENDING qualification. The instant a candidate qualifies it is
removed from that dict (`expired_keys`/`self._candidates.pop`), so its
observation stream stops at that exact moment. Confirmed live on $LEGS
(03/09, pool `0xf6214b4907a2871408a568c585947ec16b6dc8ea`): qualified via
`cold_read` on the very first check, exactly ONE row ever recorded in
`onchain_activity_observation_log`, every activity field NULL -- zero
trajectory data, even though the operator explicitly asked to be able to
reconstruct "t=0 creation, t=5s X swaps, t=10s volume=X... t=120s
acheteurs=...".

**Raw observations, never derived features (operator's own requirement,
verbatim: "il faut enregistrer les fenetres et les observations BRUTES
necessaires pour recalculer les features. Sinon dans trois semaines on
aura acceleration=4.72 mais impossible de savoir avec quelles observations
elle avait ete calculee").** This module computes nothing -- it only
schedules repeated calls into the existing, already-tested, append-only
`onchain_activity_observation.record_observation`, mirroring EXACTLY the
call shape `onchain_pool_discovery.py` already uses for pending candidates
(same field list, same `quote_is_weth`-gated `eth_usd_rate_at_observation`
rule) so a pool's trajectory is one continuous, uniformly-shaped series
whether the rows were recorded before or after qualification. Any feature
(acceleration, buyer growth...) is a separate, on-demand READ of this raw
history, computed later, never stored here -- so a future recalculation
can always prove exactly which observations were available at time t.

**Never touches Entry/Exit.** No code path here calls `record_signals`,
`advance_exit_simulation`, or any trading decision -- per the operator's
explicit scoping of this chantier: "Je ne toucherais pas encore a
Entry/Exit ni a la logique de la poche autorisee."

**Bounded window, not indefinite.** `list_active`/`advance_tracking_cycle`
only consider a candidate while `now - qualified_at < window_seconds`
(default 300s / 5 minutes -- covers the full early-life trajectory the
operator described, t=0 through t=120s and beyond, without tracking a pool
forever). A candidate past the window is simply no longer polled; its
tracking row stays in the table (never deleted) as a factual record of
when tracking started and stopped.

**04/09, second pass -- a "Candidate" state, internal only, NEVER Telegram
(operator go, twice-revised same day).** First revision: real incident,
`$OPENAI` alerted at pool age 4 seconds, 1 swap, zero real trajectory -- a
coordinated-funding cluster (11 wallets funded together, 33 buys / 0
sells) only became visible on an external tool (InsightX) several minutes
later. A first design made ON-CHAIN + SECURITY validation the gate for a
Telegram alert. **Second revision, same day, operator explicit correction:
that first design was still too permissive** -- "je ne garderais pas le
modele ON-CHAIN + SECURITY suffisent pour alerter. Ca risque de
reproduire exactement le probleme... avec $OPENAI." A real alert
("Investable Alert" / "High-Conviction") requires a strong CONVERGENCE
across ON-CHAIN + SECURITY + SOCIAL + CHART + COORDINATION -- signals
this pipeline does not measure yet. Rather than let `N/A` silently count
as a pass for those missing families, the operator drew a three-level
model: Observation (Early-Life, this module's original scope) -> Candidate
(ON-CHAIN + SECURITY validated, what this second pass builds) -> Investable
Alert (full convergence, NOT designed/built, Telegram-worthy). **This
module's `candidate_validated_at`/`security_status`/etc. are Candidate-level
bookkeeping ONLY -- no code path here or in any caller sends a Telegram
message from reaching this state.** RADAR V1's old immediate-alert
behavior is NOT restored either; Telegram stays silent for Robinhood
day-zero candidates until a real Investable Alert design exists.

`MIN_ONCHAIN_TRAJECTORY_SECONDS`/`MIN_ONCHAIN_OBSERVATIONS` are a
DELIBERATELY UNCALIBRATED starting point, not a measured threshold --
same doctrine the operator applied to their own "10 minutes" suggestion
("d'abord une hypothese experimentale, pas une verite biologique du
marche"). The real age at which a signal becomes discriminant is meant to
be measured later from Chantier A's own accumulating data, per the
operator's own method. `SECURITY_RETRY_INTERVAL_SECONDS` exists because a
single GoPlus check is not enough -- its real indexing lag on very young
pools (documented in HANDOFF_PIPELINE_MOMENTUM.md's 04/09 serial-deploy-bot
entry) means "unknown" must be retried, never treated as "safe"."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from . import db_migrations
from . import onchain_activity_observation
from .paths import shadow_db_path
from .services.geckoterminal import TrendingPool

logger = logging.getLogger(__name__)

TABLE = "early_life_tracking"

DEFAULT_WINDOW_SECONDS = 300.0

# 04/09, second pass -- see module docstring. Starting points, not measured
# thresholds; revisit once Chantier A has accumulated enough trajectories to
# measure "the age at which the signal becomes discriminant" empirically.
# 04/09, operator-directed clarification after $GIL: this value is a
# TECHNICAL calculability floor (enough of a trajectory exists to compute
# has_minimum_onchain_trajectory at all), NOT a validated security/maturation
# delay -- do not read 60.0 as "60 seconds is long enough to trust a
# candidate". The real question of how long a pool must be observed before a
# coordinated-funding cluster would become visible stays explicitly OPEN,
# to be formally decided later, never inferred from this constant. Telegram
# itself stays OFF regardless of this value until the X-account and
# funding-cluster gates exist (see shadow_persistent.py's own comment on the
# disabled send).
MIN_ONCHAIN_TRAJECTORY_SECONDS = 60.0
MIN_ONCHAIN_OBSERVATIONS = 1
SECURITY_RETRY_INTERVAL_SECONDS = 20.0

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return str(shadow_db_path())


def _activity_db_path() -> str:
    return onchain_activity_observation._db_path()  # noqa: SLF001 -- same DB, test convenience alias


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                token_address TEXT NOT NULL,
                qualified_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                UNIQUE(chain, pool_address)
            )
            """
        )
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_chain ON {TABLE} (chain, qualified_at)")
        # 04/09, second pass -- catch up a table already created in
        # production (before this pass) with the original 5-column schema.
        # ``CREATE TABLE IF NOT EXISTS`` above only covers a fresh database.
        await db_migrations.ensure_columns(db, TABLE, [
            ("symbol", "TEXT"),
            ("total_supply", "REAL"),
            ("market_cap_usd", "REAL"),
            ("pool_created_at", "TEXT"),
            ("candidate_validated_at", "TEXT"),
            ("security_status", "TEXT"),
            ("last_security_check_at", "TEXT"),
            ("candidate_suppressed_at", "TEXT"),
            ("candidate_suppressed_reason", "TEXT"),
        ])
        await db.commit()
    _ensured_db_paths.add(path)


async def start_tracking(
    chain: str, pool_address: str, token_address: str, *, qualified_at: datetime | None = None,
    symbol: str | None = None, total_supply: float | None = None,
    market_cap_usd: float | None = None, pool_created_at: datetime | None = None,
) -> None:
    """Registers a just-qualified candidate for post-qualification
    observation. Idempotent -- a pool already tracked (same chain +
    pool_address) is never re-inserted, so a candidate re-notified isn't
    double-tracked.

    ``symbol``/``total_supply``/``market_cap_usd``/``pool_created_at`` (04/09,
    second pass) are the qualification-time-only fields the eventual radar
    alert needs but that ``onchain_activity_observation_log`` never carries
    (it's an activity log, not a static-metadata one) -- stored once here so
    ``build_snapshot_pool`` can reconstruct a full ``TrendingPool`` later
    without a second network call. ``market_cap_usd`` is a snapshot of the
    qualification moment, same "display only, never a filter" doctrine as
    everywhere else in this dome -- never refreshed here."""
    await _ensure_table()
    at = qualified_at or datetime.now(timezone.utc)
    started_at = datetime.now(timezone.utc)
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE}
                (chain, pool_address, token_address, qualified_at, started_at,
                 symbol, total_supply, market_cap_usd, pool_created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chain, pool_address, token_address, at.isoformat(), started_at.isoformat(),
                symbol, total_supply, market_cap_usd,
                pool_created_at.isoformat() if pool_created_at else None,
            ),
        )
        await db.commit()


async def list_active(
    chain: str, *, now: datetime | None = None, window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> list[dict]:
    """Candidates for ``chain`` still within their observation window
    (``now - qualified_at < window_seconds``). Never mutates the table --
    an expired candidate simply stops appearing here, its row is never
    deleted."""
    await _ensure_table()
    at = now or datetime.now(timezone.utc)
    cutoff = (at - timedelta(seconds=window_seconds)).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT chain, pool_address, token_address, qualified_at, symbol, "
            f"total_supply, market_cap_usd, pool_created_at, candidate_validated_at, "
            f"security_status, last_security_check_at, candidate_suppressed_at, "
            f"candidate_suppressed_reason FROM {TABLE} "
            f"WHERE chain = ? AND qualified_at >= ?",
            (chain, cutoff),
        )
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def advance_tracking_cycle(
    chain: str, *, ws_feed, now: datetime | None = None, window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> int:
    """For every active candidate on ``chain``, pulls one snapshot from
    ``ws_feed`` and records it via the existing, already-tested
    ``onchain_activity_observation.record_observation`` -- the exact same
    call shape ``onchain_pool_discovery.py`` uses for PENDING candidates,
    so a pool's row series is uniform whether recorded before or after
    qualification. Best-effort per candidate: one snapshot failure never
    stops the others. Returns the number of snapshots successfully
    recorded."""
    at = now or datetime.now(timezone.utc)
    active = await list_active(chain, now=at, window_seconds=window_seconds)
    recorded = 0
    for cand in active:
        pool_address = cand["pool_address"]
        try:
            snapshot = ws_feed.get_snapshot(pool_address)
            await onchain_activity_observation.record_observation(
                chain=chain, pool_address=pool_address, token_address=cand["token_address"],
                available=snapshot.available, family=snapshot.family if snapshot.available else None,
                swap_count=snapshot.swap_count if snapshot.available else None,
                cumulative_volume_quote=(
                    snapshot.cumulative_volume_quote if snapshot.available else None
                ),
                distinct_traders_count=(
                    snapshot.distinct_traders_count if snapshot.available else None
                ),
                last_swap_age_seconds=snapshot.stale_seconds if snapshot.available else None,
                buy_count=snapshot.buy_count if snapshot.available else None,
                sell_count=snapshot.sell_count if snapshot.available else None,
                undetermined_count=snapshot.undetermined_count if snapshot.available else None,
                buy_volume_quote=snapshot.buy_volume_quote if snapshot.available else None,
                sell_volume_quote=snapshot.sell_volume_quote if snapshot.available else None,
                undetermined_volume_quote=(
                    snapshot.undetermined_volume_quote if snapshot.available else None
                ),
                liquidity_added_quote=(
                    snapshot.liquidity_added_quote if snapshot.available else None
                ),
                liquidity_removed_quote=(
                    snapshot.liquidity_removed_quote if snapshot.available else None
                ),
                liquidity_added_raw=snapshot.liquidity_added_raw if snapshot.available else None,
                liquidity_removed_raw=snapshot.liquidity_removed_raw if snapshot.available else None,
                price_quote=snapshot.price_quote if snapshot.available else None,
                price_usd=snapshot.price_usd if snapshot.available else None,
                reserve_usd=snapshot.reserve_usd if snapshot.available else None,
                raw_liquidity=snapshot.raw_liquidity if snapshot.available else None,
                quote_reserve_raw=snapshot.quote_reserve_raw if snapshot.available else None,
                eth_usd_rate_at_observation=(
                    ws_feed.onchain_eth_usd_rate()
                    if snapshot.available and snapshot.quote_is_weth
                    else None
                ),
            )
            recorded += 1
        except Exception as exc:  # noqa: BLE001 -- one candidate's failure must never stop the cycle
            logger.info("early_life_observation: snapshot failed for %s (%s)", pool_address, exc)
    return recorded


async def has_minimum_onchain_trajectory(
    chain: str, pool_address: str, *, now: datetime | None = None,
    min_age_seconds: float = MIN_ONCHAIN_TRAJECTORY_SECONDS,
    min_observations: int = MIN_ONCHAIN_OBSERVATIONS,
) -> bool:
    """True once BOTH hold: the candidate has aged at least
    ``min_age_seconds`` since ``qualified_at``, AND at least
    ``min_observations`` real (``available=True``, i.e. ``swap_count IS NOT
    NULL``) rows exist for it in ``onchain_activity_observation_log``. An
    unknown pool (never tracked) is always ``False``, never a guess."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            f"SELECT qualified_at FROM {TABLE} WHERE chain = ? AND pool_address = ?",
            (chain, pool_address),
        )
        row = await cur.fetchone()
    if row is None:
        return False
    qualified_at = datetime.fromisoformat(row[0])
    at = now or datetime.now(timezone.utc)
    if (at - qualified_at).total_seconds() < min_age_seconds:
        return False

    await onchain_activity_observation._ensure_table(_activity_db_path())  # noqa: SLF001 -- same DB, needed before the count query below
    async with aiosqlite.connect(_activity_db_path()) as db:
        cur = await db.execute(
            f"SELECT COUNT(*) FROM {onchain_activity_observation.TABLE} "
            f"WHERE chain = ? AND pool_address = ? AND swap_count IS NOT NULL",
            (chain, pool_address),
        )
        (count,) = await cur.fetchone()
    return count >= min_observations


def should_retry_security_check(
    last_check_at: str | None, *, now: datetime | None = None,
    min_interval_seconds: float = SECURITY_RETRY_INTERVAL_SECONDS,
) -> bool:
    """True when a security check has never run (``last_check_at is None``)
    or the last one is older than ``min_interval_seconds`` -- throttles
    GoPlus retries so a candidate stuck in "unknown" for its whole tracking
    window doesn't hammer the API once per discovery cycle (~5s)."""
    if last_check_at is None:
        return True
    at = now or datetime.now(timezone.utc)
    last = datetime.fromisoformat(last_check_at)
    return (at - last).total_seconds() >= min_interval_seconds


async def touch_security_check_at(chain: str, pool_address: str, *, at: datetime | None = None) -> None:
    """Records that a GoPlus check just ran and stayed INCONCLUSIVE (neither
    positively safe nor positively blocked) -- updates only the retry
    throttle timestamp, never ``security_status`` (which stays ``NULL``,
    "unknown"). Distinct from ``update_security_status`` on purpose: an
    inconclusive check is not a third status value, it's the absence of one."""
    await _ensure_table()
    checked_at = (at or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"UPDATE {TABLE} SET last_security_check_at = ? WHERE chain = ? AND pool_address = ?",
            (checked_at, chain, pool_address),
        )
        await db.commit()


async def update_security_status(
    chain: str, pool_address: str, status: str, *, at: datetime | None = None,
) -> None:
    """Persists the outcome of a GoPlus check -- ``"safe"`` (positively
    confirmed, never re-checked again) or ``"blocked"`` (positively
    confirmed unsafe, permanent, never alerted). Never called with anything
    else -- an inconclusive check (GoPlus unavailable or not yet indexed)
    leaves ``security_status`` at its default ``NULL`` ("unknown") and only
    updates ``last_security_check_at`` via the caller's own retry bookkeeping
    (``should_retry_security_check`` reads that column directly)."""
    await _ensure_table()
    checked_at = (at or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"UPDATE {TABLE} SET security_status = ?, last_security_check_at = ? "
            f"WHERE chain = ? AND pool_address = ?",
            (status, checked_at, chain, pool_address),
        )
        await db.commit()


async def mark_candidate_validated(chain: str, pool_address: str, *, at: datetime | None = None) -> None:
    """Records that this candidate cleared ON-CHAIN + SECURITY validation --
    reached the "Candidate" state. Purely internal bookkeeping: no Telegram
    message is ever sent from this state (04/09, operator-corrected scope --
    see module docstring). ``list_pending_candidate_evaluation`` excludes a
    validated candidate from then on, so it is never re-evaluated twice."""
    await _ensure_table()
    alerted_at = (at or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"UPDATE {TABLE} SET candidate_validated_at = ? WHERE chain = ? AND pool_address = ?",
            (alerted_at, chain, pool_address),
        )
        await db.commit()


async def mark_candidate_suppressed(
    chain: str, pool_address: str, reason: str, *, at: datetime | None = None,
) -> None:
    """Records that this candidate was evaluated and deliberately excluded
    from reaching the "Candidate" state for a factual, non-security reason
    (today: matched a recent liquidity-signature duplicate, see
    ``radar_series_dedup.py``) -- distinct from ``candidate_validated_at``
    (validation succeeded) and from ``security_status="blocked"`` (a
    security verdict). Without this, a duplicate candidate would be
    re-evaluated against ``radar_series_dedup`` every single cycle for its
    whole tracking window, writing a redundant row each time."""
    await _ensure_table()
    suppressed_at = (at or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"UPDATE {TABLE} SET candidate_suppressed_at = ?, candidate_suppressed_reason = ? "
            f"WHERE chain = ? AND pool_address = ?",
            (suppressed_at, reason, chain, pool_address),
        )
        await db.commit()


async def list_pending_candidate_evaluation(
    chain: str, *, now: datetime | None = None, window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> list[dict]:
    """Active candidates not yet validated, not security-blocked, and not
    already suppressed -- the real work list for the per-cycle Candidate
    evaluation (ON-CHAIN + SECURITY, internal only, see module docstring).
    A candidate that ages out of the window without ever satisfying every
    gate simply stops appearing here; it never reaches "Candidate"
    retroactively (honest silence, not a missed opportunity to fabricate
    one)."""
    active = await list_active(chain, now=now, window_seconds=window_seconds)
    return [
        cand for cand in active
        if cand["candidate_validated_at"] is None
        and cand["security_status"] != "blocked"
        and cand["candidate_suppressed_at"] is None
    ]


async def build_snapshot_pool(chain: str, pool_address: str) -> TrendingPool | None:
    """Reconstructs a ``TrendingPool`` from already-collected data -- the
    static qualification-time fields (``symbol``/``total_supply``/
    ``market_cap_usd``) stored by ``start_tracking``, plus the MOST RECENT
    real (``available=True``) activity row -- zero extra network call. This
    is what lets Candidate-state evaluation (``is_radar_eligible`` and any
    future consumer) reflect the pool's CURRENT trajectory, not its state
    at the instant it qualified.

    ``None`` when the pool isn't tracked, or when it has no real activity
    observation yet (nothing to reconstruct from) -- never a snapshot built
    from a single ``available=False`` gap.

    ``volume_usd``/``buy_volume_usd``/``sell_volume_usd`` are converted from
    the raw quote-unit cumulative/buy/sell volumes using
    ``eth_usd_rate_at_observation`` recorded on that same row -- ``None``
    when that rate wasn't resolved (a non-WETH-quoted pool, or the rate was
    briefly unavailable), same "never fabricate a USD figure" doctrine as
    the rest of this dome."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT token_address, symbol, total_supply, market_cap_usd, pool_created_at, "
            f"qualified_at FROM {TABLE} WHERE chain = ? AND pool_address = ?",
            (chain, pool_address),
        )
        tracking_row = await cur.fetchone()
    if tracking_row is None:
        return None

    await onchain_activity_observation._ensure_table(_activity_db_path())  # noqa: SLF001 -- same DB, needed before the query below
    async with aiosqlite.connect(_activity_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {onchain_activity_observation.TABLE} "
            f"WHERE chain = ? AND pool_address = ? AND swap_count IS NOT NULL "
            f"ORDER BY id DESC LIMIT 1",
            (chain, pool_address),
        )
        activity_row = await cur.fetchone()
    if activity_row is None:
        return None

    rate = activity_row["eth_usd_rate_at_observation"]
    cumulative_quote = activity_row["cumulative_volume_quote"]
    buy_quote = activity_row["buy_volume_quote"]
    sell_quote = activity_row["sell_volume_quote"]
    volume_usd = cumulative_quote * rate if rate is not None and cumulative_quote is not None else None
    buy_volume_usd = buy_quote * rate if rate is not None and buy_quote is not None else None
    sell_volume_usd = sell_quote * rate if rate is not None and sell_quote is not None else None

    pool_created_raw = tracking_row["pool_created_at"] or tracking_row["qualified_at"]

    return TrendingPool(
        pool_address=pool_address,
        token_address=tracking_row["token_address"],
        symbol=tracking_row["symbol"],
        price_usd=activity_row["price_usd"],
        price_change_pct={},
        transactions_m15=None,
        volume_usd_m15=None,
        reserve_usd=activity_row["reserve_usd"],
        pool_created_at=datetime.fromisoformat(pool_created_raw) if pool_created_raw else None,
        buy_count=activity_row["buy_count"],
        sell_count=activity_row["sell_count"],
        buy_volume_quote=buy_quote,
        sell_volume_quote=sell_quote,
        cumulative_volume_quote=cumulative_quote,
        distinct_traders_count=activity_row["distinct_traders_count"],
        total_supply=tracking_row["total_supply"],
        market_cap_usd=tracking_row["market_cap_usd"],
        volume_usd=volume_usd,
        swap_count=activity_row["swap_count"],
        buy_volume_usd=buy_volume_usd,
        sell_volume_usd=sell_volume_usd,
    )
