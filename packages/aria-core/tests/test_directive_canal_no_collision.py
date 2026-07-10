"""Garde-fou anti-récidive (10/07) : /canal (ARIA -> Claude Code, pilote) doit rester
son propre nom de fonction/commande, jamais réutilisé pour autre chose.

Incident réel : le pilote /canal a d'abord été écrit avec le MÊME nom de fonction
Python (_handle_directive) qu'une commande /directive déjà existante -- une
redéfinition de fonction au niveau module écrase silencieusement la précédente à
l'import, sans erreur, sans warning. La commande opérateur /directive est devenue
injoignable en prod tant que ça n'a pas été repéré manuellement. Aucun test ne l'a
détecté sur le coup.

La commande /directive d'origine a depuis été retirée entièrement (jamais utilisée
en pratique, doublon du vrai flux : demander à Claude Code d'éditer directives.md
directement, revu et testé) -- voir directives.md. Ce fichier verrouille que
/canal reste unique et correctement enregistré, pour que toute future addition ne
recrée pas silencieusement la même collision de nom.
"""
from __future__ import annotations

from aria_core.gateway import telegram_bot


def test_directive_command_fully_removed():
    assert not hasattr(telegram_bot, "_handle_directive")


def test_canal_registered_under_its_own_command():
    import inspect

    src = inspect.getsource(telegram_bot)
    assert 'CommandHandler("canal", _handle_aria_channel)' in src
    assert 'CommandHandler("directive"' not in src
