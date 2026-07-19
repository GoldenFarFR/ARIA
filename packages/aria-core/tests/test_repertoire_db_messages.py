"""save_message/get_messages (repertoire_db.py) -- bug réel trouvé le 18/07 (#213)
en ajoutant un nouveau test file : les deux faisaient un INSERT/SELECT brut sur
``agent_messages`` sans jamais garantir son existence, ne fonctionnant que si
``init_repertoire_db()`` avait DÉJÀ tourné ailleurs dans le process (vrai en
prod au boot FastAPI, jamais garanti dans un contexte de test isolé -- exact
même famille que le bug ``auth_db_path`` déjà documenté #149, 13/07).

``DB_PATH`` est un module-level constant (calculé une seule fois à l'import) --
ces tests pointent explicitement ``repertoire_db.DB_PATH`` vers un fichier
tmp_path FRAIS, jamais initialisé par ``init_repertoire_db()``, pour reproduire
fidèlement les conditions exactes du bug plutôt que de dépendre de l'ordre de
collection des autres tests."""
from __future__ import annotations

import pytest

from aria_core import repertoire_db


@pytest.fixture(autouse=True)
def _fresh_never_initialized_db(tmp_path, monkeypatch):
    """DB_PATH pointé sur un fichier qui n'existe pas encore et qui n'a JAMAIS
    vu passer init_repertoire_db() -- reproduit exactement la condition du bug."""
    monkeypatch.setattr(repertoire_db, "DB_PATH", str(tmp_path / "never_initialized" / "aria.db"))


@pytest.mark.asyncio
async def test_save_message_works_without_prior_init_repertoire_db():
    """LE régression test -- save_message() sur une DB jamais initialisée ne
    doit jamais lever `no such table: agent_messages`."""
    msg_id = await repertoire_db.save_message("user", "test message", visitor_id="v1")
    assert msg_id


@pytest.mark.asyncio
async def test_get_messages_works_without_prior_init_repertoire_db():
    messages = await repertoire_db.get_messages(limit=10)
    assert messages == []  # DB fraîche, jamais crash


@pytest.mark.asyncio
async def test_save_then_get_round_trip_on_fresh_db():
    await repertoire_db.save_message("user", "bonjour", visitor_id="v1")
    await repertoire_db.save_message("aria", "salut !", visitor_id="v1")

    messages = await repertoire_db.get_messages(limit=10, visitor_id="v1")
    assert len(messages) == 2
    contents = {m["content"] for m in messages}
    assert contents == {"bonjour", "salut !"}


@pytest.mark.asyncio
async def test_ensure_agent_messages_table_idempotent_across_calls():
    """CREATE TABLE IF NOT EXISTS répété plusieurs fois -- jamais une erreur,
    jamais une perte de données déjà écrites."""
    await repertoire_db.save_message("user", "premier", visitor_id="v1")
    # Un 2e appel direct à save_message ré-invoque _ensure_agent_messages_table
    # en interne -- ne doit jamais effacer/dupliquer le message précédent.
    await repertoire_db.save_message("user", "second", visitor_id="v1")

    messages = await repertoire_db.get_messages(limit=10, visitor_id="v1")
    assert len(messages) == 2
