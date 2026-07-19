"""Garde d'initialisation de repertoire_db.py (18/07, #213, bug réel -- 2 tours).

get_all/get_by_id/delete_item/archive_item/get_holding_id/create faisaient une
requête SQL brute sur `repertoire` sans jamais garantir la table -- ne
fonctionnait que parce qu'en prod, le boot FastAPI appelle init_repertoire_db()
une fois avant tout trafic réel. Trouvé en ajoutant un nouveau fichier de test
isolé (`no such table: repertoire`) -- même famille de bug déjà documentée pour
auth_db_path (#149, 13/07), jamais fermée pour ce module précis avant ce soir.

Un 1er correctif (flag booléen "déjà initialisé, ne jamais rejouer") a semblé
fonctionner en isolation MAIS a été réfuté par un 2e tour de suite complète :
le flag restait `True` en mémoire même après qu'un tmp_path antérieur avait été
nettoyé par pytest (fichier SQLite disparu, table avec) -- un faux négatif pire
que l'absence de garde. `_ensure_initialized()` rejoue donc SYSTÉMATIQUEMENT
init_repertoire_db() à chaque appel (idempotent, jamais de cache qui peut
mentir sur l'état réel du disque)."""
from __future__ import annotations

import pytest

from aria_core import repertoire_db


@pytest.fixture(autouse=True)
def _fresh_never_initialized_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repertoire_db, "DB_PATH", str(tmp_path / "never_initialized" / "aria.db"))


@pytest.mark.asyncio
async def test_get_all_works_without_prior_init():
    # Ne lève jamais "no such table" -- la holding auto-amorcée (_seed_holding_group,
    # appelée via init_repertoire_db()) est la seule entrée attendue à ce stade.
    items = await repertoire_db.get_all()
    assert len(items) == 1
    assert items[0].entity_type.value == "holding"


@pytest.mark.asyncio
async def test_get_holding_id_works_without_prior_init():
    # Le seed de la holding tourne via _ensure_initialized() -> init_repertoire_db()
    # -> _seed_holding_group() -- doit exister dès le premier appel, jamais None.
    holding_id = await repertoire_db.get_holding_id()
    assert holding_id is not None


@pytest.mark.asyncio
async def test_create_works_without_prior_init():
    item = await repertoire_db.create("Test Projet", description="fixture")
    assert item.name == "Test Projet"

    fetched = await repertoire_db.get_by_id(item.id)
    assert fetched is not None
    assert fetched.name == "Test Projet"


@pytest.mark.asyncio
async def test_archive_and_delete_work_without_prior_init():
    item = await repertoire_db.create("À archiver")
    ok, _msg, archived = await repertoire_db.archive_item(item.id)
    assert ok is True
    assert archived.status.value == "archived"

    ok, _msg, _item = await repertoire_db.delete_item(item.id)
    assert ok is True
    assert await repertoire_db.get_by_id(item.id) is None


@pytest.mark.asyncio
async def test_ensure_initialized_replays_every_call_never_caches(monkeypatch):
    """Verrouille explicitement le choix du 2e tour (18/07) : jamais de flag
    "déjà fait" qui pourrait mentir si le fichier SQLite disparaît sous les
    pieds du process (ex. tmp_path nettoyé par pytest, ou tout autre reset du
    disque sans redémarrage du process) -- init_repertoire_db() est rejoué à
    CHAQUE appel, idempotent par construction (CREATE TABLE IF NOT EXISTS,
    seed/purge qui vérifient avant d'écrire)."""
    calls = {"count": 0}
    real_init = repertoire_db.init_repertoire_db

    async def counting_init():
        calls["count"] += 1
        await real_init()

    monkeypatch.setattr(repertoire_db, "init_repertoire_db", counting_init)

    await repertoire_db.get_all()
    await repertoire_db.get_all()
    await repertoire_db.get_holding_id()

    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_survives_table_deleted_from_under_it(monkeypatch, tmp_path):
    """LE régression test du vrai bug trouvé ce soir -- reproduit fidèlement
    "le flag dit True mais le fichier a disparu" en supprimant le fichier
    SQLite entre deux appels, sans jamais toucher DB_PATH lui-même (même
    chemin, fichier removed puis recréé vide par SQLite au prochain connect)."""
    await repertoire_db.get_all()  # 1er appel -- crée le fichier + la table

    import os as _os

    _os.remove(repertoire_db.DB_PATH)  # simule un disque nettoyé sous le process

    # Sans le correctif (flag en cache), ce 2e appel aurait levé
    # "no such table: repertoire" -- la vraie ré-initialisation doit se
    # redéclencher malgré la "mémoire" d'un appel précédent réussi.
    items = await repertoire_db.get_all()
    assert len(items) == 1
