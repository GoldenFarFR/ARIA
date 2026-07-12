"""Crawler Base — découverte + absorption (déterministe, réseau injecté)."""
from __future__ import annotations

import pytest

from aria_core import base_crawler as bc


def _payload(addrs):
    return {"data": [{"relationships": {"base_token": {"data": {"id": "base_" + a}}}} for a in addrs]}


def test_extract_token_contracts_valid_and_malformed():
    a1 = "0x" + "a" * 40
    a2 = "0x" + "b" * 40
    payload = _payload([a1, a2])
    payload["data"].append({"bad": "entry"})              # ignoré
    payload["data"].append({"relationships": {}})          # ignoré
    got = bc._extract_token_contracts(payload)
    assert got == [a1, a2]


def test_extract_ignores_short_address():
    assert bc._extract_token_contracts(_payload(["0x1234"])) == []


@pytest.mark.asyncio
async def test_discover_dedupes_across_paths():
    a1, a2 = "0x" + "a" * 40, "0x" + "b" * 40

    async def fetch(path):
        return _payload([a1, a2])  # mêmes tokens sur les deux endpoints

    tokens = await bc.discover_base_tokens(fetch=fetch)
    assert tokens == [a1, a2]  # dédoublonnés


@pytest.mark.asyncio
async def test_crawl_and_absorb_counts_verdicts():
    async def discover():
        return ["0xGOOD", "0xRUG", "0xKNOWN"]

    async def absorber(contract):
        return {"0xGOOD": "kept", "0xRUG": "rejected", "0xKNOWN": "skip_rejected"}[contract]

    counts = await bc.crawl_and_absorb(discover=discover, absorber=absorber)
    assert counts == {"kept": 1, "rejected": 1, "skip_rejected": 1}


@pytest.mark.asyncio
async def test_crawl_absorber_error_is_not_fatal():
    async def discover():
        return ["0xA", "0xB"]

    async def absorber(contract):
        if contract == "0xB":
            raise RuntimeError("scan down")
        return "kept"

    counts = await bc.crawl_and_absorb(discover=discover, absorber=absorber)
    assert counts.get("kept") == 1 and counts.get("error") == 1


def _pool_payload(pairs):
    return {
        "data": [
            {
                "relationships": {"base_token": {"data": {"id": "base_" + a}}},
                "attributes": {"reserve_in_usd": str(r)},
            }
            for a, r in pairs
        ]
    }


@pytest.mark.asyncio
async def test_top_pools_filters_by_liquidity_floor():
    liquid, thin = "0x" + "a" * 40, "0x" + "b" * 40

    async def fetch(path):
        return _pool_payload([(liquid, 80_000), (thin, 5_000)])

    assert await bc.discover_top_pools(fetch=fetch, min_liquidity_usd=30_000) == [liquid]


@pytest.mark.asyncio
async def test_top_pools_missing_reserve_is_filtered():
    a = "0x" + "c" * 40

    async def fetch(path):
        return {"data": [{"relationships": {"base_token": {"data": {"id": "base_" + a}}}}]}

    assert await bc.discover_top_pools(fetch=fetch, min_liquidity_usd=1) == []


@pytest.mark.asyncio
async def test_top_pools_requests_sort_by_volume():
    """Suite audit #77 : le tri GeckoTerminal par défaut (h24_tx_count_desc) n'est
    pas la profondeur/volume — on pin explicitement sort=h24_volume_usd_desc."""
    seen_paths = []

    async def fetch(path):
        seen_paths.append(path)
        return _pool_payload([])

    await bc.discover_top_pools(fetch=fetch)
    assert len(seen_paths) == 1
    assert "sort=h24_volume_usd_desc" in seen_paths[0]
    assert seen_paths[0].startswith("/networks/base/pools")


@pytest.mark.asyncio
async def test_top_pools_default_liquidity_floor_is_45k():
    """Suite audit #77 diversification (12/07) : relevé de 30k à 45k -- marge de
    sécurité contre l'écart GeckoTerminal (reserve_in_usd, checké ici) vs DexScreener
    (liquidité réellement testée par safety_screen), pas un nouveau critère de
    sécurité en soi."""
    above, below = "0x" + "e" * 40, "0x" + "f" * 40

    async def fetch(path):
        return _pool_payload([(above, 45_000), (below, 44_999)])

    assert await bc.discover_top_pools(fetch=fetch) == [above]


def _pool_payload_with_age(triples):
    """triples: (addr, reserve, pool_created_at ISO str ou None)."""
    return {
        "data": [
            {
                "relationships": {"base_token": {"data": {"id": "base_" + a}}},
                "attributes": {"reserve_in_usd": str(r), "pool_created_at": created_at},
            }
            for a, r, created_at in triples
        ]
    }


@pytest.mark.asyncio
async def test_top_pools_min_age_days_none_is_noop():
    """Défaut inchangé : sans min_age_days, un pool créé il y a 1h passe toujours."""
    fresh = "0x" + "e" * 40

    async def fetch(path):
        return _pool_payload_with_age([(fresh, 80_000, "2026-07-12T08:00:00Z")])

    assert await bc.discover_top_pools(fetch=fetch, min_liquidity_usd=1) == [fresh]


@pytest.mark.asyncio
async def test_top_pools_min_age_days_filters_fresh_pools():
    old, fresh = "0x" + "f" * 40, "0x" + "1" * 40

    async def fetch(path):
        return _pool_payload_with_age(
            [(old, 80_000, "2026-06-01T00:00:00Z"), (fresh, 80_000, "2026-07-12T08:00:00Z")]
        )

    got = await bc.discover_top_pools(fetch=fetch, min_liquidity_usd=1, min_age_days=7)
    assert got == [old]


@pytest.mark.asyncio
async def test_top_pools_min_age_days_excludes_unknown_age():
    """Âge inconnu (pool_created_at absent) = fail-closed dès que min_age_days est actif."""
    unknown = "0x" + "2" * 40

    async def fetch(path):
        return _pool_payload_with_age([(unknown, 80_000, None)])

    assert await bc.discover_top_pools(fetch=fetch, min_liquidity_usd=1, min_age_days=7) == []


@pytest.mark.asyncio
async def test_discover_virtuals_extracts_addresses():
    a1 = "0x" + "d" * 40

    class _VT:
        token_address = a1

    class _Client:
        async def fetch_prototypes(self):
            return [_VT(), _VT()]

    assert await bc.discover_virtuals_tokens(client=_Client()) == [a1]


@pytest.mark.asyncio
async def test_discover_virtuals_degrades_gracefully():
    class _Boom:
        async def fetch_prototypes(self):
            raise RuntimeError("down")

    assert await bc.discover_virtuals_tokens(client=_Boom()) == []


@pytest.mark.asyncio
async def test_discover_virtuals_graduated_extracts_addresses():
    a1 = "0x" + "e" * 40

    class _VT:
        token_address = a1

    class _Client:
        async def fetch_graduated(self):
            return [_VT(), _VT()]

    assert await bc.discover_virtuals_graduated_tokens(client=_Client()) == [a1]


@pytest.mark.asyncio
async def test_discover_virtuals_graduated_degrades_gracefully():
    class _Boom:
        async def fetch_graduated(self):
            raise RuntimeError("down")

    assert await bc.discover_virtuals_graduated_tokens(client=_Boom()) == []


@pytest.mark.asyncio
async def test_retry_stale_pending_calls_absorber_on_stale_rows():
    # audit #77 : le pool actif reste à 0 parce que rien ne retente PROACTIVEMENT
    # un candidat 'pending' laissé de côté -- ce test vérifie juste le câblage
    # (lister -> absorber), la logique de filtrage reste 100% dans token_absorber.
    async def lister():
        return [{"contract": "0xSTALE1"}, {"contract": "0xSTALE2"}]

    calls: list[str] = []

    async def absorber(contract):
        calls.append(contract)
        return {"0xSTALE1": "kept", "0xSTALE2": "skip_incomplete"}[contract]

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber)
    assert calls == ["0xSTALE1", "0xSTALE2"]
    assert counts == {"kept": 1, "skip_incomplete": 1}


@pytest.mark.asyncio
async def test_retry_stale_pending_no_stale_rows_is_noop():
    async def lister():
        return []

    async def absorber(contract):
        raise AssertionError("ne doit jamais être appelé sans candidat")

    assert await bc.retry_stale_pending(lister=lister, absorber=absorber) == {}


@pytest.mark.asyncio
async def test_retry_stale_pending_error_is_not_fatal():
    async def lister():
        return [{"contract": "0xA"}, {"contract": "0xB"}]

    async def absorber(contract):
        if contract == "0xB":
            raise RuntimeError("scan down")
        return "kept"

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber)
    assert counts == {"kept": 1, "error": 1}


@pytest.mark.asyncio
async def test_retry_stale_pending_default_lister_uses_screened_pool(monkeypatch):
    # Vérifie le branchement par défaut (screened_pool.list_stale_pending +
    # token_absorber.absorb) sans dupliquer leurs propres tests unitaires dédiés.
    from aria_core import screened_pool as sp

    calls: dict[str, int] = {}

    async def fake_list_stale_pending(*, older_than_hours=24, limit=20):
        calls["older_than_hours"] = older_than_hours
        calls["limit"] = limit
        return [{"contract": "0xDEFAULT"}]

    monkeypatch.setattr(sp, "list_stale_pending", fake_list_stale_pending)

    absorbed: list[str] = []

    async def fake_absorb(contract):
        absorbed.append(contract)
        return "kept"

    import aria_core.token_absorber as token_absorber_module

    monkeypatch.setattr(token_absorber_module, "absorb", fake_absorb)

    counts = await bc.retry_stale_pending()
    assert absorbed == ["0xDEFAULT"]
    assert counts == {"kept": 1}
    assert calls == {"older_than_hours": 24, "limit": 20}


# --- plafond anti-boucle-infinie (suite audit #77/#105) ----------------------------

@pytest.mark.asyncio
async def test_retry_stale_pending_abandons_past_threshold():
    # Encore MOU après ce passage ET au-delà du seuil -> bascule en 'abandoned',
    # PAS 'skip_incomplete' -- c'est le comptage qui doit refléter l'arrêt définitif.
    async def lister():
        return [{"contract": "0xSTUCK"}]

    async def absorber(contract):
        return "skip_incomplete"

    async def abandon_checker(contract):
        assert contract == "0xSTUCK"
        return True

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber,
                                          abandon_checker=abandon_checker)
    assert counts == {"abandoned": 1}


@pytest.mark.asyncio
async def test_retry_stale_pending_keeps_skip_incomplete_below_threshold():
    # Encore MOU mais pas encore au-delà du seuil -> reste 'skip_incomplete', retenté
    # au prochain cycle (comportement inchangé pour un candidat qui a encore une chance).
    async def lister():
        return [{"contract": "0xTRYING"}]

    async def absorber(contract):
        return "skip_incomplete"

    async def abandon_checker(contract):
        return False

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber,
                                          abandon_checker=abandon_checker)
    assert counts == {"skip_incomplete": 1}


@pytest.mark.asyncio
async def test_retry_stale_pending_does_not_check_abandon_on_resolved_verdicts():
    # 'kept' (mûri) et 'rejected' (malveillant confirmé) sont déjà des verdicts
    # terminaux -- abandon_checker ne doit JAMAIS être consulté dans ces cas
    # (pas de double logique, un seul chemin décide du sort d'un candidat résolu).
    async def lister():
        return [{"contract": "0xGOOD"}, {"contract": "0xBAD"}]

    async def absorber(contract):
        return {"0xGOOD": "kept", "0xBAD": "rejected"}[contract]

    async def abandon_checker(contract):
        raise AssertionError("ne doit jamais être appelé sur un verdict déjà résolu")

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber,
                                          abandon_checker=abandon_checker)
    assert counts == {"kept": 1, "rejected": 1}


@pytest.mark.asyncio
async def test_retry_stale_pending_default_abandon_checker_uses_screened_pool(monkeypatch):
    from aria_core import screened_pool as sp

    async def lister():
        return [{"contract": "0xSTUCK2"}]

    async def absorber(contract):
        return "skip_incomplete"

    calls: dict[str, object] = {}

    async def fake_abandon_stale_pending(contract, *, max_retries=5, max_age_days=7):
        calls["contract"] = contract
        calls["max_retries"] = max_retries
        calls["max_age_days"] = max_age_days
        return True

    monkeypatch.setattr(sp, "abandon_stale_pending", fake_abandon_stale_pending)

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber)
    assert counts == {"abandoned": 1}
    assert calls == {"contract": "0xSTUCK2", "max_retries": 5, "max_age_days": 7}
