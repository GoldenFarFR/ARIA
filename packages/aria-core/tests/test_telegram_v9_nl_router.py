"""Natural-language routing -> v9 command TEMPLATES (06/08, operator request,
live-caught gap: "modifier le signal sur v9" fell through to an unrelated
skill match, screenshot). Distinct from the read-only NL router: these
commands WRITE, so this router never executes anything -- it only replies
with the real command, pre-filled with the real current values, for the
operator to edit and send back themselves. Offline, no real network call
(the watchlist self-seeds SPX, see scalping_v9.py's module docstring)."""
from __future__ import annotations

import asyncio

import pytest

from aria_core import paper_trader as pt
from aria_core import scalping_v9 as v9
from aria_core.gateway import telegram_bot


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    return tmp_path


@pytest.mark.asyncio
async def test_no_v9_mention_never_matches(tmp_db):
    assert await telegram_bot._try_nl_v9_command("comment va le marché aujourd'hui ?") is None


@pytest.mark.asyncio
async def test_bare_virtual_mention_never_matches(tmp_db):
    """The exact false-positive the operator caught live (screenshot): a
    message about v9 that happened to contain "virtual" was misrouted to
    the unrelated launchpad skill inside brain.py. This router is anchored
    on "v9" specifically -- "virtual" alone must never trigger it (nor,
    separately, mislead the operator into thinking this router fixes THAT
    specific regex; it only guarantees a real v9 mention gets a real reply)."""
    assert await telegram_bot._try_nl_v9_command("parle-moi de virtuals protocol") is None


@pytest.mark.asyncio
async def test_add_intent_returns_syntax_template(tmp_db):
    reply = await telegram_bot._try_nl_v9_command("je veux ajouter un token à v9")
    assert reply is not None
    assert "/v9add" in reply


@pytest.mark.asyncio
async def test_modify_intent_returns_real_current_values(tmp_db):
    entry = (await v9.get_watchlist())[0]
    reply = await telegram_bot._try_nl_v9_command("je veut modifier le signal sur v9")
    assert reply is not None
    assert "/v9set" in reply
    assert entry["contract"] in reply
    assert f"rsi={entry['rsi_period']}/{entry['rsi_lower']:g}" in reply
    assert f"mfi={entry['mfi_period']}/{entry['mfi_lower']:g}" in reply


class _FakePair:
    def __init__(self, price=1.0, liquidity=500_000.0, symbol="TOK2"):
        self.pair_address = "0xpool2"
        self.price_usd = price
        self.base_symbol = symbol
        self.liquidity_usd = liquidity
        self.market_cap_usd = 300_000_000.0


@pytest.mark.asyncio
async def test_modify_intent_narrows_to_addressed_contract(tmp_db, monkeypatch):
    async def fake_pair_lookup(contract, *, chain="base"):
        return _FakePair()

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    entry2, error = await v9.add_watchlist_token("0x" + "d" * 40)
    assert error == ""
    seed = (await v9.get_watchlist())[0]

    reply = await telegram_bot._try_nl_v9_command(
        f"modifie le rsi de {seed['contract']} sur v9",
    )
    assert reply is not None
    assert seed["contract"] in reply
    assert entry2["contract"] not in reply


@pytest.mark.asyncio
async def test_remove_intent_returns_v9remove_template(tmp_db):
    reply = await telegram_bot._try_nl_v9_command("retire ce contrat de v9")
    assert reply is not None
    assert "/v9remove" in reply


@pytest.mark.asyncio
async def test_list_intent_returns_real_watchlist(tmp_db):
    entry = (await v9.get_watchlist())[0]
    reply = await telegram_bot._try_nl_v9_command("montre-moi la watchlist v9")
    assert reply is not None
    assert entry["symbol"] in reply
    assert entry["contract"] in reply


@pytest.mark.asyncio
async def test_empty_watchlist_modify_intent_points_to_add(tmp_db):
    await v9.remove_watchlist_token(v9.V9_WATCHLIST[0]["contract"])
    reply = await telegram_bot._try_nl_v9_command("modifie le réglage v9")
    assert reply is not None
    assert "/v9add" in reply
