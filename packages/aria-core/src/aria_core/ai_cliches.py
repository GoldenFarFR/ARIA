"""Clause partagée « mots interdits » — clichés de remplissage IA générique.

Distinct de `x_voice.py` (qui bannit l'AUTO-RÉFÉRENCE : « as an AI », « autonomous
agent »). Ici : le vocabulaire de remplissage générique qu'un LLM produit par défaut
même sans jamais se référencer lui-même (« processus complexe qui nécessite une
approche multidisciplinaire », « delve », « tapestry »...). Texte de PROMPT
uniquement — jamais de nettoyage post-génération ici : sur une surface dense en
chiffres (rapport /vc), retoucher le texte généré risquerait d'altérer un chiffre ;
la clause doit donc uniquement guider la génération, jamais réécrire la sortie.

Additif : à ajouter à un bloc système existant, jamais pour le remplacer.
"""
from __future__ import annotations

CLICHES_FR = (
    "processus complexe", "approche multidisciplinaire", "il est important de noter",
    "il convient de souligner", "jouer un rôle crucial", "jouer un rôle vital",
    "un vaste éventail", "riche tapisserie", "dans le paysage", "à ne pas négliger",
    "clé pour", "essentiel de",
)

CLICHES_EN = (
    "delve", "intricate", "commendable", "meticulous", "surpass", "elevate",
    "foster", "tapestry", "realm", "navigate the landscape", "boasts a",
    "plays a crucial role", "plays a vital role", "testament to",
    "comprehensive understanding", "rich tapestry", "in today's fast-paced world",
    "ever-evolving landscape",
)


def forbidden_cliches_prompt(lang: str = "en") -> str:
    """Clause additive FR/EN — mots/tournures de remplissage générique interdits."""
    if lang == "fr":
        exemples = ", ".join(f"« {c} »" for c in CLICHES_FR)
        return (
            "CLICHÉS DE REMPLISSAGE IA (interdit) : n'utilise jamais ces tournures "
            f"génériques : {exemples}. Écris une phrase directe et concrète à la "
            "place — jamais de généralité qui pourrait s'appliquer à n'importe quel "
            "sujet."
        )
    exemples = ", ".join(f"“{c}”" for c in CLICHES_EN)
    return (
        "AI FILLER CLICHÉS (forbidden): never use these generic phrasings: "
        f"{exemples}. Write a direct, concrete sentence instead — never a "
        "generality that could apply to any topic."
    )
