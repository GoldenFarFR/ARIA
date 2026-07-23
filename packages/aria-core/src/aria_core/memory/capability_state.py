"""ARIA capability state — block computed LIVE from the real environment.

Unlike aria_goals.yaml/aria_values.yaml (hand-written text that must be
manually updated on every change), this module contains NO frozen claim
about what's active or not. It re-reads the real environment variables on
every call and always renders an exact state — eliminates the bug class
observed on 14/07 (aria_values.yaml claimed Tavily was "activatable" while
it had already been active in prod for days, never manually corrected).

Deliberately restricted registry: only "capability"-type variables relevant
to describe to ARIA (never a secret, never plumbing like
CORS_ORIGINS/DEBUG). Adding a capability = one line in _CAPABILITY_GATES,
nothing else to sync elsewhere.
"""
from __future__ import annotations

import os

_CAPABILITY_GATES: tuple[tuple[str, str], ...] = (
    ("ARIA_WEB_FETCH_ENABLED", "Lecture directe d'une page web depuis une URL (admin-only)"),
    ("ARIA_WALLET_SCORING_ENABLED", "Scoring smart-wallet maison (/walletscore, admin-only)"),
    ("ARIA_WALLET_SCAN_QUEUE_ENABLED", "File d'attente de scan wallet en arrière-plan (/walletqueue)"),
    ("ARIA_WALLET_CANDIDATE_SOURCING_ENABLED", "Sourcing automatique de wallets candidats (historique ARIA)"),
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
    # 20/07 -- real gap found while auditing an ARIA self-description that claimed
    # "zero real capital, capital later": this registry mentioned the agent-wallet
    # pilot NOWHERE, even though it's ACTIVE IN PROD (decides AND executes REAL
    # swaps without Telegram validation) since 18/07. Without this line, she has
    # structurally no way of knowing this real capital already exists.
    ("ARIA_AGENT_WALLET_PILOT_ENABLED", "ARGENT RÉEL -- pilote agent-wallet (Coinbase, ~10-15$) : décide ET exécute des swaps réels, sans validation Telegram"),
    ("ARIA_AGENT_WALLET_TRANSFER_ENABLED", "ARGENT RÉEL -- pilote agent-wallet : capacité de transfert USDC vers une adresse unique autorisée"),
    ("ARIA_AGENT_WALLET_MONITOR_ENABLED", "Surveillance du wallet agent réel (dépôts/sorties, lecture seule)"),
)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _is_enabled(env_var: str) -> bool:
    return os.environ.get(env_var, "").strip().lower() in _TRUE_VALUES


def get_capability_state_text() -> str:
    """Markdown block listing the capabilities actually active RIGHT NOW.

    Always recomputed from os.environ — never a hand-written claim, never
    stale. Distinct from aria_values.yaml (which describes WHAT a capability
    does): this block only says WHETHER it's active right now.
    """
    lines = ["# État des capacités ARIA (calculé en direct, jamais périmé)"]
    for env_var, label in _CAPABILITY_GATES:
        state = "ACTIVE" if _is_enabled(env_var) else "inactive"
        lines.append(f"- **{label}** ({env_var}) : {state}")

    provider = os.environ.get("ARIA_WEB_SEARCH_PROVIDER", "").strip().lower() or "ddg"
    lines.append(f"- **Fournisseur de recherche web actuellement actif** : {provider}")

    return "\n".join(lines)
