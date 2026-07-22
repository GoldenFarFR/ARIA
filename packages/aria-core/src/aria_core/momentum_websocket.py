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
import collections
import json
import logging
import os
import time

from aria_core import outgoing_pause
from aria_core.momentum_entry import (
    DEFAULT_CHAINS,
    _batch_liquidity_prefilter,
    normalize_contract_case,
)
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
DEDUP_TTL_SECONDS = 15 * 60       # 15 minutes -- anti-spam de frames rapprochées sur
                                  # un même candidat, PAS le cooldown de rescan (cf.
                                  # RESCAN_COOLDOWN_SECONDS ci-dessous, 22/07).

# 22/07 -- décision opérateur explicite : "un contrat n'a pas besoin d'être scanné
# toutes les 60 secondes, toutes les 4h suffit" -- ADAPTATIF, pas rigide (précisé par
# l'opérateur : "si c'est un token sans signal ou avec signal ça doit s'adapter") :
# un candidat déjà vu dans les 4h ne redéclenche PAS une évaluation complète, SAUF si
# son prix a bougé de plus de RESCAN_PRICE_MOVE_THRESHOLD_PCT depuis le dernier passage
# -- un vrai mouvement de prix peut annoncer un nouveau setup qui mérite d'être regardé
# tout de suite, pas dans 4h. Le prix de comparaison vient de _batch_liquidity_
# prefilter (déjà appelé pour tout candidat frais, DexScreener par lot -- AUCUN appel
# réseau supplémentaire dédié à ce mécanisme), jamais un nouvel appel juste pour ça.
RESCAN_COOLDOWN_SECONDS = 4 * 3600  # 4h
RESCAN_PRICE_MOVE_THRESHOLD_PCT = 0.10  # 10% -- valeur de départ proposée, ajustable
MAX_NEW_PER_DRAIN = 3             # même pacing que le défaut heartbeat (run_paper_cycle max_new) --
                                  # MAX_CANDIDATES_PER_DRAIN borne les candidats ÉVALUÉS, pas le
                                  # nombre de nouvelles positions OUVERTES par vidange (délibérément
                                  # plus prudent qu'un simple len(candidats), pour ne pas dumper plus
                                  # de nouvelles entrées par vidange que le cycle heartbeat n'en
                                  # ouvrirait lui-même en 15 minutes).

# 19/07 -- plafond de débit ajouté AVANT activation (question opérateur légitime : "ça ne
# va pas casser les rouages des API ?"). Sans lui, le pire cas théorique est
# MAX_CANDIDATES_PER_DRAIN (20) toutes les DRAIN_INTERVAL_SECONDS (30s) = jusqu'à
# ~2400 candidats évalués/heure -- un facteur ~30x le débit du cycle heartbeat classique
# (20 candidats x 4 cycles/heure = 80/heure). GeckoTerminal/GoPlus ont un throttle CLIENT
# partagé (protège contre un vrai 429 -- les appels sont sérialisés, pas parallélisés),
# mais CoinMarketCap n'a AUCUN throttle client, et aucun des trois n'a de plafond de
# QUOTA horaire/journalier codé quelque part : un débit soutenu pourrait épuiser un
# quota payant mensuel en quelques jours sans jamais déclencher un seul 429 individuel
# qui alerterait quelqu'un. Ramène le débit WebSocket au MÊME ORDRE DE GRANDEUR que le
# régime actuel (80/heure) -- garde l'avantage de LATENCE (détection quasi-immédiate)
# sans exploser le VOLUME total consommé par les API en aval.
MAX_EVALUATIONS_PER_HOUR = 80

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
        # 22/07 -- (last_drained_ts, last_known_price_usd|None) : le prix sert au
        # cooldown adaptatif (RESCAN_COOLDOWN_SECONDS), jamais confondu avec le TTL
        # anti-spam (DEDUP_TTL_SECONDS) qui bloque, lui, sans condition de prix.
        self._seen: dict[tuple[str, str], tuple[float, float | None]] = {}
        # 19/07 -- fenêtre glissante 1h pour MAX_EVALUATIONS_PER_HOUR (un timestamp par
        # candidat réellement évalué, pas par vidange -- une vidange à 20 candidats compte
        # pour 20, pas pour 1).
        self._evaluation_timestamps: collections.deque[float] = collections.deque()

    def _evaluation_budget_remaining(self, now: float) -> int:
        cutoff = now - 3600.0
        while self._evaluation_timestamps and self._evaluation_timestamps[0] < cutoff:
            self._evaluation_timestamps.popleft()
        return max(0, MAX_EVALUATIONS_PER_HOUR - len(self._evaluation_timestamps))

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
                chain = listing.chain_id.strip().lower()
                # 19/07 -- bug réel trouvé en activant ce chemin pour la première fois
                # (jamais exercé jusqu'ici) : un .lower() aveugle corrompait toute adresse
                # Solana (base58, sensible à la casse -- contrairement à Base/Robinhood en
                # hex EVM). Même bug déjà corrigé le 18/07 côté REST
                # (momentum_entry.normalize_contract_case), jamais reporté ici -- ce module
                # avait été écrit AVANT cette découverte. Symptôme observé en prod : RugCheck
                # (repli honeypot Solana, #207) rejetait en 400 "invalid length" des adresses
                # dont la couverture réelle n'a jamais été vérifiée avec la bonne casse.
                contract = normalize_contract_case(listing.token_address.strip(), chain)
                if not contract or not chain or chain not in _ALLOWED_CHAINS:
                    continue
                # 22/07 -- même filtre que discover_momentum_candidates (momentum_entry.
                # _add_candidate) : WETH/stablecoins ne sont jamais des candidats
                # spéculatifs légitimes, et déclenchaient un repli x402 payant en boucle
                # sur le check holder_concentration (cf. commentaire détaillé côté
                # momentum_entry.py -- ce chemin WebSocket a son PROPRE ajout de candidat,
                # jamais couvert par le filtre côté heartbeat classique).
                from aria_core.momentum_entry import reference_tokens_excluded

                if contract.lower() in reference_tokens_excluded(chain):
                    continue
                key = (contract, chain)
                last = self._seen.get(key)
                if last is not None and (now - last[0]) < DEDUP_TTL_SECONDS:
                    continue  # déjà déclenché récemment -- jamais un re-déclenchement en boucle
                # 22/07 -- au-delà du TTL anti-spam (15min), le candidat rejoint quand
                # même _pending -- le VRAI cooldown adaptatif (4h sauf mouvement de
                # prix) se décide dans _drain_once, où le prix est disponible sans
                # coût réseau dédié (cf. RESCAN_COOLDOWN_SECONDS).
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
            # 22/07 -- capture l'ANCIEN (timestamp, prix) AVANT de l'écraser -- c'est
            # la référence du cooldown adaptatif ci-dessous. La mise à jour de _seen
            # elle-même est différée après le prefilter (où le prix frais devient
            # disponible), pour toujours écrire le prix le plus à jour connu.
            previous_seen = {key: self._seen.get(key) for key in batch_keys}
            for key in batch_keys:
                self._pending.pop(key, None)

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

        # 22/07 -- met à jour _seen pour TOUT le batch (peu importe qui survit au
        # cooldown ci-dessous) : un candidat qu'on vient de regarder, même rejeté,
        # ne doit pas redéclencher une vérification avant le prochain cooldown réel.
        now_ts = time.time()
        price_by_key: dict[tuple[str, str], float | None] = {}
        for c in filtered:
            key = (c["contract"], c["chain"])
            price_by_key[key] = c.get("price_usd")
        for key in batch_keys:
            # Prix inconnu à CE passage (prefilter sans donnée) -- conserve l'ancien
            # prix de référence plutôt que de le perdre (jamais une régression
            # d'information sous prétexte d'une panne ponctuelle du prefilter).
            price = price_by_key.get(key)
            if price is None:
                old = previous_seen.get(key)
                price = old[1] if old is not None else None
            self._seen[key] = (now_ts, price)

        # 22/07 -- cooldown adaptatif (RESCAN_COOLDOWN_SECONDS, 4h) : un candidat déjà
        # vu récemment (au-delà du TTL anti-spam, sous le cooldown complet) ne
        # redéclenche PAS d'évaluation, SAUF si son prix a bougé de plus de
        # RESCAN_PRICE_MOVE_THRESHOLD_PCT depuis le dernier passage. Fail-open sur
        # donnée manquante (prix ancien ou nouveau inconnu) -- ne bloque jamais sur
        # une incertitude, seulement sur une comparaison réellement possible.
        def _still_in_cooldown(c: dict) -> bool:
            key = (c["contract"], c["chain"])
            old = previous_seen.get(key)
            if old is None:
                return False  # jamais vu -- pas de cooldown possible
            old_ts, old_price = old
            if (now_ts - old_ts) >= RESCAN_COOLDOWN_SECONDS:
                return False  # cooldown complet écoulé
            new_price = price_by_key.get(key)
            if old_price is None or new_price is None or old_price <= 0:
                return False  # comparaison impossible -- fail-open, jamais bloquant
            move_pct = abs(new_price - old_price) / old_price
            return move_pct < RESCAN_PRICE_MOVE_THRESHOLD_PCT

        before_cooldown_count = len(filtered)
        filtered = [c for c in filtered if not _still_in_cooldown(c)]
        if len(filtered) < before_cooldown_count:
            logger.info(
                "momentum_websocket: %d candidat(s) en cooldown adaptatif (déjà vus, "
                "prix stable) -- vidange réduite à %d",
                before_cooldown_count - len(filtered), len(filtered),
            )

        if not filtered:
            return

        from aria_core import paper_trader

        candidates = [c["contract"] for c in filtered]

        # 19/07 -- plafond de débit horaire (cf. MAX_EVALUATIONS_PER_HOUR) : tronque la
        # liste plutôt que d'annuler toute la vidange -- dégradation progressive, jamais
        # tout-ou-rien. Les candidats tronqués restent marqués "vus" (_seen, ci-dessus) :
        # ils ne seront pas réévalués avant DEDUP_TTL_SECONDS, compromis volontaire pour
        # ne pas créer un pic de rattrapage au drain suivant.
        now = time.time()
        budget = self._evaluation_budget_remaining(now)
        if budget <= 0:
            logger.info(
                "momentum_websocket: plafond horaire atteint (%d/h) -- vidange ignorée",
                MAX_EVALUATIONS_PER_HOUR,
            )
            return
        if len(candidates) > budget:
            candidates = candidates[:budget]
        self._evaluation_timestamps.extend([now] * len(candidates))

        chain_by_contract = {c["contract"]: c["chain"] for c in filtered}
        analyzer = paper_trader._default_momentum_analyzer(chain_by_contract)
        try:
            from aria_core.gateway.telegram_bot import send_trading_notification

            # 20/07 -- bug réel trouvé en conditions réelles (position MAGIC achetée
            # sans jamais notifier Telegram, seule sa vente par le heartbeat suivant
            # est arrivée) : ce chemin n'avait jamais transmis de notifier à
            # run_paper_cycle -- toute position ouverte via le WebSocket temps réel
            # restait silencieuse jusqu'à sa clôture (gérée par le heartbeat, qui
            # notifie déjà). Même fonction que le heartbeat, jamais une 2e implémentation.
            await paper_trader.run_paper_cycle(
                candidates=candidates,
                analyzer=analyzer,
                max_new=MAX_NEW_PER_DRAIN,
                skip_position_management=True,
                notifier=send_trading_notification,
            )
        except Exception as exc:  # noqa: BLE001 -- une vidange qui plante ne tue pas le service
            logger.exception("momentum_websocket: run_paper_cycle échoué (%s)", exc)


momentum_websocket_listener = MomentumWebsocketListener()
