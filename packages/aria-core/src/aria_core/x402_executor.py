"""Mécanisme générique d'exécution d'un paiement x402 (#202) -- indépendant de la
ressource finale (#199 pas encore tranché). Sert de couche commune quel que soit le
premier service payé (Nansen pay-per-call, x402stock.xyz, CoinGecko premium, ...) --
poser cette infrastructure maintenant plutôt que de la coupler au premier choix.

Décision opérateur explicite (16/07, CLAUDE.md) sur l'autonomie des micropaiements x402 :
pas de clic Telegram par appel (incompatible avec le machine-speed du protocole, ~200ms/
appel) -- modèle "vérifier après" au lieu de "valider avant" : plafond de dépense dur dans
le code (``x402_budget.py``, 5$/semaine), coupe-circuit ``/stop`` dessus, chaque appel
loggé et auditable. Scope STRICTEMENT limité aux micropaiements de données/API (centimes)
-- ne touche PAS et ne redéfinit PAS la règle absolue de validation humaine sur le trading
avec du capital réel (swaps, positions), qui reste sur son propre chemin séparé, inchangé
(``agent_wallet_pilot.py``, ``wallet_guard.py``).

Ordre strict de chaque tentative (fail-closed à chaque étape) :
  1. Requête HTTP à la ressource. Si la réponse n'est PAS 402 -> renvoyée telle quelle,
     RIEN loggé dans ``x402_budget`` (aucun paiement en jeu, dégradation gracieuse).
  2. Si 402 : coupe-circuit ``/stop`` (``outgoing_pause.is_paused(strict=True)``) --
     même doctrine que ``agent_wallet_pilot.py``. Vérifié EN PREMIER, avant même de
     savoir combien coûte la ressource -- c'est la porte la plus large, elle ne dépend
     d'aucune donnée déjà lue.
  3. Corps 402 parsé défensivement (schéma x402 v1, cf. ``services/x402.py``) -- actif
     non-USDC ou montant illisible -> bloqué (le plafond ``x402_budget`` est dénominé en
     dollars, il ne veut rien dire pour un autre actif).
  4. Plafond hebdomadaire (``x402_budget.can_spend(montant)``) -- refuse et logge si le
     montant dépasserait le budget restant de la semaine calendaire.
  5. Solde RÉEL du wallet (``balance_fn`` injecté, même patron que
     ``agent_wallet_pilot.attempt_swap``) -- fail-closed si indisponible ou insuffisant.
  6. Signature + construction du header de paiement (``pay_fn`` injecté -- jamais un
     vrai appel SDK ici ; voir ``x402_cdp_signer.py`` pour l'implémentation réelle CDP).
  7. Nouvelle requête HTTP avec le header ``X-PAYMENT``.
  8. ``x402_budget.record_spend()`` avec le résultat RÉEL (ok/failed/blocked) -- jamais
     seulement les succès, un refus ou un échec doit rester tracé et auditable.

Aucune clé privée ici (même doctrine que tout le dôme) : ``pay_fn``/``balance_fn`` sont
injectés par l'appelant -- l'exécution réelle (signature CDP, lecture de solde) tourne
côté adaptateur dédié, jamais dans ce module. Zéro appel réseau dans la suite de tests
(``http_fetch_fn``/``balance_fn``/``pay_fn`` toujours des fakes en test)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from aria_core import outgoing_pause, x402_budget
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 12.0
X_PAYMENT_HEADER = "X-PAYMENT"
_SUPPORTED_ASSET = "USDC"
_USDC_DECIMALS = 1_000_000  # USDC natif Base -- 6 décimales
# Réseau déclaré par le 402 jamais pris pour argent comptant (même doctrine que le
# slippage forcé, 09/07 -- ne jamais faire confiance à une valeur fournie par un tiers) :
# formes acceptées pour Base mainnet, plate (schéma v1, services/x402.py) ou CAIP-2 (v2).
_ALLOWED_NETWORKS = {"base", "eip155:8453"}


@dataclass(frozen=True)
class HttpResult:
    """Réponse HTTP minimale, découplée d'``httpx`` -- trivialement fakeable en test."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class X402ExecutionResult:
    status: str  # "ok" | "blocked" | "failed"
    reason: str = ""
    amount_usd: float = 0.0
    http_status: int | None = None
    body: bytes = b""


BalanceFn = Callable[[], Awaitable[float | None]]
PayFn = Callable[[dict[str, Any]], Awaitable[str]]
HttpFetchFn = Callable[..., Awaitable[HttpResult]]


async def _default_http_fetch(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None
) -> HttpResult:
    """Implémentation réelle par défaut (httpx). Jamais utilisée dans les tests --
    toujours remplacée par un fake (cf. test_x402_executor.py)."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        r = await client.request(method, url, headers=headers or {})
    return HttpResult(status_code=r.status_code, headers=dict(r.headers), body=r.content)


def _extract_payment_requirement(body: bytes) -> dict[str, Any] | None:
    """Parse défensif du corps 402 (schéma x402 v1 : ``{"accepts": [...]}``, cf.
    ``services/x402.py::payment_required_response``) -- renvoie le premier ``accepts[0]``
    si présent et bien formé, sinon ``None`` (dégradation gracieuse, jamais d'exception)."""
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001 — corps illisible, jamais une exception qui remonte
        return None
    accepts = data.get("accepts") if isinstance(data, dict) else None
    if not isinstance(accepts, list) or not accepts:
        return None
    first = accepts[0]
    return first if isinstance(first, dict) else None


def _amount_to_usd(requirement: dict[str, Any]) -> float | None:
    """Convertit le montant (plus petite unité, ex. 6 décimales USDC) en dollars.
    Fail-closed : actif non-USDC ou montant malformé -> ``None``.

    Bug réel corrigé le 17/07 (jamais exercé contre un vrai facilitator jusqu'ici,
    ``x402_executor.py`` le dit lui-même en tête de fichier -- premier vrai appel
    fait ce soir contre Cybercentry, réglé via le facilitator Coinbase CDP officiel) :
    le schéma x402 v1 RÉEL n'a pas de champ ``amount`` (``maxAmountRequired``,
    string en plus petite unité) et ``asset`` est l'ADRESSE DE CONTRAT du token
    (ex. USDC sur Base = ``0x8335...``), jamais la chaîne littérale ``"USDC"`` --
    avec l'ancien code, CHAQUE appel réel aurait été rejeté par ce garde-fou
    (fail-closed, donc sans risque, mais aussi sans jamais fonctionner). Les deux
    anciennes conventions restent acceptées en repli, au cas où un autre facilitator
    les utilise un jour -- jamais une régression pour un futur appelant."""
    asset = str(requirement.get("asset") or "").strip()
    is_usdc = asset.upper() == _SUPPORTED_ASSET or asset.lower() == USDC_BASE_ADDRESS.lower()
    if not is_usdc:
        return None
    raw = requirement.get("maxAmountRequired", requirement.get("amount"))
    try:
        return float(raw) / _USDC_DECIMALS
    except (TypeError, ValueError):
        return None


async def fetch_paid_resource(
    url: str,
    *,
    resource: str,
    provider: str = "",
    method: str = "GET",
    balance_fn: BalanceFn,
    pay_fn: PayFn,
    http_fetch_fn: HttpFetchFn = _default_http_fetch,
) -> X402ExecutionResult:
    """Tente de récupérer ``url``, payant automatiquement si la ressource répond 402.

    ``resource``/``provider`` identifient l'appel dans le journal ``x402_budget``
    (auditabilité -- jamais un paiement anonyme). ``balance_fn``/``pay_fn`` sont
    injectés par l'appelant : en production, ``x402_cdp_signer.py`` fournit
    l'implémentation réelle (wallet CDP dédié) ; en test, toujours des fakes."""
    try:
        first = await http_fetch_fn(url, method=method, headers=None)
    except Exception as exc:  # noqa: BLE001
        return X402ExecutionResult(status="failed", reason=f"requête initiale échouée : {exc}")

    if first.status_code != 402:
        # Pas de paiement en jeu -- dégradation gracieuse, rien à journaliser.
        return X402ExecutionResult(status="ok", http_status=first.status_code, body=first.body)

    # Coupe-circuit /stop : porte la plus large, vérifiée avant même de savoir combien
    # coûte la ressource -- même doctrine que agent_wallet_pilot.py.
    if outgoing_pause.is_paused(strict=True):
        return await _blocked(
            resource, provider, 0.0,
            reason=outgoing_pause.blocked_notice("Ce paiement x402"),
        )

    requirement = _extract_payment_requirement(first.body)
    if requirement is None:
        return await _blocked(resource, provider, 0.0, reason="corps 402 illisible/mal formé")

    amount_usd = _amount_to_usd(requirement)
    if amount_usd is None:
        return await _blocked(
            resource, provider, 0.0,
            reason=f"actif non supporté ou montant illisible ({requirement.get('asset')!r})",
        )

    # 17/07 -- adresse de règlement du 402, déjà connue ici (aucun appel réseau
    # supplémentaire) : journalisée avec chaque tentative pour qu'agent_wallet_monitor.py
    # puisse corréler un mouvement on-chain détecté à un paiement x402 déjà connu
    # (cf. commentaire x402_budget.py::_ADDED_COLUMNS -- trouvé après un vrai faux
    # positif "SORTIE NON INITIÉE PAR ARIA" sur le tout premier paiement réel).
    pay_to = str(requirement.get("payTo") or "")

    network = str(requirement.get("network") or "").lower()
    if network not in _ALLOWED_NETWORKS:
        return await _blocked(
            resource, provider, amount_usd,
            reason=f"réseau non autorisé ({network!r}) -- jamais signer hors de l'allowlist",
            pay_to=pay_to,
        )

    if not await x402_budget.can_spend(amount_usd):
        return await _blocked(
            resource, provider, amount_usd,
            reason=f"plafond hebdomadaire x402 dépassé ({amount_usd}$ demandé)",
            pay_to=pay_to,
        )

    try:
        balance_usd = await balance_fn()
    except Exception as exc:  # noqa: BLE001
        return await _blocked(
            resource, provider, amount_usd,
            reason=f"solde réel indisponible (fail-closed) : {exc}",
            pay_to=pay_to,
        )
    if balance_usd is None:
        return await _blocked(
            resource, provider, amount_usd,
            reason="solde réel indisponible (fail-closed) : balance_fn a renvoyé None",
            pay_to=pay_to,
        )
    if amount_usd > balance_usd:
        return await _blocked(
            resource, provider, amount_usd,
            reason=f"montant {amount_usd}$ > solde réel {balance_usd}$",
            pay_to=pay_to,
        )

    try:
        payment_header = await pay_fn(requirement)
    except Exception as exc:  # noqa: BLE001
        await x402_budget.record_spend(
            resource=resource, provider=provider, amount_usd=amount_usd,
            status="failed", reason=f"signature échouée : {exc}", pay_to=pay_to,
        )
        return X402ExecutionResult(status="failed", reason=str(exc), amount_usd=amount_usd)

    try:
        paid = await http_fetch_fn(url, method=method, headers={X_PAYMENT_HEADER: payment_header})
    except Exception as exc:  # noqa: BLE001
        await x402_budget.record_spend(
            resource=resource, provider=provider, amount_usd=amount_usd,
            status="failed", reason=f"requête payée échouée : {exc}", pay_to=pay_to,
        )
        return X402ExecutionResult(status="failed", reason=str(exc), amount_usd=amount_usd)

    if paid.status_code == 402:
        await x402_budget.record_spend(
            resource=resource, provider=provider, amount_usd=amount_usd,
            status="failed", reason="toujours 402 après paiement (règlement refusé)", pay_to=pay_to,
        )
        return X402ExecutionResult(
            status="failed", reason="toujours 402 après paiement", amount_usd=amount_usd,
            http_status=402,
        )

    await x402_budget.record_spend(
        resource=resource, provider=provider, amount_usd=amount_usd, status="ok", pay_to=pay_to,
    )
    return X402ExecutionResult(
        status="ok", amount_usd=amount_usd, http_status=paid.status_code, body=paid.body,
    )


async def _blocked(
    resource: str, provider: str, amount_usd: float, *, reason: str, pay_to: str = "",
) -> X402ExecutionResult:
    logger.warning("x402 paiement bloqué (%s) : %s", resource, reason)
    await x402_budget.record_spend(
        resource=resource, provider=provider, amount_usd=amount_usd,
        status="blocked", reason=reason, pay_to=pay_to,
    )
    return X402ExecutionResult(status="blocked", reason=reason, amount_usd=amount_usd)
