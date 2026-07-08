"""Seam x402 — protocole de paiement agentique sur Base (HTTP 402 Payment Required).

ANTICIPATION (mot d'ordre) : ce module POSE le point d'ancrage pour l'économie agentique
de Base (x402 / paiements USDC onchain) sans rien activer de vivant. Il est **gaté OFF par
défaut** (`ARIA_X402_ENABLED`) et respecte le dôme :

  - **Aucune dépense financière automatique** : le côté « ARIA paie » ne fait QUE construire
    une PROPOSITION marquée `requires_human=True`. Il n'exécute jamais un paiement lui-même
    (validation humaine Telegram/Tangem, comme wallet_guard). Rien à signer ici.
  - **Aucune clé sur le serveur** : ce module ne détient, ne génère et ne lit aucune clé.
  - **Dégradation gracieuse** : désactivé ou mal configuré → retour neutre (`None` / invalide),
    jamais d'exception qui remonte. Fail-closed : dans le doute, on refuse.
  - **Client externe isolé** : le seul appel réseau (vérif d'un règlement via un facilitator)
    est un client `httpx` à timeout, tolérant aux pannes.

Deux directions, deux niveaux de risque :
  1. **ARIA encaisse** (revenu) : `build_payment_requirement` / `payment_required_response`
     construisent la demande de paiement (402) qui garderait une ressource premium. Aucun
     mouvement de fonds d'ARIA. Sûr.
  2. **ARIA paie** (dépense) : `propose_payment` renvoie une proposition à valider par
     l'opérateur. **Jamais d'exécution autonome** (dôme, règle 3).

Câblage vivant (le jour venu, gaté opérateur) : poser `ARIA_X402_ENABLED=1`,
`ARIA_X402_PAY_TO=<adresse Base d'encaissement>`, `ARIA_X402_FACILITATOR_URL=<facilitator>`,
puis brancher `payment_required_response` sur la ressource premium de la vitrine.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

# Réseau et actif par défaut de l'économie agentique Base (USDC natif sur Base).
_DEFAULT_NETWORK = "base"
_DEFAULT_ASSET = "USDC"
_HTTP_TIMEOUT = 12.0


def x402_enabled() -> bool:
    """Seam gaté OFF par défaut. Rien de x402 ne s'active tant que ce flag n'est pas posé."""
    return os.environ.get("ARIA_X402_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _pay_to() -> str:
    """Adresse Base d'encaissement (ARIA reçoit). Jamais une clé, juste une adresse publique."""
    return (os.environ.get("ARIA_X402_PAY_TO", "") or "").strip()


def _facilitator_url() -> str:
    return (os.environ.get("ARIA_X402_FACILITATOR_URL", "") or "").strip().rstrip("/")


@dataclass
class X402PaymentRequirement:
    """Demande de paiement (côté ARIA encaisse) — sert à gater une ressource premium."""
    scheme: str
    network: str
    asset: str
    amount: str            # montant en plus petite unité (string, pour ne pas perdre la précision)
    pay_to: str
    resource: str
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme, "network": self.network, "asset": self.asset,
            "amount": self.amount, "payTo": self.pay_to, "resource": self.resource,
            "description": self.description,
        }


@dataclass
class X402Verification:
    """Résultat de vérification d'un règlement. Fail-closed : par défaut invalide."""
    valid: bool = False
    reason: str = "x402 disabled"
    tx_hash: str | None = None


@dataclass
class X402PaymentProposal:
    """Côté ARIA PAIE — PROPOSITION uniquement. `requires_human` toujours True, jamais exécutée."""
    amount: str
    to: str
    resource: str
    network: str = _DEFAULT_NETWORK
    asset: str = _DEFAULT_ASSET
    requires_human: bool = True
    status: str = "proposed"
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount, "to": self.to, "resource": self.resource,
            "network": self.network, "asset": self.asset,
            "requires_human": True, "status": self.status, "reason": self.reason,
        }


def build_payment_requirement(
    resource: str,
    amount: str,
    *,
    description: str = "",
    scheme: str = "exact",
    network: str = _DEFAULT_NETWORK,
    asset: str = _DEFAULT_ASSET,
) -> X402PaymentRequirement | None:
    """Construit la demande de paiement pour gater `resource`. Fail-closed : renvoie `None` si
    le seam est OFF ou si l'adresse d'encaissement n'est pas configurée."""
    if not x402_enabled():
        return None
    pay_to = _pay_to()
    if not resource or not amount or not pay_to:
        return None
    return X402PaymentRequirement(
        scheme=scheme, network=network, asset=asset, amount=str(amount),
        pay_to=pay_to, resource=resource, description=description or resource,
    )


def payment_required_response(requirement: X402PaymentRequirement | None) -> dict[str, Any] | None:
    """Enveloppe HTTP 402 qu'un futur gateway web renverrait pour exiger le paiement.
    Renvoie `None` (donc pas de gating) si la demande est absente — dégradation gracieuse :
    sans config x402, la ressource n'est simplement pas gatée par ce mécanisme."""
    if requirement is None:
        return None
    return {
        "status": 402,
        "headers": {"X-Payment-Required": "x402"},
        "body": {"x402Version": 1, "accepts": [requirement.as_dict()]},
    }


async def verify_settlement(payload: dict[str, Any]) -> X402Verification:
    """Vérifie un règlement auprès du facilitator (dégradation gracieuse, fail-closed).

    `payload` = preuve de paiement fournie par le client (header X-PAYMENT décodé). On NE fait
    confiance à rien : la validité vient du facilitator, pas du client. Toute panne/timeout →
    invalide (on refuse plutôt que d'accorder l'accès à tort)."""
    if not x402_enabled():
        return X402Verification(valid=False, reason="x402 disabled")
    url = _facilitator_url()
    if not url:
        return X402Verification(valid=False, reason="no facilitator configured")
    if not isinstance(payload, dict) or not payload:
        return X402Verification(valid=False, reason="empty payload")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(f"{url}/verify", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception:
        # Dôme : une panne du facilitator ne casse pas le flux ET n'accorde pas l'accès.
        return X402Verification(valid=False, reason="facilitator unreachable")
    if not isinstance(data, dict):
        return X402Verification(valid=False, reason="bad facilitator response")
    valid = bool(data.get("isValid") or data.get("valid"))
    return X402Verification(
        valid=valid,
        reason=str(data.get("reason") or ("ok" if valid else "not settled")),
        tx_hash=(data.get("txHash") or data.get("transaction") or None),
    )


def propose_payment(*, amount: str, to: str, resource: str, reason: str = "") -> X402PaymentProposal:
    """Côté ARIA PAIE : construit une PROPOSITION à valider par l'opérateur. N'exécute JAMAIS
    un paiement (dôme, règle 3 : aucune exécution financière automatique). Le mouvement de
    fonds, s'il a lieu un jour, passe par la validation humaine et la signature locale."""
    return X402PaymentProposal(
        amount=str(amount), to=(to or "").strip(), resource=resource,
        status="proposed", reason=reason or "requires operator validation",
    )
