"""Ticket #144 : non-régression sur le crash UnboundLocalError du chemin opérateur
(_general_response), corrigé.

Cause confirmée par lecture de code ET reproduction empirique (voir rapport de
diagnostic) : `brain.py` (autour de la L971-985) importait `wants_capability_improvement`
(et 7 autres noms) UNIQUEMENT dans la branche `else` d'un `if re.search(...\btonb...)`,
mais les référençait ensuite SANS CONDITION (même niveau d'indentation que le if/else).
Si le message contenait un des mots "humour", "sérieux", "sérieuse", "trop sérieux",
"ton" ou "personnalité" comme mot isolé (le pronom possessif "ton" suffit -- "donne moi
TON avis", "de TON wallet officiel"), la branche qui importe n'était jamais exécutée -->
UnboundLocalError garanti et déterministe sur ce texte, à chaque appel. Corrigé en
ré-indentant les usages à l'intérieur du même `else` que les imports.

Cas #2 et #3 de l'incident du 12/07. Le cas #1 ("tu pense qu'il manipule les gens et le
marché pour sa personne ?") ne contenait aucun de ces mots-déclencheurs -- confirmé ne
jamais avoir été ce bug précis ; sa cause reste à déterminer séparément (traceback
complet ajouté en prod, voir telegram_bot.py) et n'est PAS résolue par ce correctif.
"""
from __future__ import annotations

import pytest

from aria_core.brain import AriaBrain
from aria_core.locale import LANG_FR

CASE_2 = "donne moi ton avis sur ce qui va et ce qui va pas et propose moi un nouveau prompt complet que tu juge mieu"
CASE_3 = "rapelle moi ladresse de ton wallet officiel evm"
CASE_1 = "tu pense qu'il manipule les gens et le marché pour sa personne ?"


async def _fake_repertoire_summary(lang):
    return "stub"


@pytest.mark.asyncio
async def test_case_2_no_longer_crashes(monkeypatch):
    import aria_core.brain as brain_mod

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)
    brain = AriaBrain()
    reply, skill, actions, data, _ = await brain._general_response(CASE_2, LANG_FR, public=False)
    assert isinstance(reply, str)


@pytest.mark.asyncio
async def test_case_3_no_longer_crashes(monkeypatch):
    import aria_core.brain as brain_mod

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)
    brain = AriaBrain()
    reply, skill, actions, data, _ = await brain._general_response(CASE_3, LANG_FR, public=False)
    assert isinstance(reply, str)


@pytest.mark.asyncio
async def test_case_1_still_does_not_hit_this_specific_bug(monkeypatch):
    """Documente que le cas #1 empruntait déjà un autre chemin avant le fix -- ne pas
    conclure à tort que ce correctif résout aussi #1."""
    import aria_core.brain as brain_mod

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)
    brain = AriaBrain()
    reply, skill, actions, data, _ = await brain._general_response(CASE_1, LANG_FR, public=False)
    assert isinstance(reply, str)
