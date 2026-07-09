"""Précision du filtre is_live_info_question / is_explicit_web_request (09/07).

Deux faux positifs trouvés en testant le filtre sur des exemples variés (demande
opérateur) et corrigés dans le même commit :
- "nba" matchait en sous-chaîne dans n'importe quel mot le contenant (ex. "Coinbase").
- "aujourd'hui"/"ce soir"/"demain" seuls déclenchaient le chemin web même dans du
  smalltalk banal ("comment vas-tu aujourd'hui ?").
"""
from __future__ import annotations

from aria_core.knowledge.web_verify import is_explicit_web_request, is_live_info_question


def test_nba_substring_inside_coinbase_no_longer_false_positive():
    assert not is_live_info_question("cherche sur internet qui est le CEO de Coinbase")


def test_nba_standalone_still_matches():
    assert is_live_info_question("qui a gagné le match NBA hier soir ?")


def test_bare_temporal_word_no_longer_triggers_alone():
    assert not is_live_info_question("comment vas-tu aujourd'hui ?")
    assert not is_live_info_question("on se voit demain ?")
    assert not is_live_info_question("à ce soir !")


def test_temporal_word_paired_with_real_signal_still_triggers():
    assert is_live_info_question("quel est le cours du bitcoin aujourd'hui ?")
    assert is_live_info_question("à quelle heure joue le PSG ce soir ?")


def test_explicit_web_request_detected():
    assert is_explicit_web_request("vérifie sur le web si cette adresse est bien Paradigm")
    assert is_explicit_web_request("cherche sur internet qui est le CEO de Coinbase")
    assert is_explicit_web_request("fais une recherche sur les standards ERC")
    assert is_explicit_web_request("search the web for the latest Base grants")


def test_explicit_web_request_not_triggered_by_casual_text():
    assert not is_explicit_web_request("tu penses quoi de la vie en général ?")
    assert not is_explicit_web_request("explique-moi ta stratégie d'investissement")


def test_explicit_web_request_excludes_ecosystem_product_apk():
    # Même garde-fou que is_live_info_question : jamais de recherche web pour un APK
    # "Aria Market" (anti-scam, cf. is_ecosystem_product_query).
    assert not is_explicit_web_request("cherche sur internet l'apk de Aria Market")


def test_opinion_question_never_triggers_either_path():
    msg = "tu penses quoi de la vie en général ?"
    assert not is_live_info_question(msg)
    assert not is_explicit_web_request(msg)


def test_internal_strategy_question_never_triggers_web():
    # Question sur SA PROPRE stratégie -- connaissance interne, pas une recherche web.
    msg = "explique-moi ta stratégie d'investissement"
    assert not is_live_info_question(msg)
    assert not is_explicit_web_request(msg)
