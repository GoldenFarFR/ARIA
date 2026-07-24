"""Tests for the Virtuals bonding-curve entry engine (bonding_entry.py, 24/07
chantier). No real network call anywhere in this suite -- every external
dependency (VirtualsClient, virtual_usd_rate, entry_signals.detect_entry,
momentum_entry._technical_alignment, indicators.atr_series) is monkeypatched
or given deterministic real inputs.
"""
from __future__ import annotations

import pytest

from aria_core import bonding_entry
from aria_core.services.virtuals import VirtualToken, VirtualTrade
from aria_core.skills.entry_signals import EntrySignal


def _bonding_token(**overrides) -> VirtualToken:
    defaults = dict(
        name="Bonding Token",
        symbol="BOND",
        status="UNDERGRAD",
        chain="BASE",
        token_address=None,
        pre_token_address="0xPRE0000000000000000000000000000000000abcd",
        dev_holding_pct=0.5,
        top10_holder_pct=40.0,
        holder_count=20,
        liquidity_usd=15_000.0,
    )
    defaults.update(overrides)
    return VirtualToken(**defaults)


class _FakeVirtualsClient:
    def __init__(self, *, token: VirtualToken | None, trades: list[VirtualTrade] | None = None):
        self._token = token
        self._trades = trades if trades is not None else []
        self.fetch_by_address_calls = 0
        self.fetch_recent_trades_calls = 0

    async def fetch_by_address(self, token_address, chain="BASE"):
        self.fetch_by_address_calls += 1
        return self._token

    async def fetch_recent_trades(self, token_address, *, limit=200, chain_id=0):
        self.fetch_recent_trades_calls += 1
        return self._trades


def _patch_client(monkeypatch, client) -> None:
    monkeypatch.setattr("aria_core.services.virtuals.virtuals_client", client)


def _patch_usd_rate(monkeypatch, rate: float | None) -> None:
    async def _fake_rate():
        return rate

    monkeypatch.setattr("aria_core.services.virtuals.virtual_usd_rate", _fake_rate)


def _trades(n: int, *, base_price: float = 0.001) -> list[VirtualTrade]:
    return [
        VirtualTrade(timestamp=i, price=base_price * (1 + i * 0.01), is_buy=(i % 2 == 0))
        for i in range(n)
    ]


# ── Token resolution / bonding status ───────────────────────────────────────
@pytest.mark.asyncio
async def test_returns_none_when_token_unresolved(monkeypatch):
    _patch_client(monkeypatch, _FakeVirtualsClient(token=None))
    assert await bonding_entry.evaluate_bonding_entry("0xabc") is None


@pytest.mark.asyncio
async def test_returns_none_when_already_graduated(monkeypatch):
    graduated = _bonding_token(status="AVAILABLE", token_address="0xGRAD00000000000000000000000000000000")
    _patch_client(monkeypatch, _FakeVirtualsClient(token=graduated))
    assert await bonding_entry.evaluate_bonding_entry("0xabc") is None


# ── Gates: dev holding / top10 concentration / liquidity ────────────────────
@pytest.mark.asyncio
async def test_hold_when_dev_holding_too_high(monkeypatch):
    token = _bonding_token(dev_holding_pct=10.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "dev_holding_too_high"
    assert result["chain"] == bonding_entry.CHAIN_MARKER


@pytest.mark.asyncio
async def test_hold_when_dev_holding_unknown_fail_closed(monkeypatch):
    token = _bonding_token(dev_holding_pct=None)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "dev_holding_too_high"


@pytest.mark.asyncio
async def test_hold_when_top10_concentration_too_high(monkeypatch):
    token = _bonding_token(top10_holder_pct=95.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "holder_concentration"


@pytest.mark.asyncio
async def test_hold_when_top10_concentration_unknown_fail_closed(monkeypatch):
    token = _bonding_token(top10_holder_pct=None)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "holder_concentration"


@pytest.mark.asyncio
async def test_concentration_gate_neutralized_below_min_holders(monkeypatch):
    """24/07, real gap found live right after deploy: with too few genuine
    buyers, top10_holder_pct is mechanically ~100% (not a rug signal) --
    verified against the 100 real bonding prototypes at the time, every
    single one failed this gate, including the one with the most holders
    (33). Below _MIN_HOLDERS_FOR_CONCENTRATION_CHECK, the ratio is now
    treated as uninformative rather than a hard veto."""
    assert bonding_entry._MIN_HOLDERS_FOR_CONCENTRATION_CHECK > 3
    token = _bonding_token(top10_holder_pct=100.0, holder_count=3)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, []))
    _patch_usd_rate(monkeypatch, 0.5)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"  # never rejected on holder_concentration


@pytest.mark.asyncio
async def test_concentration_gate_neutralized_when_holder_count_unknown(monkeypatch):
    """Same neutralization when holder_count itself is missing -- we can't
    judge whether the ratio is meaningful, so it doesn't become a veto
    (dev_holding_pct/liquidity_usd remain the real guards, unaffected)."""
    token = _bonding_token(top10_holder_pct=100.0, holder_count=None)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, []))
    _patch_usd_rate(monkeypatch, 0.5)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"


@pytest.mark.asyncio
async def test_concentration_gate_still_enforced_above_min_holders(monkeypatch):
    """Regression guard: the gate must still apply once there ARE enough
    holders to make the ratio meaningful (matches the operator's own
    real-world example: a graduated token with 317 holders reads 54.2%,
    comfortably passing -- a token with enough holders but genuinely
    concentrated must still be rejected)."""
    token = _bonding_token(top10_holder_pct=95.0, holder_count=50)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "holder_concentration"


@pytest.mark.asyncio
async def test_hold_when_liquidity_below_bonding_floor(monkeypatch):
    token = _bonding_token(liquidity_usd=1_000.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "insufficient_liquidity"


@pytest.mark.asyncio
async def test_hold_when_liquidity_unknown_fail_closed(monkeypatch):
    token = _bonding_token(liquidity_usd=None)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "insufficient_liquidity"


@pytest.mark.asyncio
async def test_gates_never_call_goplus_or_dexscreener(monkeypatch):
    """Structural check (operator go, 24/07): this path must NOT depend on
    GoPlus/DexScreener at all -- a bonding token has neither a real DEX pool
    for the latter nor any relevance for the former (see module docstring).
    Simply asserting the function runs to completion using ONLY the fake
    Virtuals client (no other network client patched/available) proves this."""
    token = _bonding_token(liquidity_usd=1_000.0)  # will HOLD on liquidity gate
    client = _FakeVirtualsClient(token=token)
    _patch_client(monkeypatch, client)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "insufficient_liquidity"
    assert client.fetch_recent_trades_calls == 0  # gates short-circuit before trades


# ── No usable trade history ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hold_when_no_trades(monkeypatch):
    token = _bonding_token()
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=[]))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "ohlcv_unavailable"


# ── No entry signal / R/R too weak ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_hold_when_no_entry_signal(monkeypatch):
    token = _bonding_token()
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=False, reasons=["pas de setup"])

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "no_entry_signal"


@pytest.mark.asyncio
async def test_hold_when_rr_below_direct_threshold(monkeypatch):
    token = _bonding_token()
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup faible"], rr=1.2, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (0, []))

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] == "rr_below_direct_threshold"


# ── $VIRTUAL/USD conversion ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hold_when_usd_rate_unavailable(monkeypatch):
    token = _bonding_token()
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup fort"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, ["EMA/MACD"]))
    _patch_usd_rate(monkeypatch, None)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "usd_rate_unavailable"
    assert result["price"] is None  # never a fabricated USD price


@pytest.mark.asyncio
async def test_buy_converts_price_target_invalidation_to_usd(monkeypatch):
    """The core correctness fix of this chantier: VirtualTrade.price/
    EntrySignal.target/invalidation are all in $VIRTUAL -- must come back
    multiplied by the $VIRTUAL/USD rate, never left raw."""
    token = _bonding_token(dev_holding_pct=0.08, top10_holder_pct=54.19, liquidity_usd=13_792.21)
    trades = _trades(20, base_price=0.0011)  # trades[0].price after sort will be the entry
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=trades))

    execution_price_virtual = trades[0].price

    def fake_detect_entry(candles, *, execution_price=None):
        assert execution_price == execution_price_virtual
        return EntrySignal(
            present=True, reasons=["golden pocket + divergence RSI"],
            rr=3.0, target=execution_price_virtual * 1.5, invalidation=execution_price_virtual * 0.8,
        )

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, ["EMA/MACD"]))
    _patch_usd_rate(monkeypatch, 0.6055)

    result = await bonding_entry.evaluate_bonding_entry("0xabc", current_regime="neutre")

    assert result["action"] == "BUY"
    assert result["chain"] == bonding_entry.CHAIN_MARKER
    assert result["strategy"] == "momentum"
    assert result["price"] == pytest.approx(execution_price_virtual * 0.6055)
    assert result["target"] == pytest.approx(execution_price_virtual * 1.5 * 0.6055)
    assert result["invalidation"] == pytest.approx(execution_price_virtual * 0.8 * 0.6055)
    assert result["liquidity_usd"] == pytest.approx(13_792.21)
    assert result["regime"] == "neutre"
    # entry_atr_pct is a RATIO (ATR / price, both in $VIRTUAL) -- must NOT be
    # affected by the USD conversion (see module docstring).
    assert result["entry_atr_pct"] is None or result["entry_atr_pct"] >= 0


@pytest.mark.asyncio
async def test_buy_defaults_regime_to_neutre_when_absent(monkeypatch):
    token = _bonding_token()
    trades = _trades(20)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=trades))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, []))
    _patch_usd_rate(monkeypatch, 0.5)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["regime"] == "neutre"


# ── Conviction diligence (24/07, operator: "elle doit miser + sur le produit,
# la team, et le potentiel de hausse" -- product/team/adoption bet, since
# on-chain metrics don't mean anything yet at this stage) ───────────────────

def _setup_buy_mocks(monkeypatch, *, holder_count=20, top10_holder_pct=40.0):
    token = _bonding_token(holder_count=holder_count, top10_holder_pct=top10_holder_pct)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, []))
    _patch_usd_rate(monkeypatch, 0.5)
    return token


def test_socials_to_known_links_maps_virtuals_native_labels():
    socials = [
        {"label": "WEBSITE", "url": "https://holostudio.example"},
        {"label": "TWITTER", "url": "https://x.com/holostudio"},
        {"label": "github", "url": "https://github.com/holostudio/core"},
        {"label": "telegram", "url": "https://t.me/holostudio"},
        {"label": "warpcast", "url": "https://warpcast.com/holostudio"},
        {"label": "discord", "url": "https://discord.gg/holostudio"},  # unmapped, passes through
        {"label": "", "url": ""},  # no url -- dropped
    ]

    mapped = bonding_entry._socials_to_known_links(socials)

    by_label = {m["label"]: m["url"] for m in mapped}
    assert by_label["Site officiel"] == "https://holostudio.example"
    assert by_label["X (Twitter)"] == "https://x.com/holostudio"
    assert by_label["GitHub"] == "https://github.com/holostudio/core"
    assert by_label["Telegram"] == "https://t.me/holostudio"
    assert by_label["Farcaster"] == "https://warpcast.com/holostudio"
    assert by_label["discord"] == "https://discord.gg/holostudio"
    assert len(mapped) == 6  # the empty-url entry is dropped


def test_socials_to_known_links_empty_input():
    assert bonding_entry._socials_to_known_links([]) == []
    assert bonding_entry._socials_to_known_links(None) == []


@pytest.mark.asyncio
async def test_buy_forwards_socials_as_known_links_to_conviction_research(monkeypatch):
    """The whole point of this chantier: a bonding token's own declared
    GitHub/site/X should be used DIRECTLY by conviction_research, not
    rediscovered by heuristic -- same shortcut the standard momentum
    pipeline gets from DexScreener's project_links."""
    socials = [
        {"label": "WEBSITE", "url": "https://holostudio.example"},
        {"label": "github", "url": "https://github.com/holostudio/core"},
    ]
    token = _setup_buy_mocks(monkeypatch)
    token.socials = socials

    captured = {}

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        captured["contract"] = contract
        captured["chain"] = chain
        captured["known_links"] = known_links
        return bonding_entry.ConvictionResearch(available=False)  # not the point of this test

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    await bonding_entry.evaluate_bonding_entry("0xabc")

    assert captured["chain"] == "base"  # never CHAIN_MARKER -- see module comment
    by_label = {l["label"]: l["url"] for l in captured["known_links"]}
    assert by_label["Site officiel"] == "https://holostudio.example"
    assert by_label["GitHub"] == "https://github.com/holostudio/core"


@pytest.mark.asyncio
async def test_buy_uses_conviction_score_for_sizing_when_available(monkeypatch):
    """potential_score/conviction_* must reach the returned dict as-is --
    paper_trader.compute_entry_alloc already reads potential_score from it,
    no further wiring needed downstream."""
    _setup_buy_mocks(monkeypatch)

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(
            available=True, website_url="https://holostudio.example",
            potential_score=7.5, rationale="produit réel, revenus déjà générés",
            posting_cadence="active", contract_corroborated=True,
            process_trail=["Tavily tenté", "Site officiel trouvé"],
        )

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"
    assert result["potential_score"] == pytest.approx(7.5)
    assert result["conviction_website_corroborated"] is True
    assert result["conviction_posting_cadence"] == "active"
    assert result["conviction_process_trail"] == "Tavily tenté -> Site officiel trouvé"
    assert "potentiel fondamental 7.5/10" in " ".join(result["reasons"])


@pytest.mark.asyncio
async def test_buy_potential_score_none_when_conviction_research_unavailable(monkeypatch):
    """Never a gate: conviction_research disabled/unavailable degrades to
    potential_score=None, the BUY still goes through (fail-open on unknown,
    same doctrine as momentum_entry.py)."""
    _setup_buy_mocks(monkeypatch)

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=False, reason="ARIA_CONVICTION_RESEARCH_ENABLED désactivé")

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"
    assert result["potential_score"] is None
    assert result["conviction_process_trail"] is None


# ── Sizing reduction constant (sanity, wired in paper_trader.py) ────────────
def test_bonding_size_reduction_is_conservative():
    assert 0.0 < bonding_entry.BONDING_SIZE_REDUCTION < 1.0
