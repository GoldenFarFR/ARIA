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

from aria_core.qualified_candidate_radar import (
    format_candidate_alert, format_qualified_candidate, is_radar_eligible, is_security_blocked,
)
from aria_core.services.geckoterminal import TrendingPool
from aria_core.services.goplus import TokenSecurity


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


def _security(**overrides) -> TokenSecurity:
    base = dict(address="0xtoken", available=True)
    base.update(overrides)
    return TokenSecurity(**base)


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


# --- 03/09, operator go: FLOW IMBALANCE -- factual only, never a causal/
# security conclusion ("wash trading"/"scam"/"rug" are explicitly banned) ---

def test_flow_imbalance_shown_when_zero_sells_and_enough_buys():
    text = format_qualified_candidate(
        _pool(buy_count=41, sell_count=0, buy_volume_usd=784.21, sell_volume_usd=0.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=200.0,
    )
    assert "FLOW IMBALANCE" in text
    assert "41 buys / 0 sells" in text
    assert "784.21" in text
    assert "Sell volume: $0" in text or "Sell volume: $0.00" in text


def test_flow_imbalance_never_states_a_causal_security_conclusion():
    text = format_qualified_candidate(
        _pool(buy_count=41, sell_count=0, buy_volume_usd=784.21, sell_volume_usd=0.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=200.0,
    )
    for forbidden in (
        "wash trading", "wash-trading", "scam", "rug", "arnaque", "manipulation",
    ):
        assert forbidden not in text.lower()


def test_flow_imbalance_absent_when_some_sells_exist():
    text = format_qualified_candidate(
        _pool(buy_count=41, sell_count=1, buy_volume_usd=784.21, sell_volume_usd=5.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=200.0,
    )
    assert "FLOW IMBALANCE" not in text


def test_flow_imbalance_absent_below_the_provisional_buy_threshold():
    """sell_count == 0 but too few buys to say anything -- threshold is
    explicitly provisional/display-only, see the module's own comment."""
    text = format_qualified_candidate(
        _pool(buy_count=3, sell_count=0, buy_volume_usd=12.0, sell_volume_usd=0.0),
        chain="robinhood", regime_open=False, liquidity_floor_usd=200.0,
    )
    assert "FLOW IMBALANCE" not in text


def test_flow_imbalance_absent_when_counts_unavailable():
    text = format_qualified_candidate(
        _pool(buy_count=None, sell_count=None),
        chain="robinhood", regime_open=False, liquidity_floor_usd=200.0,
    )
    assert "FLOW IMBALANCE" not in text


# --- 03/09, operator go: is_radar_eligible -- Telegram threshold ($20k),
# distinct from and never touching the discovery/logging qualification
# threshold (MIN_LIQUIDITY_USD_DAY_ZERO, $200, unchanged). Sub-threshold
# candidates keep being discovered/recorded, just not sent to Telegram. ---

def test_radar_eligible_above_threshold():
    assert is_radar_eligible(_pool(reserve_usd=20_000.0)) is True
    assert is_radar_eligible(_pool(reserve_usd=50_000.0)) is True


def test_radar_not_eligible_below_threshold():
    assert is_radar_eligible(_pool(reserve_usd=19_999.99)) is False
    assert is_radar_eligible(_pool(reserve_usd=5_000.0)) is False


def test_radar_not_eligible_when_reserve_unknown_fail_closed():
    """Same fail-closed doctrine as the rest of this pipeline (never treat
    an unmeasurable pool as tradable/notify-worthy)."""
    assert is_radar_eligible(_pool(reserve_usd=None)) is False


def test_radar_eligible_threshold_is_overridable_for_recalibration():
    assert is_radar_eligible(_pool(reserve_usd=5_000.0), threshold_usd=4_000.0) is True


# --- 03/09, operator go: is_security_blocked -- minimal GoPlus honeypot
# gate. Confirmed live (real API call, chain_id=4663) that GoPlus DOES
# cover Robinhood Chain, on the exact $R404 pool independently flagged
# "Potential scam" by fomo.io the same minute. Never blocks on unknown
# (None) -- same doctrine as goplus.py's own module docstring: only a
# POSITIVELY confirmed signal penalizes, a network outage never bans a
# good token. ---

def test_security_blocked_on_confirmed_honeypot():
    assert is_security_blocked(_security(is_honeypot=True)) is True


def test_security_blocked_on_confirmed_cannot_sell_all():
    assert is_security_blocked(_security(cannot_sell_all=True)) is True


def test_security_blocked_on_confirmed_cannot_buy():
    assert is_security_blocked(_security(cannot_buy=True)) is True


def test_security_not_blocked_when_everything_confirmed_clean():
    assert is_security_blocked(
        _security(is_honeypot=False, cannot_sell_all=False, cannot_buy=False)
    ) is False


def test_security_not_blocked_on_unknown_never_fail_open_to_fail_closed():
    """None (unknown) must never block -- a network outage/no-data result
    must never ban a good token, same doctrine as the rest of the pipeline."""
    assert is_security_blocked(
        _security(is_honeypot=None, cannot_sell_all=None, cannot_buy=None)
    ) is False


def test_security_not_blocked_when_goplus_unavailable():
    assert is_security_blocked(_security(available=False)) is False


# ---------------------------------------------------------------------------
# format_candidate_alert -- 04/09, operator go: RADAR V1's 40-line dump is
# a "Candidate" state event now, not an investable verdict (see
# early_life_observation.py's own module docstring for the full history of
# that decision). Telegram is a HUMAN SUMMARY SCREEN, never a data dump --
# operator verbatim: "Telegram ne doit afficher que: Qu'est-ce qu'ARIA
# vient de faire / pense / observe ?". Target: 5-8 lines, not 40. Every
# field the pipeline doesn't measure stays an explicit N/A, same doctrine
# as format_qualified_candidate -- the full data never disappears, it just
# moves out of Telegram (stays in early_life_tracking/onchain_activity_
# observation_log for ARIA's own reasoning).
# ---------------------------------------------------------------------------

def test_candidate_alert_is_short():
    text = format_candidate_alert(
        _pool(), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert len(text.splitlines()) <= 8


def test_candidate_alert_never_a_trade_verdict():
    text = format_candidate_alert(
        _pool(), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "Observation only" in text
    for forbidden in ("BUY", "SELL", "WATCH", "IGNORE", "QUALIFIED"):
        assert forbidden not in text


def test_candidate_alert_renders_symbol():
    text = format_candidate_alert(
        _pool(symbol="MOONCAT"), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "MOONCAT" in text


def test_candidate_alert_abbreviates_known_chains():
    assert "RH" in format_candidate_alert(
        _pool(), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "BASE" in format_candidate_alert(
        _pool(), chain="base", regime_open=False, security_status="safe",
    )


def test_candidate_alert_unknown_chain_never_crashes():
    text = format_candidate_alert(
        _pool(), chain="polygon", regime_open=False, security_status="safe",
    )
    assert "POLYGON" in text


def test_candidate_alert_renders_age():
    text = format_candidate_alert(
        _pool(pool_created_at=datetime(2026, 9, 4, 11, 59, 56, tzinfo=timezone.utc)),
        chain="robinhood", regime_open=False, security_status="safe",
        now=datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert "4s" in text


def test_candidate_alert_renders_liquidity_compact():
    text = format_candidate_alert(
        _pool(reserve_usd=51_099.81), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "51.1k" in text


def test_candidate_alert_liquidity_na_when_unknown():
    text = format_candidate_alert(
        _pool(reserve_usd=None), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "N/A" in text


def test_candidate_alert_renders_buy_sell():
    text = format_candidate_alert(
        _pool(buy_count=11, sell_count=0), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "11/0" in text


def test_candidate_alert_buy_sell_na_when_unknown():
    text = format_candidate_alert(
        _pool(buy_count=None, sell_count=None), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "N/A" in text


def test_candidate_alert_renders_volume():
    text = format_candidate_alert(
        _pool(volume_usd=0.01), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "0.01" in text


def test_candidate_alert_acceleration_always_na():
    """Never computed anywhere in the pipeline today -- must always render
    N/A, never a fabricated figure (same doctrine as format_qualified_
    candidate's Momentum/Acceleration fields)."""
    text = format_candidate_alert(
        _pool(), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "acceleration N/A" in text


def test_candidate_alert_renders_security_status():
    text = format_candidate_alert(
        _pool(), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "PASS" in text


def test_candidate_alert_renders_regime_open_and_closed():
    open_text = format_candidate_alert(
        _pool(), chain="robinhood", regime_open=True, security_status="safe",
    )
    closed_text = format_candidate_alert(
        _pool(), chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "OPEN" in open_text
    assert "CLOSED" in closed_text


def test_candidate_alert_renders_clickable_links():
    text = format_candidate_alert(
        _pool(pool_address="0xabc", token_address="0xdef"),
        chain="robinhood", regime_open=False, security_status="safe",
    )
    assert "https://dexscreener.com/robinhood/0xabc" in text
    assert "https://fomo.family/tokens/robinhood/0xdef" in text
