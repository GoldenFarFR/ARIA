"""Poids et seuils tunables de l'évaluateur wallet-centrique (#157) — ISOLÉS
dans ce module UNIQUE à la demande explicite de l'opérateur (14/07) : ce sont
les paramètres qui constituent une partie de l'avantage compétitif de la
formule maison, pas un détail d'implémentation interchangeable.

Distinction volontaire :
- Les FORMULES génériques (calcul FIFO, ratio de Sortino, Maximum Drawdown)
  restent dans le code public (`services/smart_money.py`) — ce sont des
  calculs de finance standard, documentés publiquement, pas un secret.
- Les VALEURS de seuil/pondération ci-dessous (à partir de quel win rate un
  wallet est "suspect positif", combien de trades minimum avant de faire
  confiance au Sortino, combien de tokens analyser en profondeur, etc.) sont
  regroupées ICI, dans un seul endroit identifiable, pour pouvoir être
  déplacées/masquées facilement si le commandement décide qu'elles ne doivent
  pas être lisibles publiquement sur GitHub.

DÉCISION OPÉRATEUR (14/07) : ce module reste dans le dépôt PUBLIC ARIA tel
quel, avec les valeurs par défaut du dataclass ci-dessous — mais ces valeurs
par défaut NE SONT PAS les vraies valeurs de production tunées. Ce sont de
simples valeurs de départ raisonnables pour que le code fonctionne sans
configuration externe (dev/local/tests). Les vraies valeurs de production
seront déposées manuellement plus tard par l'opérateur dans un fichier privé
sur le VPS, hors de ce dépôt et hors de ce chantier.

Au démarrage, `WEIGHTS` tente de charger ses valeurs réelles depuis un fichier
externe YAML/JSON désigné par la variable d'environnement
`ARIA_WALLET_SCORING_WEIGHTS_PATH` — même patron que les secrets existants
(`.env` jamais commité, lu via variable d'environnement) : pas une nouvelle
doctrine, une application de l'existante. Si la variable n'est pas définie, ou
si le fichier est introuvable/illisible/invalide, repli explicite (loggé, pas
silencieux) sur les valeurs par défaut du dataclass — comportement actuel
inchangé en local/dev/tests, aucune configuration externe requise pour faire
tourner la suite de tests.

Portée : UNIQUEMENT les seuils introduits par le chantier #157 (l'évaluateur
wallet-centrique multi-token). Les constantes préexistantes de
`smart_money.py` côté token-centrique (`_LARGEST_BUY_SHARE_MAX`,
`_EARLY_ENTRY_WINDOW_SECONDS`, `_WASH_TRADING_COUNTERPARTY_SHARE`,
`_MIN_TRANSFERS_FOR_WASH_CHECK`) sont une question séparée, explicitement
laissée de côté par l'opérateur pour l'instant — pas touchées ici.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields

import yaml

logger = logging.getLogger(__name__)

_WEIGHTS_PATH_ENV_VAR = "ARIA_WALLET_SCORING_WEIGHTS_PATH"


@dataclass(frozen=True)
class WalletScoringWeights:
    # Plafond de tokens distincts analysés en profondeur par wallet (récence/
    # nombre de trades) -- décision opérateur du 14/07 : 20->50 puis ramené à
    # 10 le même jour (scans plus rapides par défaut). Le paramètre
    # ``max_tokens`` de ``score_wallets`` permet toujours de passer un
    # plafond différent ponctuellement sans retoucher cette valeur par défaut.
    max_tokens_analyzed: int = 10

    # Sous ce nombre de trades clôturés, le ratio de Sortino est jugé trop
    # bruité pour être présenté comme fiable (research doc #157) -- indisponible
    # plutôt qu'un chiffre trompeur.
    min_closed_trades_for_sortino: int = 5

    # Drapeau "suspect positif" (couche 3, #157) -- nombre minimal d'axes
    # indépendants dépassés simultanément avant de lever le drapeau, et les
    # seuils par axe.
    suspect_positive_min_axes: int = 3
    suspect_win_rate_min: float = 0.7
    suspect_sortino_min: float = 1.5
    suspect_diversification_min_tokens: int = 3
    suspect_diversification_ratio_min: float = 0.6
    suspect_recurrence_min: int = 3

    # Pagination bornée de l'historique de transactions (ancienneté du wallet /
    # source de financement) -- Blockscout n'offre pas de tri "plus ancien
    # d'abord" bon marché (vérifié en direct, #157) : plafond de pages avant de
    # présenter le résultat comme une borne plutôt qu'une valeur exacte.
    funding_source_max_pages: int = 5

    # Fenêtre de bougies regardée en arrière pour qualifier une entrée précoce
    # d'"informée" (volume faible + figure chartiste juste avant l'achat).
    technical_entry_lookback_candles: int = 20

    # Anti-faux-positif wash-trading (#157, correction 14/07) : une contrepartie
    # qui revient sur au moins ce nombre de tokens DISTINCTS est traitée comme
    # une brique d'infrastructure DEX (pool/routeur, mécaniquement partagée
    # entre de nombreuses paires), PAS un partenaire de wash-trading (typiquement
    # lié à UN seul token/schéma) -- exclue du calcul de contrepartie dominante.
    wash_trading_infra_min_distinct_tokens: int = 2

    # Prix par tx_hash exact (14/07, complément pool+OHLCV) : plafond de
    # tx_hash distincts tentés par token avant de retomber sur pool+OHLCV pour
    # le reste. Un wallet actif (le profil même que ce scoring cherche à
    # repérer) peut avoir des dizaines/centaines de tx distincts sur un seul
    # token -- sans plafond, une requête `/walletscore` déclencherait autant
    # d'appels Blockscout séquentiels supplémentaires. Valeur de départ
    # raisonnable, compromis précision/coût -- pas une vérité gravée,
    # ajustable comme tout `WEIGHTS.*` via le fichier de surcharge.
    max_hash_priced_legs_per_token: int = 20

    # Échantillon minimum avant de présenter un classement comme fiable (15/07,
    # décision opérateur explicite) -- même doctrine que le protocole "feu
    # vert argent réel" appliqué à ARIA elle-même (`docs/protocole-argent-
    # reel.md`, échantillon minimum avant de faire confiance) : un wallet avec
    # peu d'historique n'est pas noté "mauvais", juste marqué "échantillon
    # insuffisant" -- jamais un score présenté avec une fausse confiance.
    min_wallet_age_days: int = 90
    min_total_swaps: int = 100

    # Robustesse anti-chance (15/07, décision opérateur ; corrigé le même jour
    # après revue externe croisée Gemini/ChatGPT/Grok, convergente sur ce point) :
    # retire un POURCENTAGE (pas un compte fixe) des trades les mieux ET les
    # moins bien classés (double extrémité) avant de vérifier si le PnL
    # restant reste positif. Un compte fixe (l'ancien ``robust_trim_count=10``)
    # se dilue à mesure que l'échantillon grossit (10/30 = 33% retiré au seuil
    # minimum, mais seulement 10/20000 = 0.05% sur un wallet très actif) --
    # un attaquant peut alors noyer un unique trade "chanceux" derrière
    # suffisamment de micro-trades insignifiants pour qu'il ne soit plus dans
    # le top-N absolu retiré. Un pourcentage scale avec N et ferme ce vecteur.
    # N'est calculé que si le wallet a assez de trades pour que le retrait
    # laisse un reste significatif (sinon indisponible, jamais un chiffre sur
    # un échantillon vidé).
    robust_trim_pct: float = 0.10
    robust_trim_min_closed_trades: int = 30

    # Courbe de santé dans le temps (15/07) : compare la performance (PnL moyen
    # par trade) de la seconde moitié chronologique des trades clôturés à la
    # première -- signal "amélioration"/"stable"/"dégradation", jamais calculé
    # sous ce minimum de trades (bruit statistique sur de petits échantillons).
    # Limite connue (revue ChatGPT, 15/07) : le découpage se fait par NOMBRE de
    # trades, pas par fenêtre calendaire -- un wallet actif 3 ans puis dormant
    # 1 an peut voir sa "tendance" dominée par une reprise récente. Documenté
    # dans smart_money.py, pas corrigé cette passe (refonte plus profonde).
    health_trend_min_closed_trades: int = 10
    health_trend_stable_band_pct: float = 0.15

    # Confiance dans le cost-basis (15/07, suite revue Gemini) : part minimale
    # de jambes valorisées par un prix d'exécution EXACT (ratio tx_hash contre
    # une jambe stablecoin) plutôt que par le repli marché OHLCV, avant de
    # lever un drapeau de confiance basse à côté du score (jamais en cachant
    # win_rate/PnL -- même doctrine que ``sample_size_sufficient``, pas le
    # masquage complet de Sortino/robust_pnl/health_trend). Valeur de départ,
    # ajustable comme tout ``WEIGHTS.*``.
    min_price_confirmation_ratio: float = 0.30

    # Défense anti-dust/scam-pool (15/07, revue Gemini) : un pool RÉSOLU par
    # GeckoTerminal mais dont la liquidité réelle (``reserve_usd``) est sous ce
    # plancher n'est pas assez fiable pour valoriser un PnL réel (pool
    # trivialement manipulable, ex. token de dust envoyé par un scammeur avec
    # une "liquidité" artificielle) -- traité comme non-priced plutôt que de
    # faire confiance à un prix de marché fabriqué. Même plancher que celui
    # déjà utilisé ailleurs dans ARIA pour le tri des candidats VC
    # (``safety_screen``/``liquidity_depth``, ~30k$) -- pas un nouveau chiffre
    # arbitraire. ``reserve_usd`` inconnu (``None``, ex. tests existants ou
    # chaîne sans cette donnée) est traité comme "faire confiance" (fail-open),
    # jamais comme liquidité nulle -- seule une valeur CONFIRMÉE sous ce
    # plancher bloque la valorisation.
    min_pool_liquidity_usd_for_pricing: float = 30_000.0

    # Biais temporel (15/07, revue ChatGPT) : fenêtre récente calculée EN PLUS
    # (jamais à la place) des métriques historiques complètes -- un wallet
    # dégradé récemment reste sinon masqué par un historique agrégé favorable.
    # Valeur de départ, ajustable comme tout `WEIGHTS.*`.
    recent_window_days: int = 90


def _load_weights() -> WalletScoringWeights:
    """Charge les poids réels depuis le fichier privé désigné par
    `ARIA_WALLET_SCORING_WEIGHTS_PATH` (YAML ou JSON — `yaml.safe_load` lit les
    deux). Repli explicite sur les valeurs par défaut du dataclass si la
    variable n'est pas définie ou si le chargement échoue de quelque façon que
    ce soit -- jamais un crash au démarrage pour un fichier privé absent."""
    path = os.environ.get(_WEIGHTS_PATH_ENV_VAR)
    if not path:
        return WalletScoringWeights()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(
            "%s défini (%s) mais illisible/invalide (%s) -- repli sur les "
            "valeurs par défaut de WalletScoringWeights.",
            _WEIGHTS_PATH_ENV_VAR,
            path,
            exc,
        )
        return WalletScoringWeights()

    if not isinstance(raw, dict):
        logger.warning(
            "%s (%s) ne contient pas un mapping clé/valeur -- repli sur les "
            "valeurs par défaut de WalletScoringWeights.",
            _WEIGHTS_PATH_ENV_VAR,
            path,
        )
        return WalletScoringWeights()

    known_fields = {f.name for f in fields(WalletScoringWeights)}
    unknown_keys = set(raw) - known_fields
    if unknown_keys:
        logger.warning(
            "%s (%s) contient des clés inconnues ignorées : %s",
            _WEIGHTS_PATH_ENV_VAR,
            path,
            sorted(unknown_keys),
        )
    overrides = {k: v for k, v in raw.items() if k in known_fields}

    try:
        weights = WalletScoringWeights(**{**WalletScoringWeights().__dict__, **overrides})
    except TypeError as exc:
        logger.warning(
            "%s (%s) contient des valeurs invalides (%s) -- repli sur les "
            "valeurs par défaut de WalletScoringWeights.",
            _WEIGHTS_PATH_ENV_VAR,
            path,
            exc,
        )
        return WalletScoringWeights()

    logger.info("Poids wallet-scoring chargés depuis %s (%s)", path, _WEIGHTS_PATH_ENV_VAR)
    return weights


WEIGHTS = _load_weights()
