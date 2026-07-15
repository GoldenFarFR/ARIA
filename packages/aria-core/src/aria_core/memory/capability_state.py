"""État des capacités ARIA — bloc calculé EN DIRECT depuis l'environnement réel.

Contrairement à aria_goals.yaml/aria_values.yaml (texte écrit à la main, doit être
mis à jour manuellement à chaque changement), ce module ne contient AUCUNE
affirmation figée sur ce qui est actif ou non. Il relit les vraies variables
d'environnement à chaque appel et rend un état toujours exact — élimine la classe
de bug observée le 14/07 (aria_values.yaml affirmait Tavily "activable" alors
qu'il était déjà actif en prod depuis des jours, jamais corrigé à la main).

Registre volontairement restreint : seules les variables de type "capacité"
pertinentes à décrire à ARIA (jamais un secret, jamais de la plomberie type
CORS_ORIGINS/DEBUG). Ajouter une capacité = une ligne dans _CAPABILITY_GATES,
rien d'autre à synchroniser ailleurs.
"""
from __future__ import annotations

import os

_CAPABILITY_GATES: tuple[tuple[str, str], ...] = (
    ("ARIA_WEB_FETCH_ENABLED", "Lecture directe d'une page web depuis une URL (admin-only)"),
    ("ARIA_WALLET_SCORING_ENABLED", "Scoring smart-wallet maison (/walletscore, admin-only)"),
    ("ARIA_WALLET_SCAN_QUEUE_ENABLED", "File d'attente de scan wallet en arrière-plan (/walletqueue)"),
    ("ARIA_VISION_ENABLED", "Lecture d'image envoyée en chat (admin-only)"),
    ("ARIA_SEPOLIA_WALLET_ENABLED", "Rehearsal Sepolia — wallet testnet"),
    ("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", "Rehearsal Sepolia — décision+exécution autonome (testnet)"),
    ("ARIA_BONDING_DISCOVERY_ENABLED", "Découverte tokens en phase de bonding (Virtuals)"),
    ("ARIA_PAPER_TRADING_ENABLED", "Paper-trading 1M$ (portefeuille simulé)"),
    ("ARIA_MARKET_SENTIMENT_ENABLED", "Sentiment de marché continu (BTC/ETH)"),
    ("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "Alertes proactives haute-conviction"),
    ("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "Autopsie pump/dump automatique"),
    ("ARIA_KNOWLEDGE_INBOX_ENABLED", "Boîte de dépôt de connaissance"),
    ("ARIA_CLAUDE_MENTOR_ENABLED", "Revue de performance par Claude"),
    ("ARIA_RELAY_AUTOREPLY_ENABLED", "Réponse autonome sur le canal relay Claude Code"),
    ("ARIA_EXAM_ENABLED", "Exam pédagogique"),
    ("X_CURIOSITY_ENABLED", "Lecture de X (curiosité/radar)"),
)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _is_enabled(env_var: str) -> bool:
    return os.environ.get(env_var, "").strip().lower() in _TRUE_VALUES


def get_capability_state_text() -> str:
    """Bloc markdown listant les capacités réellement actives MAINTENANT.

    Toujours recalculé depuis os.environ — jamais une affirmation écrite à la
    main, jamais périmée. Distinct de aria_values.yaml (qui décrit CE QUE fait
    une capacité) : ce bloc dit uniquement SI elle est active en ce moment.
    """
    lines = ["# État des capacités ARIA (calculé en direct, jamais périmé)"]
    for env_var, label in _CAPABILITY_GATES:
        state = "ACTIVE" if _is_enabled(env_var) else "inactive"
        lines.append(f"- **{label}** ({env_var}) : {state}")

    provider = os.environ.get("ARIA_WEB_SEARCH_PROVIDER", "").strip().lower() or "ddg"
    lines.append(f"- **Fournisseur de recherche web actuellement actif** : {provider}")

    return "\n".join(lines)
