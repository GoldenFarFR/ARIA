"""Liste noire de contrats momentum (17/07, demande opérateur explicite après une
perte réelle sur BRIAN) -- DB isolée par test (fixture globale _isolated_runtime,
cf. conftest.py), aucun appel réseau."""
from __future__ import annotations

import pytest

from aria_core import momentum_blacklist as bl

CONTRACT = "0x" + "c" * 40
BRIAN = "0xb2000000000000000000007bf6d5cbb0e24cb301"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """``bl.DB_PATH`` est calculé une fois à l'import (``aria_db_path()``) --
    sans cette isolation explicite, tous les tests de ce fichier partageraient
    la même base réelle par défaut, quelle que soit la fixture globale
    ``_isolated_runtime`` (même piège déjà documenté dans
    test_agent_wallet_monitor.py/test_x402_budget.py)."""
    monkeypatch.setattr(bl, "DB_PATH", str(tmp_path / "momentum_blacklist_test.db"))


@pytest.mark.asyncio
async def test_brian_decoy_contract_seeded_and_blacklisted():
    """Le contrat réel à l'origine de cette liste noire (-17,9 %, -8 962 $, 17/07)
    doit être banni dès la première utilisation, sans action manuelle."""
    assert await bl.is_blacklisted(BRIAN, "base") is True


@pytest.mark.asyncio
async def test_unknown_contract_not_blacklisted_by_default():
    assert await bl.is_blacklisted(CONTRACT, "base") is False


@pytest.mark.asyncio
async def test_add_to_blacklist_then_is_blacklisted_true():
    assert await bl.is_blacklisted(CONTRACT, "base") is False
    await bl.add_to_blacklist(CONTRACT, "base", reason="test")
    assert await bl.is_blacklisted(CONTRACT, "base") is True


@pytest.mark.asyncio
async def test_blacklist_scoped_by_chain_not_global():
    """Un contrat banni sur une chaîne ne bannit pas la même adresse sur une autre
    -- des adresses identiques sur deux chaînes différentes ne sont pas le même
    contrat (Base vs Robinhood par exemple)."""
    await bl.add_to_blacklist(CONTRACT, "base", reason="test")
    assert await bl.is_blacklisted(CONTRACT, "base") is True
    assert await bl.is_blacklisted(CONTRACT, "solana") is False


@pytest.mark.asyncio
async def test_add_to_blacklist_idempotent_no_crash():
    await bl.add_to_blacklist(CONTRACT, "base", reason="premier")
    await bl.add_to_blacklist(CONTRACT, "base", reason="second appel, ignoré")
    entries = await bl.list_blacklist()
    matching = [e for e in entries if e["contract"] == CONTRACT]
    assert len(matching) == 1
    assert matching[0]["reason"] == "premier"  # INSERT OR IGNORE -- jamais écrasé


@pytest.mark.asyncio
async def test_add_to_blacklist_empty_contract_no_crash():
    await bl.add_to_blacklist("", "base", reason="jamais inséré")
    entries = await bl.list_blacklist()
    assert all(e["contract"] for e in entries)


@pytest.mark.asyncio
async def test_is_blacklisted_case_insensitive():
    await bl.add_to_blacklist(CONTRACT.upper(), "base", reason="test casse")
    assert await bl.is_blacklisted(CONTRACT.lower(), "BASE") is True
