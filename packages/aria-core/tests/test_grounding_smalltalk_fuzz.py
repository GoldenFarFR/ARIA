"""Régression is_pure_casual_smalltalk (grounding.py) -- audit fuzz post-web_verify (09/07).

Même méthode que test_web_verify_fuzz_500x3.py, appliquée à _CASUAL_SMALLTALK_RE /
_META_SELF_RE : mots-pièges business qui ressemblent à du smalltalk. Bug trouvé et
corrigé au même commit -- "ton" (possessif \"ton avis\") confondu avec \"tonalité\",
"temps"/"chat"/"cash"/"clash"/"filtre"/"frigo"/"matin"/"soir"/"jeu"/"long"/"court"
faux-positivaient sur de VRAIES questions stratégiques opérateur (vérifié empiriquement :
"Quel est ton avis sur la stratégie ?" tombait en smalltalk pur -> réponse tronquée à
2 phrases). Conséquence directe : `is_pure_casual_smalltalk` gate le budget de réponse
et plusieurs branches de routage opérateur dans brain.py (skip meta-routing, ultra-short
reply). Un faux positif dégradait silencieusement la qualité de réponse à une VRAIE
question stratégique -- pas juste un déclenchement web superflu.
"""
from __future__ import annotations

import pytest

from aria_core.grounding import is_pure_casual_smalltalk


CASES = [
    ("Quel est ton avis sur la stratégie VC réelle ?", False),  # serious_with_former_traps
    ("Explique-moi ton raisonnement sur ce token.", False),  # serious_with_former_traps
    ("Peux-tu vérifier ton calcul de sizing Kelly ?", False),  # serious_with_former_traps
    ("Comment tu évalues ton propre taux de réussite ?", False),  # serious_with_former_traps
    ("Peux-tu payer cash sur ce protocole de paiement ?", False),  # serious_with_former_traps
    ("Il y a un clash entre deux features du cockpit ?", False),  # serious_with_former_traps
    ("Peux-tu appliquer un filtre plus strict sur les scans ?", False),  # serious_with_former_traps
    ("On met ce projet au frigo pour l'instant, tu es d'accord ?", False),  # serious_with_former_traps
    ("Dans notre chat Telegram, peux-tu activer le mode admin ?", False),  # serious_with_former_traps
    ("Quelle est notre vision à long terme pour la levée de fonds ?", False),  # serious_with_former_traps
    ("On se voit demain pour revoir la stratégie de levée de fonds ?", False),  # serious_with_former_traps
    ("Il y a des news sur le partenariat Base, tu peux résumer ?", False),  # serious_with_former_traps
    ("Ce matin le marché était très volatile, tu as des chiffres ?", False),  # serious_with_former_traps
    ("Il fait le nécessaire pour protéger le wallet, tu confirmes ?", False),  # serious_with_former_traps
    ("C'est un jeu risqué ce trade, tu es sûre de ton analyse ?", False),  # serious_with_former_traps
    ("Quel est le prix de ce token ce soir ?", False),  # serious_with_former_traps
    ("On a rendez-vous demain matin pour le point stratégique.", False),  # serious_with_former_traps
    ("Hier soir tu as détecté un signal intéressant sur Base.", False),  # serious_with_former_traps
    ("Le temps presse pour cette décision, on tranche aujourd'hui ?", False),  # serious_with_former_traps
    ("Comment fonctionne le honeypot filter exactement ?", False),  # serious_with_former_traps
    ("Il fait beau aujourd'hui chez toi ?", True),  # real_smalltalk
    ("Il fait super chaud en ce moment, non ?", True),  # real_smalltalk
    ("Quel ton dois-je adopter pour ce message aux investisseurs ?", True),  # real_smalltalk
    ("Ce ton était un peu sec dans ta dernière réponse.", True),  # real_smalltalk
    ("Ta réponse était trop longue, sois plus concise.", True),  # real_smalltalk
    ("Ta réponse était trop courte, développe un peu plus.", True),  # real_smalltalk
    ("Raconte-moi une bonne blague.", True),  # real_smalltalk
    ("Tu connais une vanne sur les cryptos ?", True),  # real_smalltalk
    ("Comment ça va toi ?", True),  # real_smalltalk
    ("Tu vas bien aujourd'hui ?", True),  # real_smalltalk
    ("Comment s'est passée ta journée ?", True),  # real_smalltalk
    ("Comment s'est passée ta soirée hier ?", True),  # real_smalltalk
    ("T'as bien mangé ce midi ?", True),  # real_smalltalk
    ("Tu prends un café ce matin ?", True),  # real_smalltalk
    ("T'es fatiguée en ce moment ?", True),  # real_smalltalk
    ("T'as bien dormi cette nuit ?", True),  # real_smalltalk
    ("Tu pars en vacances bientôt ?", True),  # real_smalltalk
    ("Tu regardes des films en ce moment ?", True),  # real_smalltalk
    ("Tu écoutes quoi comme musique ?", True),  # real_smalltalk
    ("Tu as un animal de compagnie ?", True),  # real_smalltalk
    ("Tu as de la famille dans le coin ?", True),  # real_smalltalk
    ("T'as prévu de voyager bientôt ?", True),  # real_smalltalk
    ("Tu es plutôt du genre sérieux ou détendu ?", True),  # real_smalltalk
    ("T'as de l'humour toi en fait.", True),  # real_smalltalk
    ("C'était juste une provoc, ne le prends pas mal.", True),  # real_smalltalk
    ("Comment fonctionne le wallet_guard exactement ?", False),  # neutral_factual
    ("Explique-moi la méthode smart money que tu utilises.", False),  # neutral_factual
    ("Quel est le score composite du candidate_ranking ?", False),  # neutral_factual
    ("Comment tu calibres tes prédictions VC ?", False),  # neutral_factual
    ("Qu'est-ce qui différencie un builder d'un farmer ?", False),  # neutral_factual
    ("Comment fonctionne l'ancrage Merkle sur Sepolia ?", False),  # neutral_factual
    ("Peux-tu m'expliquer le stop suiveur du paper trading ?", False),  # neutral_factual
    ("Quelle est ta doctrine sur la transparence des devs ?", False),  # neutral_factual
    ("Comment fonctionne le kill-switch outgoing_pause ?", False),  # neutral_factual
    ("Explique-moi le seam architecture-extensibilite.", False),  # neutral_factual
    ("Bonjour, comment vas-tu ?", False),  # greeting_or_help_shortcircuit
    ("Salut !", False),  # greeting_or_help_shortcircuit
    ("Aide-moi à comprendre les commandes disponibles.", False),  # greeting_or_help_shortcircuit
    ("What can you do exactly?", False),  # greeting_or_help_shortcircuit
    ("QUEL EST TON AVIS SUR LA STRATEGIE ?", False),  # edge_case
    ("mon ton était peut-être un peu dur, désolé", True),  # edge_case
    ("mon avis sur le token est mitigé pour l'instant", False),  # edge_case
    ("le style de ce rapport est vraiment premium", True),  # edge_case
    ("le style d'investissement de ce fondateur m'inquiète un peu", True),  # edge_case
]


@pytest.mark.parametrize("text,expected", CASES)
def test_is_pure_casual_smalltalk_fuzz(text, expected):
    assert is_pure_casual_smalltalk(text) is expected
