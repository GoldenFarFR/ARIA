"""Garde-fou mécanique anti-récidive (18/07) : 9 commandes admin entièrement
écrites (_handle_x, _handle_avatar, _handle_repertoire, _handle_qi, _handle_level,
_handle_learn, _handle_calibrate, _handle_experiment, _handle_handles) sont restées
JAMAIS enregistrées via add_handler(CommandHandler(...)) -- inaccessibles depuis
toujours malgré une doc CLAUDE.md qui les traitait comme actives. Ce test empêche
un futur `_handle_*` d'être écrit puis oublié de _register_handlers, sans avoir à
se souvenir de le vérifier à la main à chaque nouvelle commande.
"""
from __future__ import annotations

import inspect
import re

from aria_core.gateway import telegram_bot

# Fonctions _handle_* qui ne sont PAS des commandes slash autonomes -- dispatchées
# différemment (MessageHandler générique, CallbackQueryHandler, ou appelées en
# interne par un autre handler déjà couvert). Toute nouvelle entrée ici doit être
# justifiée, jamais un moyen de faire taire ce garde-fou sans vérifier.
_NOT_STANDALONE_COMMANDS = {
    "_handle_message",       # MessageHandler(filters.TEXT & ~filters.COMMAND, ...)
    "_handle_photo",         # MessageHandler(filters.PHOTO, ...) -- dispatch par légende
    "_handle_callback",      # CallbackQueryHandler(...)
    "_handle_public_message",  # appelé en interne par _handle_message
    "_handle_vision_photo",    # appelé en interne par _handle_photo
    "_handle_avatar_photo",    # appelé en interne par _handle_photo
}


def _defined_handle_functions() -> set[str]:
    return {
        name
        for name, obj in vars(telegram_bot).items()
        if name.startswith("_handle_") and inspect.iscoroutinefunction(obj)
    }


def _registered_command_handler_targets() -> set[str]:
    """Extrait les noms de fonctions passés à CommandHandler(...) dans le SOURCE de
    _register_handlers (pas d'exécution -- python-telegram-bot n'est pas mocké ici)."""
    src = inspect.getsource(telegram_bot._register_handlers)
    # Capture ", _handle_xxx)" ou ", _handle_xxx))" après CommandHandler(
    return set(re.findall(r"CommandHandler\([^)]*?,\s*(_handle_[a-z_0-9]+)\)", src))


def test_every_standalone_handle_function_is_registered_as_a_command():
    defined = _defined_handle_functions() - _NOT_STANDALONE_COMMANDS
    registered = _registered_command_handler_targets()
    missing = sorted(defined - registered)
    assert not missing, (
        f"Handler(s) écrit(s) mais jamais enregistré(s) via CommandHandler : {missing} -- "
        "commande(s) inaccessible(s) depuis Telegram malgré un backend fonctionnel."
    )


def test_registered_targets_all_exist_as_real_functions():
    """Sens inverse : jamais un CommandHandler qui pointe vers un nom mal orthographié
    ou une fonction supprimée -- lèverait une NameError seulement au premier appel réel."""
    registered = _registered_command_handler_targets()
    defined = _defined_handle_functions()
    ghosts = sorted(registered - defined)
    assert not ghosts, f"CommandHandler référence des fonctions inexistantes : {ghosts}"


def test_all_nine_recovered_commands_specifically_registered():
    """Verrouille précisément l'incident du 18/07 -- ces 9 noms doivent apparaître,
    pas seulement "le compte total colle par coïncidence"."""
    registered = _registered_command_handler_targets()
    for fn in (
        "_handle_x", "_handle_avatar", "_handle_repertoire", "_handle_qi",
        "_handle_level", "_handle_learn", "_handle_calibrate", "_handle_experiment",
        "_handle_handles",
    ):
        assert fn in registered, f"{fn} toujours non enregistré"
