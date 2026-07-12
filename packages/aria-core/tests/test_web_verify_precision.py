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


def test_long_hypothetical_scenario_with_bare_market_word_no_longer_triggers():
    # Incident réel (12/07) : un scénario hypothétique de raisonnement (650+ caractères,
    # "Un token a : (1)... (2)... est-ce que j'achète ?") mentionnant une seule fois "prix"
    # est parti en recherche web littérale (la requête entière envoyée telle quelle à DDG).
    # Les 2492 cas du fuzz test sont tous sous 190 caractères -- jamais testé sur un texte long.
    long_hypothetical = (
        "Un token sur Base a : (1) 40% de son supply détenu par le wallet créateur mais "
        "renoncé (owner renounced) il y a 6 mois, (2) un volume 24h qui a été multiplié par "
        "15 dans les 3 dernières heures sans annonce publique trouvable, (3) une liquidité "
        "de 45k$ mais qui vient d'être divisée par deux il y a 40 minutes sur un seul "
        "retrait, (4) le compte X du projet est inactif depuis 3 semaines mais un compte "
        "tiers non-officiel avec 200 followers vient de le mentionner 6 fois en 1 heure. "
        "Le prix a pris +180% en 2 heures. Est-ce que j'achète maintenant, et si oui avec "
        "quel plan de sortie ?"
    )
    assert not is_live_info_question(long_hypothetical)
    assert not is_explicit_web_request(long_hypothetical)


def test_long_text_with_unambiguous_signal_still_triggers():
    # Le garde de longueur ne doit PAS supprimer un signal vraiment non ambigu (rugby,
    # coupe du monde, actu...) même dans un texte long -- seul un mot de marché générique
    # isolé (prix/bitcoin/crypto/...) doit perdre son pouvoir déclencheur seul.
    long_with_rugby = (
        "Je réfléchis à plein de choses en ce moment : mon portefeuille crypto, mes "
        "objectifs professionnels pour l'année, et accessoirement je me demande à quelle "
        "heure joue le Stade Toulousain ce soir, parce que j'ai prévu de regarder le match "
        "avec des amis et je veux organiser la soirée en conséquence sans me tromper d'horaire."
    )
    assert is_live_info_question(long_with_rugby)


def test_short_bare_price_question_still_triggers():
    # Non-régression explicite : une question courte et directe sur un prix doit continuer
    # à déclencher, garde de longueur non pertinente ici (bien sous le seuil).
    assert is_live_info_question("Quel est le prix du bitcoin ?")


def test_opinion_question_never_triggers_either_path():
    msg = "tu penses quoi de la vie en général ?"
    assert not is_live_info_question(msg)
    assert not is_explicit_web_request(msg)


def test_internal_strategy_question_never_triggers_web():
    # Question sur SA PROPRE stratégie -- connaissance interne, pas une recherche web.
    msg = "explique-moi ta stratégie d'investissement"
    assert not is_live_info_question(msg)
    assert not is_explicit_web_request(msg)
