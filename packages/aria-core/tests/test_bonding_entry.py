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
async def test_high_concentration_no_longer_a_hard_veto(monkeypatch):
    """#167, 28/07: the hard reject that used to sit here was removed -- a
    live empirical pass found top10_holder_pct never drops below ~93.8% even
    at 1000+ real holders, making the old 80% hard gate reject EVERY
    candidate that ever reached the sample-size floor. High concentration
    now only costs points on the score_holders pillar, never vetoes alone."""
    token = _bonding_token(top10_holder_pct=95.0, holder_count=60)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] != "holder_concentration"  # no such reason exists anymore
    assert result["action"] == "BUY"  # strong dev/product/technical carries it despite top10=95%


@pytest.mark.asyncio
async def test_unknown_concentration_scores_as_worst_case_not_a_veto(monkeypatch):
    """#167, 28/07: an unknown top10_holder_pct (above the sample-size floor)
    now scores as the worst case on the score_holders pillar (fail-closed on
    the SCORE) rather than being a hard veto -- can still be outweighed by
    strong dev/product/technical pillars. dev_holding_pct=0.0 and a strong
    potential_score are used here (rather than the defaults) precisely to
    make that outweighing arithmetic hold -- see
    test_score_holders_scale_recalibrated_to_real_data for the exact
    contribution of this pillar in isolation."""
    token = _bonding_token(dev_holding_pct=0.0, top10_holder_pct=None, holder_count=60)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research_strong(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=True, potential_score=9.5)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research_strong)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"


@pytest.mark.asyncio
async def test_concentration_gate_neutralized_below_min_holders(monkeypatch):
    """24/07, real gap found live right after deploy: with too few genuine
    buyers, top10_holder_pct is mechanically ~100% (not a rug signal) --
    verified against the 100 real bonding prototypes at the time, every
    single one failed this gate, including the one with the most holders
    (33). Below _MIN_HOLDERS_FOR_CONCENTRATION_CHECK, the ratio is treated
    as uninformative (near-zero score, never a veto -- #167 removed the
    veto entirely regardless of sample size, but this below-floor path
    predates and is unaffected by that change)."""
    assert bonding_entry._MIN_HOLDERS_FOR_CONCENTRATION_CHECK > 3
    token = _bonding_token(top10_holder_pct=100.0, holder_count=3)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
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
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"


@pytest.mark.asyncio
async def test_score_holders_scale_recalibrated_to_real_data(monkeypatch):
    """#167, 28/07: score_holders now scales between _TOP10_HOLDER_PCT_SCORE_
    FLOOR (full 15 points, the best realistic case observed empirically) and
    _MAX_TOP10_HOLDER_PCT (0 points, the worst case). Verified end-to-end via
    the composite bonding_score, holding every other pillar fixed and known:
    dev_holding_pct=0.0 -> score_dev=35.0 ; potential_score=None ->
    score_product=17.5 ; rr=3.0/align=2 -> score_setup=9.4 (same worked
    example as test_bonding_score_matches_the_validated_worked_example)."""
    expected_other_pillars = 35.0 + 17.5 + 9.4  # dev + product + setup

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=False)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    scores = {}
    for label, top10_pct in (
        ("best", bonding_entry._TOP10_HOLDER_PCT_SCORE_FLOOR),
        ("worst", bonding_entry._MAX_TOP10_HOLDER_PCT),
    ):
        token = _bonding_token(dev_holding_pct=0.0, top10_holder_pct=top10_pct, holder_count=300)
        _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))
        result = await bonding_entry.evaluate_bonding_entry("0xabc")
        scores[label] = result["bonding_score"]

    assert scores["best"] == pytest.approx(expected_other_pillars + bonding_entry._WEIGHT_HOLDER_CONCENTRATION, abs=0.01)
    assert scores["worst"] == pytest.approx(expected_other_pillars + 0.0, abs=0.01)


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

    assert result["hold_reason"] == "no_trades_available"  # #167, 28/07 -- renamed


@pytest.mark.asyncio
async def test_some_trades_but_zero_complete_candles_no_longer_hard_rejects(monkeypatch):
    """#167, 28/07: the core behavior change of this revision. A token with
    1-4 real trades (non-empty, but fewer than _TRADES_PER_CANDLE=5 -- zero
    complete candles) used to hard-reject on `not candles` before this
    fix -- correlated ~perfectly with the liquidity gate in the live sample
    that motivated it. detect_entry() degrades gracefully on the resulting
    empty candle list (present=False, never raises), so this now falls
    through to the #152 "no technical signal -> score 0 on that pillar"
    path, never a second hard reject."""
    token = _bonding_token(dev_holding_pct=0.0, holder_count=6, top10_holder_pct=100.0)
    few_trades = _trades(3)  # fewer than _TRADES_PER_CANDLE=5 -- zero complete candles
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=few_trades))
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research_strong(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=True, potential_score=9.5)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research_strong)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["hold_reason"] != "no_trades_available"
    assert result["action"] == "BUY"  # strong dev/product compensates the zeroed technical pillar
    assert result["rr"] is None  # no technical signal on 0 complete candles -- honestly reported


# ── #152, 28/07: no technical setup no longer hard-rejects -- scores 0 on
# the technical pillar, composite score decides alone. ──────────────────────
@pytest.mark.asyncio
async def test_no_entry_signal_scores_zero_setup_not_a_hard_reject(monkeypatch):
    """Real gap fixed here: a real candidate (HOLO) used to be rejected
    outright for lacking a golden-pocket setup, its team/product potential
    never evaluated. No signal now just zeroes the technical-setup pillar
    (still 15% of the composite) -- with weak diligence elsewhere, the
    composite still lands under threshold, but for the RIGHT reason."""
    token = _bonding_token(dev_holding_pct=4.9, holder_count=6, top10_holder_pct=100.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=False, reasons=["pas de setup"])

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=False)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "score_below_threshold"  # NOT no_entry_signal
    assert "pilier technique noté 0" in " ".join(result["reasons"])


@pytest.mark.asyncio
async def test_no_entry_signal_can_still_buy_on_strong_potential(monkeypatch):
    """The core behavior change of #152: a token with NO technical setup at
    all can still BUY if dev security + product conviction + holders are
    strong enough to clear the composite threshold on their own."""
    token = _bonding_token(dev_holding_pct=0.1, holder_count=60, top10_holder_pct=30.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=False, reasons=["pas de setup"])

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research_strong(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=True, potential_score=9.5)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research_strong)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"
    assert result["rr"] is None  # no technical signal -- honestly reported, never fabricated
    # Fallback target/invalidation anchored on the new exit design (#154/#155),
    # not None -- paper_trader's fresh-price re-check needs real numbers.
    assert result["target"] == pytest.approx(result["price"] * bonding_entry._FALLBACK_TARGET_MULTIPLE)
    assert result["invalidation"] == pytest.approx(result["price"] * bonding_entry._FALLBACK_INVALIDATION_MULTIPLE)


@pytest.mark.asyncio
async def test_weak_rr_no_longer_hard_rejects_scores_proportional_setup(monkeypatch):
    """A weak-but-present setup (rr=1.2, below the old direct-buy floor)
    used to hard-reject here too -- now it just contributes a small,
    proportional (not zero) technical-pillar score."""
    token = _bonding_token(dev_holding_pct=4.9, holder_count=6, top10_holder_pct=100.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup faible"], rr=1.2, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (0, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=False)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    # score_setup = (1.2/5.0*9) + (0/3*6) = 2.16 -- small, not zero, not
    # gated out before the composite even runs.
    assert result["hold_reason"] == "score_below_threshold"
    assert "score composite" in " ".join(result["reasons"])


# ── $VIRTUAL/USD conversion ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hold_when_usd_rate_unavailable(monkeypatch):
    token = _bonding_token()
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup fort"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, ["EMA/MACD"], {}))
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
        # invalidation deliberately WIDER than the #155 total-drawdown floor
        # (_FALLBACK_INVALIDATION_MULTIPLE=0.35, i.e. -65%) so this test stays
        # focused on ITS OWN responsibility (the $VIRTUAL->USD conversion) --
        # the floor-widening clamp itself is covered by its own dedicated
        # tests below, never conflated with this one.
        return EntrySignal(
            present=True, reasons=["golden pocket + divergence RSI"],
            rr=3.0, target=execution_price_virtual * 1.5, invalidation=execution_price_virtual * 0.2,
        )

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, ["EMA/MACD"], {}))
    _patch_usd_rate(monkeypatch, 0.6055)

    result = await bonding_entry.evaluate_bonding_entry("0xabc", current_regime="neutre")

    assert result["action"] == "BUY"
    assert result["chain"] == bonding_entry.CHAIN_MARKER
    assert result["strategy"] == "momentum"
    assert result["price"] == pytest.approx(execution_price_virtual * 0.6055)
    assert result["target"] == pytest.approx(execution_price_virtual * 1.5 * 0.6055)
    assert result["invalidation"] == pytest.approx(execution_price_virtual * 0.2 * 0.6055)
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
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
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
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
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
async def test_buy_forwards_virtual_id_as_known_launchpad_id(monkeypatch):
    """Item #171, 28/07: the token's own numeric Virtuals id must reach
    conviction_research so it can corroborate against a launchpad link the
    project's own X bio may declare (real false positive found and fixed on
    HOLO -- see conviction_research.py's own docstring)."""
    token = _setup_buy_mocks(monkeypatch)
    token.virtual_id = 47656

    captured = {}

    async def fake_research(contract, symbol, chain, *, known_launchpad_id=None, **kwargs):
        captured["known_launchpad_id"] = known_launchpad_id
        return bonding_entry.ConvictionResearch(available=False)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    await bonding_entry.evaluate_bonding_entry("0xabc")

    assert captured["known_launchpad_id"] == 47656


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


# ── Item #156, 28/07: supply-proportion sizing cap ───────────────────────────
def test_cap_alloc_to_supply_pct_reduces_when_over_tier_cap():
    # 1,000,000 total supply, entry price $0.01 -> full float worth $10,000.
    # "weak" tier caps at 1% of supply = 10,000 tokens = $100.
    alloc = bonding_entry.cap_alloc_to_supply_pct(500.0, 0.01, 1_000_000.0, "weak")
    assert alloc == pytest.approx(100.0)


def test_cap_alloc_to_supply_pct_never_increases_alloc():
    # A tiny alloc, well under any tier's cap, is returned unchanged.
    alloc = bonding_entry.cap_alloc_to_supply_pct(5.0, 0.01, 1_000_000.0, "strong")
    assert alloc == pytest.approx(5.0)


def test_cap_alloc_to_supply_pct_tiers_scale_with_conviction():
    args = (1_000.0, 0.01, 1_000_000.0)  # full float = $10,000
    strong = bonding_entry.cap_alloc_to_supply_pct(*args, "strong")
    moderate = bonding_entry.cap_alloc_to_supply_pct(*args, "moderate")
    weak = bonding_entry.cap_alloc_to_supply_pct(*args, "weak")
    assert strong == pytest.approx(500.0)  # 5%
    assert moderate == pytest.approx(250.0)  # 2.5%
    assert weak == pytest.approx(100.0)  # 1%
    assert strong > moderate > weak


def test_cap_alloc_to_supply_pct_unknown_tier_uses_most_conservative_default():
    alloc = bonding_entry.cap_alloc_to_supply_pct(1_000.0, 0.01, 1_000_000.0, None)
    assert alloc == pytest.approx(100.0)  # same as "weak" -- fail-closed default


def test_cap_alloc_to_supply_pct_fails_open_when_total_supply_unknown():
    """No total_supply (a real gap -- not every token in the wild exposes
    this field) -- the $-risk/price-impact caps applied generically by
    open_position() remain the real guardrails, this cap just steps aside."""
    alloc = bonding_entry.cap_alloc_to_supply_pct(50_000.0, 0.01, None, "weak")
    assert alloc == pytest.approx(50_000.0)


@pytest.mark.asyncio
async def test_buy_result_forwards_total_supply_for_paper_trader_sizing(monkeypatch):
    token = _bonding_token(total_supply=1_000_000_000.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["total_supply"] == pytest.approx(1_000_000_000.0)


# ── 24/07, composite score (operator's weights, external review confirmed) ──

def test_composite_score_weights_sum_to_100():
    assert (
        bonding_entry._WEIGHT_DEV_SECURITY
        + bonding_entry._WEIGHT_PRODUCT_CONVICTION
        + bonding_entry._WEIGHT_TECHNICAL_SETUP
        + bonding_entry._WEIGHT_HOLDER_CONCENTRATION
    ) == pytest.approx(100.0)


def test_technical_setup_components_sum_to_its_own_weight():
    assert (
        bonding_entry._RR_SCORE_COMPONENT_MAX + bonding_entry._ALIGN_SCORE_COMPONENT_MAX
    ) == pytest.approx(bonding_entry._WEIGHT_TECHNICAL_SETUP)


@pytest.mark.asyncio
async def test_bonding_score_matches_the_validated_worked_example(monkeypatch):
    """Exact worked example from the document the operator had independently
    reviewed (external LLM cross-check) before this was coded: dev=0% -> 35,
    potential_score=6.0 -> 21, rr=3.0/align=2 -> 9.4, holder_count=6 (<50
    since #152, near-zero fraction) -> 3.0, total 68.4/100 -> BUY."""
    token = _bonding_token(dev_holding_pct=0.0, holder_count=6, top10_holder_pct=100.0)
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=3.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=True, potential_score=6.0)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"
    assert result["bonding_score"] == pytest.approx(68.4, abs=0.01)


@pytest.mark.asyncio
async def test_hold_when_composite_score_below_threshold(monkeypatch):
    """A candidate that clears every hard gate can still HOLD if the
    weighted score doesn't reach the threshold -- e.g. a maxed-out dev
    holding (near the 5% hard-gate edge) with no conviction signal and a
    barely-passing technical setup."""
    token = _bonding_token(
        dev_holding_pct=4.9, holder_count=6, top10_holder_pct=100.0, liquidity_usd=10_500.0,
    )
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup faible"], rr=2.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=False)

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    # score_dev = 35*(1-4.9/5.0) = 0.7 ; score_product = 17.5 (neutral) ;
    # score_setup = (2.0/5.0*9) + (2/3*6) = 3.6+4.0 = 7.6 ; score_holders = 3.0
    # (near-zero fraction, <50 holders since #152) -- total = 0.7+17.5+7.6+3.0
    # = 28.8, well under 60.
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "score_below_threshold"
    assert result["bonding_score"] == pytest.approx(28.8, abs=0.01)
    assert result["bonding_score"] < bonding_entry._SCORE_THRESHOLD


# ── Item #165, 28/07: BTC long-cycle sizing lever ───────────────────────────


def test_late_cycle_multiplier_neutral_on_early_cycle_phases():
    assert bonding_entry.late_cycle_size_multiplier("accumulation") == 1.0
    assert bonding_entry.late_cycle_size_multiplier("hausse (markup)") == 1.0


def test_late_cycle_multiplier_reduces_on_late_cycle_phases():
    assert bonding_entry.late_cycle_size_multiplier("distribution") == pytest.approx(
        bonding_entry._BTC_LATE_CYCLE_SIZE_MULTIPLIER
    )
    assert bonding_entry.late_cycle_size_multiplier("baisse (markdown)") == pytest.approx(
        bonding_entry._BTC_LATE_CYCLE_SIZE_MULTIPLIER
    )


def test_late_cycle_multiplier_fails_open_on_unknown_or_missing():
    assert bonding_entry.late_cycle_size_multiplier(None) == 1.0
    assert bonding_entry.late_cycle_size_multiplier("some_unexpected_label") == 1.0


# ── Item #161/#162, 28/07: organic-decline (staleness) penalty ─────────────


def _iso_days_ago(days: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_staleness_penalty_none_when_launch_date_unknown():
    assert bonding_entry._staleness_penalty_multiplier(None) == 1.0


def test_staleness_penalty_none_below_threshold():
    fresh = _iso_days_ago(bonding_entry._STALENESS_DAYS_THRESHOLD - 1.0)
    assert bonding_entry._staleness_penalty_multiplier(fresh) == 1.0


def test_staleness_penalty_partial_between_threshold_and_max():
    midway_days = (bonding_entry._STALENESS_DAYS_THRESHOLD + bonding_entry._STALENESS_MAX_DAYS) / 2.0
    launched = _iso_days_ago(midway_days)
    multiplier = bonding_entry._staleness_penalty_multiplier(launched)
    assert 1.0 - bonding_entry._STALENESS_MAX_PENALTY_PCT < multiplier < 1.0


def test_staleness_penalty_capped_beyond_max_days():
    ancient = _iso_days_ago(bonding_entry._STALENESS_MAX_DAYS + 100.0)
    multiplier = bonding_entry._staleness_penalty_multiplier(ancient)
    assert multiplier == pytest.approx(1.0 - bonding_entry._STALENESS_MAX_PENALTY_PCT)


def test_staleness_penalty_waived_by_active_posting_cadence():
    """Item #162: a genuine dated catalyst (posting_cadence == "active")
    waives the decay entirely, no matter how old the token is."""
    ancient = _iso_days_ago(bonding_entry._STALENESS_MAX_DAYS + 100.0)
    multiplier = bonding_entry._staleness_penalty_multiplier(
        ancient, posting_cadence=bonding_entry._STALENESS_WAIVER_POSTING_CADENCE,
    )
    assert multiplier == 1.0


def test_staleness_penalty_not_waived_by_other_cadence_values():
    ancient = _iso_days_ago(bonding_entry._STALENESS_MAX_DAYS + 100.0)
    for cadence in ("low", "dormant", "unknown", None):
        multiplier = bonding_entry._staleness_penalty_multiplier(ancient, posting_cadence=cadence)
        assert multiplier == pytest.approx(1.0 - bonding_entry._STALENESS_MAX_PENALTY_PCT)


def test_staleness_penalty_unparsable_date_fails_open():
    assert bonding_entry._staleness_penalty_multiplier("not-a-real-date") == 1.0


@pytest.mark.asyncio
async def test_stale_token_score_reduced_and_can_flip_to_hold(monkeypatch):
    """End-to-end: an otherwise-BUY-worthy stale token (aged well past
    _STALENESS_MAX_DAYS, no active posting cadence) has its composite score
    reduced -- verified via a case exactly on the BUY/HOLD boundary so the
    penalty is what flips the verdict, not a coincidence."""
    token = _bonding_token(
        dev_holding_pct=2.0, holder_count=6, top10_holder_pct=100.0,
        launched_at=_iso_days_ago(bonding_entry._STALENESS_MAX_DAYS + 30.0),
    )
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=2.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research_quiet(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=True, potential_score=9.0, posting_cadence="dormant")

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research_quiet)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    # Same inputs as test_bonding_score_rewards_strong_conviction_over_weak_dev_security's
    # "strong" case (BUY there, no staleness) -- here the token is ancient and
    # dormant, so the penalty applies and the score comes back down.
    assert result["action"] == "HOLD"
    assert "déclin organique" in " ".join(result["reasons"])


@pytest.mark.asyncio
async def test_active_posting_cadence_protects_an_aged_token_from_the_penalty(monkeypatch):
    """Item #162's guardrail proven end-to-end: the SAME aged token as above,
    but with posting_cadence="active" -- the penalty is waived, the BUY goes
    through on the strength of the underlying score alone."""
    token = _bonding_token(
        dev_holding_pct=2.0, holder_count=6, top10_holder_pct=100.0,
        launched_at=_iso_days_ago(bonding_entry._STALENESS_MAX_DAYS + 30.0),
    )
    _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=2.0, target=0.002, invalidation=0.0009)

    monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
    monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
    _patch_usd_rate(monkeypatch, 0.5)

    async def fake_research_active(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=True, potential_score=9.0, posting_cadence="active")

    monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research_active)

    result = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert result["action"] == "BUY"
    assert "déclin organique" not in " ".join(result["reasons"])


@pytest.mark.asyncio
async def test_bonding_score_rewards_strong_conviction_over_weak_dev_security(monkeypatch):
    """Sanity check on the operator's own priority (35% product weighted
    the same as 35% dev security): a strong conviction score can push a
    candidate with mediocre (but still gate-passing) dev security over the
    threshold, where the neutral-conviction case (same setup otherwise)
    would not. dev_holding_pct=2.0 (not the original 3.0 -- #152 lowered the
    near-zero holder-concentration contribution enough that 3.0 no longer
    leaves room for the "strong" case to clear the threshold at all)."""
    base_kwargs = dict(dev_holding_pct=2.0, holder_count=6, top10_holder_pct=100.0)

    def fake_detect_entry(candles, **kwargs):
        return EntrySignal(present=True, reasons=["setup"], rr=2.0, target=0.002, invalidation=0.0009)

    async def fake_research_strong(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=True, potential_score=9.0)

    async def fake_research_neutral(contract, symbol, chain, *, known_links=None, **kwargs):
        return bonding_entry.ConvictionResearch(available=False)

    results = {}
    for label, fake_research in (("strong", fake_research_strong), ("neutral", fake_research_neutral)):
        token = _bonding_token(**base_kwargs)
        _patch_client(monkeypatch, _FakeVirtualsClient(token=token, trades=_trades(20)))
        monkeypatch.setattr(bonding_entry, "detect_entry", fake_detect_entry)
        monkeypatch.setattr("aria_core.momentum_entry._technical_alignment", lambda candles: (2, [], {}))
        monkeypatch.setattr(bonding_entry, "research_project_potential", fake_research)
        _patch_usd_rate(monkeypatch, 0.5)
        results[label] = await bonding_entry.evaluate_bonding_entry("0xabc")

    assert results["strong"]["action"] == "BUY"
    assert results["neutral"]["action"] == "HOLD"
    assert results["strong"]["bonding_score"] > results["neutral"]["bonding_score"]
