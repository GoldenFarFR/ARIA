"""Client Dune Analytics (lecture seule) -- Execute SQL API (15/07, cf.
docs/dune-integration-plan.md §3.2, §5).

Doctrine « dôme » (identique à blockscout.py/geckoterminal.py/tavily.py) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / 5xx : 1 retry après 5s, puis dégradation explicite (``available=False``).
- Aucune donnée manquante n'est jamais remplacée par une supposition.

Clé API : ``DUNE_API_KEY`` lue via ``os.environ.get`` À CHAQUE appel (jamais
mise en cache à l'import -- même patron que ``tavily.py``, plus simple à
tester avec ``monkeypatch.setenv``/``delenv``). Sans clé : ``available=False``
immédiat, AUCUN appel réseau tenté (même patron que ``TavilyClient`` sans
clé) -- la vraie clé sera ajoutée plus tard au ``.env`` du VPS par
l'opérateur, jamais fournie en session.

RÉSERVE HONNÊTE (15/07) : les noms d'endpoints/champs ci-dessous viennent de
la documentation PUBLIQUE Dune (docs.dune.com), pas d'un appel authentifié
réel -- aucune clé disponible cette session pour vérifier en direct (norme
de process du 14/07 : « toujours vérifier le nom exact des champs contre un
vrai appel réel » -- pas encore possible ici, cf. docs/dune-integration-plan.md
§4). Le parsing ci-dessous est tolérant (toute forme inattendue ->
``available=False``, jamais une exception, jamais une donnée inventée) --
mais la PREMIÈRE vraie exécution avec la clé opérateur doit revérifier ces
champs avant de considérer ce module comme fiable en prod.

Portée de ce module : client + requête SQL dédiée uniquement (§3.2 du plan).
PAS de branchement actif (pas de gate ``ARIA_DUNE_ENABLED``, pas de tâche
heartbeat, pas d'appel depuis ``wallet_candidate_sourcing.py``) -- décision
explicite de l'opérateur (15/07), l'intégration au sourcing existant est une
tâche séparée."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée Dune indisponible"

BASE_URL = "https://api.dune.com/api"

# États terminaux Dune (préfixe "QUERY_STATE_") -- COMPLETED = seul état
# permettant de lire un résultat exploitable ; les autres sont des échecs
# terminaux (jamais retentés indéfiniment, cf. `run_sql_and_wait`).
_TERMINAL_FAILURE_STATES = {"QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"}
_TERMINAL_SUCCESS_STATE = "QUERY_STATE_COMPLETED"


def dune_api_key() -> str:
    """Clé Dune depuis l'env UNIQUEMENT (jamais en dur, jamais loguée)."""
    return os.environ.get("DUNE_API_KEY", "").strip()


def is_dune_configured() -> bool:
    return bool(dune_api_key())


@dataclass
class ExecutionHandle:
    execution_id: str = ""
    state: str | None = None
    available: bool = True
    error: str | None = None


@dataclass
class ExecutionStatus:
    execution_id: str = ""
    state: str | None = None
    is_execution_finished: bool = False
    available: bool = True
    error: str | None = None


@dataclass
class ExecutionResult:
    execution_id: str = ""
    rows: list[dict] = field(default_factory=list)
    row_count: int | None = None
    available: bool = True
    error: str | None = None


async def _request(method: str, path: str, *, json_body: dict | None = None) -> tuple[object | None, str | None]:
    """GET/POST avec retry sur 429/5xx/timeout -- même politique que les
    autres clients de ce dossier. Sans clé configurée : `available=False`
    immédiat, aucun appel réseau (même patron que `tavily.py`)."""
    api_key = dune_api_key()
    if not api_key:
        return None, f"{UNAVAILABLE} (DUNE_API_KEY absente)"

    url = f"{BASE_URL}{path}"
    headers = {"X-Dune-Api-Key": api_key, "Accept": "application/json"}
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                else:
                    response = await client.post(url, headers=headers, json=json_body or {})
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dune: timeout sur %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout Dune)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("dune: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit Dune)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dune: HTTP %s sur %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur Dune {response.status_code})"

        if response.status_code in (401, 403):
            logger.warning("dune: HTTP %s sur %s (clé invalide/refusée)", response.status_code, url)
            return None, f"{UNAVAILABLE} (clé Dune invalide ou refusée)"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("dune: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def execute_sql(sql: str, *, performance: str = "medium") -> ExecutionHandle:
    """Lance une requête SQL brute (Execute SQL API, jamais besoin de la
    sauvegarder dans l'UI Dune d'abord). ``performance`` : "small"/"medium"
    (défaut, 10 crédits)/"large" (20 crédits) -- cf. docs/dune-integration-plan.md §4."""
    data, error = await _request("POST", "/v1/sql/execute", json_body={"sql": sql, "performance": performance})
    if error is not None:
        return ExecutionHandle(available=False, error=error)
    if not isinstance(data, dict):
        return ExecutionHandle(available=False, error=UNAVAILABLE)

    execution_id = str(data.get("execution_id") or "")
    if not execution_id:
        return ExecutionHandle(available=False, error=f"{UNAVAILABLE} (execution_id absent)")

    return ExecutionHandle(execution_id=execution_id, state=data.get("state"), available=True, error=None)


async def get_execution_status(execution_id: str) -> ExecutionStatus:
    """Statut d'une exécution -- endpoint gratuit côté Dune (aucun crédit
    consommé), pensé pour être sondé (`run_sql_and_wait`)."""
    data, error = await _request("GET", f"/v1/execution/{execution_id}/status")
    if error is not None:
        return ExecutionStatus(execution_id=execution_id, available=False, error=error)
    if not isinstance(data, dict):
        return ExecutionStatus(execution_id=execution_id, available=False, error=UNAVAILABLE)

    return ExecutionStatus(
        execution_id=execution_id,
        state=data.get("state"),
        is_execution_finished=bool(data.get("is_execution_finished")),
        available=True,
        error=None,
    )


async def get_execution_result(execution_id: str) -> ExecutionResult:
    """Résultat d'une exécution terminée. N'inspecte PAS `state` lui-même --
    l'appelant (`run_sql_and_wait`) doit avoir déjà confirmé l'état terminal
    via `get_execution_status` avant d'appeler ceci."""
    data, error = await _request("GET", f"/v1/execution/{execution_id}/results")
    if error is not None:
        return ExecutionResult(execution_id=execution_id, available=False, error=error)
    if not isinstance(data, dict):
        return ExecutionResult(execution_id=execution_id, available=False, error=UNAVAILABLE)

    result = data.get("result")
    if not isinstance(result, dict):
        return ExecutionResult(execution_id=execution_id, available=False, error=f"{UNAVAILABLE} (result absent)")

    rows = result.get("rows")
    if not isinstance(rows, list):
        return ExecutionResult(execution_id=execution_id, available=False, error=f"{UNAVAILABLE} (rows absent)")

    metadata = result.get("metadata") or {}
    row_count = metadata.get("row_count") if isinstance(metadata, dict) else None

    return ExecutionResult(
        execution_id=execution_id,
        rows=[r for r in rows if isinstance(r, dict)],
        row_count=row_count if isinstance(row_count, int) else None,
        available=True,
        error=None,
    )


async def run_sql_and_wait(
    sql: str, *, performance: str = "medium", poll_interval: float = 3.0, max_wait: float = 300.0,
) -> ExecutionResult:
    """Orchestration complète : lance la requête, sonde le statut (gratuit)
    jusqu'à un état terminal, puis lit le résultat une seule fois. Bornée par
    ``max_wait`` (5 min par défaut) -- jamais une attente non bornée, même si
    Dune ne termine jamais l'exécution."""
    handle = await execute_sql(sql, performance=performance)
    if not handle.available or not handle.execution_id:
        return ExecutionResult(available=False, error=handle.error or UNAVAILABLE)

    elapsed = 0.0
    while elapsed < max_wait:
        status = await get_execution_status(handle.execution_id)
        if not status.available:
            return ExecutionResult(execution_id=handle.execution_id, available=False, error=status.error)

        if status.state in _TERMINAL_FAILURE_STATES:
            logger.warning("dune: exécution %s terminée en échec (%s)", handle.execution_id, status.state)
            return ExecutionResult(
                execution_id=handle.execution_id, available=False, error=f"{UNAVAILABLE} (état {status.state})",
            )

        if status.is_execution_finished or status.state == _TERMINAL_SUCCESS_STATE:
            return await get_execution_result(handle.execution_id)

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning("dune: exécution %s toujours en cours après %ss -- abandon (jamais une attente non bornée)", handle.execution_id, max_wait)
    return ExecutionResult(
        execution_id=handle.execution_id, available=False, error=f"{UNAVAILABLE} (délai d'exécution dépassé après {max_wait}s)",
    )


# ---------------------------------------------------------------------------
# Requête SQL dédiée (#157 sourcing, §3.2 du plan) -- "wallets ayant acheté
# un token Base dans sa première heure de vie, qui a ensuite fait au moins Nx"
# ---------------------------------------------------------------------------
#
# RÉSERVE HONNÊTE (15/07) : noms de colonnes de `dex.trades` (table Dune
# Spellbook très stable/documentée publiquement : block_time, blockchain,
# project, taker, token_bought_address, token_bought_amount,
# token_sold_address, token_sold_amount, amount_usd, tx_hash) -- PAS vérifiés
# par un appel réel (aucune clé disponible cette session). À reconfirmer via
# `EXECUTE_SQL_LIMIT_1` (juste en dessous) avant tout usage en prod, norme du
# 14/07 (« ne jamais faire confiance à un schéma deviné de mémoire »).
#
# Logique de la requête :
# 1. `token_launch` : premier trade DEX Base jamais vu pour chaque token
#    (`token_bought_address`), pris comme proxy de "naissance" du token --
#    Dune n'a pas de notion native de "déploiement de contrat" dans
#    `dex.trades`, seulement des trades, donc ce proxy est une approximation
#    documentée, pas une vérité absolue (peut différer de quelques blocs du
#    vrai déploiement si le tout premier trade a mis du temps à apparaître).
# 2. `early_buyers` : wallets (`taker`) dont l'achat de ce token a eu lieu
#    dans l'heure suivant `token_launch`.
# 3. `peak_multiple` : plus haut prix USD observé sur ce token / prix USD au
#    moment du lancement -- filtre les tokens ayant fait au moins `min_multiple`.
# 4. Résultat : liste de wallets distincts ayant acheté un token qui a
#    ensuite fait ≥ N x, avec le multiple observé et le token concerné.
#
# CORRECTIF DE REVUE (15/07, avant merge) : `token_launch` calculait MIN(block_time)
# sur des lignes DÉJÀ filtrées à la fenêtre `lookback_days` -- un token ÉTABLI depuis
# longtemps dont le premier trade DANS la fenêtre tombait par hasard il y a
# `lookback_days` jours aurait été à tort classé "vient de naître", polluant tout le
# signal (le but est de trouver des acheteurs précoces de VRAIS nouveaux tokens, pas
# des acheteurs d'un token ancien pendant une remontée récente). Corrigé : l'agrégat
# MIN(block_time) porte maintenant sur l'historique COMPLET de `dex.trades` (aucun
# filtre de date dans le WHERE), et seul le résultat agrégé est filtré via HAVING --
# ne garde que les tokens dont la PREMIÈRE transaction jamais vue tombe bien dans la
# fenêtre récente. Coût plus élevé (scan complet de la table pour cette CTE), mais
# nécessaire pour la correction -- `token_peak`/`token_launch_price` restent bornées
# à la fenêtre, cohérent puisque tout trade d'un token réellement nouveau (launch_time
# dans la fenêtre) tombe forcément aussi dans la fenêtre.
#
# CORRECTIF #185 (15/07, bug réel trouvé en vérification live via le MCP dune
# -- cf. docs/dune-integration-plan.md §8.1, query 7992486) : `peak_multiple`
# ressortait aberrant (~10^22 x) sur plusieurs lignes en tête. Cause identifiée
# par inspection des valeurs intermédiaires : `launch_price_usd` quasi-nul
# (ex. 3.6e-14 $) -- `token_peak`/`token_launch_price` calculaient un prix
# unitaire (`amount_usd / token_bought_amount`) sur CHAQUE trade, y compris un
# trade de "dust" (montant infinitésimal) qui fait exploser la division sans
# être un vrai prix de marché. `NULLIF(token_bought_amount, 0)` protège contre
# la division par zéro EXACT, pas contre un montant proche de zéro.
# Corrigé en excluant les trades de dust du calcul du prix AVANT le MIN/MAX
# (`amount_usd >= {min_trade_usd}` dans `token_peak` ET `token_launch_price`
# -- le bug pouvait toucher n'importe quel côté de la division, pas seulement
# le prix de lancement) -- PAS un plafond arbitraire sur `peak_multiple` final,
# qui aurait masqué le symptôme sans corriger la cause (un token dont le VRAI
# prix de lancement était mal mesuré resterait mal mesuré, juste caché par le
# plafond au lieu d'être exclu proprement).
#
# Paramètres attendus par l'appelant (substitution simple avant l'envoi --
# CE MODULE NE FAIT AUCUNE VALIDATION/ÉCHAPPEMENT, l'appelant doit s'assurer
# que `min_multiple`/`lookback_days`/`min_trade_usd` sont des valeurs
# numériques de confiance, jamais une entrée utilisateur non filtrée --
# doctrine "lecture seule" ne protège pas contre une injection SQL si ces
# valeurs viennent d'ailleurs) :
# - `min_multiple` (float, ex. 5.0 pour "au moins 5x")
# - `lookback_days` (int, fenêtre de recherche des lancements de tokens, ex. 30)
# - `min_trade_usd` (float, défaut 1.0 -- montant USD minimum d'un trade pour
#   compter dans le calcul du prix ; exclut le dust sans exclure les tokens
#   eux-mêmes, un token peut toujours apparaître au résultat via ses AUTRES
#   trades au-dessus du plancher)
EARLY_BUYER_MULTIPLE_QUERY_TEMPLATE = """
WITH token_launch AS (
    SELECT
        token_bought_address AS token_address,
        MIN(block_time) AS launch_time
    FROM dex.trades
    WHERE blockchain = 'base'
    GROUP BY token_bought_address
    HAVING MIN(block_time) >= NOW() - INTERVAL '{lookback_days}' day
),
early_buyers AS (
    SELECT DISTINCT
        t.taker AS wallet_address,
        t.token_bought_address AS token_address,
        tl.launch_time
    FROM dex.trades t
    JOIN token_launch tl
        ON t.token_bought_address = tl.token_address
    WHERE t.blockchain = 'base'
      AND t.block_time >= tl.launch_time
      AND t.block_time < tl.launch_time + INTERVAL '1' hour
),
token_peak AS (
    SELECT
        token_bought_address AS token_address,
        MAX(amount_usd / NULLIF(token_bought_amount, 0)) AS peak_price_usd
    FROM dex.trades
    WHERE blockchain = 'base'
      AND block_time >= NOW() - INTERVAL '{lookback_days}' day
      AND amount_usd >= {min_trade_usd}
    GROUP BY token_bought_address
),
token_launch_price AS (
    SELECT
        t.token_bought_address AS token_address,
        MIN(t.amount_usd / NULLIF(t.token_bought_amount, 0)) AS launch_price_usd
    FROM dex.trades t
    JOIN token_launch tl
        ON t.token_bought_address = tl.token_address
    WHERE t.blockchain = 'base'
      AND t.block_time = tl.launch_time
      AND t.amount_usd >= {min_trade_usd}
    GROUP BY t.token_bought_address
)
SELECT
    eb.wallet_address,
    eb.token_address,
    eb.launch_time,
    tlp.launch_price_usd,
    tp.peak_price_usd,
    (tp.peak_price_usd / NULLIF(tlp.launch_price_usd, 0)) AS peak_multiple
FROM early_buyers eb
JOIN token_peak tp ON tp.token_address = eb.token_address
JOIN token_launch_price tlp ON tlp.token_address = eb.token_address
WHERE tlp.launch_price_usd > 0
  AND (tp.peak_price_usd / NULLIF(tlp.launch_price_usd, 0)) >= {min_multiple}
ORDER BY peak_multiple DESC
"""

# Requête minimale pour vérifier le schéma réel de `dex.trades` avant tout
# usage en prod de la requête ci-dessus (norme du 14/07) -- volontairement
# gardée à part, jamais envoyée automatiquement par ce module.
EXECUTE_SQL_LIMIT_1 = "SELECT * FROM dex.trades WHERE blockchain = 'base' LIMIT 1"


def build_early_buyer_multiple_query(
    *, min_multiple: float, lookback_days: int, min_trade_usd: float = 1.0,
) -> str:
    """Construit la requête ci-dessus avec les paramètres demandés. Valide
    que les trois entrées sont bien numériques AVANT toute substitution dans
    le SQL -- seule protection anti-injection pertinente ici, cette requête
    n'accepte jamais de chaîne de caractères libre."""
    if not isinstance(min_multiple, (int, float)) or min_multiple <= 0:
        raise ValueError("min_multiple doit être un nombre positif")
    if not isinstance(lookback_days, int) or lookback_days <= 0:
        raise ValueError("lookback_days doit être un entier positif")
    if not isinstance(min_trade_usd, (int, float)) or min_trade_usd <= 0:
        raise ValueError("min_trade_usd doit être un nombre positif")
    return EARLY_BUYER_MULTIPLE_QUERY_TEMPLATE.format(
        min_multiple=min_multiple, lookback_days=lookback_days, min_trade_usd=min_trade_usd,
    )


# ---------------------------------------------------------------------------
# Requête SQL dédiée (#134 « débit de scan élargi », 15/07) -- DEUXIÈME source
# INDÉPENDANTE de découverte de tokens Base, en complément (jamais en
# remplacement) de GeckoTerminal (déjà utilisé par
# ``base_crawler.discover_top_pools``). Portée EXACTE de cette tâche : client
# + requête + tests SEULEMENT -- PAS de branchement dans ``base_crawler.py``,
# PAS de gate, PAS de tâche heartbeat (décision opérateur du 15/07,
# intégration réelle au pipeline = décision séparée après relecture croisée).
#
# Abandon de la piste initiale ("/v1/dex/pairs/{chain}", §3.1 du plan) --
# vérifiée en direct (15/07) et confirmée INEXISTANTE (404 sur toute variante
# d'URL essayée, y compris avec un header d'auth présent -- contrairement à
# l'Execute SQL API, réelle, qui répond 401 sans clé valide). Cette requête
# réutilise donc STRICTEMENT la même Execute SQL API que
# ``build_early_buyer_multiple_query`` ci-dessus, aucun nouveau client.
#
# Logique de la requête :
# 1. `token_launch` : premier trade DEX Base jamais vu pour chaque token
#    (même CTE/même piège corrigé que ci-dessus -- voir avertissement
#    ci-dessous), filtré aux tokens dont ce premier trade tombe dans la
#    fenêtre récente (`lookback_hours`, ex. 24-48h).
# 2. `recent_volume` : volume USD total et nombre de trades sur la fenêtre
#    récente, par token -- borné directement par `lookback_hours` dans le
#    WHERE (safe ici, PAS le même piège que token_launch : un token dont le
#    launch_time tombe dans la fenêtre a par construction TOUS ses trades
#    dans la fenêtre aussi -- même raisonnement déjà appliqué à
#    `token_peak`/`token_launch_price` dans la requête ci-dessus).
# 3. Résultat : tokens Base nouvellement apparus (premier trade dans la
#    fenêtre) avec un volume minimum, triés par volume décroissant --
#    candidats de découverte, PAS encore un verdict de sécurité (le filtre
#    de sécurité réel reste `safety_screen`/`token_absorber`, inchangé).
#
# AVERTISSEMENT ANTI-RÉGRESSION (relecture opérateur du 15/07, même piège que
# la 1ère requête avant sa correction) : `token_launch` ne doit JAMAIS filtrer
# par date dans son WHERE -- seulement `blockchain = 'base'`. Le filtre de
# fenêtre récente s'applique UNIQUEMENT via HAVING sur l'agrégat
# MIN(block_time), sinon un token ÉTABLI depuis longtemps dont le premier
# trade DANS la fenêtre de calcul tombe par hasard il y a `lookback_hours`
# serait à tort classé "vient de naître" -- l'agrégat doit porter sur
# l'historique COMPLET de `dex.trades` pour que "premier trade jamais vu"
# soit vraiment le tout premier, pas le premier dans une fenêtre déjà filtrée.
#
# RÉSERVE HONNÊTE (mêmes colonnes `dex.trades` que ci-dessus, mêmes non
# vérifiées par appel réel -- cf. réserve en tête de fichier) : à reconfirmer
# via `EXECUTE_SQL_LIMIT_1` avant tout usage en prod.
#
# Paramètres attendus par l'appelant (substitution simple, mêmes garanties
# que ``build_early_buyer_multiple_query`` -- CE MODULE NE FAIT AUCUNE
# VALIDATION/ÉCHAPPEMENT au-delà du typage numérique, l'appelant doit
# s'assurer que ces valeurs sont de confiance, jamais une entrée utilisateur
# non filtrée) :
# - `min_volume_usd` (float, ex. 5000.0 pour "au moins 5 000$ de volume")
# - `lookback_hours` (int, fenêtre de recherche des lancements de tokens, ex. 48)
RECENT_BASE_PAIRS_QUERY_TEMPLATE = """
WITH token_launch AS (
    SELECT
        token_bought_address AS token_address,
        MIN(block_time) AS launch_time
    FROM dex.trades
    WHERE blockchain = 'base'
    GROUP BY token_bought_address
    HAVING MIN(block_time) >= NOW() - INTERVAL '{lookback_hours}' hour
),
recent_volume AS (
    -- RÉSERVE (VPS Research, 15/07, test live sur dex.trades) : `amount_usd`
    -- est `null` sur certaines lignes issues de projets agrégateurs (ex.
    -- `0x API`) -- `SUM()` ignore silencieusement ces lignes (ni erreur ni
    -- exception), donc `volume_usd` ici est un plancher, jamais un total
    -- garanti exact : un token tradé surtout via agrégateur peut être
    -- sous-évalué et manquer le seuil `min_volume_usd` à tort (faux négatif
    -- de découverte, jamais un faux positif de sécurité). À traiter avant
    -- tout usage en prod si ça s'avère significatif (ex. COALESCE + colonne
    -- séparée `trade_count_unpriced` pour rendre le manque visible, jamais
    -- une valeur inventée).
    SELECT
        token_bought_address AS token_address,
        SUM(amount_usd) AS volume_usd,
        COUNT(*) AS trade_count
    FROM dex.trades
    WHERE blockchain = 'base'
      AND block_time >= NOW() - INTERVAL '{lookback_hours}' hour
    GROUP BY token_bought_address
)
SELECT
    tl.token_address,
    tl.launch_time,
    rv.volume_usd,
    rv.trade_count
FROM token_launch tl
JOIN recent_volume rv ON rv.token_address = tl.token_address
WHERE rv.volume_usd >= {min_volume_usd}
ORDER BY rv.volume_usd DESC
"""


def build_recent_base_pairs_query(*, min_volume_usd: float, lookback_hours: int) -> str:
    """Construit la requête ci-dessus avec les paramètres demandés. Valide
    que les deux entrées sont bien numériques AVANT toute substitution dans
    le SQL -- même garantie que ``build_early_buyer_multiple_query``, cette
    requête n'accepte jamais de chaîne de caractères libre."""
    if not isinstance(min_volume_usd, (int, float)) or min_volume_usd <= 0:
        raise ValueError("min_volume_usd doit être un nombre positif")
    if not isinstance(lookback_hours, int) or lookback_hours <= 0:
        raise ValueError("lookback_hours doit être un entier positif")
    return RECENT_BASE_PAIRS_QUERY_TEMPLATE.format(min_volume_usd=min_volume_usd, lookback_hours=lookback_hours)


# ---------------------------------------------------------------------------
# Requête SQL dédiée -- renforcement du signal de financement partagé déjà
# existant (`_pairwise_convergence`/funding source dans smart_money.py) avec
# `addresses.stats.first_funded_by` (15/07, cf.
# docs/aria-learning-inbox/2026-07-15-graphsense-verifie-negatif-dune-labels-pivot.md
# §2.1 -- table testée en direct ce soir par Research, PAS une supposition de
# schéma). Portée EXACTE de cette tâche : fonction + requête + tests
# SEULEMENT -- PAS de branchement dans smart_money.py (décision opérateur du
# 15/07 ; le chantier Sybil complet -- Louvain/K-means -- reste séparé et plus
# lourd, cf. le même rapport).
#
# Colonnes confirmées PAR TEST RÉEL (pas la doc publique) : `address`,
# `first_funded_by`, `first_funded_at`, `is_eoa`, `is_smart_contract` --
# couverture confirmée Base + 11 autres chaînes. RÉSERVE reprise du rapport
# Research : `is_smart_contract`/`is_eoa` peuvent se tromper sur des adresses
# prédéployées spécifiques à Base (ex. WETH `0x4200...0006` classée `is_eoa:
# true` à tort) -- ces deux champs sont donc renvoyés tels quels, jamais
# réinterprétés ou filtrés par ce module.
#
# RÉSERVE COÛT (reprise du même rapport) : 0,963 crédit observé pour 2
# adresses SANS filtre de colonne de partition -- `addresses.stats` n'a pas
# de fenêtre temporelle naturelle côté appelant ici (contrairement à
# `dex.trades`), donc aucun filtre de date n'est ajouté ; l'appelant doit
# donc BORNER la taille de `addresses` lui-même (ne jamais envoyer une liste
# non bornée) -- pas la responsabilité de ce module de deviner une limite
# arbitraire.
#
# CORRECTIF DE VÉRIFICATION LIVE (15/07, avant merge -- bug réel trouvé, pas
# une supposition) : `address` est de type `varbinary` dans `addresses.stats`
# (confirmé par `resultMetadata` sur l'exécution réelle), PAS `varchar`.
# Un premier essai avec des littéraux entre guillemets simples
# (``address IN ('0x...', '0x...')``) a ÉCHOUÉ en exécution réelle :
# « Cannot find common type between varbinary and varchar(42) » -- DuneSQL ne
# caste PAS implicitement une chaîne vers varbinary dans un IN, contrairement
# à d'autres contextes SQL. Corrigé en émettant les adresses comme littéraux
# hexadécimaux NUS (``0x...`` sans guillemets, syntaxe varbinary native de
# Trino/DuneSQL) -- reverifié en exécution réelle après correctif (cf.
# docs/dune-integration-plan.md), résultat identique à l'essai avec
# guillemets simples sur `dex.trades.taker` (varchar, lui, cast implicite ok)
# -- ce module confirme donc qu'il ne faut JAMAIS supposer qu'un type
# d'adresse Dune est uniformément `varchar` d'une table à l'autre.
_EVM_ADDRESS_RE_SOURCE = r"^0x[a-fA-F0-9]{40}$"

ADDRESSES_STATS_QUERY_TEMPLATE = """
SELECT address, first_funded_by, first_funded_at, is_eoa, is_smart_contract
FROM addresses.stats
WHERE blockchain = '{blockchain}'
  AND address IN ({address_list})
"""


def build_addresses_stats_query(addresses: list[str], *, blockchain: str = "base") -> str:
    """Construit la requête ``addresses.stats`` pour une liste d'adresses.
    Valide chaque adresse contre un format EVM strict (``0x`` + 40 hex) AVANT
    toute substitution -- ces adresses peuvent provenir de wallets suivis
    dynamiquement (pas une constante interne comme les autres paramètres de ce
    module), donc une vraie validation anti-injection est nécessaire ici,
    contrairement aux `build_*` numériques ci-dessus. Émet des littéraux
    hexadécimaux NUS (``0x...``, pas entre guillemets) -- `address` est
    `varbinary` dans `addresses.stats`, confirmé en exécution réelle (cf.
    réserve ci-dessus) ; un littéral entre guillemets simples y échoue."""
    if not addresses:
        raise ValueError("addresses ne peut pas être vide")
    if not blockchain or not re.fullmatch(r"[a-z0-9_-]+", blockchain):
        raise ValueError("blockchain invalide")

    address_re = re.compile(_EVM_ADDRESS_RE_SOURCE)
    normalized: list[str] = []
    for addr in addresses:
        if not isinstance(addr, str) or not address_re.fullmatch(addr):
            raise ValueError(f"adresse EVM invalide : {addr!r}")
        normalized.append(addr.lower())

    address_list = ", ".join(normalized)  # littéraux hex NUS -- cf. réserve varbinary ci-dessus
    return ADDRESSES_STATS_QUERY_TEMPLATE.format(blockchain=blockchain, address_list=address_list)


@dataclass
class FundedByRecord:
    address: str
    first_funded_by: str | None = None
    first_funded_at: str | None = None
    is_eoa: bool | None = None
    is_smart_contract: bool | None = None


@dataclass
class FundedByResult:
    records: list[FundedByRecord] = field(default_factory=list)
    available: bool = True
    error: str | None = None


async def get_first_funded_by(
    addresses: list[str], *, blockchain: str = "base", performance: str = "medium",
) -> FundedByResult:
    """Interroge `addresses.stats` pour une liste d'adresses et retourne leur
    `first_funded_by` (et les autres champs confirmés de la table). Même
    doctrine dôme que le reste de ce module : sans clé -- ou en cas d'échec à
    n'importe quelle étape (exécution/statut/résultat) -- `available=False`,
    jamais une exception, jamais un enregistrement inventé. Liste vide :
    résultat vide immédiat, aucun appel réseau (pas d'aller-retour Dune inutile)."""
    if not addresses:
        return FundedByResult(records=[], available=True, error=None)

    try:
        sql = build_addresses_stats_query(addresses, blockchain=blockchain)
    except ValueError as exc:
        return FundedByResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    exec_result = await run_sql_and_wait(sql, performance=performance)
    if not exec_result.available:
        return FundedByResult(available=False, error=exec_result.error)

    records: list[FundedByRecord] = []
    for row in exec_result.rows:
        address = row.get("address")
        if not isinstance(address, str) or not address:
            continue
        records.append(
            FundedByRecord(
                address=address,
                first_funded_by=row.get("first_funded_by") if isinstance(row.get("first_funded_by"), str) else None,
                first_funded_at=row.get("first_funded_at") if isinstance(row.get("first_funded_at"), str) else None,
                is_eoa=row.get("is_eoa") if isinstance(row.get("is_eoa"), bool) else None,
                is_smart_contract=row.get("is_smart_contract") if isinstance(row.get("is_smart_contract"), bool) else None,
            )
        )

    return FundedByResult(records=records, available=True, error=None)


# ---------------------------------------------------------------------------
# Requête SQL dédiée -- dernier recours de la cascade OHLCV du pipeline momentum
# (#194, 16/07, demande opérateur explicite : "cables les tous je veux une
# toile complete avec dexscreener et dune"). Reconstruit des bougies HORAIRES
# depuis `prices.usd` (table spellbook prix, granularité minute) -- SEULEMENT
# utilisée quand GeckoTerminal ET CoinMarketCap ont tous les deux échoué, car
# une exécution Dune (`run_sql_and_wait`) prend potentiellement plusieurs
# dizaines de secondes ET consomme des crédits, contrairement aux deux
# premiers fournisseurs (rapides, gratuits) -- jamais utilisée en premier,
# jamais en parallèle des deux autres (un dernier recours, pas une course).
#
# RÉSERVE HONNÊTE (même doctrine que le reste de ce module) : `prices.usd` est
# documentée publiquement avec les colonnes `blockchain`, `contract_address`,
# `symbol`, `price`, `decimals`, `minute` -- PAS vérifiées par un appel réel
# cette session. Le type de `contract_address` n'est pas confirmé (varchar ou
# varbinary selon la table, cf. la réserve déjà documentée pour
# `addresses.stats.address` plus haut dans ce fichier, qui s'est révélée
# varbinary contrairement à `dex.trades.taker`) -- ce module émet un littéral
# hexadécimal NU (``0x...``, syntaxe varbinary) par prudence, à reconfirmer
# via une requête réelle avant tout usage en prod (norme du 14/07).
#
# Reconstruction OHLC horaire depuis des points de prix par minute (pas déjà
# des bougies) : `MIN`/`MAX` du prix sur l'heure pour low/high, et
# `array_agg(price ORDER BY minute)` + `element_at(..., 1)`/`element_at(..., -1)`
# (syntaxe Trino/DuneSQL) pour open/close -- premier et dernier prix
# chronologique de l'heure. Aucun volume dans `prices.usd` -- `volume=0.0`
# assumé (dégradation honnête, jamais une valeur inventée).
PRICE_HISTORY_QUERY_TEMPLATE = """
SELECT
    date_trunc('hour', minute) AS bucket,
    MIN(price) AS low,
    MAX(price) AS high,
    element_at(array_agg(price ORDER BY minute), 1) AS open,
    element_at(array_agg(price ORDER BY minute), -1) AS close
FROM prices.usd
WHERE blockchain = '{blockchain}'
  AND contract_address = {contract_address}
  AND minute >= NOW() - INTERVAL '{lookback_hours}' hour
GROUP BY 1
ORDER BY 1
"""


def build_price_history_query(contract_address: str, *, blockchain: str = "base", lookback_hours: int = 48) -> str:
    """Construit la requête ci-dessus. Valide l'adresse (format EVM strict) et
    `lookback_hours` AVANT toute substitution -- même garantie anti-injection
    que ``build_addresses_stats_query`` (l'adresse peut venir d'un candidat
    momentum arbitraire, jamais une constante interne)."""
    if not contract_address or not re.fullmatch(_EVM_ADDRESS_RE_SOURCE, contract_address):
        raise ValueError(f"adresse EVM invalide : {contract_address!r}")
    if not blockchain or not re.fullmatch(r"[a-z0-9_-]+", blockchain):
        raise ValueError("blockchain invalide")
    if not isinstance(lookback_hours, int) or lookback_hours <= 0:
        raise ValueError("lookback_hours doit être un entier positif")
    return PRICE_HISTORY_QUERY_TEMPLATE.format(
        blockchain=blockchain,
        contract_address=contract_address.lower(),  # littéral hex NU -- cf. réserve varbinary ci-dessus
        lookback_hours=lookback_hours,
    )


@dataclass
class DunePriceHistoryResult:
    candles: list = field(default_factory=list)  # list[Candle], import différé -- évite un cycle avec ta_levels
    available: bool = True
    error: str | None = None


async def get_price_history(
    contract_address: str, *, blockchain: str = "base", lookback_hours: int = 48, performance: str = "medium",
) -> DunePriceHistoryResult:
    """Bougies horaires reconstruites depuis Dune (dernier recours de la
    cascade OHLCV, #194) -- ``available=False`` sans clé, sur adresse
    invalide, ou en cas d'échec à n'importe quelle étape de l'exécution.
    Chaîne non supportée par ``prices.usd`` ou aucun trade sur la fenêtre :
    liste vide, jamais un prix inventé."""
    from aria_core.skills.ta_levels import Candle

    try:
        sql = build_price_history_query(contract_address, blockchain=blockchain, lookback_hours=lookback_hours)
    except ValueError as exc:
        return DunePriceHistoryResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    exec_result = await run_sql_and_wait(sql, performance=performance)
    if not exec_result.available:
        return DunePriceHistoryResult(available=False, error=exec_result.error)

    candles: list[Candle] = []
    for row in exec_result.rows:
        try:
            open_ = float(row.get("open"))
            high = float(row.get("high"))
            low = float(row.get("low"))
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        bucket = row.get("bucket")
        ts = 0
        if isinstance(bucket, str):
            import datetime

            try:
                ts = int(datetime.datetime.fromisoformat(bucket.replace("Z", "+00:00")).timestamp())
            except ValueError:
                ts = 0
        candles.append(Candle(ts=ts, open=open_, high=high, low=low, close=close, volume=0.0))

    return DunePriceHistoryResult(candles=candles, available=True, error=None)
