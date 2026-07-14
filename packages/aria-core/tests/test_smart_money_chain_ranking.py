"""#157, 14/07 — classement TVL dynamique des chaînes scannées par
/walletscore : cache SQLite (`refresh_chain_ranking_cache`), lecture avec
repli (`DEFAULT_SCAN_CHAINS`). Aucun appel réseau réel -- DefiLlama mocké au
niveau fonction (même patron que les tests DexScreener/CMC existants dans
test_smart_money_wallet_scoring.py)."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core.services import smart_money as sm


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))


class TestRefreshChainRankingCache:
    @pytest.mark.asyncio
    async def test_success_populates_table_sorted_by_rank(self, monkeypatch):
        async def _fake_ranking():
            return [("ethereum", 900.0), ("base", 100.0)]

        monkeypatch.setattr("aria_core.services.defillama.fetch_chain_tvl_ranking", _fake_ranking)

        ok = await sm.refresh_chain_ranking_cache()

        assert ok is True
        async with aiosqlite.connect(sm.DB_PATH) as db:
            cursor = await db.execute("SELECT chain, tvl_usd, rank FROM wallet_scoring_chain_ranking ORDER BY rank")
            rows = await cursor.fetchall()
        assert rows == [("ethereum", 900.0, 1), ("base", 100.0, 2)]

    @pytest.mark.asyncio
    async def test_truncates_to_max_ranked_chains(self, monkeypatch):
        ranking = [(f"chain{i}", float(100 - i)) for i in range(sm._MAX_RANKED_CHAINS + 5)]

        async def _fake_ranking():
            return ranking

        monkeypatch.setattr("aria_core.services.defillama.fetch_chain_tvl_ranking", _fake_ranking)

        ok = await sm.refresh_chain_ranking_cache()

        assert ok is True
        async with aiosqlite.connect(sm.DB_PATH) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM wallet_scoring_chain_ranking")
            (count,) = await cursor.fetchone()
        assert count == sm._MAX_RANKED_CHAINS

    @pytest.mark.asyncio
    async def test_defillama_failure_never_wipes_existing_cache(self, monkeypatch):
        async def _fake_success():
            return [("base", 1.0)]

        monkeypatch.setattr("aria_core.services.defillama.fetch_chain_tvl_ranking", _fake_success)
        assert await sm.refresh_chain_ranking_cache() is True

        async def _fake_failure():
            return None

        monkeypatch.setattr("aria_core.services.defillama.fetch_chain_tvl_ranking", _fake_failure)
        ok = await sm.refresh_chain_ranking_cache()

        assert ok is False
        async with aiosqlite.connect(sm.DB_PATH) as db:
            cursor = await db.execute("SELECT chain FROM wallet_scoring_chain_ranking")
            rows = await cursor.fetchall()
        assert rows == [("base",)]  # le dernier bon classement sert toujours


class TestDefaultScanChains:
    @pytest.mark.asyncio
    async def test_empty_cache_falls_back_to_static_tuple(self):
        chains = await sm.DEFAULT_SCAN_CHAINS()

        assert chains == sm._FALLBACK_SCAN_CHAINS

    @pytest.mark.asyncio
    async def test_populated_cache_respected_in_rank_order(self, monkeypatch):
        async def _fake_ranking():
            # Déjà trié décroissant par TVL, comme le fait le vrai
            # defillama.fetch_chain_tvl_ranking() -- refresh_chain_ranking_cache
            # fait confiance à l'ordre reçu, ne re-trie pas lui-même.
            return [("ethereum", 900.0), ("arbitrum", 500.0), ("base", 100.0)]

        monkeypatch.setattr("aria_core.services.defillama.fetch_chain_tvl_ranking", _fake_ranking)
        await sm.refresh_chain_ranking_cache()

        chains = await sm.DEFAULT_SCAN_CHAINS()

        assert chains == ("ethereum", "arbitrum", "base")

    @pytest.mark.asyncio
    async def test_db_error_falls_back_never_raises(self, monkeypatch):
        def _broken_connect(*args, **kwargs):
            raise OSError("simulated DB failure")

        monkeypatch.setattr(sm.aiosqlite, "connect", _broken_connect)

        chains = await sm.DEFAULT_SCAN_CHAINS()

        assert chains == sm._FALLBACK_SCAN_CHAINS
