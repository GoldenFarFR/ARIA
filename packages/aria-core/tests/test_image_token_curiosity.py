"""Tests for skills/image_token_curiosity.py -- verified curiosity on a token
mentioned in a Telegram image (26/07)."""
from __future__ import annotations

import pytest

from aria_core.services import goplus as goplus_module
from aria_core.services.dexscreener import PairSnapshot
from aria_core.services.goplus import TokenSecurity
from aria_core.skills import image_token_curiosity as curiosity


def _patch_goplus(monkeypatch, fake_get_token_security):
    """Patches the CLASS (``type(goplus_client)``), never the singleton
    INSTANCE directly -- `goplus_client` lives for the whole pytest process
    (module-level singleton). Patching the instance leaves a residual
    instance attribute after monkeypatch's teardown (the original bound
    method gets *restored* as an instance attribute, permanently shadowing
    any later class-level patch from other test files) -- real pollution
    found and fixed live: it broke `test_momentum_entry.py`/
    `test_paper_trader_risk.py`'s honeypot tests whenever this file ran
    first in the same pytest session. Same pattern already used everywhere
    else in this codebase (see test_momentum_entry.py)."""
    monkeypatch.setattr(
        type(goplus_module.goplus_client), "get_token_security", staticmethod(fake_get_token_security),
    )


def _pair(symbol="STONK", address="0xabc", chain="base", liquidity=100_000.0, dex="uniswap"):
    return PairSnapshot(
        pair_address="0xpool",
        dex_id=dex,
        liquidity_usd=liquidity,
        volume_24h_usd=50_000.0,
        price_usd=0.01234,
        price_change_24h=12.5,
        base_address=address,
        base_symbol=symbol,
        quote_symbol="ETH",
        chain_id=chain,
    )


# ── _extract_ticker ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_ticker_parses_structured_reply(monkeypatch):
    async def fake_chat(*a, **kw):
        return "TICKER: STONK"

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_ticker("data:image/jpeg;base64,x") == "STONK"


@pytest.mark.asyncio
async def test_extract_ticker_strips_dollar_sign(monkeypatch):
    async def fake_chat(*a, **kw):
        return "TICKER: $STONK"

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_ticker("uri") == "STONK"


@pytest.mark.asyncio
async def test_extract_ticker_none_on_aucun(monkeypatch):
    async def fake_chat(*a, **kw):
        return "AUCUN"

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_ticker("uri") is None


@pytest.mark.asyncio
async def test_extract_ticker_none_on_llm_unavailable(monkeypatch):
    async def fake_chat(*a, **kw):
        return None

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_ticker("uri") is None


@pytest.mark.asyncio
async def test_extract_ticker_none_on_unparsable_reply(monkeypatch):
    async def fake_chat(*a, **kw):
        return "je ne sais pas trop"

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_ticker("uri") is None


# ── _pick_dominant_match ─────────────────────────────────────────────────────

def test_pick_dominant_match_single_exact():
    pairs = [_pair(symbol="STONK", liquidity=50_000.0)]
    match = curiosity._pick_dominant_match("STONK", pairs)
    assert match is not None
    assert match.base_symbol == "STONK"


def test_pick_dominant_match_case_insensitive():
    pairs = [_pair(symbol="stonk", liquidity=50_000.0)]
    assert curiosity._pick_dominant_match("STONK", pairs) is not None


def test_pick_dominant_match_picks_most_liquid_when_dominant():
    pairs = [
        _pair(symbol="STONK", address="0xsmall", liquidity=1_000.0),
        _pair(symbol="STONK", address="0xbig", liquidity=500_000.0),
    ]
    match = curiosity._pick_dominant_match("STONK", pairs)
    assert match is not None
    assert match.base_address == "0xbig"


def test_pick_dominant_match_none_when_ambiguous():
    pairs = [
        _pair(symbol="STONK", address="0xone", liquidity=100_000.0),
        _pair(symbol="STONK", address="0xtwo", liquidity=80_000.0),
    ]
    assert curiosity._pick_dominant_match("STONK", pairs) is None


def test_pick_dominant_match_none_when_no_exact_symbol():
    pairs = [_pair(symbol="OTHER", liquidity=50_000.0)]
    assert curiosity._pick_dominant_match("STONK", pairs) is None


def test_pick_dominant_match_none_when_no_address():
    pairs = [_pair(symbol="STONK", address="", liquidity=50_000.0)]
    assert curiosity._pick_dominant_match("STONK", pairs) is None


# ── _pick_dominant_match with chains= (Item #236 follow-up, real incident: ──
# GITLAWB/KellyClaude, real liquid Base tokens, misreported "ambiguous"
# because of a same-ticker pair on a chain the pipeline never trades.

def test_pick_dominant_match_ignores_out_of_scope_chain_when_scoped():
    """Reproduces the real GITLAWB incident: a real Base match ($1.09M) was
    blocked because a "robinhood" pair ($2.71M, ratio ~2.47x, under 3x) was
    picked as top by raw liquidity -- scoping to chains=("base",) removes
    the out-of-scope competitor entirely, letting the real match through."""
    pairs = [
        _pair(symbol="GITLAWB", address="0xbase", chain="base", liquidity=1_097_368.0),
        _pair(symbol="GITLAWB", address="0xrobinhood", chain="robinhood", liquidity=2_711_046.0),
    ]
    assert curiosity._pick_dominant_match("GITLAWB", pairs, chains=None) is None  # unscoped: still ambiguous
    match = curiosity._pick_dominant_match("GITLAWB", pairs, chains=("base",))
    assert match is not None
    assert match.base_address == "0xbase"


def test_pick_dominant_match_still_ambiguous_within_scoped_chains():
    """Scoping doesn't relax the dominance check itself -- two genuinely
    close matches on the SAME allowed chain remain ambiguous."""
    pairs = [
        _pair(symbol="STONK", address="0xone", chain="base", liquidity=100_000.0),
        _pair(symbol="STONK", address="0xtwo", chain="base", liquidity=80_000.0),
    ]
    assert curiosity._pick_dominant_match("STONK", pairs, chains=("base",)) is None


def test_pick_dominant_match_none_when_only_out_of_scope_chains_match():
    pairs = [_pair(symbol="STONK", chain="robinhood", liquidity=500_000.0)]
    assert curiosity._pick_dominant_match("STONK", pairs, chains=("base",)) is None


def test_pick_dominant_match_chains_case_insensitive():
    pairs = [_pair(symbol="STONK", chain="BASE", liquidity=500_000.0)]
    assert curiosity._pick_dominant_match("STONK", pairs, chains=("base",)) is not None


# ── _honeypot_line ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_honeypot_line_none_when_chain_not_covered():
    match = _pair(chain="some-exotic-chain")
    assert await curiosity._honeypot_line(match) is None


@pytest.mark.asyncio
async def test_honeypot_line_flags_confirmed_honeypot(monkeypatch):
    match = _pair(chain="base")

    async def fake_security(address, *, chain_id):
        return TokenSecurity(address=address, is_honeypot=True, available=True)

    _patch_goplus(monkeypatch, fake_security)
    line = await curiosity._honeypot_line(match)
    assert line is not None
    assert "HONEYPOT" in line


@pytest.mark.asyncio
async def test_honeypot_line_clean_with_taxes(monkeypatch):
    match = _pair(chain="base")

    async def fake_security(address, *, chain_id):
        return TokenSecurity(
            address=address, is_honeypot=False, buy_tax=0.05, sell_tax=0.07, available=True,
        )

    _patch_goplus(monkeypatch, fake_security)
    line = await curiosity._honeypot_line(match)
    assert line is not None
    assert "5.0%" in line and "7.0%" in line


@pytest.mark.asyncio
async def test_honeypot_line_none_when_unavailable(monkeypatch):
    match = _pair(chain="base")

    async def fake_security(address, *, chain_id):
        return TokenSecurity(address=address, available=False, error="timeout")

    _patch_goplus(monkeypatch, fake_security)
    assert await curiosity._honeypot_line(match) is None


@pytest.mark.asyncio
async def test_honeypot_line_none_on_exception(monkeypatch):
    match = _pair(chain="base")

    async def raising(*a, **kw):
        raise RuntimeError("boom")

    _patch_goplus(monkeypatch, raising)
    assert await curiosity._honeypot_line(match) is None


# ── verify_token_mention (integration) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_token_mention_empty_when_no_ticker(monkeypatch):
    async def fake_extract(image_data_uri):
        return None

    monkeypatch.setattr(curiosity, "_extract_ticker", fake_extract)
    assert await curiosity.verify_token_mention("uri") == ""


@pytest.mark.asyncio
async def test_verify_token_mention_honest_when_no_search_result(monkeypatch):
    async def fake_extract(image_data_uri):
        return "GHOST"

    async def fake_search(query):
        return []

    monkeypatch.setattr(curiosity, "_extract_ticker", fake_extract)
    monkeypatch.setattr(curiosity, "search_pairs", fake_search)
    block = await curiosity.verify_token_mention("uri")
    assert "GHOST" in block
    assert "Aucun resultat" in block


@pytest.mark.asyncio
async def test_verify_token_mention_honest_when_ambiguous(monkeypatch):
    async def fake_extract(image_data_uri):
        return "STONK"

    async def fake_search(query):
        return [
            _pair(symbol="STONK", address="0xone", liquidity=100_000.0),
            _pair(symbol="STONK", address="0xtwo", liquidity=90_000.0),
        ]

    monkeypatch.setattr(curiosity, "_extract_ticker", fake_extract)
    monkeypatch.setattr(curiosity, "search_pairs", fake_search)
    block = await curiosity.verify_token_mention("uri")
    assert "Plusieurs tokens DISTINCTS" in block


@pytest.mark.asyncio
async def test_verify_token_mention_builds_full_block_on_clear_match(monkeypatch):
    async def fake_extract(image_data_uri):
        return "STONK"

    async def fake_search(query):
        return [_pair(symbol="STONK", address="0xbig", liquidity=500_000.0, chain="base")]

    async def fake_honeypot_line(match):
        return "- GoPlus : pas de honeypot detecte."

    monkeypatch.setattr(curiosity, "_extract_ticker", fake_extract)
    monkeypatch.setattr(curiosity, "search_pairs", fake_search)
    monkeypatch.setattr(curiosity, "_honeypot_line", fake_honeypot_line)

    block = await curiosity.verify_token_mention("uri")

    assert "0xbig" in block
    assert "Liquidite" in block
    assert "pas de honeypot" in block


@pytest.mark.asyncio
async def test_verify_token_mention_degrades_on_extract_exception(monkeypatch):
    async def raising(image_data_uri):
        raise RuntimeError("boom")

    monkeypatch.setattr(curiosity, "_extract_ticker", raising)
    assert await curiosity.verify_token_mention("uri") == ""


@pytest.mark.asyncio
async def test_verify_token_mention_degrades_on_search_exception(monkeypatch):
    async def fake_extract(image_data_uri):
        return "STONK"

    async def raising(query):
        raise RuntimeError("boom")

    monkeypatch.setattr(curiosity, "_extract_ticker", fake_extract)
    monkeypatch.setattr(curiosity, "search_pairs", raising)
    assert await curiosity.verify_token_mention("uri") == ""


# ── _extract_all_tickers / queue_tokens_from_screenshot (Item #236 follow-up) ──

@pytest.fixture(autouse=True)
def _isolated_manual_candidates_db(tmp_path, monkeypatch):
    from aria_core import manual_candidates as mcq

    monkeypatch.setattr(mcq, "DB_PATH", str(tmp_path / "manual_candidates_test.db"))


@pytest.mark.asyncio
async def test_extract_all_tickers_parses_multiple_lines(monkeypatch):
    async def fake_chat(*a, **kw):
        return "TICKER: SOL\nTICKER: $VIRTUAL\nTICKER: BRETT"

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_all_tickers("uri") == ["SOL", "VIRTUAL", "BRETT"]


@pytest.mark.asyncio
async def test_extract_all_tickers_dedupes(monkeypatch):
    async def fake_chat(*a, **kw):
        return "TICKER: SOL\nTICKER: sol\nTICKER: SOL"

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_all_tickers("uri") == ["SOL"]


@pytest.mark.asyncio
async def test_extract_all_tickers_empty_on_aucun(monkeypatch):
    async def fake_chat(*a, **kw):
        return "AUCUN"

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    assert await curiosity._extract_all_tickers("uri") == []


@pytest.mark.asyncio
async def test_extract_all_tickers_caps_at_max(monkeypatch):
    lines = "\n".join(f"TICKER: TOK{i}" for i in range(50))

    async def fake_chat(*a, **kw):
        return lines

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    tickers = await curiosity._extract_all_tickers("uri")
    assert len(tickers) == curiosity._MAX_TICKERS_PER_IMAGE


@pytest.mark.asyncio
async def test_queue_tokens_from_screenshot_no_tickers(monkeypatch):
    async def fake_extract(uri):
        return []

    monkeypatch.setattr(curiosity, "_extract_all_tickers", fake_extract)
    summary = await curiosity.queue_tokens_from_screenshot("uri")
    assert "Aucun ticker" in summary


@pytest.mark.asyncio
async def test_queue_tokens_from_screenshot_degrades_on_extraction_failure(monkeypatch):
    async def raising(uri):
        raise RuntimeError("boom")

    monkeypatch.setattr(curiosity, "_extract_all_tickers", raising)
    summary = await curiosity.queue_tokens_from_screenshot("uri")
    assert "reessaie" in summary


@pytest.mark.asyncio
async def test_queue_tokens_from_screenshot_queues_resolved_and_reports_unresolved(monkeypatch):
    from aria_core import manual_candidates as mcq

    async def fake_extract(uri):
        return ["SOL", "GHOST", "AMBIG"]

    async def fake_search(ticker):
        if ticker == "SOL":
            return [_pair(symbol="SOL", address="0xsol", liquidity=500_000.0, chain="base")]
        if ticker == "AMBIG":
            return [
                _pair(symbol="AMBIG", address="0xone", liquidity=100_000.0),
                _pair(symbol="AMBIG", address="0xtwo", liquidity=90_000.0),
            ]
        return []

    monkeypatch.setattr(curiosity, "_extract_all_tickers", fake_extract)
    monkeypatch.setattr(curiosity, "search_pairs", fake_search)

    summary = await curiosity.queue_tokens_from_screenshot("uri")

    assert "3 ticker(s)" in summary
    assert "1 ajoute" in summary
    assert "2 non resolu" in summary
    assert "GHOST" in summary and "AMBIG" in summary

    pending = await mcq.list_pending_manual_candidates()
    assert len(pending) == 1
    assert pending[0]["contract"] == "0xsol"
    assert pending[0]["chain"] == "base"


@pytest.mark.asyncio
async def test_queue_tokens_from_screenshot_tolerates_search_failure(monkeypatch):
    async def fake_extract(uri):
        return ["SOL"]

    async def raising(ticker):
        raise RuntimeError("boom")

    monkeypatch.setattr(curiosity, "_extract_all_tickers", fake_extract)
    monkeypatch.setattr(curiosity, "search_pairs", raising)

    summary = await curiosity.queue_tokens_from_screenshot("uri")
    assert "1 non resolu" in summary


@pytest.mark.asyncio
async def test_queue_tokens_from_screenshot_resolves_real_gitlawb_incident(monkeypatch):
    """Item #236 follow-up (30/07, real operator report): GITLAWB is a real
    liquid Base token ($1.09M) but was reported as unresolved -- a
    "robinhood" pair ($2.71M, this pipeline never trades that chain) beat it
    on raw liquidity without dominating 3x, tripping the ambiguity guard.
    Fixed by scoping resolution to momentum_entry.DEFAULT_CHAINS."""
    from aria_core import manual_candidates as mcq

    async def fake_extract(uri):
        return ["GITLAWB"]

    async def fake_search(ticker):
        return [
            _pair(symbol="GITLAWB", address="0xrealbase", chain="base", liquidity=1_097_368.0),
            _pair(symbol="GITLAWB", address="0xrobinhood", chain="robinhood", liquidity=2_711_046.0),
        ]

    monkeypatch.setattr(curiosity, "_extract_all_tickers", fake_extract)
    monkeypatch.setattr(curiosity, "search_pairs", fake_search)

    summary = await curiosity.queue_tokens_from_screenshot("uri")

    assert "1 ajoute" in summary
    assert "0 non resolu" not in summary or "non resolu" not in summary
    pending = await mcq.list_pending_manual_candidates()
    assert len(pending) == 1
    assert pending[0]["contract"] == "0xrealbase"
    assert pending[0]["chain"] == "base"
