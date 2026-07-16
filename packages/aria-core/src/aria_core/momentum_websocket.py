"""Sourcing temps réel de candidats momentum via WebSocket DexScreener (#196,
fast-follow de #194). Réduit drastiquement la latence de sourcing par rapport au
polling REST périodique (heartbeat ``paper_trade_cycle``, 15 min) -- objectif
opérateur explicite : « si il y a de l'argent à gagner ARIA doit y être avant
tout le monde ». N'introduit JAMAIS un second chemin de décision : les
candidats détectés ici passent par le MÊME pipeline que #194
(``momentum_entry.evaluate_momentum_entry`` -- honeypot GoPlus, R/R golden
pocket/RSI, confirmation LLM légère) via ``paper_trader.run_paper_cycle``.

Vérifié en direct (16/07, VPS Principal, AVANT d'écrire ce module -- norme
#157 : jamais un schéma supposé non confronté à un vrai appel) :
  - ``wss://api.dexscreener.com/token-boosts/latest/v1`` et
    ``/token-profiles/latest/v1`` acceptent une connexion WebSocket standard
    (librairie ``websockets``, déjà utilisée côté serveur dans
    ``vanguard/backend``, ajoutée ici en dépendance de BASE d'aria-core --
    lecture seule, aucun secret/capital associé, même tier que httpx/requests).
  - Le PREMIER message reçu après connexion est un instantané complet :
    ``{"limit": N, "data": [...]}``, où chaque élément de ``data`` a
    EXACTEMENT la forme attendue par ``services.dexscreener.parse_listing``
    (mêmes clés ``chainId``/``tokenAddress``/``description``/``links`` que
    la réponse REST équivalente) -- réutilisé tel quel, aucun parsing dupliqué.
  - Ensuite, la connexion reste ouverte et envoie des frames de battement de
    coeur ``{"type": "heartbeat"}`` toutes les ~15-30s. **Aucune nouvelle
    donnée observée sur une connexion maintenue ouverte pendant plus de 2
    minutes d'observation continue** -- contrairement à l'hypothèse initiale
    du plan (« connexion maintenue, notifiée à l'instant »), le serveur ne
    semble PAS pousser de mises à jour incrémentales sur une connexion
    longue : il faut RECONNECTER pour obtenir un nouvel instantané. Le
    design ci-dessous en tient compte -- chaque boucle par endpoint se
    reconnecte toutes les ``DRAIN_INTERVAL_SECONDS`` pour tirer un
    instantané frais, plutôt que de garder 4 sockets ouverts en attendant des
    pushes qui n'arrivent pas (observation ponctuelle, pas un contrat d'API
    documenté -- si un futur passage constate de vraies frames incrémentales
    sur une connexion longue, ce module les traiterait déjà correctement :
    chaque frame « data » est diffée contre le set de dédoublonnage, peu
    importe son origine/fréquence).
  - Seuls ``token-boosts/latest`` et ``token-profiles/latest`` vérifiés
    directement ce jour ; ``token-boosts/top``/``token-profiles/recent-updates``
    supposés identiques (même famille d'API, même version ``/v1``) -- à
    reconfirmer si un comportement différent est observé en prod.

Périmètre strictement respecté (16/07, plan validé opérateur) :
  - Uniquement le SOURCING de nouveaux candidats. Ne touche ni au honeypot
    check, ni à la gestion des positions déjà ouvertes (#186/#187), ni au
    comportement par défaut du cycle heartbeat ``paper_trade_cycle`` (appel
    sans arguments -- strictement inchangé).
  - Gate dédié ``ARIA_MOMENTUM_WEBSOCKET_ENABLED``, OFF par défaut, lu UNE
    SEULE FOIS à ``start()`` (même doctrine que le reste du dôme -- basculer
    nécessite un redémarrage, pas un hot-reload).
  - Avant tout déclenchement de ``run_paper_cycle`` : revérifie
    ``ARIA_PAPER_TRADING_ENABLED`` (le système paper-trading lui-même doit
    être actif) ET ``outgoing_pause.is_paused()`` (kill-switch ``/stop`` --
    ce chemin contourne ``heartbeat._tick()``, qui fait normalement cette
    vérification, donc elle doit être refaite ici explicitement).
  - Verrou de concurrence obligatoire (correctif opérateur, relecture du
    plan) : ``paper_trader.run_paper_cycle`` enveloppe déjà TOUT appel dans
    ``paper_trader._run_cycle_lock`` (module partagé) -- jamais deux cycles
    en parallèle, quel que soit l'appelant (heartbeat OU ce service).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from aria_core import outgoing_pause
from aria_core.momentum_entry import DEFAULT_CHAINS, _batch_liquidity_prefilter
from aria_core.services.dexscreener import parse_listing

logger = logging.getLogger(__name__)

WS_BASE_URL = "wss://api.dexscreener.com"
ENDPOINTS: tuple[str, ...] = (
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/token-profiles/latest/v1",
    "/token-profiles/recent-updates/v1",
)

# Décisions opérateur explicites, 16/07 (#196).
DRAIN_INTERVAL_SECONDS = 30       # borne basse de la fourchette proposée -- l'objectif est la vitesse
MAX_CANDIDATES_PER_DRAIN = 20     # même ordre que le plafond déjà accepté pour paper_trade_cycle
DEDUP_TTL_SECONDS = 15 * 60       # 15 minutes
MAX_NEW_PER_DRAIN = 3             # même pacing que le défaut heartbeat (run_paper_cycle max_new) --
                                  # MAX_CANDIDATES_PER_DRAIN borne les candidats ÉVALUÉS, pas le
                                  # nombre de nouvelles positions OUVERTES par vidange (délibérément
                                  # plus prudent qu'un simple len(candidats), pour ne pas dumper plus
                                  # de nouvelles entrées par vidange que le cycle heartbeat n'en
                                  # ouvrirait lui-même en 15 minutes).

_CONNECT_TIMEOUT_SECONDS = 8
_RECV_TIMEOUT_SECONDS = 15
_BACKOFF_INITIAL_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 60.0

_ALLOWED_CHAINS = frozenset(DEFAULT_CHAINS)


def momentum_websocket_enabled() -> bool:
    """Gate dédié, OFF par défaut -- fail-closed, même doctrine que le reste du dôme."""
    return os.environ.get("ARIA_MOMENTUM_WEBSOCKET_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _paper_trading_enabled() -> bool:
    """Revérifié explicitement avant chaque déclenchement -- ce chemin contourne
    ``heartbeat._tick()``, qui fait normalement cette vérification pour
    ``paper_trade_cycle``."""
    return os.environ.get("ARIA_PAPER_TRADING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


class MomentumWebsocketListener:
    """Service de fond (démarré/arrêté par l'hôte -- ``vanguard/backend/app/main.py``,
    même patron que ``aria_heartbeat``) : rafraîchit périodiquement les 4 endpoints
    DexScreener, dédoublonne, et déclenche l'évaluation momentum sur les candidats
    FRAIS via le pipeline existant -- jamais un second chemin de décision."""

    def __init__(self) -> None:
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()  # protège _pending/_seen entre les boucles par endpoint et la vidange
        self._pending: dict[tuple[str, str], float] = {}  # (contract, chain) -> first_seen ts
        self._seen: dict[tuple[str, str], float] = {}      # (contract, chain) -> last_triggered ts (TTL)

    async def start(self) -> None:
        if self._running:
            return
        if not momentum_websocket_enabled():
            logger.info(
                "momentum_websocket: ARIA_MOMENTUM_WEBSOCKET_ENABLED désactivé, service non démarré"
            )
            return
        self._running = True
        for endpoint in ENDPOINTS:
            self._tasks.append(asyncio.create_task(self._endpoint_loop(endpoint)))
        self._tasks.append(asyncio.create_task(self._drain_loop()))
        logger.info("momentum_websocket: démarré (%d endpoints)", len(ENDPOINTS))

    async def stop(self) -> None:
        self._running = False
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _endpoint_loop(self, endpoint: str) -> None:
        """Une connexion courte par cycle (connecte, lit UN instantané, ferme) --
        pas une connexion maintenue en espérant des pushes (cf. docstring module :
        aucune donnée observée au-delà de l'instantané initial + heartbeats).
        Reconnexion avec backoff exponentiel sur erreur, jamais un abandon
        définitif (service persistant, pas un appel ponctuel)."""
        import websockets

        backoff = _BACKOFF_INITIAL_SECONDS
        while self._running:
            try:
                url = f"{WS_BASE_URL}{endpoint}"
                async with websockets.connect(url, open_timeout=_CONNECT_TIMEOUT_SECONDS) as ws:
                    msg = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_SECONDS)
                    await self._ingest_frame(msg)
                backoff = _BACKOFF_INITIAL_SECONDS  # succès -- réinitialise le backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- jamais un crash silencieux de la boucle
                logger.info(
                    "momentum_websocket: %s échoué (%s), retry dans %.1fs", endpoint, exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_SECONDS)
                continue
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)

    async def _ingest_frame(self, raw_msg: str) -> None:
        try:
            payload = json.loads(raw_msg)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("type") == "heartbeat":
            return
        items = payload.get("data")
        if not isinstance(items, list):
            return

        now = time.time()
        async with self._lock:
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                listing = parse_listing(raw)
                contract = listing.token_address.strip().lower()
                chain = listing.chain_id.strip().lower()
                if not contract or not chain or chain not in _ALLOWED_CHAINS:
                    continue
                key = (contract, chain)
                last = self._seen.get(key)
                if last is not None and (now - last) < DEDUP_TTL_SECONDS:
                    continue  # déjà déclenché récemment -- jamais un re-déclenchement en boucle
                self._pending.setdefault(key, now)

    async def _drain_loop(self) -> None:
        while self._running:
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- une vidange qui plante ne tue pas le service
                logger.exception("momentum_websocket: vidange échouée (%s)", exc)

    async def _drain_once(self) -> None:
        async with self._lock:
            if not self._pending:
                return
            batch_keys = list(self._pending.keys())[:MAX_CANDIDATES_PER_DRAIN]
            for key in batch_keys:
                self._pending.pop(key, None)
                self._seen[key] = time.time()

        if not batch_keys:
            return
        if not _paper_trading_enabled():
            logger.info("momentum_websocket: ARIA_PAPER_TRADING_ENABLED désactivé, vidange ignorée")
            return
        if outgoing_pause.is_paused():
            logger.info("momentum_websocket: kill-switch actif, vidange ignorée")
            return

        raw_candidates = [{"contract": c, "chain": ch} for (c, ch) in batch_keys]
        try:
            filtered = await _batch_liquidity_prefilter(raw_candidates)
        except Exception as exc:  # noqa: BLE001 -- le pré-filtre ne doit jamais bloquer la vidange
            logger.info("momentum_websocket: pré-filtre de liquidité échoué (%s)", exc)
            filtered = raw_candidates

        if not filtered:
            return

        from aria_core import paper_trader

        candidates = [c["contract"] for c in filtered]
        chain_by_contract = {c["contract"]: c["chain"] for c in filtered}
        analyzer = paper_trader._default_momentum_analyzer(chain_by_contract)
        try:
            await paper_trader.run_paper_cycle(
                candidates=candidates,
                analyzer=analyzer,
                max_new=MAX_NEW_PER_DRAIN,
                skip_position_management=True,
            )
        except Exception as exc:  # noqa: BLE001 -- une vidange qui plante ne tue pas le service
            logger.exception("momentum_websocket: run_paper_cycle échoué (%s)", exc)


momentum_websocket_listener = MomentumWebsocketListener()
