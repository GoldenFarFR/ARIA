"""Shadow narratif -- signaux événementiels vérifiables (DefiLlama revenu réel,
phase de listing Coinbase), purement observationnel, jamais un trade routé."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import narrative_signal_shadow as ns


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """DB dédiée par test + reset des caches module-level (même piège que
    _top_pools_cache/_candles_cache : un état partagé entre tests)."""
    db_path = str(tmp_path / "shadow.db")
    monkeypatch.setattr(ns, "DB_PATH", db_path)
    monkeypatch.setattr(ns, "_table_ready", False)
    monkeypatch.setattr(ns, "_defillama_cache", None)
    monkeypatch.setattr(ns, "_coinbase_cache", None)
    yield


def _prime_catalogs(
    monkeypatch, *,
    dl_addr: dict | None = None, dl_sym: dict | None = None, cb: dict | None = None,
):
    """Peuple les caches directement -- jamais un vrai appel réseau en test."""
    import time

    now = time.monotonic()
    monkeypatch.setattr(ns, "_defillama_cache", (now, dl_addr or {}, dl_sym or {}))
    monkeypatch.setattr(ns, "_coinbase_cache", (now, cb or {}))


_REVENUE_ENTRY = {"slug": "testproto", "name": "TestProto", "revenue_30d": 50_000.0}
CONTRACT = "0x" + "a" * 40


@pytest.mark.asyncio
async def test_defillama_address_match_records_signal(monkeypatch):
    _prime_catalogs(monkeypatch, dl_addr={CONTRACT: _REVENUE_ENTRY})
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=1.0)
    result = await ns.summary()
    assert result[ns.SIGNAL_DEFILLAMA_REVENUE]["signals"] == 1


@pytest.mark.asyncio
async def test_defillama_symbol_match_is_tagged_weak(monkeypatch):
    _prime_catalogs(monkeypatch, dl_sym={"TP": _REVENUE_ENTRY})
    await ns.record_evaluation(CONTRACT, "base", symbol="tp", price_usd=1.0)
    async with aiosqlite.connect(ns.DB_PATH) as db:
        cursor = await db.execute("SELECT match_strength FROM narrative_signal_shadow")
        rows = await cursor.fetchall()
    assert rows == [("symbol_only",)]


@pytest.mark.asyncio
async def test_no_match_records_nothing(monkeypatch):
    _prime_catalogs(monkeypatch, dl_addr={"0x" + "b" * 40: _REVENUE_ENTRY})
    await ns.record_evaluation(CONTRACT, "base", symbol="ZZZ", price_usd=1.0)
    assert await ns.summary() == {}


@pytest.mark.asyncio
async def test_dedup_window_prevents_relogging(monkeypatch):
    _prime_catalogs(monkeypatch, dl_addr={CONTRACT: _REVENUE_ENTRY})
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=1.0)
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=1.1)
    result = await ns.summary()
    assert result[ns.SIGNAL_DEFILLAMA_REVENUE]["signals"] == 1


@pytest.mark.asyncio
async def test_coinbase_first_run_seeds_silently(monkeypatch):
    """Tout le catalogue est 'nouveau' au premier passage -- seed silencieux,
    jamais 500 faux signaux d'un coup."""
    _prime_catalogs(monkeypatch, cb={"TP": {"launch_phase": False, "product_ids": ["TP-USD"]}})
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=1.0)
    assert await ns.summary() == {}
    # Le symbole est maintenant connu.
    assert "TP" in await ns._known_coinbase_symbols()


@pytest.mark.asyncio
async def test_coinbase_new_symbol_after_seed_is_a_signal(monkeypatch):
    _prime_catalogs(monkeypatch, cb={"TP": {"launch_phase": False, "product_ids": ["TP-USD"]}})
    await ns._mark_coinbase_symbols_seen({"OLD"})  # seed déjà fait, TP est bien NOUVEAU
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=1.0)
    result = await ns.summary()
    assert result[ns.SIGNAL_CEX_LISTING_PHASE]["signals"] == 1


@pytest.mark.asyncio
async def test_coinbase_launch_phase_is_a_signal_even_if_known(monkeypatch):
    _prime_catalogs(monkeypatch, cb={"TP": {"launch_phase": True, "product_ids": ["TP-USD"]}})
    await ns._mark_coinbase_symbols_seen({"TP", "OLD"})
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=1.0)
    result = await ns.summary()
    assert result[ns.SIGNAL_CEX_LISTING_PHASE]["signals"] == 1


@pytest.mark.asyncio
async def test_forward_price_24h_filled_by_later_evaluation(monkeypatch):
    _prime_catalogs(monkeypatch, dl_addr={CONTRACT: _REVENUE_ENTRY})
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=2.0)
    # Vieillit artificiellement la ligne de 25h.
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    async with aiosqlite.connect(ns.DB_PATH) as db:
        await db.execute("UPDATE narrative_signal_shadow SET observed_at = ?", (old,))
        await db.commit()
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=3.0)
    result = await ns.summary()
    stats = result[ns.SIGNAL_DEFILLAMA_REVENUE]
    assert stats["resolved_24h"] == 1
    assert stats["avg_return_24h_pct"] == pytest.approx(50.0, rel=1e-6)
    assert stats["resolved_7d"] == 0  # 25h < 7 jours


@pytest.mark.asyncio
async def test_never_raises_even_on_broken_db(monkeypatch):
    """Best-effort absolu -- le chemin réel d'évaluation ne paie jamais."""
    monkeypatch.setattr(ns, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(ns, "_table_ready", False)
    await ns.record_evaluation(CONTRACT, "base", symbol="TP", price_usd=1.0)  # ne lève pas


@pytest.mark.asyncio
async def test_empty_contract_is_ignored(monkeypatch):
    _prime_catalogs(monkeypatch, dl_addr={CONTRACT: _REVENUE_ENTRY})
    await ns.record_evaluation("", "base", symbol="TP", price_usd=1.0)
    assert await ns.summary() == {}


def test_revenue_floor_calibration_documented():
    """Le seuil sépare le cas gitlawb (~$8k, marketing) des vrais mécanismes
    (clanker $274k/30j) -- verrouille la constante contre une dérive muette."""
    assert ns.MIN_REVENUE_30D_USD == 10_000.0


@pytest.mark.asyncio
async def test_record_external_signal_generic_detector(monkeypatch):
    """3e détecteur (conviction_research) -- point d'entrée générique, même
    dédup/doctrine que les catalogues."""
    await ns.record_external_signal(
        CONTRACT, "base", symbol="tp",
        signal_type=ns.SIGNAL_CONVICTION_RESEARCH,
        detail="score 8.5/10: vrai produit", price_usd=1.0,
    )
    await ns.record_external_signal(  # dédup dans la fenêtre
        CONTRACT, "base", symbol="tp",
        signal_type=ns.SIGNAL_CONVICTION_RESEARCH,
        detail="score 8.5/10: vrai produit", price_usd=1.2,
    )
    result = await ns.summary()
    assert result[ns.SIGNAL_CONVICTION_RESEARCH]["signals"] == 1
