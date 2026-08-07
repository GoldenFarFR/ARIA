"""Tests for the pocket deployment contract (07/08, operator request).

The point of `pocket_spec` is to REFUSE an incomplete configuration, so the
tests that matter most are the ones proving it actually refuses -- a gate
that passes everything is worse than no gate (it manufactures confidence).
"""
from __future__ import annotations

import pytest

from aria_core import pocket_spec as ps


def _complete_spec() -> ps.PocketSpec:
    """A spec that answers every field -- the baseline the tests break."""
    spec = ps.PocketSpec(name="scalping_test")
    for f in ps.SPEC_FIELDS:
        if f.key == "timeframe":
            spec.values[f.key] = "15"
        elif f.key == "chains":
            spec.values[f.key] = "base"
        elif f.key in ("alloc_pct", "risk_cap"):
            spec.values[f.key] = "0.05"
        elif f.key in ("starting_capital", "max_candidates", "cycle_interval",
                       "min_candles", "min_liquidity", "max_positions"):
            spec.values[f.key] = "10"
        else:
            spec.values[f.key] = "oui"
    return spec


def test_complete_spec_is_deployable():
    ps.assert_deployable(_complete_spec())  # must not raise
    assert ps.blocking_errors(_complete_spec()) == []


def test_empty_spec_blocks_and_names_every_missing_point():
    spec = ps.PocketSpec()
    errors = ps.blocking_errors(spec)
    required_keys = {f.key for f in ps.SPEC_FIELDS if f.required or f.guardrail}
    reported = {e.key for e in errors}
    assert required_keys <= reported, "un champ obligatoire n'est pas signalé"
    assert "name" in reported
    with pytest.raises(ps.SpecIncomplete):
        ps.assert_deployable(spec)


def test_geckoterminal_is_never_called_for_a_granularity_it_cannot_serve():
    """The 07/08 discovery, now fixed at the routing level rather than by
    banning the granularity: GeckoTerminal has no 30-min aggregate, so two v9
    tokens paid a doomed call every cycle for days before landing on Mobula
    at 15 min. The cascade must simply not include it there."""
    assert "geckoterminal" not in ps.providers_for_timeframe(30, scalping=False)
    assert "geckoterminal" in ps.providers_for_timeframe(15)


@pytest.mark.parametrize("minutes", [1, 5, 15, 60, 240, 720, 1440])
def test_every_real_geckoterminal_aggregate_is_accepted(minutes):
    """Served by the primary provider -> never blocking (a fallback gap is
    only a warning)."""
    spec = _complete_spec()
    spec.values["timeframe"] = str(minutes)
    assert [e for e in ps.validate(spec) if e.key == "timeframe" and e.blocking] == []


@pytest.mark.parametrize("minutes", [45, 120, 480, 7, 13])
def test_granularities_no_provider_serves_are_rejected(minutes):
    spec = _complete_spec()
    spec.values["timeframe"] = str(minutes)
    assert [e for e in ps.validate(spec) if e.key == "timeframe" and e.blocking]


def test_robust_timeframes_are_served_by_every_scalping_provider():
    """15 min is the only sub-hourly value every real provider serves -- which
    is exactly why it is the only one that never showed a `degraded` flag in
    production."""
    assert 15 in ps.ROBUST_TIMEFRAME_MINUTES
    for minutes in ps.ROBUST_TIMEFRAME_MINUTES:
        cascade = ps.providers_for_timeframe(minutes)
        assert len(cascade) >= 3, f"{minutes} min n'a que {cascade} comme sources"


def test_thirty_minutes_is_allowed_now_that_the_cascade_is_adaptive():
    """The operator was right to push back on the blanket 30-min ban. It was
    correct only while the cascade order was FIXED (a doomed GeckoTerminal
    call every cycle before landing on Mobula at 15 min). With capability
    routing, Mobula serves 30 min NATIVELY and first -- so the granularity
    genuinely works and must no longer be blocked."""
    assert ps.providers_for_timeframe(30)[0] == "mobula"
    spec = _complete_spec()
    spec.values["timeframe"] = "30"
    assert [e for e in ps.validate(spec) if e.key == "timeframe" and e.blocking] == []
    ps.assert_deployable(spec)  # must not raise


def test_single_provider_timeframe_warns_without_blocking():
    """6h is served by Mobula ALONE: it works, but nothing catches the pocket
    if that one source is down -- worth saying out loud, never forbidding."""
    assert ps.providers_for_timeframe(360, scalping=False) == ("mobula",)
    spec = _complete_spec()
    spec.values["timeframe"] = "360"
    warnings = [e for e in ps.validate(spec) if e.key == "timeframe" and not e.blocking]
    assert warnings and "aucun repli" in warnings[0].message
    ps.assert_deployable(spec)  # a warning must never block


def test_codex_is_part_of_the_cascade_crossing():
    """Operator caught this omission ("et les autres api") -- the OHLCV cascade
    has FOUR tiers, not three. A provider silently absent from a completeness
    check is the exact failure this module exists to prevent."""
    assert ps.timeframe_support(15)["codex"] is True
    assert ps.timeframe_support(5)["codex"] is False
    for minutes in ps.ROBUST_TIMEFRAME_MINUTES:
        assert ps.timeframe_support(minutes)["codex"], f"{minutes} min ignore Codex"


def test_cascade_never_calls_a_provider_that_cannot_serve_the_granularity():
    """Operator rule (07/08): "il faut faire de l'adaptatif pour pas surcharger
    les api à usage limité". Calling a provider that structurally cannot serve
    the requested granularity spends quota to return nothing -- exactly what
    the 30-minute tokens did to GeckoTerminal on every single cycle for days."""
    for minutes in (1, 5, 15, 30, 60, 240, 1440):
        for name in ps.providers_for_timeframe(minutes, scalping=False):
            assert minutes in ps._PROVIDER_MINUTES[name], (
                f"{name} appelé pour {minutes} min qu'il ne sert pas"
            )


def test_mobula_is_alone_with_codex_on_thirty_minutes():
    """The granularity only Mobula and Codex serve: GeckoTerminal must not be
    called at all -- the concrete case the operator asked for."""
    assert ps.providers_for_timeframe(30) == ("mobula", "codex")
    assert "geckoterminal" not in ps.providers_for_timeframe(30)


def test_budgeted_provider_is_always_last():
    """Codex carries a 9 500/month cap against ~21 000 calls of real traffic,
    so it may only ever be a last resort."""
    for minutes in (15, 30, 60, 240):
        cascade = ps.providers_for_timeframe(minutes, scalping=False)
        if "codex" in cascade and len(cascade) > 1:
            assert cascade[-1] == "codex", f"codex n'est pas dernier à {minutes} min"


def test_mobula_is_spared_where_geckoterminal_can_do_the_job():
    """Operator rule (07/08): "mettre mobula dans les autres cascades en
    deuxieme ou plus pour eviter de tirer dessus sur d'autres timeframes".
    Mobula carries 36.5% of real traffic AND is the only 30-min source --
    spending it where GeckoTerminal suffices burns the safety net for nothing.
    It leads at 30 min only because capability routing leaves nobody ahead."""
    assert ps.providers_for_timeframe(15)[0] == "geckoterminal"
    assert ps.providers_for_timeframe(15).index("mobula") == 1
    assert ps.providers_for_timeframe(30)[0] == "mobula"


def test_coinmarketcap_never_enters_a_scalping_cascade():
    """Hour-scale only -- feeding day-scale candles to a scalping RSI corrupts
    the read with no visible error."""
    for minutes in (1, 5, 15, 30, 60, 240):
        assert "coinmarketcap" not in ps.providers_for_timeframe(minutes, scalping=True)
    assert "coinmarketcap" in ps.providers_for_timeframe(60, scalping=False)


def test_removed_providers_are_gone_from_every_table():
    """DexPaprika and Dune returned ZERO candles while reporting success
    (live-tested 07/08). A fallback slot filled by a provider that never
    delivers is worse than an empty one."""
    for table in (ps._PROVIDER_MINUTES, dict.fromkeys(ps._PROVIDER_COST_ORDER)):
        assert "dexpaprika" not in table
        assert "dune" not in table
    assert "dexpaprika" not in ps.timeframe_support(15)


def test_only_base_and_ethereum_have_both_hard_guardrails():
    """The chain axis matters more than timeframes: a granularity mismatch
    degrades a signal, a chain gap REMOVES a mandatory safety check."""
    assert ps.FULLY_GUARDED_CHAINS == ("base", "ethereum")


@pytest.mark.parametrize("chain", ["base", "ethereum"])
def test_fully_guarded_chains_pass(chain):
    spec = _complete_spec()
    spec.values["chains"] = chain
    assert [e for e in ps.validate(spec) if e.key == "chains"] == []


@pytest.mark.parametrize("chain", ["solana", "robinhood"])
def test_chains_without_holder_data_are_blocked(chain):
    """Both pass the honeypot check but have no holder-concentration source.
    Explicitly relevant to the standing 'disable Solana before real capital'
    note, and to the Monad diligence that stalled on the same gap."""
    support = ps.chain_support(chain)
    assert support["goplus_honeypot"] and not support["blockscout_holders"]
    spec = _complete_spec()
    spec.values["chains"] = chain
    errors = [e for e in ps.validate(spec) if e.key == "chains" and e.blocking]
    assert errors and "concentration des détenteurs" in errors[0].message


def test_unknown_chain_is_blocked_outright():
    spec = _complete_spec()
    spec.values["chains"] = "monad"
    errors = [e for e in ps.validate(spec) if e.key == "chains" and e.blocking]
    assert errors and "AUCUN garde-fou" in errors[0].message


def test_a_chain_list_is_rejected_if_any_single_chain_fails():
    """Operator rule applied to chains: one uncovered chain cancels the list."""
    spec = _complete_spec()
    spec.values["chains"] = "base, solana"
    assert [e for e in ps.validate(spec) if e.key == "chains" and e.blocking]


def test_objective_becomes_the_thesis_prefix_of_every_position():
    """07/08 -- the box used to do nothing. It now prefixes each position's
    thesis, which is the field that made the v8 bootstrap-vs-divergence call
    provable after the fact."""
    spec = ps.PocketSpec(name="scalping_v10", values={"objective": "rebond après capitulation"})
    assert spec.thesis_prefix() == "[scalping_v10 — rebond après capitulation]"


def test_thesis_prefix_degrades_cleanly_without_an_objective():
    assert ps.PocketSpec(name="poche_x").thesis_prefix() == "[poche_x]"
    assert ps.PocketSpec().thesis_prefix() == "[poche]"


def test_a_guardrail_can_never_be_answered_with_a_disabling_value():
    """An optional field may legitimately be 'aucune' (a pocket with no staged
    take-profit). A guardrail may not -- that is the difference between the two."""
    for f in ps.SPEC_FIELDS:
        if not f.guardrail:
            continue
        spec = _complete_spec()
        spec.values[f.key] = "non"
        errors = [e for e in ps.validate(spec) if e.key == f.key]
        assert errors and errors[0].blocking, f"{f.key} accepte d'être désactivé"


def test_optional_field_left_empty_warns_but_never_blocks():
    optional = [f for f in ps.SPEC_FIELDS if not f.required and not f.guardrail]
    assert optional, "le gabarit n'a plus aucun champ optionnel -- test à revoir"
    spec = _complete_spec()
    for f in optional:
        spec.values.pop(f.key)
    errors = ps.validate(spec)
    assert all(not e.blocking for e in errors if e.key in {f.key for f in optional})
    ps.assert_deployable(spec)  # must not raise


def test_capital_share_must_be_a_fraction_not_a_percent():
    """5 typed instead of 0.05 would size positions 100x too large."""
    spec = _complete_spec()
    spec.values["alloc_pct"] = "5"
    errors = [e for e in ps.validate(spec) if e.key == "alloc_pct"]
    assert errors and "fraction" in errors[0].message


def test_completion_counts_answered_fields():
    assert ps.completion(ps.PocketSpec()) == (0, len(ps.SPEC_FIELDS))
    answered, total = ps.completion(_complete_spec())
    assert answered == total == len(ps.SPEC_FIELDS)


def test_template_rows_cover_every_field_exactly_once():
    """The HTML template is a VIEW of SPEC_FIELDS -- never a parallel list."""
    rows = ps.as_template_rows()
    assert len(rows) == len(ps.SPEC_FIELDS)
    assert {r["key"] for r in rows} == {f.key for f in ps.SPEC_FIELDS}


def test_export_round_trips_through_the_parser():
    """The operator fills the HTML, exports text, pastes it back to a session.
    That text must reconstruct the same answers, or the whole flow is broken."""
    original = _complete_spec()
    lines = [f"Poche      : {original.name}"]
    for f in ps.SPEC_FIELDS:
        lines.append(f"  [x] {f.label} = {original.values[f.key]}")
    parsed = ps.parse_export("\n".join(lines))
    assert parsed.name == original.name
    assert parsed.values == original.values
    ps.assert_deployable(parsed)


def test_parser_survives_noise_around_the_export():
    """Copy/paste through Telegram or a PDF adds junk -- it must not raise."""
    spec = ps.parse_export(
        "blah blah\n\nPoche      : essai\n  [x] timeframe_min = 15\n"
        "ligne non reconnue\n  [ ] MAX_POSITIONS\n---\n"
    )
    assert spec.name == "essai"
    assert spec.values["timeframe"] == "15"
    assert "max_positions" not in spec.values  # coché sans valeur -> non renseigné


def test_mobula_period_matches_the_configured_granularity():
    """The cbXRP bug in one assertion: the cascade used to ask Mobula for
    "15m" first whatever the pocket had configured, so a 30-min pocket got
    15-min candles on the first (successful) iteration and never reached 30m.
    Three real v9 buys fired on half-width windows before this was found."""
    assert ps.mobula_period(30) == "30m"
    assert ps.mobula_period(15) == "15m"
    assert ps.mobula_period(60) == "1h"
    assert ps.mobula_period(1440) == "1d"


def test_mobula_refuses_the_eight_hour_trap():
    """Measured 07/08: Mobula accepts period="8h", returns 60 candles and no
    error, but the real spacing is 60 MINUTES -- hourly data under an 8-hour
    label, which no `degraded` flag can catch since the call 'succeeds'."""
    assert ps.mobula_period(480) is None
    assert 480 not in ps._MOBULA_MINUTES
    assert ps.providers_for_timeframe(480, scalping=False) == ()
