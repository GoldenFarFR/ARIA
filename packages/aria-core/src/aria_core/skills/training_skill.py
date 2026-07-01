"""Training portfolio skill — read status, log simulated business actions."""

from __future__ import annotations

import re

from aria_core.training_portfolio import append_entry, get_balance, read_portfolio_text


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
        hint = (
            "Commandes utiles : « analyse le portefeuille », « statut entraînement », "
            "ou décris une action (vente simulée, coût, leçon) pour que je la journalise."
        )
    else:
        hint = "Try: analyze portfolio, training status, or describe a simulated sale/cost to log."

    return header + hint + "\n\n" + text[:1500], {"balance": balance, "action": "summary"}


def log_simulated_action(
    title: str,
    reasoning: str,
    action: str,
    result: str,
    lesson: str,
    *,
    revenue: float = 0.0,
    cost: float = 0.0,
) -> float:
    new_balance = get_balance() + revenue - cost
    append_entry(
        title=title,
        reasoning=reasoning,
        action=action,
        result=result + f"\n- Revenus fictifs : +{revenue:.2f} $\n- Coûts fictifs : -{cost:.2f} $",
        lesson=lesson,
        balance=new_balance,
    )
    return new_balance