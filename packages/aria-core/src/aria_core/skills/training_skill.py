"""Training portfolio skill — read status of the fictional balance/history."""

from __future__ import annotations

import re

from aria_core.training_portfolio import get_balance, read_portfolio_text


def wants_training(message: str) -> bool:
    lower = message.lower()
    return bool(
        re.search(
            r"entraînement|entrainement|training portfolio|portefeuille fictif|"
            r"training_portfolio|signal brief|programme d.entraînement",
            lower,
        )
    )


async def execute_training(message: str, lang: str = "fr") -> tuple[str, dict]:
    text = read_portfolio_text()
    balance = get_balance()

    if lang == "fr":
        header = (
            f"Portefeuille d'entraînement ARIA\n"
            f"Solde fictif : {balance:.2f} $\n"
            f"Fichier : data/memory/training_portfolio.md\n\n"
        )
    else:
        header = (
            f"ARIA training portfolio\n"
            f"Fictional balance: ${balance:.2f}\n"
            f"File: data/memory/training_portfolio.md\n\n"
        )

    if any(w in message.lower() for w in ("analyse", "analyze", "status", "état", "etat", "lis", "read")):
        preview = text[:3200] + ("…" if len(text) > 3200 else "")
        return header + preview, {"balance": balance, "action": "read"}

    if lang == "fr":
        hint = "Commandes utiles : « analyse le portefeuille », « statut entraînement »."
    else:
        hint = "Try: analyze portfolio, training status."

    return header + hint + "\n\n" + text[:1500], {"balance": balance, "action": "summary"}