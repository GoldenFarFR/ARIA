"""Incident réel (12/07, test manuel Telegram) : l'opérateur écrit en français, sans
accent et avec des fautes de frappe ("tu a scanner de nouveau projet qui t'interresse ?"),
juste après avoir tapé /whoami -- ARIA répond entièrement en anglais.

Diagnostic (confirmé, voir rapport) : le bug n'a RIEN à voir avec /whoami (aucun état de
langue partagé/persisté n'existe dans le code -- detect_lang() est stateless, appelé à
neuf sur chaque message). C'est detect_lang() lui-même qui bascule en anglais sur CE
message pris seul, à cause de son heuristique trop stricte (aucun accent + un seul
mot-indice sur les deux requis).

Correctif validé (option 2, feu vert opérateur) : le chemin opérateur (_handle_message et
les 4 autres commandes admin de telegram_bot.py) utilise désormais detect_operator_lang()
-- jamais de repli anglais, l'opérateur est un interlocuteur unique francophone par
doctrine, pas un cas à détecter. Le chemin public (_handle_public_message) garde
detect_lang() inchangé (détecteur générique, hors scope de ce correctif).
"""
from __future__ import annotations

from aria_core.locale import LANG_EN, LANG_FR, detect_lang, detect_operator_lang

INCIDENT_TEXT = "tu a scanner de nouveau projet qui t'interresse ?"


def test_operator_lang_never_falls_back_to_english_on_incident_message():
    # Le cas exact de l'incident du 12/07, sur le chemin opérateur -- doit rester FR.
    assert detect_operator_lang(INCIDENT_TEXT) == LANG_FR


def test_operator_lang_stays_french_on_any_ambiguous_message():
    # Message opérateur volontairement ambigu (pas de signal fort côté français) --
    # l'opérateur n'est pas un cas à détecter, doit rester FR quand même.
    assert detect_operator_lang("ok merci") == LANG_FR
    assert detect_operator_lang("status") == LANG_FR
    assert detect_operator_lang("42") == LANG_FR


def test_operator_lang_stays_french_on_accented_french_too():
    # Signal fort français (accent) -- toujours FR, sans surprise.
    assert detect_operator_lang("Peux-tu vérifier le déploiement ?") == LANG_FR


def test_public_lang_detection_unchanged_on_same_incident_message():
    # Le chemin public garde son détecteur générique inchangé (hors scope) -- sur ce
    # même message, il retombe toujours sur l'anglais par défaut, comme avant le fix.
    assert detect_lang(INCIDENT_TEXT) == LANG_EN


def test_public_lang_detection_still_detects_clear_french():
    # Non-régression : le détecteur générique fonctionne toujours normalement sur un
    # signal fort (accent ou >=2 mots-indices).
    assert detect_lang("Bonjour, comment ça va ?") == LANG_FR
