"""Détection copy-trading/bot -- corrélation d'horodatages d'entrée entre wallets
déjà scoré. DB isolée par test (même piège que test_momentum_blacklist.py :
``DB_PATH`` est calculé une fois à l'import, la fixture globale ne suffit pas)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.skills import copy_trading_detection as ctd
from aria_core.skills.copy_trading_detection import (
    CopyTradingFacts,
    gather_copy_trading_facts,
    judge_copy_trading,
    record_entry,
)

WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40
WALLET_C = "0x" + "c" * 40
TOKEN_1 = "0x" + "1" * 40
TOKEN_2 = "0x" + "2" * 40
TOKEN_3 = "0x" + "3" * 40
TOKEN_4 = "0x" + "4" * 40

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ctd, "DB_PATH", str(tmp_path / "copy_trading_test.db"))


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_copy_trading(CopyTradingFacts(available=False, error="pas de données"))
    assert v.flag == "unknown"


def test_below_threshold_is_independent():
    v = judge_copy_trading(CopyTradingFacts(distinct_tokens_followed=2, available=True))
    assert v.flag == "independent"


def test_at_threshold_is_copy_trading_suspected():
    v = judge_copy_trading(
        CopyTradingFacts(distinct_tokens_followed=3, followed_wallets=[WALLET_B], available=True)
    )
    assert v.flag == "copy_trading_suspected"
    assert any("copy-trading" in p for p in v.points)


# ── Enregistrement + corrélation réelle (SQLite isolé) ──────────────────────


@pytest.mark.asyncio
async def test_record_entry_then_no_correlation_alone():
    await record_entry(WALLET_A, TOKEN_1, "base", T0)
    facts = await gather_copy_trading_facts(WALLET_A, "base")
    assert facts.available is True
    assert facts.distinct_tokens_followed == 0  # aucun autre wallet à corréler


@pytest.mark.asyncio
async def test_entry_10min_after_another_wallet_correlates():
    await record_entry(WALLET_B, TOKEN_1, "base", T0)
    await record_entry(WALLET_A, TOKEN_1, "base", T0 + timedelta(minutes=10))

    facts = await gather_copy_trading_facts(WALLET_A, "base")

    assert facts.available is True
    assert facts.distinct_tokens_followed == 1
    assert facts.followed_wallets == [WALLET_B]


@pytest.mark.asyncio
async def test_entry_too_soon_does_not_correlate():
    """Sous 5 min -- indiscernable d'un carnet d'ordres réactif normal, pas un signal."""
    await record_entry(WALLET_B, TOKEN_1, "base", T0)
    await record_entry(WALLET_A, TOKEN_1, "base", T0 + timedelta(minutes=2))

    facts = await gather_copy_trading_facts(WALLET_A, "base")

    assert facts.distinct_tokens_followed == 0


@pytest.mark.asyncio
async def test_entry_too_late_does_not_correlate():
    """Au-delà de 15 min -- la corrélation temporelle s'affaiblit trop."""
    await record_entry(WALLET_B, TOKEN_1, "base", T0)
    await record_entry(WALLET_A, TOKEN_1, "base", T0 + timedelta(minutes=20))

    facts = await gather_copy_trading_facts(WALLET_A, "base")

    assert facts.distinct_tokens_followed == 0


@pytest.mark.asyncio
async def test_entry_before_other_wallet_does_not_correlate():
    """A entre AVANT B -- A ne suit pas B, la corrélation est directionnelle."""
    await record_entry(WALLET_B, TOKEN_1, "base", T0 + timedelta(minutes=10))
    await record_entry(WALLET_A, TOKEN_1, "base", T0)

    facts = await gather_copy_trading_facts(WALLET_A, "base")

    assert facts.distinct_tokens_followed == 0


@pytest.mark.asyncio
async def test_single_token_overlap_is_not_suspicious_end_to_end():
    """Un chevauchement isolé sur un seul token n'est jamais un pattern -- deux
    wallets indépendants peuvent réagir à la même annonce publique."""
    await record_entry(WALLET_B, TOKEN_1, "base", T0)
    await record_entry(WALLET_A, TOKEN_1, "base", T0 + timedelta(minutes=10))

    facts = await gather_copy_trading_facts(WALLET_A, "base")
    verdict = judge_copy_trading(facts)

    assert verdict.flag == "independent"


@pytest.mark.asyncio
async def test_systematic_follow_across_many_tokens_is_suspected_end_to_end():
    for i, token in enumerate([TOKEN_1, TOKEN_2, TOKEN_3, TOKEN_4]):
        await record_entry(WALLET_B, token, "base", T0 + timedelta(hours=i))
        await record_entry(WALLET_A, token, "base", T0 + timedelta(hours=i, minutes=8))

    facts = await gather_copy_trading_facts(WALLET_A, "base")
    verdict = judge_copy_trading(facts)

    assert facts.distinct_tokens_followed == 4
    assert verdict.flag == "copy_trading_suspected"


@pytest.mark.asyncio
async def test_different_chain_does_not_correlate():
    await record_entry(WALLET_B, TOKEN_1, "base", T0)
    await record_entry(WALLET_A, TOKEN_1, "solana", T0 + timedelta(minutes=10))

    facts = await gather_copy_trading_facts(WALLET_A, "base")

    assert facts.distinct_tokens_followed == 0


@pytest.mark.asyncio
async def test_upsert_does_not_duplicate_same_pair():
    await record_entry(WALLET_A, TOKEN_1, "base", T0)
    await record_entry(WALLET_A, TOKEN_1, "base", T0 + timedelta(minutes=1))  # ré-écrit, pas dupliqué
    await record_entry(WALLET_B, TOKEN_1, "base", T0)

    facts = await gather_copy_trading_facts(WALLET_A, "base")
    # entrée la plus récente (T0+1min) doit rester > 5min de la fenêtre pour compter --
    # ici elle est à 1min de WALLET_B, donc PAS corrélée (sous le seuil).
    assert facts.distinct_tokens_followed == 0


@pytest.mark.asyncio
async def test_malformed_inputs_ignored_silently():
    await record_entry("", TOKEN_1, "base", T0)
    await record_entry(WALLET_A, "", "base", T0)
    await record_entry(WALLET_A, TOKEN_1, "", T0)
    facts = await gather_copy_trading_facts(WALLET_A, "base")
    assert facts.available is True
    assert facts.distinct_tokens_followed == 0


@pytest.mark.asyncio
async def test_gather_missing_wallet_or_chain_unavailable():
    facts = await gather_copy_trading_facts("", "base")
    assert facts.available is False
