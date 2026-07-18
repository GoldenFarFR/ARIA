"""Client de lecture seule RugCheck.xyz -- second avis de sécurité pour les tokens
Solana que GoPlus ne couvre pas encore (#207, 18/07).

Contexte vérifié en direct (curl réel, pas une supposition) : le pipeline momentum
(`momentum_entry._check_honeypot`) rejette par prudence (fail-closed) tout token dont
GoPlus n'a AUCUNE donnée -- comportement délibérément conservé (décision opérateur
explicite, 17/07 : "Sur Solana c'est un marché dangereux ... elle doit se contenter
des tokens safe"). Mais sur 3 tokens Solana fraîchement lancés (pump.fun, découverts
via le même flux DexScreener que le pipeline momentum) : GoPlus `token_security/solana`
répond `{"code":1,"message":"OK","result":null}` sur les 3 -- pas une panne, une
vraie absence de couverture. ARIA rejetait donc ces candidats FAUTE DE DONNÉE, jamais
parce qu'un signal de danger était confirmé.

RugCheck.xyz (API publique, gratuite, AUCUNE clé requise -- vérifié en direct, HTTP
200 sans authentification) a une couverture réelle sur ces mêmes 3 tokens, et sur l'un
d'eux a détecté "Creator history of rugged tokens" (niveau "danger") -- un signal que
GoPlus, avec son scan de contrat pur (honeypot/mint/freeze), ne peut structurellement
pas voir (historique du CRÉATEUR, pas du contrat). Axe COMPLÉMENTAIRE, jamais un
remplacement de GoPlus.

Doctrine (jamais assouplie, appliquée dans `momentum_entry._check_honeypot`) : ce
client est un SECOND AVIS, consulté UNIQUEMENT quand GoPlus n'a explicitement AUCUNE
donnée (`TokenSecurity.no_data=True`), jamais en cas de vraie panne réseau GoPlus (le
fail-closed existant reste inchangé dans ce cas), et jamais sur une autre chaîne que
Solana (GoPlus couvre déjà Base entièrement, doctrine "aussi stricte que sur Base"
inchangée). Un token doit revenir CONFIRMÉ propre par RugCheck pour passer -- si
RugCheck lui-même n'a pas la donnée ou est indisponible, le rejet fail-closed reste
inchangé (ouvre de la couverture, n'assouplit jamais le garde-fou).

Rate limit observé en direct (en-têtes `x-rate-limit-limit`/`-remaining`, 18/07) :
15 requêtes, fenêtre non documentée -- traité prudemment comme 1 minute, throttle
calé sur ~4.5s/requête pour rester large sous ce plafond.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rugcheck.xyz/v1"
UNAVAILABLE = "donnée RugCheck indisponible"

_MIN_INTERVAL_S = 4.5  # ~13/min, sous le plafond observé de 15/min (fenêtre inconnue)
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()


@dataclass
class RugCheckResult:
    """Second avis de sécurité Solana. `rugged`/`danger_risks` restent None/[] si
    RugCheck n'a pas la donnée -- jamais un True/False inventé."""

    address: str
    available: bool = False
    error: str | None = None
    rugged: bool | None = None
    score_normalised: float | None = None
    danger_risks: list[str] = field(default_factory=list)

    @property
    def confirmed_clean(self) -> bool:
        """True seulement si RugCheck a répondu ET n'a trouvé ni `rugged` ni risque
        de niveau "danger" -- jamais déduit d'une absence de donnée (RugCheck
        indisponible => available=False => confirmed_clean=False, fail-closed)."""
        return self.available and self.rugged is False and not self.danger_risks


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        elapsed = time.monotonic() - _last_call_at
        if elapsed < _MIN_INTERVAL_S:
            await asyncio.sleep(_MIN_INTERVAL_S - elapsed)
        _last_call_at = time.monotonic()


async def _get_json(url: str) -> tuple[object | None, str | None]:
    """GET avec retry sur 429/5xx/timeout -- même politique que dexscreener.py."""
    await _throttle()
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("rugcheck: timeout sur %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("rugcheck: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("rugcheck: HTTP %s sur %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Inclut le cas mint inconnu/jamais scanné par RugCheck (vérifié en
            # direct : 400 "invalid length" sur une adresse mal formée -- un mint
            # valide mais jamais indexé retomberait dans cette même branche
            # générique, jamais confondu avec un "clean" confirmé).
            logger.warning("rugcheck: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def get_report_summary(mint: str) -> RugCheckResult:
    """Second avis de sécurité pour un mint Solana -- `/tokens/{mint}/report/summary`
    (endpoint léger, suffisant pour un gate booléen : `rugged` + risques de niveau
    "danger"). Best-effort, jamais bloquant en dehors de son usage explicite dans
    `momentum_entry._check_honeypot`."""
    addr = (mint or "").strip()
    if not addr:
        return RugCheckResult(address=addr, available=False, error="adresse vide")

    data, error = await _get_json(f"{BASE_URL}/tokens/{addr}/report/summary")
    if error is not None:
        return RugCheckResult(address=addr, available=False, error=error)
    if not isinstance(data, dict):
        return RugCheckResult(address=addr, available=False, error=UNAVAILABLE)

    risks = data.get("risks")
    danger_risks = (
        [
            str(r.get("name") or "risque non nommé")
            for r in risks
            if isinstance(r, dict) and str(r.get("level") or "").lower() == "danger"
        ]
        if isinstance(risks, list)
        else []
    )

    rugged = data.get("rugged")
    score = data.get("score_normalised")

    return RugCheckResult(
        address=addr,
        available=True,
        error=None,
        rugged=bool(rugged) if isinstance(rugged, bool) else None,
        score_normalised=float(score) if isinstance(score, (int, float)) else None,
        danger_risks=danger_risks,
    )
