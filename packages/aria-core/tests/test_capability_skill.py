"""wants_capability() -- routage de la question "j'aimerai un rapport de niveaux ?"
vers la skill dédiée, distinct de la conversation générale.

Incident #146 (12/07) : "capacit"/"compétence" en sous-chaîne libre matchaient
n'importe quelle phrase normale contenant le mot "capacité" -- une question
personnelle de l'opérateur a été aiguillée vers le rapport "Indice ARIA" au lieu
de la conversation. Retirés du regex, cf. capability_skill.py.
"""
from __future__ import annotations

from aria_core.skills.capability_skill import wants_capability


def test_incident_146_personal_question_not_misrouted():
    # Texte exact de l'incident (12/07).
    text = (
        "j'aimerai un jour grace au revenu ouvrir un chenil pour animaux "
        "totalement financer par les gain que tu génere, sa te donnerai une "
        "motivation supplémentaire ou tu t'en fou tu suis ta voie ? ou alors "
        "tu t'investirai au dela de l'argent dans mon projet grace a ta "
        "capacité à gérer plus qu'un humain plusieurs chose ?"
    )
    assert wants_capability(text) is False


def test_generic_capacite_sentences_not_misrouted():
    assert wants_capability("quelle est ta capacité à analyser un token rapidement ?") is False
    assert wants_capability("tu penses avoir la compétence pour ça ?") is False
    assert wants_capability("ta capacité de jugement m'impressionne") is False


def test_legitimate_capability_requests_still_routed():
    assert wants_capability("montre moi ton qi") is True
    assert wants_capability("indice aria") is True
    assert wants_capability("quel est ton niveau actuel ?") is True
    assert wants_capability("level up codage") is True
    assert wants_capability("score aria") is True
    assert wants_capability("progression aria cette semaine") is True
