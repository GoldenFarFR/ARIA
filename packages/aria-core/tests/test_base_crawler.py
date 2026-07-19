"""Crawler Base — découverte + absorption (déterministe, réseau injecté)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    # 19/07 -- corrigé : les deux dates étaient codées EN DUR (2026-06-01/2026-07-12),
    # bombe à retardement pure -- "fresh" (12/07) a fini par dépasser le seuil de 7j
    # simplement parce que le temps a passé, faisant échouer le test sans aucun
    # rapport avec le comportement testé. Calculées relativement à `now` désormais :
    # le test reste valide indéfiniment, peu importe quand il tourne.
    old, fresh = "0x" + "f" * 40, "0x" + "1" * 40
    now = datetime.now(timezone.utc)
    old_iso = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_iso = (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def fetch(path):
        return _pool_payload_with_age([(old, 80_000, old_iso), (fresh, 80_000, fresh_iso)])

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

    async def absorber(contract, **kw):
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

    async def absorber(contract, **kw):
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
    ages: list[object] = []

    async def fake_absorb(contract, *, known_age_days=None):
        absorbed.append(contract)
        ages.append(known_age_days)
        return "kept"

    import aria_core.token_absorber as token_absorber_module

    monkeypatch.setattr(token_absorber_module, "absorb", fake_absorb)

    counts = await bc.retry_stale_pending()
    assert absorbed == ["0xDEFAULT"]
    assert counts == {"kept": 1}
    assert calls == {"older_than_hours": 24, "limit": 20}
    # Pas de 'first_screened_at' sur la ligne fake -> known_age_days=None (Volet C,
    # 12/07) : le pré-filtre par défaut ne peut rien déduire, pas d'exception non plus.
    assert ages == [None]


# --- plafond anti-boucle-infinie (suite audit #77/#105) ----------------------------

@pytest.mark.asyncio
async def test_retry_stale_pending_abandons_past_threshold():
    # Encore MOU après ce passage ET au-delà du seuil -> bascule en 'abandoned',
    # PAS 'skip_incomplete' -- c'est le comptage qui doit refléter l'arrêt définitif.
    async def lister():
        return [{"contract": "0xSTUCK"}]

    async def absorber(contract, **kw):
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

    async def absorber(contract, **kw):
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

    async def absorber(contract, **kw):
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

    async def absorber(contract, **kw):
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


# --- Volet C (12/07) : wrapper par défaut passant known_age_days au pré-filtre -----

@pytest.mark.asyncio
async def test_retry_stale_pending_default_wrapper_derives_known_age_days(monkeypatch):
    # first_screened_at à 3 jours -> known_age_days ~3.0 transmis au pré-filtre par
    # défaut de token_absorber.absorb (câblage uniquement, la décision reste dans
    # token_absorber -- cf. test_token_absorber.py pour la logique du pré-filtre).
    from datetime import datetime, timedelta, timezone

    from aria_core import screened_pool as sp

    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    async def lister():
        return [{"contract": "0xOLD", "first_screened_at": old_ts}]

    ages: list[object] = []

    async def fake_absorb(contract, *, known_age_days=None):
        ages.append(known_age_days)
        return "kept"

    import aria_core.token_absorber as token_absorber_module

    monkeypatch.setattr(token_absorber_module, "absorb", fake_absorb)

    await bc.retry_stale_pending(lister=lister)
    assert len(ages) == 1
    assert ages[0] == pytest.approx(3.0, abs=0.05)


@pytest.mark.asyncio
async def test_retry_stale_pending_default_wrapper_missing_first_screened_at_is_none(monkeypatch):
    async def lister():
        return [{"contract": "0xNOTS"}]  # pas de 'first_screened_at'

    ages: list[object] = []

    async def fake_absorb(contract, *, known_age_days=None):
        ages.append(known_age_days)
        return "kept"

    import aria_core.token_absorber as token_absorber_module

    monkeypatch.setattr(token_absorber_module, "absorb", fake_absorb)

    await bc.retry_stale_pending(lister=lister)
    assert ages == [None]


@pytest.mark.asyncio
async def test_retry_stale_pending_known_age_days_reaches_custom_absorber():
    # Correctif du 12/07 : ``heartbeat.py`` injecte TOUJOURS son propre ``absorber``
    # en prod (wrapper Volet A qui tague ``source``) -- un ``absorber`` personnalisé
    # DOIT recevoir ``known_age_days`` comme n'importe quel autre, sinon le pré-filtre
    # Volet C ne se déclenche jamais réellement (bug trouvé en vérifiant la prod).
    from datetime import datetime, timedelta, timezone

    old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

    async def lister():
        return [{"contract": "0xCUSTOM", "first_screened_at": old_ts}]

    received: list[object] = []

    async def absorber(contract, *, known_age_days=None):
        received.append(known_age_days)
        return "kept"

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber)
    assert counts == {"kept": 1}
    assert len(received) == 1
    assert received[0] == pytest.approx(5.0, abs=0.05)


@pytest.mark.asyncio
async def test_retry_stale_pending_known_age_days_reaches_heartbeat_style_wrapper():
    # Reproduit EXACTEMENT la forme du wrapper Volet A en prod
    # (``heartbeat.py::_absorb_top_pools``) : ``async def _wrap(contract, **kw)`` qui
    # retransmet ``**kw`` tel quel -- c'est précisément le trou qui avait échappé aux
    # tests existants (l'ancien câblage ne passait ``known_age_days`` qu'à un
    # absorber par défaut, jamais consulté puisque ``heartbeat.py`` en injecte
    # toujours un). Ici on vérifie que le kwarg traverse bien ce style de wrapper.
    from datetime import datetime, timedelta, timezone

    old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

    async def lister():
        return [{"contract": "0xHB", "first_screened_at": old_ts}]

    received_kw: list[dict] = []

    async def _underlying_absorb(contract, *, source="", known_age_days=None):
        received_kw.append({"source": source, "known_age_days": known_age_days})
        return "kept"

    async def _absorb_top_pools(contract, **kw):  # même forme que heartbeat.py
        return await _underlying_absorb(contract, source="top_pools", **kw)

    counts = await bc.retry_stale_pending(lister=lister, absorber=_absorb_top_pools)
    assert counts == {"kept": 1}
    assert received_kw == [{"source": "top_pools", "known_age_days": pytest.approx(5.0, abs=0.05)}]


@pytest.mark.asyncio
async def test_retry_stale_pending_abandons_skip_prefiltered_past_threshold():
    # 'skip_prefiltered' (Volet C) doit déclencher le même contrôle d'abandon que
    # 'skip_incomplete' -- un candidat structurellement bloqué ne doit pas boucler.
    async def lister():
        return [{"contract": "0xBLOCKED"}]

    async def absorber(contract, **kw):
        return "skip_prefiltered"

    async def abandon_checker(contract):
        assert contract == "0xBLOCKED"
        return True

    counts = await bc.retry_stale_pending(lister=lister, absorber=absorber,
                                          abandon_checker=abandon_checker)
    assert counts == {"abandoned": 1}
