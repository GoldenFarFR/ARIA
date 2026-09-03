"""qualified_candidate_radar.format_qualified_candidate -- ARIA RADAR V1.

03/09, operator-directed go: fires the moment a candidate passes discovery
qualification, BEFORE the regime gate (never after) -- see
shadow_persistent.py's robinhood_discovery_loop for the call site. Pure
text formatting, no I/O, so every case below is a fast unit test.

Core rule under test: never fabricate a signal the pipeline doesn't
actually measure. Day-zero discovery has no real momentum/acceleration
figure (price_change_pct is always {} at this stage) and no
CHARTISTE/SOCIAL score at all (Fusion Engine not built) -- these must
render an explicit N/A, never a blank, a zero, or an invented number.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aria_core.qualified_candidate_radar import format_qualified_candidate
from aria_core.services.geckoterminal import TrendingPool


def _pool(**overrides) -> TrendingPool:
    base = dict(
        pool_address="0xpool", token_address="0xtoken", symbol="FOO",
        price_usd=0.000123, price_change_pct={}, transactions_m15=None,
        volume_usd_m15=None, reserve_usd=5000.0,
        pool_created_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        dex_id="uniswap_v2",
    )
    base.update(overrides)
    return TrendingPool(**base)


def test_status_qualified_never_a_trade_verdict():
    text = format_qualified_candidate(
        _pool(), chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "STATUS: QUALIFIED" in text
    assert "QUALIFIED — OBSERVATION ONLY" in text
    for forbidden in ("BUY", "SELL", "WATCH", "ALERT", "IGNORE"):
        assert forbidden not in text


def test_symbol_and_price_rendered():
    text = format_qualified_candidate(
        _pool(symbol="MOONCAT", price_usd=0.0007),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "MOONCAT" in text
    assert "0.0007" in text or "0.00070" in text


def test_missing_symbol_never_fabricated():
    text = format_qualified_candidate(
        _pool(symbol=None), chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "?" in text


def test_momentum_and_acceleration_always_na_day_zero():
    """Day-zero discovery never has a real price_change_pct -- these two
    fields must never render a number, even 0%."""
    text = format_qualified_candidate(
        _pool(), chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "Momentum" in text
    assert "N/A" in text.split("Momentum")[1].split("\n")[0]
    assert "Acceleration" in text
    assert "N/A" in text.split("Acceleration")[1].split("\n")[0]


def test_chartiste_social_always_na_v1():
    text = format_qualified_candidate(
        _pool(), chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "CHARTISTE" in text and "N/A" in text.split("CHARTISTE")[1].split("\n")[0]
    assert "SOCIAL" in text and "N/A" in text.split("SOCIAL")[1].split("\n")[0]


def test_buy_sell_rendered_when_available():
    text = format_qualified_candidate(
        _pool(buy_count=7, sell_count=2),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "7/2" in text


def test_buy_sell_na_when_unavailable():
    text = format_qualified_candidate(
        _pool(buy_count=None, sell_count=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "Buy/Sell" in text
    assert "N/A" in text.split("Buy/Sell")[1].split("\n")[0]


def test_volume_rendered_in_usd_when_converted():
    """03/09 -- volume_usd is the ALREADY-CONVERTED figure (same
    doppler rate as price_usd/reserve_usd), never the raw quote-unit
    number. Only volume_usd renders '$', never cumulative_volume_quote
    directly."""
    text = format_qualified_candidate(
        _pool(volume_usd=1234.5, cumulative_volume_quote=0.5),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    volume_line = text.split("Volume")[1].split("\n")[0]
    assert "1,234" in volume_line or "1234" in volume_line
    assert "$" in volume_line


def test_volume_na_when_conversion_unavailable():
    """volume_usd is None (rate unavailable) even though the raw quote
    figure exists -- must render N/A, never fall back to a mislabeled
    raw quote-unit number."""
    text = format_qualified_candidate(
        _pool(volume_usd=None, cumulative_volume_quote=0.5),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "N/A" in text.split("Volume")[1].split("\n")[0]


def test_market_cap_rendered_when_available():
    text = format_qualified_candidate(
        _pool(market_cap_usd=45231.0, total_supply=1_000_000.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    mcap_line = text.split("Market Cap")[1].split("\n")[0]
    assert "45,231" in mcap_line or "45231" in mcap_line
    assert "$" in mcap_line


def test_market_cap_na_when_unavailable_never_fabricated():
    text = format_qualified_candidate(
        _pool(market_cap_usd=None, total_supply=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "N/A" in text.split("Market Cap")[1].split("\n")[0]


def test_supply_rendered_when_available():
    text = format_qualified_candidate(
        _pool(total_supply=1_000_000_000.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    supply_line = text.split("Supply")[1].split("\n")[0]
    assert "1,000,000,000" in supply_line or "1000000000" in supply_line


def test_supply_na_when_unavailable():
    text = format_qualified_candidate(
        _pool(total_supply=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "N/A" in text.split("Supply")[1].split("\n")[0]


def test_market_cap_never_used_as_a_qualification_signal():
    """Operator-explicit: mcap is display-only, never a filter/criterion --
    this alert must never claim otherwise in its own text."""
    text = format_qualified_candidate(
        _pool(market_cap_usd=45231.0, total_supply=1_000_000.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    for forbidden in ("filtre", "critere", "critère", "seuil mcap", "seuil market cap"):
        assert forbidden not in text.lower()


def test_dexscreener_link_present_and_correct():
    text = format_qualified_candidate(
        _pool(pool_address="0xABCDEF"),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "https://dexscreener.com/robinhood/0xABCDEF" in text


def test_fomo_family_link_present_and_correct_domain():
    """The real domain is fomo.family, NOT fomo.io (fomo.io is an unrelated
    crypto casino, verified live 03/09) -- the wrong domain would send the
    operator to a completely different product."""
    text = format_qualified_candidate(
        _pool(token_address="0xTOKEN123"),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "https://fomo.family/tokens/robinhood/0xTOKEN123" in text
    assert "fomo.io" not in text


def test_candidate_id_is_chain_and_pool_address():
    """No separate sequential-ID system invented -- reuses the exact key
    already used by brain_correlation.py (chain, pool_address), so a
    future consumer never has to reconcile two different candidate
    identifiers for the same pool."""
    text = format_qualified_candidate(
        _pool(pool_address="0xABC"),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "robinhood:0xABC" in text


def test_pool_age_rendered_in_seconds_when_fresh():
    now = datetime(2026, 9, 3, 12, 0, 43, tzinfo=timezone.utc)
    text = format_qualified_candidate(
        _pool(pool_created_at=datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0, now=now,
    )
    age_line = text.split("Pool age")[1].split("\n")[0]
    assert "43" in age_line
    assert "sec" in age_line


def test_pool_age_rendered_in_minutes_when_older():
    now = datetime(2026, 9, 3, 12, 5, 0, tzinfo=timezone.utc)
    text = format_qualified_candidate(
        _pool(pool_created_at=datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0, now=now,
    )
    age_line = text.split("Pool age")[1].split("\n")[0]
    assert "5.0 min" in age_line


def test_pool_age_na_when_pool_created_at_missing():
    text = format_qualified_candidate(
        _pool(pool_created_at=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "N/A" in text.split("Pool age")[1].split("\n")[0]


def test_swap_count_rendered_when_available():
    text = format_qualified_candidate(
        _pool(swap_count=7),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "7" in text.split("Swaps")[1].split("\n")[0]


def test_swap_count_na_when_unavailable():
    text = format_qualified_candidate(
        _pool(swap_count=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "N/A" in text.split("Swaps")[1].split("\n")[0]


def test_buy_sell_volume_usd_rendered_when_available():
    text = format_qualified_candidate(
        _pool(buy_volume_usd=120.5, sell_volume_usd=30.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    line = text.split("Buy/Sell $")[1].split("\n")[0]
    assert "120.5" in line or "120.50" in line
    assert "30.0" in line or "30.00" in line


def test_buy_sell_volume_usd_na_when_unavailable():
    text = format_qualified_candidate(
        _pool(buy_volume_usd=None, sell_volume_usd=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "N/A" in text.split("Buy/Sell $")[1].split("\n")[0]


def test_liquidity_shows_real_reserve_usd():
    text = format_qualified_candidate(
        _pool(reserve_usd=8123.45),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "8123" in text or "8,123" in text


def test_regime_status_reflected_closed():
    text = format_qualified_candidate(
        _pool(), chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "CLOSED" in text
    assert "Régime: OPEN" not in text


def test_regime_status_reflected_open():
    text = format_qualified_candidate(
        _pool(), chain="robinhood", regime_open=True, liquidity_floor_usd=4000.0,
    )
    assert "OPEN" in text


def test_chain_label_present():
    text = format_qualified_candidate(
        _pool(), chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    assert "robinhood" in text.lower()


def test_no_field_left_blank():
    """Every declared section must render SOMETHING (a value or an explicit
    N/A) -- an empty line after a label would look like a rendering bug,
    not a deliberate absence."""
    text = format_qualified_candidate(
        _pool(buy_count=None, sell_count=None, cumulative_volume_quote=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=4000.0,
    )
    for label in ("Momentum", "Liquidity", "Buy/Sell", "Volume", "Acceleration"):
        line = text.split(label)[1].split("\n")[0].strip(": \t")
        assert line, f"{label} line rendered empty"
