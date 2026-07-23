"""Dune Analytics client (read-only) -- Execute SQL API (15/07, see
docs/dune-integration-plan.md §3.2, §5).

"Dome" doctrine (identical to blockscout.py/geckoterminal.py/tavily.py):
- 429: exponential backoff, 3 attempts max, then give up without blocking the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation (``available=False``).
- Missing data is never replaced by a guess.

API key: ``DUNE_API_KEY`` read via ``os.environ.get`` on EVERY call (never
cached at import time -- same pattern as ``tavily.py``, simpler to test with
``monkeypatch.setenv``/``delenv``). Without a key: immediate
``available=False``, NO network call attempted (same pattern as
``TavilyClient`` without a key) -- the real key will be added later to the
VPS ``.env`` by the operator, never supplied in-session.

HONEST RESERVATION (15/07): the endpoint/field names below come from Dune's
PUBLIC documentation (docs.dune.com), not a real authenticated call -- no key
was available this session to verify live (14/07 process norm: "always
verify the exact field name against a real live call" -- not yet possible
here, see docs/dune-integration-plan.md §4). The parsing below is tolerant
(any unexpected shape -> ``available=False``, never an exception, never
fabricated data) -- but the FIRST real execution with the operator's key must
re-verify these fields before considering this module reliable in prod.

Scope of this module: client + dedicated SQL query only (plan §3.2). NO
active wiring (no ``ARIA_DUNE_ENABLED`` gate, no heartbeat task, no call from
``wallet_candidate_sourcing.py``) -- explicit operator decision (15/07),
integration into the existing sourcing is a separate task."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "Dune data unavailable"

BASE_URL = "https://api.dune.com/api"

# Dune terminal states (prefix "QUERY_STATE_") -- COMPLETED = the only state
# from which a usable result can be read; the others are terminal failures
# (never retried indefinitely, see `run_sql_and_wait`).
_TERMINAL_FAILURE_STATES = {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"}
_TERMINAL_SUCCESS_STATE = "QUERY_STATE_COMPLETED"


def dune_api_key() -> str:
    """Dune key from the env ONLY (never hardcoded, never logged)."""
    return os.environ.get("DUNE_API_KEY", "").strip()


def is_dune_configured() -> bool:
    return bool(dune_api_key())


@dataclass
class ExecutionHandle:
    execution_id: str = ""
    state: str | None = None
    available: bool = True
    error: str | None = None


@dataclass
class ExecutionStatus:
    execution_id: str = ""
    state: str | None = None
    is_execution_finished: bool = False
    available: bool = True
    error: str | None = None


@dataclass
class ExecutionResult:
    execution_id: str = ""
    rows: list[dict] = field(default_factory=list)
    row_count: int | None = None
    available: bool = True
    error: str | None = None


# 21/07 -- first proactive throttle for this client (there was none before --
# only a reactive retry after an already-received 429). CLAUDE.md doctrine
# "Throughput calibrated to 90%": Free tier confirmed
# (docs.dune.com/api-reference/overview/rate-limits) -- two independent
# counters, 15/min (low limit) and 40/min (high limit); the low limit is the
# binding constraint first. 90% of 15/min = 13.5/min = 4.44s.
_MIN_INTERVAL = 4.44
_last_request = 0.0
_throttle_lock = asyncio.Lock()


async def _throttle() -> None:
    global _last_request
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL - (now - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = asyncio.get_event_loop().time()


async def _request(method: str, path: str, *, json_body: dict | None = None) -> tuple[object | None, str | None]:
    """GET/POST with retry on 429/5xx/timeout -- same policy as the other
    clients in this folder. Without a configured key: immediate
    `available=False`, no network call (same pattern as `tavily.py`)."""
    api_key = dune_api_key()
    if not api_key:
        return None, f"{UNAVAILABLE} (DUNE_API_KEY missing)"

    url = f"{BASE_URL}{path}"
    headers = {"X-Dune-Api-Key": api_key, "Accept": "application/json"}
    attempt_429 = 0
    timeout_retried = False

    while True:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                else:
                    response = await client.post(url, headers=headers, json=json_body or {})
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dune: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (Dune timeout)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("dune: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (Dune rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dune: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (Dune server error {response.status_code})"

        if response.status_code in (401, 403):
            logger.warning("dune: HTTP %s on %s (invalid/rejected key)", response.status_code, url)
            return None, f"{UNAVAILABLE} (invalid or rejected Dune key)"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("dune: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def execute_sql(sql: str, *, performance: str = "small") -> ExecutionHandle:
    """Runs a raw SQL query (Execute SQL API, never need to save it in the
    Dune UI first). 18/07 -- real bug found while testing live with a real
    key (free account): "medium" AND "large" are BOTH rejected by the API
    ("Invalid performance tier"), contrary to what the general docs suggest
    -- only "small" works on this account. Default fixed accordingly. See
    docs/dune-integration-plan.md §4 (to be updated)."""
    data, error = await _request("POST", "/v1/sql/execute", json_body={"sql": sql, "performance": performance})
    if error is not None:
        return ExecutionHandle(available=False, error=error)
    if not isinstance(data, dict):
        return ExecutionHandle(available=False, error=UNAVAILABLE)

    execution_id = str(data.get("execution_id") or "")
    if not execution_id:
        return ExecutionHandle(available=False, error=f"{UNAVAILABLE} (execution_id missing)")

    return ExecutionHandle(execution_id=execution_id, state=data.get("state"), available=True, error=None)


async def get_execution_status(execution_id: str) -> ExecutionStatus:
    """Status of an execution -- free endpoint on Dune's side (no credit
    consumed), meant to be polled (`run_sql_and_wait`)."""
    data, error = await _request("GET", f"/v1/execution/{execution_id}/status")
    if error is not None:
        return ExecutionStatus(execution_id=execution_id, available=False, error=error)
    if not isinstance(data, dict):
        return ExecutionStatus(execution_id=execution_id, available=False, error=UNAVAILABLE)

    return ExecutionStatus(
        execution_id=execution_id,
        state=data.get("state"),
        is_execution_finished=bool(data.get("is_execution_finished")),
        available=True,
        error=None,
    )


async def get_execution_result(execution_id: str) -> ExecutionResult:
    """Result of a finished execution. Does NOT inspect `state` itself --
    the caller (`run_sql_and_wait`) must have already confirmed the terminal
    state via `get_execution_status` before calling this."""
    data, error = await _request("GET", f"/v1/execution/{execution_id}/results")
    if error is not None:
        return ExecutionResult(execution_id=execution_id, available=False, error=error)
    if not isinstance(data, dict):
        return ExecutionResult(execution_id=execution_id, available=False, error=UNAVAILABLE)

    result = data.get("result")
    if not isinstance(result, dict):
        return ExecutionResult(execution_id=execution_id, available=False, error=f"{UNAVAILABLE} (result missing)")

    rows = result.get("rows")
    if not isinstance(rows, list):
        return ExecutionResult(execution_id=execution_id, available=False, error=f"{UNAVAILABLE} (rows missing)")

    metadata = result.get("metadata") or {}
    row_count = metadata.get("row_count") if isinstance(metadata, dict) else None

    return ExecutionResult(
        execution_id=execution_id,
        rows=[r for r in rows if isinstance(r, dict)],
        row_count=row_count if isinstance(row_count, int) else None,
        available=True,
        error=None,
    )


async def run_sql_and_wait(
    sql: str, *, performance: str = "small", poll_interval: float = 3.0, max_wait: float = 300.0,
) -> ExecutionResult:
    """Full orchestration: runs the query, polls the status (free) until a
    terminal state, then reads the result once. Bounded by ``max_wait``
    (5 min by default) -- never an unbounded wait, even if Dune never
    finishes the execution."""
    handle = await execute_sql(sql, performance=performance)
    if not handle.available or not handle.execution_id:
        return ExecutionResult(available=False, error=handle.error or UNAVAILABLE)

    elapsed = 0.0
    while elapsed < max_wait:
        status = await get_execution_status(handle.execution_id)
        if not status.available:
            return ExecutionResult(execution_id=handle.execution_id, available=False, error=status.error)

        if status.state in _TERMINAL_FAILURE_STATES:
            logger.warning("dune: execution %s finished with failure (%s)", handle.execution_id, status.state)
            return ExecutionResult(
                execution_id=handle.execution_id, available=False, error=f"{UNAVAILABLE} (state {status.state})",
            )

        if status.is_execution_finished or status.state == _TERMINAL_SUCCESS_STATE:
            return await get_execution_result(handle.execution_id)

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning("dune: execution %s still running after %ss -- giving up (never an unbounded wait)", handle.execution_id, max_wait)
    return ExecutionResult(
        execution_id=handle.execution_id, available=False, error=f"{UNAVAILABLE} (execution timeout exceeded after {max_wait}s)",
    )


# ---------------------------------------------------------------------------
# Dedicated SQL query (#157 sourcing, plan §3.2) -- "wallets that bought a
# Base token within its first hour of life, which then did at least Nx"
# ---------------------------------------------------------------------------
#
# HONEST RESERVATION (15/07): `dex.trades` column names (Dune Spellbook
# table, very stable/publicly documented: block_time, blockchain, project,
# taker, token_bought_address, token_bought_amount, token_sold_address,
# token_sold_amount, amount_usd, tx_hash) -- NOT verified by a real call (no
# key available this session). To be reconfirmed via `EXECUTE_SQL_LIMIT_1`
# (just below) before any prod use, 14/07 norm ("never trust a schema
# guessed from memory").
#
# Query logic:
# 1. `token_launch`: first Base DEX trade ever seen for each token
#    (`token_bought_address`), taken as a proxy for the token's "birth" --
#    Dune has no native notion of "contract deployment" in `dex.trades`,
#    only trades, so this proxy is a documented approximation, not an
#    absolute truth (can differ by a few blocks from the real deployment if
#    the very first trade took time to appear).
# 2. `early_buyers`: wallets (`taker`) whose purchase of this token happened
#    within the hour following `token_launch`.
# 3. `peak_multiple`: highest USD price observed on this token / USD price at
#    launch time -- filters tokens that did at least `min_multiple`.
# 4. Result: list of distinct wallets that bought a token that then did
#    >= N x, with the observed multiple and the token in question.
#
# REVIEW FIX (15/07, before merge): `token_launch` was computing
# MIN(block_time) on rows ALREADY filtered to the `lookback_days` window -- a
# token ESTABLISHED for a long time whose first trade WITHIN the window
# happened to fall `lookback_days` days ago would have been wrongly
# classified as "just born", polluting the whole signal (the goal is to find
# early buyers of REAL new tokens, not buyers of an old token during a
# recent rally). Fixed: the MIN(block_time) aggregate now runs over the
# COMPLETE history of `dex.trades` (no date filter in the WHERE), and only
# the aggregated result is filtered via HAVING -- keeps only tokens whose
# FIRST transaction ever seen actually falls within the recent window.
# Higher cost (full table scan for this CTE), but necessary for the fix --
# `token_peak`/`token_launch_price` remain bounded to the window, consistent
# since any trade of a genuinely new token (launch_time within the window)
# necessarily also falls within the window.
#
# FIX #185 (15/07, real bug found during live verification via the dune MCP
# -- see docs/dune-integration-plan.md §8.1, query 7992486): `peak_multiple`
# was coming out aberrant (~10^22 x) on several top rows. Cause identified by
# inspecting intermediate values: `launch_price_usd` near-zero (e.g.
# 3.6e-14 $) -- `token_peak`/`token_launch_price` were computing a unit price
# (`amount_usd / token_bought_amount`) on EVERY trade, including a "dust"
# trade (infinitesimal amount) that blows up the division without being a
# real market price. `NULLIF(token_bought_amount, 0)` protects against
# EXACT division by zero, not against an amount close to zero.
# Fixed by excluding dust trades from the price calculation BEFORE the
# MIN/MAX (`amount_usd >= {min_trade_usd}` in both `token_peak` AND
# `token_launch_price` -- the bug could affect either side of the division,
# not just the launch price) -- NOT an arbitrary cap on the final
# `peak_multiple`, which would have masked the symptom without fixing the
# cause (a token whose REAL launch price was mismeasured would stay
# mismeasured, just hidden by the cap instead of being cleanly excluded).
#
# Parameters expected from the caller (simple substitution before sending --
# THIS MODULE DOES NO VALIDATION/ESCAPING, the caller must ensure that
# `min_multiple`/`lookback_days`/`min_trade_usd` are trusted numeric values,
# never unfiltered user input -- the "read-only" doctrine doesn't protect
# against SQL injection if these values come from elsewhere):
# - `min_multiple` (float, e.g. 5.0 for "at least 5x")
# - `lookback_days` (int, search window for token launches, e.g. 30)
# - `min_trade_usd` (float, default 1.0 -- minimum USD amount of a trade to
#   count toward the price calculation; excludes dust without excluding the
#   tokens themselves, a token can still appear in the result via its OTHER
#   trades above the floor)
EARLY_BUYER_MULTIPLE_QUERY_TEMPLATE = """
WITH token_launch AS (
    SELECT
        token_bought_address AS token_address,
        MIN(block_time) AS launch_time
    FROM dex.trades
    WHERE blockchain = 'base'
    GROUP BY token_bought_address
    HAVING MIN(block_time) >= NOW() - INTERVAL '{lookback_days}' day
),
early_buyers AS (
    SELECT DISTINCT
        t.taker AS wallet_address,
        t.token_bought_address AS token_address,
        tl.launch_time
    FROM dex.trades t
    JOIN token_launch tl
        ON t.token_bought_address = tl.token_address
    WHERE t.blockchain = 'base'
      AND t.block_time >= tl.launch_time
      AND t.block_time < tl.launch_time + INTERVAL '1' hour
),
token_peak AS (
    SELECT
        token_bought_address AS token_address,
        MAX(amount_usd / NULLIF(token_bought_amount, 0)) AS peak_price_usd
    FROM dex.trades
    WHERE blockchain = 'base'
      AND block_time >= NOW() - INTERVAL '{lookback_days}' day
      AND amount_usd >= {min_trade_usd}
    GROUP BY token_bought_address
),
token_launch_price AS (
    SELECT
        t.token_bought_address AS token_address,
        MIN(t.amount_usd / NULLIF(t.token_bought_amount, 0)) AS launch_price_usd
    FROM dex.trades t
    JOIN token_launch tl
        ON t.token_bought_address = tl.token_address
    WHERE t.blockchain = 'base'
      AND t.block_time = tl.launch_time
      AND t.amount_usd >= {min_trade_usd}
    GROUP BY t.token_bought_address
)
SELECT
    eb.wallet_address,
    eb.token_address,
    eb.launch_time,
    tlp.launch_price_usd,
    tp.peak_price_usd,
    (tp.peak_price_usd / NULLIF(tlp.launch_price_usd, 0)) AS peak_multiple
FROM early_buyers eb
JOIN token_peak tp ON tp.token_address = eb.token_address
JOIN token_launch_price tlp ON tlp.token_address = eb.token_address
WHERE tlp.launch_price_usd > 0
  AND (tp.peak_price_usd / NULLIF(tlp.launch_price_usd, 0)) >= {min_multiple}
ORDER BY peak_multiple DESC
"""

# Minimal query to verify the real schema of `dex.trades` before any prod
# use of the query above (14/07 norm) -- deliberately kept separate, never
# sent automatically by this module.
EXECUTE_SQL_LIMIT_1 = "SELECT * FROM dex.trades WHERE blockchain = 'base' LIMIT 1"


def build_early_buyer_multiple_query(
    *, min_multiple: float, lookback_days: int, min_trade_usd: float = 1.0,
) -> str:
    """Builds the query above with the requested parameters. Validates that
    the three inputs are indeed numeric BEFORE any substitution into the
    SQL -- the only relevant anti-injection protection here, this query
    never accepts a free-form string."""
    if not isinstance(min_multiple, (int, float)) or min_multiple <= 0:
        raise ValueError("min_multiple must be a positive number")
    if not isinstance(lookback_days, int) or lookback_days <= 0:
        raise ValueError("lookback_days must be a positive integer")
    if not isinstance(min_trade_usd, (int, float)) or min_trade_usd <= 0:
        raise ValueError("min_trade_usd must be a positive number")
    return EARLY_BUYER_MULTIPLE_QUERY_TEMPLATE.format(
        min_multiple=min_multiple, lookback_days=lookback_days, min_trade_usd=min_trade_usd,
    )


# ---------------------------------------------------------------------------
# Dedicated SQL query (#134 "wider scan throughput", 15/07) -- SECOND
# INDEPENDENT source of Base token discovery, complementing (never
# replacing) GeckoTerminal (already used by
# ``base_crawler.discover_top_pools``). EXACT scope of this task: client +
# query + tests ONLY -- NO wiring into ``base_crawler.py``, NO gate, NO
# heartbeat task (operator decision of 15/07, real pipeline integration is a
# separate decision after cross-review).
#
# Abandoned the initial lead ("/v1/dex/pairs/{chain}", plan §3.1) -- verified
# live (15/07) and confirmed NONEXISTENT (404 on every URL variant tried,
# including with an auth header present -- unlike the Execute SQL API,
# which is real and responds 401 without a valid key). This query therefore
# STRICTLY reuses the same Execute SQL API as
# ``build_early_buyer_multiple_query`` above, no new client.
#
# Query logic:
# 1. `token_launch`: first Base DEX trade ever seen for each token (same
#    CTE/same pitfall fixed as above -- see warning below), filtered to
#    tokens whose first trade falls within the recent window
#    (`lookback_hours`, e.g. 24-48h).
# 2. `recent_volume`: total USD volume and trade count over the recent
#    window, per token -- bounded directly by `lookback_hours` in the WHERE
#    (safe here, NOT the same pitfall as token_launch: a token whose
#    launch_time falls within the window has, by construction, ALL its
#    trades within the window too -- same reasoning already applied to
#    `token_peak`/`token_launch_price` in the query above).
# 3. Result: newly-appeared Base tokens (first trade within the window) with
#    a minimum volume, sorted by descending volume -- discovery candidates,
#    NOT yet a security verdict (the real security filter remains
#    `safety_screen`/`token_absorber`, unchanged).
#
# ANTI-REGRESSION WARNING (operator review of 15/07, same pitfall as the 1st
# query before its fix): `token_launch` must NEVER filter by date in its
# WHERE -- only `blockchain = 'base'`. The recent-window filter applies
# ONLY via HAVING on the MIN(block_time) aggregate, otherwise a token
# ESTABLISHED for a long time whose first trade WITHIN the calculation
# window happens to fall `lookback_hours` ago would be wrongly classified as
# "just born" -- the aggregate must run over the COMPLETE history of
# `dex.trades` so that "first trade ever seen" is truly the very first, not
# the first within an already-filtered window.
#
# HONEST RESERVATION (same `dex.trades` columns as above, same lack of
# verification by real call -- see reservation at the top of the file): to
# be reconfirmed via `EXECUTE_SQL_LIMIT_1` before any prod use.
#
# Parameters expected from the caller (simple substitution, same guarantees
# as ``build_early_buyer_multiple_query`` -- THIS MODULE DOES NO
# VALIDATION/ESCAPING beyond numeric typing, the caller must ensure these
# values are trusted, never unfiltered user input):
# - `min_volume_usd` (float, e.g. 5000.0 for "at least $5,000 of volume")
# - `lookback_hours` (int, search window for token launches, e.g. 48)
RECENT_BASE_PAIRS_QUERY_TEMPLATE = """
WITH token_launch AS (
    SELECT
        token_bought_address AS token_address,
        MIN(block_time) AS launch_time
    FROM dex.trades
    WHERE blockchain = 'base'
    GROUP BY token_bought_address
    HAVING MIN(block_time) >= NOW() - INTERVAL '{lookback_hours}' hour
),
recent_volume AS (
    -- RESERVATION (VPS Research, 15/07, live test on dex.trades): `amount_usd`
    -- is `null` on some rows coming from aggregator projects (e.g.
    -- `0x API`) -- `SUM()` silently ignores these rows (no error, no
    -- exception), so `volume_usd` here is a floor, never a guaranteed exact
    -- total: a token traded mostly via an aggregator can be
    -- under-valued and wrongly miss the `min_volume_usd` threshold (a false
    -- negative for discovery, never a false positive for security). To
    -- address before any prod use if this proves significant (e.g. COALESCE +
    -- a separate `trade_count_unpriced` column to make the gap visible, never
    -- a fabricated value).
    SELECT
        token_bought_address AS token_address,
        SUM(amount_usd) AS volume_usd,
        COUNT(*) AS trade_count
    FROM dex.trades
    WHERE blockchain = 'base'
      AND block_time >= NOW() - INTERVAL '{lookback_hours}' hour
    GROUP BY token_bought_address
)
SELECT
    tl.token_address,
    tl.launch_time,
    rv.volume_usd,
    rv.trade_count
FROM token_launch tl
JOIN recent_volume rv ON rv.token_address = tl.token_address
WHERE rv.volume_usd >= {min_volume_usd}
ORDER BY rv.volume_usd DESC
"""


def build_recent_base_pairs_query(*, min_volume_usd: float, lookback_hours: int) -> str:
    """Builds the query above with the requested parameters. Validates that
    the two inputs are indeed numeric BEFORE any substitution into the SQL --
    same guarantee as ``build_early_buyer_multiple_query``, this query never
    accepts a free-form string."""
    if not isinstance(min_volume_usd, (int, float)) or min_volume_usd <= 0:
        raise ValueError("min_volume_usd must be a positive number")
    if not isinstance(lookback_hours, int) or lookback_hours <= 0:
        raise ValueError("lookback_hours must be a positive integer")
    return RECENT_BASE_PAIRS_QUERY_TEMPLATE.format(min_volume_usd=min_volume_usd, lookback_hours=lookback_hours)


# ---------------------------------------------------------------------------
# Dedicated SQL query -- reinforcing the shared funding-source signal already
# in place (`_pairwise_convergence`/funding source in smart_money.py) with
# `addresses.stats.first_funded_by` (15/07, see
# docs/aria-learning-inbox/2026-07-15-graphsense-verifie-negatif-dune-labels-pivot.md
# §2.1 -- table tested live tonight by Research, NOT a guessed schema).
# EXACT scope of this task: function + query + tests ONLY -- NO wiring into
# smart_money.py (operator decision of 15/07; the full Sybil project --
# Louvain/K-means -- remains separate and heavier, see the same report).
#
# Columns confirmed BY REAL TEST (not the public docs): `address`,
# `first_funded_by`, `first_funded_at`, `is_eoa`, `is_smart_contract` --
# coverage confirmed for Base + 11 other chains. RESERVATION carried over
# from the Research report: `is_smart_contract`/`is_eoa` can be wrong on
# Base-specific predeployed addresses (e.g. WETH `0x4200...0006` wrongly
# classified `is_eoa: true`) -- these two fields are therefore returned
# as-is, never reinterpreted or filtered by this module.
#
# COST RESERVATION (carried over from the same report): 0.963 credit
# observed for 2 addresses WITHOUT a partition column filter --
# `addresses.stats` has no natural time window on the caller's side here
# (unlike `dex.trades`), so no date filter is added; the caller must
# therefore BOUND the size of `addresses` itself (never send an unbounded
# list) -- not this module's responsibility to guess an arbitrary limit.
#
# LIVE VERIFICATION FIX (15/07, before merge -- real bug found, not a guess):
# `address` is of type `varbinary` in `addresses.stats` (confirmed by
# `resultMetadata` on the real execution), NOT `varchar`. A first attempt
# with single-quoted literals (``address IN ('0x...', '0x...')``) FAILED on
# real execution: "Cannot find common type between varbinary and
# varchar(42)" -- DuneSQL does NOT implicitly cast a string to varbinary in
# an IN, unlike other SQL contexts. Fixed by emitting the addresses as BARE
# hexadecimal literals (``0x...`` without quotes, native Trino/DuneSQL
# varbinary syntax) -- re-verified on real execution after the fix (see
# docs/dune-integration-plan.md), result identical to the single-quoted
# attempt on `dex.trades.taker` (varchar, which does cast implicitly fine)
# -- this module therefore confirms one must NEVER assume a Dune address
# type is uniformly `varchar` across tables.
_EVM_ADDRESS_RE_SOURCE = r"^0x[a-fA-F0-9]{40}$"

ADDRESSES_STATS_QUERY_TEMPLATE = """
SELECT address, first_funded_by, first_funded_at, is_eoa, is_smart_contract
FROM addresses.stats
WHERE blockchain = '{blockchain}'
  AND address IN ({address_list})
"""


def build_addresses_stats_query(addresses: list[str], *, blockchain: str = "base") -> str:
    """Builds the ``addresses.stats`` query for a list of addresses.
    Validates each address against a strict EVM format (``0x`` + 40 hex)
    BEFORE any substitution -- these addresses can come from dynamically
    tracked wallets (not an internal constant like this module's other
    parameters), so real anti-injection validation is needed here, unlike
    the numeric `build_*` functions above. Emits BARE hexadecimal literals
    (``0x...``, not quoted) -- `address` is `varbinary` in
    `addresses.stats`, confirmed on real execution (see reservation above);
    a single-quoted literal fails there."""
    if not addresses:
        raise ValueError("addresses cannot be empty")
    if not blockchain or not re.fullmatch(r"[a-z0-9_-]+", blockchain):
        raise ValueError("invalid blockchain")

    address_re = re.compile(_EVM_ADDRESS_RE_SOURCE)
    normalized: list[str] = []
    for addr in addresses:
        if not isinstance(addr, str) or not address_re.fullmatch(addr):
            raise ValueError(f"invalid EVM address: {addr!r}")
        normalized.append(addr.lower())

    address_list = ", ".join(normalized)  # bare hex literals -- see varbinary reservation above
    return ADDRESSES_STATS_QUERY_TEMPLATE.format(blockchain=blockchain, address_list=address_list)


@dataclass
class FundedByRecord:
    address: str
    first_funded_by: str | None = None
    first_funded_at: str | None = None
    is_eoa: bool | None = None
    is_smart_contract: bool | None = None


@dataclass
class FundedByResult:
    records: list[FundedByRecord] = field(default_factory=list)
    available: bool = True
    error: str | None = None


async def get_first_funded_by(
    addresses: list[str], *, blockchain: str = "base", performance: str = "small",
) -> FundedByResult:
    """Queries `addresses.stats` for a list of addresses and returns their
    `first_funded_by` (and the table's other confirmed fields). Same dome
    doctrine as the rest of this module: without a key -- or on failure at
    any step (execution/status/result) -- `available=False`, never an
    exception, never a fabricated record. Empty list: immediate empty
    result, no network call (no wasted Dune round trip)."""
    if not addresses:
        return FundedByResult(records=[], available=True, error=None)

    try:
        sql = build_addresses_stats_query(addresses, blockchain=blockchain)
    except ValueError as exc:
        return FundedByResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    exec_result = await run_sql_and_wait(sql, performance=performance)
    if not exec_result.available:
        return FundedByResult(available=False, error=exec_result.error)

    records: list[FundedByRecord] = []
    for row in exec_result.rows:
        address = row.get("address")
        if not isinstance(address, str) or not address:
            continue
        records.append(
            FundedByRecord(
                address=address,
                first_funded_by=row.get("first_funded_by") if isinstance(row.get("first_funded_by"), str) else None,
                first_funded_at=row.get("first_funded_at") if isinstance(row.get("first_funded_at"), str) else None,
                is_eoa=row.get("is_eoa") if isinstance(row.get("is_eoa"), bool) else None,
                is_smart_contract=row.get("is_smart_contract") if isinstance(row.get("is_smart_contract"), bool) else None,
            )
        )

    return FundedByResult(records=records, available=True, error=None)


# ---------------------------------------------------------------------------
# 22/07 -- early buyers of ONE specific token (already judged a "winner" by
# ARIA upstream, see wallet_candidate_sourcing.list_strong_performers) --
# RELIEVES Blockscout (get_token_holders, CURRENT holders) on wallet
# candidate SOURCING. EXACT scope of this task: query + function + tests --
# wiring into wallet_candidate_sourcing.py done in the SAME session,
# explicit operator decision ("let's relieve Blockscout as much as possible").
#
# VERIFIED BY A REAL AUTHENTICATED CALL (22/07, not the public docs -- 14/07
# norm): a first attempt with ``token_bought_address = '0x...'`` (single
# quotes, syntax that works on ``dex.trades.taker``) FAILED on real
# execution -- ``Cannot apply operator: varbinary = varchar(42)``. Pitfall
# confirmed: EVEN WITHIN THE SAME TABLE ``dex.trades``, ``taker`` is
# ``varchar`` but ``token_bought_address`` is ``varbinary`` -- exactly the
# reservation already documented earlier in this file for
# ``addresses.stats``, reconfirmed here on a different table. Fixed by
# emitting a BARE hexadecimal literal (``0x1234``, not quoted) for
# ``token_bought_address`` -- re-verified on real execution after the fix (5
# real WETH wallets returned, sorted by chronological first transaction).
TOKEN_EARLY_BUYERS_QUERY_TEMPLATE = """
SELECT taker AS wallet_address, MIN(block_time) AS first_buy_at
FROM dex.trades
WHERE blockchain = '{blockchain}'
  AND token_bought_address = {token_address}
  AND block_time >= NOW() - INTERVAL '{lookback_days}' day
GROUP BY taker
ORDER BY first_buy_at ASC
LIMIT {limit}
"""


def build_token_early_buyers_query(
    contract: str, *, blockchain: str = "base", lookback_days: int = 90, limit: int = 40,
) -> str:
    """Builds the query for the earliest buyers (by timestamp) of ONE
    specific token. Validates ``contract`` against a strict EVM format (0x +
    40 hex) BEFORE any substitution -- this parameter comes from a caller
    handling external token contracts, never an internal constant, so real
    anti-injection validation is needed (same doctrine as
    ``build_addresses_stats_query``). Emits a bare hexadecimal literal for
    ``token_address`` (varbinary, see reservation above) -- never
    single-quoted."""
    if not contract or not re.fullmatch(_EVM_ADDRESS_RE_SOURCE, contract):
        raise ValueError(f"invalid EVM contract address: {contract!r}")
    if not blockchain or not re.fullmatch(r"[a-z0-9_-]+", blockchain):
        raise ValueError("invalid blockchain")
    if not isinstance(lookback_days, int) or lookback_days <= 0:
        raise ValueError("lookback_days must be a positive integer")
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    return TOKEN_EARLY_BUYERS_QUERY_TEMPLATE.format(
        blockchain=blockchain, token_address=contract.lower(), lookback_days=lookback_days, limit=limit,
    )


@dataclass
class TokenEarlyBuyersResult:
    wallets: list[str] = field(default_factory=list)
    available: bool = True
    error: str | None = None


async def get_token_early_buyers(
    contract: str, *, blockchain: str = "base", lookback_days: int = 90, limit: int = 40,
    performance: str = "small",
) -> TokenEarlyBuyersResult:
    """Earliest buyers (by timestamp) of a specific token, sorted oldest to
    most recent -- same dome doctrine as the rest of this module: without a
    key, invalid address, or failure at any step ->
    ``available=False``, never an exception, never a fabricated wallet."""
    try:
        sql = build_token_early_buyers_query(
            contract, blockchain=blockchain, lookback_days=lookback_days, limit=limit,
        )
    except ValueError as exc:
        return TokenEarlyBuyersResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    exec_result = await run_sql_and_wait(sql, performance=performance)
    if not exec_result.available:
        return TokenEarlyBuyersResult(available=False, error=exec_result.error)

    wallets = [
        row["wallet_address"] for row in exec_result.rows
        if isinstance(row.get("wallet_address"), str) and row["wallet_address"]
    ]
    return TokenEarlyBuyersResult(wallets=wallets, available=True, error=None)


# ---------------------------------------------------------------------------
# 22/07 -- "disguised liquidity exit" (picked up from stress-test Part 11 --
# a proposal evaluated hypothetically, never coded before this day).
# Objective: spot wallets that received a DIRECT distribution from the
# deployer (or the initial mint) shortly after launch -- "insiders" who
# never carry the "creator" label and therefore fully escape dev_wallet.py
# (which monitors ONLY the deployer wallet itself).
#
# VERIFIED by a real authenticated call (22/07) on a real case (CNX): the
# raw table `erc20_base.evt_transfer` (ALL ERC-20 transfers on Base, not
# just DEX trades like `dex.trades`) confirms the expected schema -- first
# transfer = mint from the zero address to the deployer, then distribution
# from the deployer to several secondary wallets in the following hours.
# Bare hex literals (contract_address, zero/deployer address) -- confirmed
# working on real execution against this table, same syntax as
# `dex.trades.token_bought_address` (varbinary).
#
# MANDATORY time window (unlike `dex.trades` where some existing queries
# scan without a bound): an unbounded scan on `erc20_base.evt_transfer`
# remains possible (tested, ~15s on a 28-day-old token) but gets more
# expensive as the token's history grows -- the caller must supply a
# reasonable window (e.g. the 14 days following pair creation, already known
# via `PairSnapshot.pair_created_at`).
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

INSIDER_RECIPIENTS_QUERY_TEMPLATE = """
SELECT "to" AS recipient, SUM(CAST(value AS DOUBLE)) AS total_received_raw,
       MIN(evt_block_time) AS first_received_at
FROM erc20_base.evt_transfer
WHERE contract_address = {token_address}
  AND ("from" = {deployer_address} OR "from" = {zero_address})
  AND evt_block_time >= TIMESTAMP '{window_start}'
  AND evt_block_time <= TIMESTAMP '{window_end}'
GROUP BY "to"
ORDER BY total_received_raw DESC
LIMIT {limit}
"""


def build_insider_recipients_query(
    contract: str, deployer: str, *, window_start: str, window_end: str, limit: int = 15,
) -> str:
    """Builds the query for wallets that received a DIRECT distribution from
    the deployer or the initial mint (zero address), within a given time
    window. Validates ``contract``/``deployer`` against a strict EVM format
    BEFORE any substitution (same doctrine as
    ``build_token_early_buyers_query``). ``window_start``/``window_end``:
    ISO strings ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS`` -- not validated
    here beyond a safe character format (the caller is responsible for
    supplying real dates, never free-form user input)."""
    if not contract or not re.fullmatch(_EVM_ADDRESS_RE_SOURCE, contract):
        raise ValueError(f"invalid EVM contract address: {contract!r}")
    if not deployer or not re.fullmatch(_EVM_ADDRESS_RE_SOURCE, deployer):
        raise ValueError(f"invalid EVM deployer address: {deployer!r}")
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2})?$")
    if not window_start or not date_re.fullmatch(window_start):
        raise ValueError(f"invalid window_start: {window_start!r}")
    if not window_end or not date_re.fullmatch(window_end):
        raise ValueError(f"invalid window_end: {window_end!r}")

    return INSIDER_RECIPIENTS_QUERY_TEMPLATE.format(
        token_address=contract.lower(), deployer_address=deployer.lower(),
        zero_address=_ZERO_ADDRESS, window_start=window_start, window_end=window_end, limit=limit,
    )


@dataclass
class InsiderRecipient:
    address: str
    total_received_raw: float
    first_received_at: str | None = None


@dataclass
class InsiderRecipientsResult:
    recipients: list[InsiderRecipient] = field(default_factory=list)
    available: bool = True
    error: str | None = None


async def get_insider_recipients(
    contract: str, deployer: str, *, window_start: str, window_end: str,
    limit: int = 15, performance: str = "small",
) -> InsiderRecipientsResult:
    """Wallets that received a direct distribution from the deployer/initial
    mint, sorted by descending amount. Same dome doctrine as the rest of the
    module: without a key, invalid address, or failure at any step ->
    ``available=False``, never an exception, never a fabricated wallet."""
    try:
        sql = build_insider_recipients_query(
            contract, deployer, window_start=window_start, window_end=window_end, limit=limit,
        )
    except ValueError as exc:
        return InsiderRecipientsResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    exec_result = await run_sql_and_wait(sql, performance=performance)
    if not exec_result.available:
        return InsiderRecipientsResult(available=False, error=exec_result.error)

    recipients: list[InsiderRecipient] = []
    for row in exec_result.rows:
        addr = row.get("recipient")
        if not isinstance(addr, str) or not addr:
            continue
        total = row.get("total_received_raw")
        if not isinstance(total, (int, float)):
            continue
        recipients.append(InsiderRecipient(
            address=addr, total_received_raw=float(total),
            first_received_at=row.get("first_received_at") if isinstance(row.get("first_received_at"), str) else None,
        ))
    return InsiderRecipientsResult(recipients=recipients, available=True, error=None)


# ---------------------------------------------------------------------------
# Dedicated SQL query -- last-resort fallback of the momentum pipeline's
# OHLCV cascade (#194, 16/07, explicit operator request: "wire them all up,
# I want a complete web with dexscreener and dune"). Reconstructs HOURLY
# candles from `prices.usd` (spellbook price table, minute granularity) --
# ONLY used when GeckoTerminal AND CoinMarketCap have both failed, since a
# Dune execution (`run_sql_and_wait`) potentially takes several dozen
# seconds AND consumes credits, unlike the two first providers (fast, free)
# -- never used first, never in parallel with the other two (a last resort,
# not a race).
#
# HONEST RESERVATION (same doctrine as the rest of this module): `prices.usd`
# is publicly documented with columns `blockchain`, `contract_address`,
# `symbol`, `price`, `decimals`, `minute` -- NOT verified by a real call this
# session. The type of `contract_address` is not confirmed (varchar or
# varbinary depending on the table, see the reservation already documented
# for `addresses.stats.address` earlier in this file, which turned out to be
# varbinary unlike `dex.trades.taker`) -- this module emits a bare
# hexadecimal literal (``0x...``, varbinary syntax) out of caution, to be
# reconfirmed via a real query before any prod use (14/07 norm).
#
# Hourly OHLC reconstruction from per-minute price points (not already
# candles): `MIN`/`MAX` of the price over the hour for low/high, and
# `array_agg(price ORDER BY minute)` + `element_at(..., 1)`/`element_at(..., -1)`
# (Trino/DuneSQL syntax) for open/close -- first and last chronological price
# of the hour. No volume in `prices.usd` -- `volume=0.0` assumed (honest
# degradation, never a fabricated value).
PRICE_HISTORY_QUERY_TEMPLATE = """
SELECT
    date_trunc('hour', minute) AS bucket,
    MIN(price) AS low,
    MAX(price) AS high,
    element_at(array_agg(price ORDER BY minute), 1) AS open,
    element_at(array_agg(price ORDER BY minute), -1) AS close
FROM prices.usd
WHERE blockchain = '{blockchain}'
  AND contract_address = {contract_address}
  AND minute >= NOW() - INTERVAL '{lookback_hours}' hour
GROUP BY 1
ORDER BY 1
"""


def build_price_history_query(contract_address: str, *, blockchain: str = "base", lookback_hours: int = 48) -> str:
    """Builds the query above. Validates the address (strict EVM format) and
    `lookback_hours` BEFORE any substitution -- same anti-injection
    guarantee as ``build_addresses_stats_query`` (the address can come from
    an arbitrary momentum candidate, never an internal constant)."""
    if not contract_address or not re.fullmatch(_EVM_ADDRESS_RE_SOURCE, contract_address):
        raise ValueError(f"invalid EVM address: {contract_address!r}")
    if not blockchain or not re.fullmatch(r"[a-z0-9_-]+", blockchain):
        raise ValueError("invalid blockchain")
    if not isinstance(lookback_hours, int) or lookback_hours <= 0:
        raise ValueError("lookback_hours must be a positive integer")
    return PRICE_HISTORY_QUERY_TEMPLATE.format(
        blockchain=blockchain,
        contract_address=contract_address.lower(),  # bare hex literal -- see varbinary reservation above
        lookback_hours=lookback_hours,
    )


@dataclass
class DunePriceHistoryResult:
    candles: list = field(default_factory=list)  # list[Candle], deferred import -- avoids a cycle with ta_levels
    available: bool = True
    error: str | None = None


async def get_price_history(
    contract_address: str, *, blockchain: str = "base", lookback_hours: int = 48, performance: str = "small",
) -> DunePriceHistoryResult:
    """Hourly candles reconstructed from Dune (last-resort fallback of the
    OHLCV cascade, #194) -- ``available=False`` without a key, on invalid
    address, or on failure at any execution step. Chain unsupported by
    ``prices.usd`` or no trade within the window: empty list, never a
    fabricated price."""
    from aria_core.skills.ta_levels import Candle

    try:
        sql = build_price_history_query(contract_address, blockchain=blockchain, lookback_hours=lookback_hours)
    except ValueError as exc:
        return DunePriceHistoryResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    exec_result = await run_sql_and_wait(sql, performance=performance)
    if not exec_result.available:
        return DunePriceHistoryResult(available=False, error=exec_result.error)

    candles: list[Candle] = []
    for row in exec_result.rows:
        try:
            open_ = float(row.get("open"))
            high = float(row.get("high"))
            low = float(row.get("low"))
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        bucket = row.get("bucket")
        ts = 0
        if isinstance(bucket, str):
            import datetime

            try:
                ts = int(datetime.datetime.fromisoformat(bucket.replace("Z", "+00:00")).timestamp())
            except ValueError:
                ts = 0
        candles.append(Candle(ts=ts, open=open_, high=high, low=low, close=close, volume=0.0))

    return DunePriceHistoryResult(candles=candles, available=True, error=None)
