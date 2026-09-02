"""Reconstruct a token's ENTIRE on-chain history from raw events, for replay.

Why this exists (02/09, operator-directed): every price source ARIA can reach
is an aggregator, and aggregators were caught lying this same session -- one
bot showed -67.8% in its header, -5% in its own table, while the chain read
-91.88%; another showed 47 X followers where there were 75. The operator's
conclusion, which this module implements: *"il faut utiliser notre pipeline
rpc"*, with a tolerance of 1-5s against the chain. Block timestamps on
Robinhood give 0.101s granularity, so the chain is not just more trustworthy
here, it is an order of magnitude more precise.

**The raw table is immutable and holds NO interpretation.** ``topics_json``
and ``data_hex`` are stored exactly as the node returned them. Decoded columns
sit beside them as a convenience, never as the source of truth -- so if our
decoding turns out wrong (already happened once this session, on V4 offsets),
it is recomputed from the stored bytes without re-spending a single RU. The
operator's rule verbatim: *"conserve le brut avant toute interpretation"*.

**What this module deliberately does NOT do**: no buy/sell classification, no
RSI, no EMA, no bottom detection, no score, no signal. Those belong to a later,
strictly causal observation layer. This pass answers one question only -- what
actually happened on chain, event by event. Mixing the two is how a replay ends
up describing a pattern it was built to find.

**tx_sender is NOT the trader.** The V4 ``Swap`` event's own ``sender`` is the
router, never the person. This module records ``tx.from`` under the name
``tx_sender`` precisely because even that is not necessarily the economic
buyer (aggregator, smart wallet, relayer). Naming it ``trader`` would bake an
unverified claim into the schema.

Cost, measured on the real chain before writing this (MEOW, ~24h of history):
86 ``eth_getLogs`` calls cover the token's whole life at Chainstack's hard
10,000-block range limit -- 0.013% of Robinhood's 650k daily cap. Timestamps
and senders come from ``eth_getBlockByNumber(full=True)``, ONE call per block
that actually carries an event, which also yields every ``tx.from`` in it --
strictly cheaper than one ``eth_getTransactionByHash`` per swap whenever a
block holds more than one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import httpx

from aria_core.paths import aria_db_path
from aria_core.services import chainstack_ru_budget
from aria_core.services.doppler import POOL_MANAGER_ADDRESS
from aria_core.services.evm_swap_ws import (
    _POOL_MANAGER_BY_CHAIN,
    _V4_MODIFY_LIQUIDITY_TOPIC,
    _V4_SWAP_TOPIC,
    _topic0,
)

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
TABLE = "onchain_replay_raw"
POOL_TABLE = "onchain_replay_pool_discovery"

# Chainstack's hard limit, measured live 02/09: 10,000 accepted, 20,000
# refused with "Block range limit exceeded". Not guessed, not copied from a
# doc -- probed against the real endpoint (the repo's standing rule for any
# throughput constant).
MAX_BLOCK_RANGE = 10_000

# Same Initialize signature doppler.py already carries as an ABI dict. Only
# the topic0 is needed here (we filter, never decode through web3), and
# currency0/currency1 are INDEXED -- which is what makes on-chain pool
# discovery a single filtered query instead of a full-chain scan.
_V4_INITIALIZE_TOPIC = _topic0(
    "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
)

EVENT_INITIALIZE = "initialize"
EVENT_SWAP = "swap"
EVENT_MODIFY_LIQUIDITY = "modify_liquidity"

_TOPIC_TO_EVENT = {
    _V4_INITIALIZE_TOPIC.lower(): EVENT_INITIALIZE,
    _V4_SWAP_TOPIC.lower(): EVENT_SWAP,
    _V4_MODIFY_LIQUIDITY_TOPIC.lower(): EVENT_MODIFY_LIQUIDITY,
}

_HTTP_TIMEOUT_S = 60.0
_MAX_RETRIES = 4

_RAW_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    token TEXT NOT NULL,
    pool_id TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    block_timestamp INTEGER,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    data_hex TEXT NOT NULL,
    tx_sender TEXT,
    rpc_provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(chain, tx_hash, log_index)
)
"""

_POOL_DDL = f"""
CREATE TABLE IF NOT EXISTS {POOL_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    token TEXT NOT NULL,
    pool_id TEXT NOT NULL,
    currency0 TEXT,
    currency1 TEXT,
    init_block INTEGER,
    swap_count INTEGER NOT NULL DEFAULT 0,
    liquidity_event_count INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL,
    decision_reason TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    UNIQUE(chain, token, pool_id)
)
"""


@dataclass
class PoolCandidate:
    """One pool found on chain, kept whether selected or not.

    Rejected pools are persisted deliberately: the operator's requirement is
    that the replay stays auditable -- "pool A selected, pool B rejected:
    insufficient activity" -- rather than silently reconstructing one
    trajectory and calling it the token's. MEOW has five pools, and the one
    with $0.01 of liquidity reports a $28bn market cap; a backfill that picks
    the first one it finds is wrong by six orders of magnitude.
    """

    pool_id: str
    currency0: str | None = None
    currency1: str | None = None
    init_block: int | None = None
    swap_count: int = 0
    liquidity_event_count: int = 0
    selected: bool = False
    decision_reason: str = ""


@dataclass
class BackfillResult:
    token: str
    chain: str
    pools: list[PoolCandidate] = field(default_factory=list)
    events_written: int = 0
    events_seen: int = 0
    blocks_resolved: int = 0
    rpc_calls: int = 0
    first_block: int | None = None
    last_block: int | None = None
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    error: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rpc_url(chain: str) -> str:
    """HTTPS endpoint derived from the chain's WSS variable.

    The repo's standing wss->https rule: same host, same credential, different
    scheme, never a second variable to keep in sync. The credentialed URL is
    never returned to a caller nor logged -- only ``rpc_provider`` is stored
    (two real secret leaks through Bash in July made that absolute).
    """
    var = f"ARIA_{chain.upper()}_RPC_WS"
    ws = (os.environ.get(var, "") or "").strip()
    if not ws:
        return ""
    if ws.startswith("wss://"):
        return "https://" + ws[len("wss://"):]
    if ws.startswith("ws://"):
        return "http://" + ws[len("ws://"):]
    return ""


def pool_manager_for(chain: str) -> str:
    """Per-chain PoolManager, resolved through evm_swap_ws's own mapping.

    NEVER the imported ``POOL_MANAGER_ADDRESS`` constant directly: that is
    Base's, and using it on Robinhood returns zero logs for the whole chain --
    a failure that looks exactly like "this token never traded". Cost me a
    wrong diagnosis on 02/09 before I checked the mapping.
    """
    return _POOL_MANAGER_BY_CHAIN.get(chain, POOL_MANAGER_ADDRESS)


class _Rpc:
    """Minimal JSON-RPC caller with retry, and RU accounting on every call."""

    def __init__(self, chain: str, url: str) -> None:
        self.chain = chain
        self._url = url
        self.calls = 0

    async def __aenter__(self) -> "_Rpc":
        self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def call(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                res = await self._client.post(self._url, json=payload)
                self.calls += 1
                # Every standard call is 1 RU. Recorded through the EXISTING
                # budget mechanism -- never a second accounting of our own.
                chainstack_ru_budget.record_usage_fast(self.chain, 1)
                body = res.json()
                if "error" in body:
                    raise RuntimeError(str(body["error"].get("message", "rpc error"))[:200])
                return body.get("result")
            except Exception as exc:  # noqa: BLE001 -- retried below, surfaced if final
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"rpc {method} failed after {_MAX_RETRIES} attempts: {type(last_exc).__name__}")


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_RAW_DDL)
        await db.execute(_POOL_DDL)
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_replay_token_block "
            f"ON {TABLE} (chain, token, block_number, log_index)"
        )
        await db.commit()


def _pad_address_topic(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


async def discover_pools(rpc: _Rpc, token: str, chain: str, from_block: int, to_block: int) -> list[PoolCandidate]:
    """Every pool this token was initialized in, found ON CHAIN.

    Not from an aggregator: ``Initialize`` has ``currency0``/``currency1``
    indexed, so one filtered query per side returns exactly this token's
    pools. That matters because the aggregator consulted this session was
    wrong on three separate metrics -- taking its pool list on faith would
    inherit whatever it got wrong.
    """
    pm = pool_manager_for(chain)
    token_topic = _pad_address_topic(token)
    found: dict[str, PoolCandidate] = {}

    # currency0 is topic2, currency1 is topic3 (topic1 being the poolId), and
    # a token sits on either side depending on address ordering -- so both are
    # queried. The filter list MUST hold four entries: a three-entry list
    # silently shifts the query onto currency0 for both passes, which is
    # exactly the bug this comment replaces. Measured symptom: MEOW's real
    # pool (3161 swaps, MEOW as currency1 against native ETH at
    # 0x000...000) was structurally invisible, and discovery returned only a
    # dead pool with 0 swaps -- a false "this token barely traded".
    for position in (2, 3):
        topics: list[Any] = [_V4_INITIALIZE_TOPIC, None, None, None]
        topics[position] = token_topic
        block = from_block
        while block <= to_block:
            end = min(block + MAX_BLOCK_RANGE, to_block)
            logs = await rpc.call("eth_getLogs", [{
                "fromBlock": hex(block), "toBlock": hex(end),
                "address": pm, "topics": topics,
            }]) or []
            for log in logs:
                tp = log.get("topics") or []
                if len(tp) < 4:
                    continue
                pid = tp[1]
                found.setdefault(pid, PoolCandidate(
                    pool_id=pid,
                    currency0="0x" + tp[2][-40:],
                    currency1="0x" + tp[3][-40:],
                    init_block=int(log["blockNumber"], 16),
                ))
            block = end + 1
    return list(found.values())


async def _count_pool_activity(rpc: _Rpc, chain: str, pools: list[PoolCandidate], from_block: int, to_block: int) -> None:
    """Swap and liquidity-event counts per pool -- the selection criterion.

    Activity, not declared liquidity: a pool can hold a nominal balance and
    never trade. Counting real events is both cheaper (already fetched) and
    harder to game than a reported TVL figure.
    """
    if not pools:
        return
    pm = pool_manager_for(chain)
    by_id = {p.pool_id.lower(): p for p in pools}
    block = from_block
    while block <= to_block:
        end = min(block + MAX_BLOCK_RANGE, to_block)
        logs = await rpc.call("eth_getLogs", [{
            "fromBlock": hex(block), "toBlock": hex(end), "address": pm,
            "topics": [[_V4_SWAP_TOPIC, _V4_MODIFY_LIQUIDITY_TOPIC], [p.pool_id for p in pools]],
        }]) or []
        for log in logs:
            tp = log.get("topics") or []
            if len(tp) < 2:
                continue
            cand = by_id.get(tp[1].lower())
            if cand is None:
                continue
            if tp[0].lower() == _V4_SWAP_TOPIC.lower():
                cand.swap_count += 1
            else:
                cand.liquidity_event_count += 1
        block = end + 1


def select_pools(pools: list[PoolCandidate], *, min_swaps: int = 10) -> list[PoolCandidate]:
    """Mark which pools carry the token's real trading, and why.

    ``min_swaps`` is an explicitly UNCALIBRATED starting value, not a validated
    threshold -- it exists to separate a live market from dust, and MEOW's real
    split made that separation obvious rather than borderline (3161 swaps on
    one pool, 13 across the other four). Every pool keeps its reason string so
    a later reader can disagree with this call without re-running the scan.
    """
    for p in pools:
        if p.swap_count >= min_swaps:
            p.selected = True
            p.decision_reason = f"selected: {p.swap_count} swaps"
        else:
            p.selected = False
            p.decision_reason = f"rejected: only {p.swap_count} swaps (< {min_swaps})"
    return [p for p in pools if p.selected]


async def _persist_pools(token: str, chain: str, pools: list[PoolCandidate]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        for p in pools:
            await db.execute(
                f"INSERT INTO {POOL_TABLE} (chain, token, pool_id, currency0, currency1, init_block, "
                f"swap_count, liquidity_event_count, selected, decision_reason, discovered_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                f"ON CONFLICT(chain, token, pool_id) DO UPDATE SET "
                f"swap_count=excluded.swap_count, liquidity_event_count=excluded.liquidity_event_count, "
                f"selected=excluded.selected, decision_reason=excluded.decision_reason",
                (chain, token.lower(), p.pool_id, p.currency0, p.currency1, p.init_block,
                 p.swap_count, p.liquidity_event_count, int(p.selected), p.decision_reason, _now_iso()),
            )
        await db.commit()


async def backfill(
    token: str,
    chain: str,
    *,
    lookback_blocks: int = 900_000,
    resolve_senders: bool = True,
    provider: str = "chainstack",
) -> BackfillResult:
    """Collect a token's full raw event history into the immutable raw table.

    ``lookback_blocks`` defaults to ~25h at Robinhood's measured 0.101s block
    time -- enough to cover a token born within the day. Idempotent by
    construction: ``UNIQUE(chain, tx_hash, log_index)`` means re-running adds
    only what was missing, so an interrupted pass resumes for free.
    """
    result = BackfillResult(token=token.lower(), chain=chain)
    url = rpc_url(chain)
    if not url:
        result.error = f"no RPC configured for chain '{chain}'"
        return result
    if not await chainstack_ru_budget.can_spend(chain):
        result.error = "chainstack RU budget exhausted for today"
        return result

    await _ensure_tables()
    pm = pool_manager_for(chain)

    async with _Rpc(chain, url) as rpc:
        head = int(await rpc.call("eth_blockNumber", []), 16)
        from_block = max(0, head - lookback_blocks)

        pools = await discover_pools(rpc, token, chain, from_block, head)
        if not pools:
            result.error = "no pool found on chain for this token in the window"
            result.rpc_calls = rpc.calls
            return result

        await _count_pool_activity(rpc, chain, pools, from_block, head)
        selected = select_pools(pools)
        await _persist_pools(token, chain, pools)
        result.pools = pools
        if not selected:
            result.error = "no pool carried enough activity to reconstruct"
            result.rpc_calls = rpc.calls
            return result

        # Start from the earliest Initialize rather than the lookback floor:
        # the operator's requirement is the token's REAL T0, not an arbitrary
        # window edge. A pool with no init block found falls back to the floor.
        init_blocks = [p.init_block for p in selected if p.init_block is not None]
        scan_from = min(init_blocks) if init_blocks else from_block

        rows: list[tuple] = []
        pool_ids = [p.pool_id for p in selected]
        by_block: dict[int, list[int]] = {}

        block = scan_from
        while block <= head:
            end = min(block + MAX_BLOCK_RANGE, head)
            logs = await rpc.call("eth_getLogs", [{
                "fromBlock": hex(block), "toBlock": hex(end), "address": pm,
                "topics": [[_V4_INITIALIZE_TOPIC, _V4_SWAP_TOPIC, _V4_MODIFY_LIQUIDITY_TOPIC], pool_ids],
            }]) or []
            for log in logs:
                tp = log.get("topics") or []
                if not tp:
                    continue
                etype = _TOPIC_TO_EVENT.get(tp[0].lower())
                if etype is None:
                    continue
                bn = int(log["blockNumber"], 16)
                li = int(log["logIndex"], 16)
                by_block.setdefault(bn, []).append(li)
                rows.append((
                    chain, token.lower(), tp[1] if len(tp) > 1 else "",
                    bn, None, log["transactionHash"], li, etype,
                    json.dumps(tp), log.get("data", ""), None, provider, _now_iso(),
                ))
            block = end + 1

        result.events_seen = len(rows)
        if not rows:
            result.rpc_calls = rpc.calls
            result.error = "no event found for the selected pools"
            return result

        # ONE eth_getBlockByNumber(full=True) per block that actually carries
        # an event: it returns the timestamp AND every tx.from in that block,
        # so it replaces one getTransactionByHash per swap whenever a block
        # holds more than one event. Strictly cheaper, never worse.
        ts_by_block: dict[int, int] = {}
        sender_by_tx: dict[str, str] = {}
        if resolve_senders:
            for bn in sorted(by_block):
                blk = await rpc.call("eth_getBlockByNumber", [hex(bn), True])
                if not blk:
                    continue
                ts_by_block[bn] = int(blk["timestamp"], 16)
                for tx in blk.get("transactions") or []:
                    if isinstance(tx, dict) and tx.get("hash"):
                        sender_by_tx[tx["hash"].lower()] = (tx.get("from") or "").lower()
            result.blocks_resolved = len(ts_by_block)

        final_rows = [
            (r[0], r[1], r[2], r[3], ts_by_block.get(r[3]), r[5], r[6], r[7], r[8], r[9],
             sender_by_tx.get(r[5].lower()), r[11], r[12])
            for r in rows
        ]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.executemany(
                f"INSERT OR IGNORE INTO {TABLE} "
                f"(chain, token, pool_id, block_number, block_timestamp, tx_hash, log_index, "
                f"event_type, topics_json, data_hex, tx_sender, rpc_provider, fetched_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                final_rows,
            )
            await db.commit()
            cur = await db.execute(
                f"SELECT COUNT(*), MIN(block_number), MAX(block_number), "
                f"MIN(block_timestamp), MAX(block_timestamp) FROM {TABLE} WHERE chain=? AND token=?",
                (chain, token.lower()),
            )
            row = await cur.fetchone()

        result.events_written = row[0] if row else 0
        result.first_block, result.last_block = (row[1], row[2]) if row else (None, None)
        result.first_timestamp, result.last_timestamp = (row[3], row[4]) if row else (None, None)
        result.rpc_calls = rpc.calls

    await chainstack_ru_budget.flush_pending()
    return result


async def summary(token: str, chain: str) -> dict:
    """What was collected, straight from storage -- never from memory."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT event_type, COUNT(*) n, COUNT(DISTINCT tx_sender) senders "
            f"FROM {TABLE} WHERE chain=? AND token=? GROUP BY 1",
            (chain, token.lower()),
        )
        by_type = {r["event_type"]: {"n": r["n"], "distinct_senders": r["senders"]} for r in await cur.fetchall()}
        cur = await db.execute(
            f"SELECT MIN(block_timestamp) a, MAX(block_timestamp) b, COUNT(*) n, "
            f"COUNT(DISTINCT pool_id) pools FROM {TABLE} WHERE chain=? AND token=?",
            (chain, token.lower()),
        )
        span = dict(await cur.fetchone() or {})
    return {"by_type": by_type, "span": span}
