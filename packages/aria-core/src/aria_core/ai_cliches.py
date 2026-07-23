"""Shared "forbidden words" clause -- generic AI filler cliches.

Distinct from `x_voice.py` (which bans SELF-REFERENCE: "as an AI", "autonomous
agent"). Here: the generic filler vocabulary an LLM produces by default even
without ever referencing itself ("complex process requiring a multidisciplinary
approach", "delve", "tapestry"...). PROMPT text only — never post-generation
cleanup here: on a surface dense with numbers (the /vc report), touching up
generated text would risk altering a figure; the clause must therefore only
guide the generation, never rewrite the output.

Additive: meant to be appended to an existing system block, never to replace it.
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
    """Additive FR/EN clause — forbidden generic filler words/phrasings."""
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
