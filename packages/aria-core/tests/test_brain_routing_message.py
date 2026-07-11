"""_routing_message (pont Cursor/KART) — nom configurable, jamais en dur (#114).

Le nom réel de l'opérateur ne doit JAMAIS apparaître dans ce repo public --
y compris ici : ces tests utilisent des noms de test génériques pour prouver
que le mécanisme fonctionne quel que soit le nom configuré via
``settings.aria_operator_display_name``, sans jamais écrire le vrai nom.
"""
from __future__ import annotations

from aria_core.brain import _routing_message


def test_routing_message_matches_configured_confirms_prefix(test_settings):
    test_settings.aria_operator_display_name = "TestOperator"
    msg = "TestOperator confirme : go pour le déploiement"
    assert _routing_message(msg) == "go pour le déploiement"


def test_routing_message_matches_configured_current_message_prefix(test_settings):
    test_settings.aria_operator_display_name = "TestOperator"
    msg = "Message actuel de TestOperator: vérifie le PR"
    assert _routing_message(msg) == "vérifie le PR"


def test_routing_message_case_insensitive(test_settings):
    test_settings.aria_operator_display_name = "TestOperator"
    msg = "testoperator CONFIRME : ok"
    assert _routing_message(msg) == "ok"


def test_routing_message_still_matches_grok_prefix_independent_of_operator_name(test_settings):
    # Le préfixe "Grok" est un second émetteur du même pont, distinct du nom opérateur --
    # ne doit jamais dépendre de la valeur configurée.
    test_settings.aria_operator_display_name = "TestOperator"
    msg = "Message actuel de Grok: réponse automatique"
    assert _routing_message(msg) == "réponse automatique"


def test_routing_message_default_operator_name_is_generic_not_hardcoded(test_settings):
    # Sans configuration explicite, le défaut est "Operator" (générique) -- jamais un nom
    # réel en dur dans le code. Le préfixe par défaut fonctionne quand même.
    msg = "Operator confirme : ok"
    assert _routing_message(msg) == "ok"


def test_routing_message_a_different_configured_name_does_not_leak_into_another(test_settings):
    # Un message qui utilise un AUTRE nom que celui configuré ne doit pas matcher --
    # preuve que la détection dépend bien de la config, pas d'un nom en dur oublié.
    test_settings.aria_operator_display_name = "TestOperator"
    msg = "SomeoneElse confirme : ceci ne doit pas être extrait"
    assert _routing_message(msg) == msg


def test_routing_message_ordinary_visitor_message_passes_through_unchanged(test_settings):
    # Un message public normal (aucun préfixe de pont) doit toujours être traité tel
    # quel, jamais altéré par cette détection -- non-régression sur le chemin visiteur.
    test_settings.aria_operator_display_name = "TestOperator"
    msg = "quel est le prix du bitcoin aujourd'hui ?"
    assert _routing_message(msg) == msg


def test_routing_message_multiline_current_message_prefix(test_settings):
    test_settings.aria_operator_display_name = "TestOperator"
    msg = "Message actuel de TestOperator: ligne un\nligne deux ignorée par (.+?)"
    assert _routing_message(msg) == "ligne un"
