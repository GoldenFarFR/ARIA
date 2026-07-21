"""Client de lecture seule GoPlus Security (Token Security API) — détection honeypot.

Complète le scan ABI « statique » de Blockscout (quelles fonctions EXISTENT) par une
lecture de COMPORTEMENT dynamique que l'ABI seule ne révèle pas : le token est-il un
honeypot (revente bloquée), quelles sont les taxes d'achat/vente RÉELLES, l'owner est-il
caché, peut-il reprendre la propriété, les transferts sont-ils suspendables, etc.

API GoPlus, chain Base = 8453. Lecture seule, aucun appel autre que GET/POST-token.
Authentification OPTIONNELLE (#207, 18/07) : si `GOPLUS_APP_KEY`/`GOPLUS_APP_SECRET`
sont présentes dans l'environnement, un access_token (JWT, valide 2h, renouvelé
automatiquement) est joint en en-tête `Authorization: Bearer <token>` sur chaque appel
(corrigé le 21/07 -- l'ancien en-tête `access-token` n'était pas reconnu, cf. commentaire
dans `_get_json`) -- sépare le quota d'ARIA de la limite anonyme par IP (~30 req/min,
cause directe des `code 4029` observés le 17-18/07). Sans ces identifiants, comportement
historique inchangé (API publique sans clé). Même politique d'erreurs que blockscout.py
(dôme) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / 5xx : 1 retry après 5s, puis fallback explicite.
- Donnée manquante JAMAIS remplacée par une supposition : `available=False` + `error`,
  et chaque drapeau vaut None (inconnu) plutôt que False quand GoPlus ne répond pas.
- Échec d'authentification (token, réseau) : jamais bloquant, repli silencieux sur
  l'appel sans en-tête (même comportement que si aucune clé n'était configurée).
- Coupe-circuit réactif (21/07, filet de sécurité quota) : au-delà de
  `_CIRCUIT_FAIL_THRESHOLD` échecs consécutifs (429/code 4029/timeout/5xx), le client
  arrête d'appeler le réseau pendant `_CIRCUIT_COOLDOWN_S` -- protège tout plafond
  caché (mensuel/journalier, jamais confirmé par GoPlus, donc jamais chiffré en dur
  ici) sans inventer de nombre. Purement réactif, même patron que le coupe-circuit
  par fournisseur déjà construit sur la cascade OHLCV (`momentum_entry._fetch_candles`).

Un drapeau None (inconnu) ne bloque jamais à lui seul : seul un signal POSITIVEMENT
confirmé (honeypot=1, cannot_sell=1, taxe élevée…) pénalise. Cohérent avec la doctrine :
une panne réseau ne bannit pas un bon token.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.gopluslabs.io/api/v1"
BASE_CHAIN_ID = "8453"  # Base mainnet

UNAVAILABLE = "donnée GoPlus indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3
_TOKEN_REFRESH_MARGIN_S = 300  # renouvelle 5 min avant l'expiration annoncée par GoPlus

# 21/07 -- coupe-circuit réactif (filet de sécurité quota). Aucun plafond mensuel/
# journalier GoPlus n'a jamais été confirmé (seul le débit 150 CU/min est vérifié au
# dashboard) -- pas de chiffre inventé ici, seulement une pause défensive une fois
# qu'un échec SOUTENU est observé, quelle qu'en soit la cause réelle (rate limit,
# quota mensuel épuisé, panne côté GoPlus). 5 échecs consécutifs (au-delà du seuil de
# simple log à 3) avant d'arrêter d'appeler le réseau pendant 5 minutes, plutôt que de
# marteler un compte à sec candidat après candidat.
_CIRCUIT_FAIL_THRESHOLD = 5
_CIRCUIT_COOLDOWN_S = 300.0


def goplus_authenticated() -> bool:
    """True si les identifiants app_key/app_secret sont configurés dans l'environnement."""
    return bool(os.environ.get("GOPLUS_APP_KEY", "").strip() and os.environ.get("GOPLUS_APP_SECRET", "").strip())


@dataclass
class TokenSecurity:
    """Lecture de sécurité dynamique d'un token (GoPlus). Chaque drapeau : True (confirmé),
    False (confirmé absent), ou None (inconnu / GoPlus n'a pas la donnée)."""

    address: str
    # Le plus important : la revente est-elle possible ?
    is_honeypot: bool | None = None
    cannot_sell_all: bool | None = None
    cannot_buy: bool | None = None
    # Taxes réelles (fraction : 0.05 = 5 %). None si inconnu.
    buy_tax: float | None = None
    sell_tax: float | None = None
    # Pouvoirs cachés du dev.
    hidden_owner: bool | None = None
    can_take_back_ownership: bool | None = None
    owner_change_balance: bool | None = None
    transfer_pausable: bool | None = None
    trading_cooldown: bool | None = None
    slippage_modifiable: bool | None = None
    is_blacklisted: bool | None = None       # le contrat PEUT blacklister
    is_mintable: bool | None = None
    is_open_source: bool | None = None       # 0 = code non vérifié
    is_proxy: bool | None = None
    available: bool = False
    error: str | None = None
    # #207, 18/07 : True UNIQUEMENT quand GoPlus a répondu proprement (pas de panne
    # réseau/HTTP) mais n'a AUCUNE donnée pour ce contrat (`result` vide/null --
    # fréquent sur Solana pour un token tout juste lancé, vérifié en direct). Distinct
    # d'une vraie panne (timeout, 5xx, rate limit) -- seul ce cas précis autorise un
    # second avis (services/rugcheck.py) dans momentum_entry._check_honeypot.
    no_data: bool = False


@dataclass
class AddressSecurity:
    """Lecture GoPlus Malicious Address API (AML) -- #157. ``flags`` ne contient
    QUE les catégories POSITIVEMENT confirmées (True) ; une absence de clé =
    catégorie non signalée, jamais reconstruite comme False (cohérent avec
    l'esprit de ``_tri`` : silence ≠ confirmation d'innocence)."""

    address: str
    flags: dict[str, bool] = field(default_factory=dict)
    is_malicious: bool = False  # True si AU MOINS UNE catégorie positivement confirmée
    available: bool = False
    error: str | None = None


# Champs de la réponse `address_security` qui ne sont PAS des catégories de
# risque (métadonnées) -- exclus du calcul de `is_malicious`.
_ADDRESS_SECURITY_META_FIELDS = {"contract_address", "data_source", "number_of_malicious_contracts_created"}


def _tri(value: object) -> bool | None:
    """"1" -> True, "0" -> False, "" / None / autre -> None (inconnu)."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "1":
        return True
    if s == "0":
        return False
    return None


def _tax(value: object) -> float | None:
    """Convertit une taxe GoPlus ("0.05") en fraction float, ou None si illisible/absente."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class GoPlusClient:
    """Client HTTP async, lecture seule, throttle modéré (API publique sans clé)."""

    # 21/07 -- CORRECTION d'un premier calibrage erroné fait le même jour (1.212s,
    # basé sur un test empirique en rafale mal interprété -- le "blocage à la 11e
    # requête" observé n'était pas un plafond ambigu à ~55/min, c'est EXACTEMENT
    # 150 CU / 15 CU-par-token = 10 requêtes, confirmé une fois la vraie structure
    # de facturation connue). Root cause : GoPlus facture PAR TOKEN VÉRIFIÉ (15 CU
    # pour Token Security API sur EVM, 30 CU pour Solana), pas par appel HTTP --
    # `get_token_security()` ci-dessous interroge TOUJOURS un seul contrat par
    # appel, donc 1 appel = 15 CU sur Base. Vraie limite du compte CONFIRMÉE en
    # DIRECT sur le dashboard GoPlus réel (gopluslabs.io/dashboard, palier Free,
    # "Rate Limit: 150 CU/Min") -- source la plus fiable possible, au-dessus même
    # d'un test empirique : 150 CU/min / 15 CU/token = **10 req/min réelles**.
    # Doctrine CLAUDE.md "Débit calibré à 90%" : 90% de 10/min = 9/min = 6.667s.
    # Si un jour ce client interroge Solana (30 CU/token) sans passer par
    # `_check_honeypot_rugcheck_fallback`, la vraie limite tomberait à 5 req/min --
    # non géré ici, ce client ne fait actuellement que des appels 1-token/EVM.
    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 6.667) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def _ensure_access_token(self) -> str | None:
        """Renouvelle l'access_token si absent/proche expiration. Retourne None sans
        identifiants configurés (chemin public inchangé) ou en cas d'échec réseau --
        jamais bloquant, jamais une exception propagée vers l'appelant."""
        app_key = os.environ.get("GOPLUS_APP_KEY", "").strip()
        app_secret = os.environ.get("GOPLUS_APP_SECRET", "").strip()
        if not app_key or not app_secret:
            return None

        async with self._token_lock:
            now = time.time()
            if self._access_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN_S:
                return self._access_token

            t = int(now)
            sign = hashlib.sha1(f"{app_key}{t}{app_secret}".encode()).hexdigest()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        f"{self.base_url}/token",
                        data={"app_key": app_key, "time": t, "sign": sign},
                    )
                body = response.json()
            except Exception as exc:  # réseau, timeout, JSON invalide -- jamais bloquant
                logger.warning("goplus: echec renouvellement access_token (%s) — repli sur l'API publique", exc)
                return self._access_token

            result = body.get("result") if isinstance(body, dict) else None
            token = result.get("access_token") if isinstance(result, dict) else None
            expires_in = result.get("expires_in") if isinstance(result, dict) else None
            if not token:
                logger.warning("goplus: reponse /token sans access_token — repli sur l'API publique")
                return self._access_token

            self._access_token = token
            self._token_expires_at = now + float(expires_in or 0)
            logger.info("goplus: access_token renouvele (expire dans %ss)", expires_in)
            return self._access_token

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _CIRCUIT_FAIL_THRESHOLD:
            self._circuit_open_until = time.time() + _CIRCUIT_COOLDOWN_S
            logger.warning(
                "goplus: coupe-circuit ouvert apres %s echecs consecutifs (dernier: %s) — "
                "pause %ss avant nouvel essai",
                self._consecutive_failures,
                detail,
                _CIRCUIT_COOLDOWN_S,
            )
        elif self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "goplus: %s echecs consecutifs (dernier: %s) — pas encore de coupe-circuit",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "goplus: echec appel (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    def circuit_open(self) -> bool:
        return time.time() < self._circuit_open_until

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
        """GET avec la politique d'erreurs du dôme. Retourne (data, error)."""
        if self.circuit_open():
            return None, f"{UNAVAILABLE} (coupe-circuit ouvert, échecs consécutifs récents)"

        url = f"{self.base_url}{path}"
        attempt_429 = 0
        retried = False
        # 21/07 -- bug réel trouvé en investiguant pourquoi le compte GoPlus n'affichait
        # AUCUNE consommation même sur 30 jours malgré des appels authentifiés réussis :
        # mauvais nom d'en-tête. La doc officielle (docs.gopluslabs.io/reference/
        # tokensecurityusingget_1) exige "Authorization: Bearer <token>", jamais
        # "access-token: <token>" -- l'ancien en-tête n'était simplement pas reconnu.
        # L'endpoint reste tolérant (renvoie 200 même sans jeton valide, testé en
        # direct), ce qui a masqué le bug pendant tout ce temps : les appels
        # "réussissaient" mais n'étaient jamais rattachés au compte authentifié.
        token = await self._ensure_access_token()
        headers = {"Authorization": f"Bearer {token}"} if token else None

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} (timeout GoPlus)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    self._record_failure(f"{url} -> HTTP 429 apres {attempt_429} tentatives")
                    return None, f"{UNAVAILABLE} (rate limit GoPlus)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            # 17/07 -- bug réel trouvé en investiguant le faible débit d'achats du test 1M$ :
            # GoPlus signale son rate-limit via un HTTP 200 avec {"code":4029,"message":"too
            # many requests"} dans le corps, PAS un vrai HTTP 429 -- la branche ci-dessus ne se
            # déclenchait donc jamais pour ce cas précis, confirmé par appel réel (20 candidats
            # d'affilée : les 9 premiers OK, les 11 suivants code=4029). Sans retry, chaque
            # candidat touché tombait silencieusement en "aucune donnée pour ce contrat" (faux
            # négatif de couverture, pas un vrai verdict de sécurité) -- même politique de
            # backoff que le vrai 429, sur le même compteur `attempt_429`.
            if response.status_code == 200:
                try:
                    probe = response.json()
                except ValueError:
                    probe = None
                if isinstance(probe, dict) and probe.get("code") == 4029:
                    attempt_429 += 1
                    if attempt_429 >= 3:
                        self._record_failure(f"{url} -> code 4029 apres {attempt_429} tentatives")
                        return None, f"{UNAVAILABLE} (rate limit GoPlus)"
                    await asyncio.sleep(0.5 * (2**attempt_429))
                    continue

            if response.status_code >= 500:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> HTTP {response.status_code}")
                return None, f"{UNAVAILABLE} (erreur serveur GoPlus)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_token_security(
        self, address: str, *, chain_id: str = BASE_CHAIN_ID
    ) -> TokenSecurity:
        """Interroge GoPlus Token Security pour un contrat. Best-effort, jamais bloquant."""
        addr = (address or "").strip()
        if not addr:
            return TokenSecurity(address=addr, available=False, error="adresse vide")

        data, error = await self._get_json(
            f"/token_security/{chain_id}", params={"contract_addresses": addr}
        )
        if error is not None:
            return TokenSecurity(address=addr, available=False, error=error)
        if not isinstance(data, dict):
            return TokenSecurity(address=addr, available=False, error=UNAVAILABLE)

        # GoPlus : {"code":1,"message":"OK","result":{"<addr_lower>":{...}}}
        result = data.get("result")
        if not isinstance(result, dict) or not result:
            # code != 1 ou résultat vide = GoPlus n'a pas (encore) la donnée pour ce
            # token -- réponse HTTP propre, pas une panne (no_data=True, #207).
            msg = str(data.get("message") or "").strip()
            return TokenSecurity(
                address=addr,
                available=False,
                no_data=True,
                error=f"{UNAVAILABLE} (aucune donnée pour ce contrat{': ' + msg if msg else ''})",
            )

        row = result.get(addr.lower())
        if not isinstance(row, dict):
            # Clé insensible à la casse : prend la première entrée si l'adresse exacte manque.
            row = next((v for v in result.values() if isinstance(v, dict)), None)
        if not isinstance(row, dict):
            return TokenSecurity(address=addr, available=False, error=UNAVAILABLE)

        return TokenSecurity(
            address=addr,
            is_honeypot=_tri(row.get("is_honeypot")),
            cannot_sell_all=_tri(row.get("cannot_sell_all")),
            cannot_buy=_tri(row.get("cannot_buy")),
            buy_tax=_tax(row.get("buy_tax")),
            sell_tax=_tax(row.get("sell_tax")),
            hidden_owner=_tri(row.get("hidden_owner")),
            can_take_back_ownership=_tri(row.get("can_take_back_ownership")),
            owner_change_balance=_tri(row.get("owner_change_balance")),
            transfer_pausable=_tri(row.get("transfer_pausable")),
            trading_cooldown=_tri(row.get("trading_cooldown")),
            slippage_modifiable=_tri(row.get("slippage_modifiable")),
            is_blacklisted=_tri(row.get("is_blacklisted")),
            is_mintable=_tri(row.get("is_mintable")),
            is_open_source=_tri(row.get("is_open_source")),
            is_proxy=_tri(row.get("is_proxy")),
            available=True,
            error=None,
        )


    # ------------------------------------------------------------------
    # 2. Adresse malveillante connue (AML) -- #157, couche 1 disqualifiante de
    # l'évaluateur wallet-centrique. Second endpoint du même fournisseur déjà
    # intégré ci-dessus (aucune nouvelle dépendance/diligence éditeur).
    # ------------------------------------------------------------------
    async def get_address_security(self, address: str, *, chain_id: str = BASE_CHAIN_ID) -> "AddressSecurity":
        """Interroge GoPlus Malicious Address API (AML). Vérifié en direct sur Base
        (docs/aria-learning-inbox/2026-07-14-veille-registre-wallets-malveillants-157.md,
        14/07), puis ÉTENDU ce même soir aux 13 chain_id du scan multi-chaînes
        (base, ethereum, arbitrum, optimism, polygon, celo, gnosis, scroll,
        zksync, rootstock, unichain, soneium, mode) : les 13 répondent
        `code: 1, "ok"` avec le MÊME format -- couverture format confirmée
        partout SANS clé d'autorisation. PAS la densité réelle des données
        malveillantes (le test en direct portait sur une adresse burn, pas une
        adresse effectivement flaggée) -- et probablement variable par chaîne :
        le champ `contract_address` (résolution "est-ce un contrat ?") revient
        indéterminé (`"-1"`) sur celo/rootstock/unichain/soneium/mode pour la
        même adresse burn, alors qu'il se résout sur les 8 autres chaînes --
        signal indirect qu'une couverture plus fine existe pour certaines
        chaînes. Traiter comme un filtre probabiliste supplémentaire, jamais
        présenté comme exhaustif, quelle que soit la chaîne -- même doctrine
        que le reste du dôme : une indisponibilité ne vaut jamais "non
        malveillant", elle reste indisponible."""
        addr = (address or "").strip()
        if not addr:
            return AddressSecurity(address=addr, available=False, error="adresse vide")

        data, error = await self._get_json(f"/address_security/{addr}", params={"chain_id": chain_id})
        if error is not None:
            return AddressSecurity(address=addr, available=False, error=error)
        if not isinstance(data, dict):
            return AddressSecurity(address=addr, available=False, error=UNAVAILABLE)

        if data.get("code") != 1:
            msg = str(data.get("message") or "").strip()
            return AddressSecurity(
                address=addr, available=False, error=f"{UNAVAILABLE} ({msg or 'code GoPlus != 1'})",
            )

        result = data.get("result")
        if not isinstance(result, dict):
            return AddressSecurity(address=addr, available=False, error=UNAVAILABLE)

        flags = {
            key: True
            for key, raw in result.items()
            if key not in _ADDRESS_SECURITY_META_FIELDS and _tri(raw) is True
        }
        return AddressSecurity(address=addr, flags=flags, is_malicious=bool(flags), available=True, error=None)


goplus_client = GoPlusClient()
