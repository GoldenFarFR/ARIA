"""Early legitimacy shadow (15/08) -- owner_renounced/lp_lock_snapshot mocked
Web3 clients (no real RPC calls), persistence/upsert behavior, and the
forward-price-tracking helpers against a real (isolated tmp) candle_history
table. Same isolated-tmp-sqlite pattern used across this dome's shadow-pocket tests."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import early_legitimacy_shadow as shadow

CONTRACT = "0x" + "a" * 40
CHAIN = "base"
PAIR = "0x" + "b" * 40


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    # price_at_horizon reads candle_history directly, assuming it already
    # exists (owned by candle_history.py in prod) -- create the empty
    # table here so tests don't depend on that other module.
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS candle_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chain TEXT, pool_address TEXT, "
            "contract TEXT, timeframe TEXT, mode TEXT, ts INTEGER, "
            "open REAL, high REAL, low REAL, close REAL, volume REAL)"
        )
        await db.commit()
    yield
    shadow._ensured_db_paths.clear()


async def _row(contract=CONTRACT, chain=CHAIN):
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM early_legitimacy_shadow_log WHERE contract = ? AND chain = ?",
            (contract, chain),
        )
        row = await cur.fetchone()
        return dict(row) if row is not None else None


class _FakeFunctionCall:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises

    def call(self):
        if self._raises is not None:
            raise self._raises
        return self._result


class _FakeFunctions:
    def __init__(self, owner_result=None, owner_raises=None):
        self._owner_result = owner_result
        self._owner_raises = owner_raises

    def owner(self):
        return _FakeFunctionCall(self._owner_result, self._owner_raises)


class _FakeOwnerContract:
    def __init__(self, owner_result=None, owner_raises=None):
        self.functions = _FakeFunctions(owner_result, owner_raises)


class _FakeEventFilter:
    def __init__(self, logs_by_range: dict[tuple[int, int], list[dict]]):
        self._logs_by_range = logs_by_range

    def get_logs(self, from_block, to_block):
        return self._logs_by_range.get((from_block, to_block), [])


class _FakeEvents:
    def __init__(self, logs_by_range):
        self._logs_by_range = logs_by_range

    def Transfer(self):
        return _FakeEventFilter(self._logs_by_range)


class _FakeLpContract:
    def __init__(self, logs_by_range):
        self.events = _FakeEvents(logs_by_range)


class _FakeEth:
    def __init__(self, block_number, contract_factory):
        self.block_number = block_number
        self._contract_factory = contract_factory

    def contract(self, address, abi):
        return self._contract_factory(address, abi)


class _FakeW3:
    def __init__(self, block_number=1000, contract_factory=None):
        self.eth = _FakeEth(block_number, contract_factory or (lambda address, abi: None))

    def to_checksum_address(self, addr):
        return addr


# --- owner_renounced -------------------------------------------------------


def test_owner_renounced_true_for_zero_address():
    w3 = _FakeW3(contract_factory=lambda address, abi: _FakeOwnerContract(
        owner_result="0x0000000000000000000000000000000000000000"
    ))
    assert shadow.owner_renounced(CONTRACT, w3=w3) is True


def test_owner_renounced_false_for_real_owner():
    w3 = _FakeW3(contract_factory=lambda address, abi: _FakeOwnerContract(
        owner_result="0x000000000000000000000000000000000000AbCd"
    ))
    assert shadow.owner_renounced(CONTRACT, w3=w3) is False


def test_owner_renounced_none_when_function_absent():
    w3 = _FakeW3(contract_factory=lambda address, abi: _FakeOwnerContract(
        owner_raises=Exception("execution reverted")
    ))
    assert shadow.owner_renounced(CONTRACT, w3=w3) is None


def test_owner_renounced_none_for_empty_address():
    assert shadow.owner_renounced("", w3=_FakeW3()) is None


# --- lp_lock_snapshot -------------------------------------------------------


def test_lp_lock_snapshot_none_without_pair_address():
    assert shadow.lp_lock_snapshot("", w3=_FakeW3()) is None


def test_lp_lock_snapshot_computes_burned_pct():
    # tip=999, chunk_blocks=500, max_chunks=1 -> from_block=499, first chunk
    # covers (499, 998), a trailing 1-block chunk (999, 999) follows (inclusive
    # range math means the loop can run one small extra chunk past max_chunks,
    # never past chunk_blocks in size -- the safety bound this test cares about).
    burn = "0x000000000000000000000000000000000000dead"
    logs_by_range = {
        (499, 998): [
            {"args": {"from": "0x0000000000000000000000000000000000cafe", "to": burn, "value": 80}},
            {"args": {"from": "0x0000000000000000000000000000000000cafe", "to": "0x00000000000000000000000000000000001234", "value": 20}},
        ],
    }
    w3 = _FakeW3(block_number=999, contract_factory=lambda address, abi: _FakeLpContract(logs_by_range))
    result = shadow.lp_lock_snapshot(PAIR, w3=w3, chunk_blocks=500, max_chunks=1)
    assert result is not None
    assert result["locked_or_burned_pct"] == pytest.approx(80.0)
    assert result["complete"] is True


def test_lp_lock_snapshot_none_total_when_no_transfers():
    w3 = _FakeW3(block_number=999, contract_factory=lambda address, abi: _FakeLpContract({}))
    result = shadow.lp_lock_snapshot(PAIR, w3=w3, chunk_blocks=500, max_chunks=1)
    assert result == {"locked_or_burned_pct": None, "blocks_covered": 501, "complete": True}


def test_lp_lock_snapshot_partial_on_chunk_failure():
    class _RaisingEvents(_FakeEvents):
        def Transfer(self):
            class _Raiser:
                def get_logs(self, from_block, to_block):
                    raise Exception("413 payload too large")
            return _Raiser()

    class _RaisingLpContract:
        def __init__(self):
            self.events = _RaisingEvents({})

    w3 = _FakeW3(block_number=999, contract_factory=lambda address, abi: _RaisingLpContract())
    result = shadow.lp_lock_snapshot(PAIR, w3=w3, chunk_blocks=500, max_chunks=1)
    assert result is not None
    assert result["complete"] is False


def test_lp_lock_snapshot_none_on_chain_tip_failure():
    class _BrokenEth:
        @property
        def block_number(self):
            raise Exception("RPC down")

    class _BrokenW3:
        eth = _BrokenEth()

        def to_checksum_address(self, addr):
            return addr

    assert shadow.lp_lock_snapshot(PAIR, w3=_BrokenW3()) is None


# --- record_observation / already_computed ---------------------------------


@pytest.mark.asyncio
async def test_record_observation_persists_both_signals():
    w3 = _FakeW3(
        block_number=999,
        contract_factory=lambda address, abi: (
            _FakeOwnerContract(owner_result="0x0000000000000000000000000000000000000000")
            if abi is shadow._OWNER_ABI
            else _FakeLpContract({})
        ),
    )
    await shadow.record_observation(
        CONTRACT, CHAIN, symbol="TOK", lp_pair_address=PAIR, w3=w3,
    )
    row = await _row()
    assert row is not None
    assert row["symbol"] == "TOK"
    assert row["owner_renounced"] == 1
    assert row["lp_pair_address"] == PAIR


@pytest.mark.asyncio
async def test_record_observation_no_lp_pair_leaves_lp_fields_null():
    w3 = _FakeW3(contract_factory=lambda address, abi: _FakeOwnerContract(owner_raises=Exception("no owner()")))
    await shadow.record_observation(CONTRACT, CHAIN, symbol="TOK", lp_pair_address=None, w3=w3)
    row = await _row()
    assert row["owner_renounced"] is None
    assert row["lp_pair_address"] is None
    assert row["lp_locked_or_burned_pct"] is None


@pytest.mark.asyncio
async def test_record_observation_upserts_on_reevaluation():
    w3 = _FakeW3(contract_factory=lambda address, abi: _FakeOwnerContract(owner_raises=Exception("no owner()")))
    await shadow.record_observation(CONTRACT, CHAIN, symbol="OLD", w3=w3)
    await shadow.record_observation(CONTRACT, CHAIN, symbol="NEW", w3=w3)
    async with aiosqlite.connect(shadow._db_path()) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM early_legitimacy_shadow_log WHERE contract = ? AND chain = ?",
            (CONTRACT, CHAIN),
        )
        (count,) = await cur.fetchone()
    assert count == 1
    row = await _row()
    assert row["symbol"] == "NEW"


@pytest.mark.asyncio
async def test_record_observation_never_raises_on_empty_contract():
    await shadow.record_observation("", CHAIN, w3=_FakeW3())
    assert await _row("") is None


@pytest.mark.asyncio
async def test_already_computed_false_then_true():
    assert await shadow.already_computed(CONTRACT, CHAIN) is False
    w3 = _FakeW3(contract_factory=lambda address, abi: _FakeOwnerContract(owner_raises=Exception("no owner()")))
    await shadow.record_observation(CONTRACT, CHAIN, w3=w3)
    assert await shadow.already_computed(CONTRACT, CHAIN) is True


@pytest.mark.asyncio
async def test_list_recent_newest_first():
    w3 = _FakeW3(contract_factory=lambda address, abi: _FakeOwnerContract(owner_raises=Exception("no owner()")))
    await shadow.record_observation(CONTRACT, CHAIN, symbol="A", w3=w3)
    other = "0x" + "c" * 40
    await shadow.record_observation(other, CHAIN, symbol="B", w3=w3)
    rows = await shadow.list_recent()
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"A", "B"}


# --- price_at_horizon / forward_price_deltas_pct ----------------------------


async def _insert_candle(ts: int, close: float, contract=CONTRACT, chain=CHAIN):
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS candle_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chain TEXT, pool_address TEXT, "
            "contract TEXT, timeframe TEXT, mode TEXT, ts INTEGER, "
            "open REAL, high REAL, low REAL, close REAL, volume REAL)"
        )
        await db.execute(
            "INSERT INTO candle_history (chain, pool_address, contract, timeframe, mode, ts, "
            "open, high, low, close, volume) VALUES (?, 'pool', ?, '1h', 'live', ?, ?, ?, ?, ?, 1.0)",
            (chain, contract, ts, close, close, close, close),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_price_at_horizon_finds_closest_within_tolerance():
    base_ts = 1_700_000_000
    await _insert_candle(base_ts, 100.0)
    await _insert_candle(base_ts + 3600, 110.0)
    from_iso = "2023-11-14T22:13:20+00:00"  # == base_ts
    price = await shadow.price_at_horizon(CONTRACT, CHAIN, from_iso, 1.0)
    assert price == pytest.approx(110.0)


@pytest.mark.asyncio
async def test_price_at_horizon_none_outside_tolerance():
    base_ts = 1_700_000_000
    await _insert_candle(base_ts, 100.0)
    from_iso = "2023-11-14T22:13:20+00:00"
    price = await shadow.price_at_horizon(CONTRACT, CHAIN, from_iso, 24.0)
    assert price is None


@pytest.mark.asyncio
async def test_price_at_horizon_none_on_bad_iso():
    assert await shadow.price_at_horizon(CONTRACT, CHAIN, "not-a-date", 1.0) is None


@pytest.mark.asyncio
async def test_forward_price_deltas_pct_computes_available_horizons():
    base_ts = 1_700_000_000
    from_iso = "2023-11-14T22:13:20+00:00"
    await _insert_candle(base_ts, 100.0)
    await _insert_candle(base_ts + 3600, 120.0)  # +20% at 1h
    await _insert_candle(base_ts + 6 * 3600, 90.0)  # -10% at 6h
    deltas = await shadow.forward_price_deltas_pct(CONTRACT, CHAIN, from_iso)
    assert deltas["1h"] == pytest.approx(20.0)
    assert deltas["6h"] == pytest.approx(-10.0)
    assert deltas["24h"] is None
    assert deltas["7d"] is None


@pytest.mark.asyncio
async def test_forward_price_deltas_pct_all_none_without_entry_anchor():
    deltas = await shadow.forward_price_deltas_pct(CONTRACT, CHAIN, "2023-11-14T22:13:20+00:00")
    assert all(v is None for v in deltas.values())
