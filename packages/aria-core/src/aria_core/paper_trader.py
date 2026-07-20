"""Portefeuille papier 1 M$ (mode TRADING) — le banc d'essai de la preuve.

ARIA applique ses VRAIS rapports à un portefeuille FICTIF de 1 000 000 $ : elle ouvre et
ferme des positions imaginaires au prix RÉEL du marché, émet des alertes d'achat et de
vente CLAIREMENT FICTIVES, et mesure sa performance dans le temps. Objectif : prouver la
performance sur ~20 jours AVANT tout argent réel (pacte docs/protocole-argent-reel.md).

Mode TRADING (pas VC) : horizon court, niveaux dérivés de l'analyse réelle. Gestion de
position par STOP SUIVEUR (se resserre avec le plus haut atteint, ne se relâche jamais en
dessous de l'invalidation d'origine) + PRISE DE PROFIT ÉCHELONNÉE (vend par tiers à +50 %,
+100 %, +200 % de gain plutôt qu'un tout-ou-rien à la cible) — protège les gains acquis
sans couper le potentiel restant. AUCUNE exécution on-chain, AUCUNE signature, AUCUN
argent réel — de la simulation persistée en local (aria.db). Le prix de marché est réel ;
les ordres sont fictifs.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core import momentum_funnel_log
from aria_core.paths import aria_db_path
from aria_core.services.dexscreener import token_url

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

STARTING_CAPITAL_USD = 1_000_000.0
ALLOC_PCT = 0.05          # 5 % du capital de départ par position (~50 000 $) — mode trading
MAX_POSITIONS = 15        # coussin de cash + diversification
MODE = "trading"

# 18/07 -- décision opérateur explicite : remplace le protocole 30j/7j/14j. ARIA repart à
# 1M$ CHAQUE semaine, objectif +10% (1,1M$) VALIDÉ chaque semaine -- une boucle
# d'ENTRAÎNEMENT répétée (jamais une porte de sortie unique à franchir une fois). Le reset
# a lieu que la semaine ait été validée ou non -- même philosophie diagnostique que #194
# (pousser ARIA à se tromper/apprendre plutôt que sur-filtrer par excès de prudence).
WEEKLY_CYCLE_DAYS = 7
WEEKLY_TARGET_MULTIPLIER = 1.10

# #196 -- verrou PARTAGÉ, quel que soit l'appelant (heartbeat paper_trade_cycle OU le
# service websocket momentum #196) : sans lui, deux exécutions concurrentes de
# run_paper_cycle() liraient le capital disponible/le nombre de positions ouvertes AVANT
# que l'une des deux n'écrive -- risque réel de double-allocation ou de dépassement de
# MAX_POSITIONS. Un seul cycle à la fois, jamais deux en parallèle.
_run_cycle_lock = asyncio.Lock()

# Gestion de position (stop suiveur + prise de profit échelonnée) — remplace la sortie
# binaire (100 % à la cible OU à l'invalidation) par une gestion qui protège les gains
# ACQUIS sans couper le potentiel restant.
TRAIL_STOP_PCT = 0.15         # stop suiveur PAR DÉFAUT : 15 % sous le plus haut atteint
# depuis l'entrée -- repli pour toute position SANS entry_atr_pct connu (positions
# ouvertes avant le 19/07, ou tout analyzer qui ne le fournit pas, ex. l'ancien pilote
# VC-thesis). Cf. ATR_TRAIL_MULTIPLIER ci-dessous pour le calcul adaptatif par défaut.
TP_STAGES = (0.5, 1.0, 2.0)   # paliers de gain vs entrée (+50 %, +100 %, +200 %)
TP_STAGE_FRACTION = 1.0 / 3.0  # fraction de la quantité INITIALE vendue à chaque palier
TP_QTY_EPSILON = 1e-9         # reliquat négligeable après le dernier palier -> clôture complète

# 19/07 -- stop suiveur adaptatif à la volatilité (revue croisée Gemini, confirmé "oui à
# 100%" par l'opérateur) : remplace le pourcentage fixe (TRAIL_STOP_PCT) par une largeur
# calibrée sur la volatilité RÉELLE de chaque token (ATR, ``entry_atr_pct`` calculé une
# seule fois à l'entrée par momentum_entry.py -- jamais recalculé en cours de détention,
# préserve l'effet cliquet et évite toute désynchronisation de timeframe). Multiplicateur
# 2,5x -- milieu de la fourchette standard 2-3x citée par Gemini ("2×ATR à 3×ATR : le
# standard de l'industrie"). Bornes défensives : un token quasi sans volatilité (ATR
# proche de 0) ne doit jamais produire un stop si serré qu'il se déclenche sur le
# moindre bruit (plancher 5 %) ; un token extrêmement volatil ne doit jamais produire un
# stop si large qu'il ne protège plus rien (plafond 40 %, même valeur que le plafond de
# concentration #187 -- coïncidence, pas un lien fonctionnel).
ATR_TRAIL_MULTIPLIER = 2.5
MIN_ATR_TRAIL_PCT = 0.05
MAX_ATR_TRAIL_PCT = 0.40

# 20/07 -- fraîcheur du prix à l'exécution (revue croisée Gemini, remplace un premier
# design à seuil % aveugle -- corrigé le MÊME soir après un 2e passage de revue).
# ``sig["price"]`` est capturé tout au début de ``evaluate_momentum_entry`` (avant
# honeypot/concentration holders/cascade OHLCV/jusqu'à 2 appels LLM séquentiels) --
# sur un token volatile, plusieurs secondes peuvent s'écouler avant que ce prix ne
# soit réellement utilisé pour ouvrir la position.
#
# Root cause du 1er design (rejeté) : un seuil % de mouvement aveugle (3%) traite
# TOUT mouvement comme mauvais, alors que la vraie question n'est jamais "le prix
# a-t-il bougé" mais "le trade reste-t-il bon". Un token qui pompe encore plus fort
# pendant la réflexion du LLM (exactement le profil que l'étape 3 recherche) se
# ferait rejeter par un seuil % -- adverse selection qui filtrerait les MEILLEURS
# setups, ne laissant passer que les configurations "molles" qui ne bougent pas.
#
# Fix : recalcule le R/R au prix FRAIS avec les MÊMES niveaux structurels (target/
# invalidation, Fibonacci -- fixes, jamais recalculés) que la décision d'entrée, et
# vérifie qu'il tient encore la barre que CE signal avait franchie à l'origine (2.0
# pour un achat direct, 1.0 pour un ambigu confirmé par LLM). Si le prix a monté
# mais que la cible est encore loin, le R/R reste bon -> exécution. Si le prix a
# légèrement baissé sans toucher l'invalidation, le R/R s'améliore mécaniquement
# (« rabais » sur la thèse) -> exécution. Rejette seulement un setup RÉELLEMENT
# dégradé (prix trop proche de la cible ou de l'invalidation), jamais un mouvement
# simplement présent.
def _fresh_rr(fresh_price: float | None, target: float | None, invalidation: float | None) -> float | None:
    """R/R recalculé au prix frais. ``None`` si la config ne permet pas un calcul
    valide (donnée absente, ou le setup est déjà résolu -- prix au-delà de la cible
    ou déjà sous l'invalidation, plus un R/R à mesurer à ce stade)."""
    if not fresh_price or fresh_price <= 0 or not target or not invalidation:
        return None
    if fresh_price <= invalidation or fresh_price >= target:
        return None
    return (target - fresh_price) / (fresh_price - invalidation)


def _execution_rr_still_valid(signal_rr: float | None, fresh_rr: float | None) -> bool:
    """``True`` si ``fresh_rr`` tient encore la barre que le signal ORIGINAL avait
    franchie -- 2.0 (achat direct) si ``signal_rr`` l'avait déjà atteint, sinon 1.0
    (le plancher ambigu, franchi via confirmation LLM). ``fresh_rr is None`` ->
    fail-closed (jamais une exécution sans donnée pour juger)."""
    if fresh_rr is None:
        return False
    from aria_core.momentum_entry import _RR_AMBIGUOUS_FLOOR, _RR_MIN_FOR_DIRECT_BUY

    bar = _RR_MIN_FOR_DIRECT_BUY if (signal_rr and signal_rr >= _RR_MIN_FOR_DIRECT_BUY) else _RR_AMBIGUOUS_FLOOR
    return fresh_rr >= bar


# 20/07 -- Formule B, discipline de sortie VC (``strategy="vc_thesis"``, revue croisée
# Gemini, décision opérateur explicite "des maintenant") : distincte de la discipline
# momentum ci-dessus (stop suiveur ATR + TP par tiers), réservée aux positions qui
# viendraient un jour de la poche VC 85% (``safety_screen``/``vc_analysis``, PAS le
# pipeline momentum actif sur le test 1M$ en cours -- ``strategy`` par défaut reste
# "momentum" pour toute position/tout appelant existant, comportement inchangé tant que
# rien ne source explicitement du "vc_thesis"). Points affinés par 3 allers-retours
# avec Gemini (relayés par l'opérateur) :
#   1. Paradoxe entrée/sortie résolu STRUCTURELLEMENT : ``strategy`` est dérivé de la
#      pipeline d'ENTRÉE réelle (momentum_entry.py -> "momentum" ; l'ancien
#      _default_analyzer, qui vient de safety_screen/vc_analysis -- déjà fondamentaux +
#      sécurité, JAMAIS Fibonacci/RSI -- -> "vc_thesis"), jamais un flag indépendant
#      qu'on pourrait mal assortir à un token purement spéculatif.
#   2. Invalidation FONDAMENTALE plutôt que technique : un niveau de support graphique
#      sur une paire jeune et peu liquide peut être traversé par une simple mèche de
#      volatilité nocturne. La liquidité du pool (donnée déjà en main à chaque cycle,
#      aucun appel réseau supplémentaire) est un signal plus robuste -- un pool ne perd
#      pas 50% de sa liquidité sur un seul trade isolé, seulement sur un vrai retrait/rug.
#      30 000$ = même plancher absolu que safety_screen.py (poche VC 85%), pas un chiffre
#      inventé pour l'occasion.
#   3. "Take Seed" (pas de TP par tiers mécanique) : une SEULE sortie partielle, dès que
#      la position double (2x), qui récupère EXACTEMENT la mise initiale -- sécurise le
#      capital pour le redéployer, laisse le reste (moonbag) courir SANS stop vers la
#      cible complète de la thèse (Power Law du VC : un x50 paie pour tous les zéros).
VC_MIN_LIQUIDITY_FLOOR_USD = 30_000.0
VC_LIQUIDITY_DROP_INVALIDATION_PCT = 0.5
VC_TAKE_SEED_MULTIPLE = 2.0


def _effective_trail_pct(entry_atr_pct: float | None) -> float:
    """Largeur du stop suiveur pour UNE position : ``TRAIL_STOP_PCT`` fixe si
    ``entry_atr_pct`` est absent/invalide (comportement historique inchangé), sinon
    ``ATR_TRAIL_MULTIPLIER * entry_atr_pct`` borné à ``[MIN_ATR_TRAIL_PCT,
    MAX_ATR_TRAIL_PCT]``."""
    if entry_atr_pct is None or entry_atr_pct <= 0:
        return TRAIL_STOP_PCT
    return max(MIN_ATR_TRAIL_PCT, min(MAX_ATR_TRAIL_PCT, ATR_TRAIL_MULTIPLIER * entry_atr_pct))


def _effective_tp_stages(target_price: float | None, entry_price: float | None) -> tuple[float, ...]:
    """Paliers de prise de profit pour UNE position -- corrige un vrai défaut trouvé en
    revue croisée (19/07, Gemini round 5) : le R/R calculé à l'entrée (``entry_signals.
    detect_entry``) s'appuie sur un ``target`` TECHNIQUE réel (le haut de la fenêtre
    golden pocket -- le niveau que le setup visait). Mais l'ancienne gestion de sortie
    ignorait totalement ce niveau : TP1 tombait toujours sur un pourcentage FIXE
    (``TP_STAGES[0]``, +50%), sans rapport avec la cible qui avait justifié l'entrée --
    un setup au R/R élevé mais dont la cible technique était plus proche (ex. +25%)
    pouvait se retourner et toucher le stop suiveur sans qu'aucun profit n'ait été pris
    au niveau réellement visé.

    TP1 s'ancre désormais sur ``target_price`` (converti en % de gain depuis
    ``entry_price``) quand les deux sont connus et cohérents (``target_price >
    entry_price``) -- sinon repli sur ``TP_STAGES`` inchangé (ex. positions ouvertes
    avant ce correctif, ou tout analyzer qui ne fournit pas de cible technique, comme
    l'ancien pilote VC-thesis dormant).

    TP2/TP3 (19/07, revue croisée Gemini round 6) -- première version : des crans FIXES
    au-dessus de TP1 (+50pt/+100pt, même écart que ``TP_STAGES``). Défaut réel trouvé par
    Gemini : ces crans restaient des points fixes en % du capital, jamais proportionnels
    à l'AMPLEUR du setup lui-même -- un TP1 modeste (setup serré) gardait quand même un
    TP2 très loin (souvent au-delà de ce qu'un token atteint avant de se retourner),
    laissant filer un profit déjà acquis. Remplacé par des MULTIPLES de la distance
    entrée->TP1 elle-même (``reward_distance``) : TP2 = 2x cette distance, TP3 = 3x --
    dynamique de bout en bout, un setup ambitieux (TP1 loin) obtient des paliers 2/3
    proportionnellement plus loin, un setup serré (TP1 proche) les obtient proportionnellement
    plus proches, jamais un point fixe arbitraire. Séquence strictement croissante par
    construction (``stage1_pct > 0`` garanti par le test ci-dessus)."""
    if target_price and entry_price and target_price > entry_price:
        stage1_pct = target_price / entry_price - 1.0
        return (stage1_pct, 2.0 * stage1_pct, 3.0 * stage1_pct)
    return TP_STAGES


def _apply_regime_to_tp_stages(
    stages: tuple[float, ...], effective_regime: str | None,
) -> tuple[float, ...]:
    """Transforme les paliers de prise de profit selon le méta-régime EFFECTIF déjà
    ratché pour cette position (cf. ``market_sentiment.more_cautious_meta_regime``,
    jamais le régime courant brut -- une position ne redevient jamais plus permissive
    qu'à son pire moment observé). Revue croisée Gemini, feu vert opérateur explicite
    (20/07, "200k mais à garder à l'œil") :

    - Peur : écrase le 3e palier -- sortie ultra-rapide, TOUT le reliquat se vend au
      niveau de l'ancien TP2 (verrouille les gains avant un retracement pendant que la
      liquidité se regroupe sur les gros actifs). ``stages[:2]`` suffit : la boucle
      appelante traite déjà tout dépassement du DERNIER palier comme une clôture
      complète (``is_last_stage``), aucune logique supplémentaire nécessaire.
    - Euphorie : neutralise le 3e palier (``float("inf")``, jamais atteignable) --
      TP1/TP2 continuent de prendre leurs tiers normalement, mais le dernier tiers
      devient un moon bag PUR, guidé uniquement par le stop suiveur ATR, jamais forcé
      à la vente par un palier mécanique ("elle va chercher les x10").
    - Neutre/inconnu : ``stages`` inchangé -- comportement historique par défaut.

    Si ``stages`` a moins de 3 éléments (ne devrait jamais arriver, ``TP_STAGES``/
    ``_effective_tp_stages`` en fournissent toujours 3) -> inchangé, jamais un index
    hors limites."""
    if len(stages) < 3:
        return stages
    if effective_regime == "peur":
        return stages[:2]
    if effective_regime == "euphorie":
        return (stages[0], stages[1], float("inf"))
    return stages


# 20/07 -- Breakeven Hard Floor (revue croisée Gemini, "Piste B" validée par
# l'opérateur) : mécanisme SÉPARÉ de la confirmation temporelle du plus-haut
# ci-dessous, répond à l'angle mort qu'elle laisse ouvert. `_advance_high_water`
# abandonne ENTIÈREMENT une candidature de plus-haut si le prix retombe sous le
# dernier plus-haut CONFIRMÉ avant d'avoir tenu HIGH_WATER_CONFIRMATION_SECONDS
# (75s, par design -- aucun crédit partiel) : un pump-puis-dump rapide (ex. +50% en
# moins de 75s) laisse donc le stop calé sur son niveau D'AVANT le pic, alors que la
# position a réellement flirté avec un gain significatif.
#
# Ce filet est INDÉPENDANT du ratchet high_water -- il lit le prix INSTANTANÉ de
# CHAQUE cycle (jamais le plus-haut confirmé), et dès qu'il touche, même un seul
# cycle, un seuil "flash" calibré sur la cible technique du setup, le stop est
# IRRÉVOCABLEMENT remonté au point mort (`entry_price`) -- ce verrou ne redescend
# JAMAIS, même si le prix retombe aussitôt sous le seuil qui l'a déclenché.
#
# Seuil = BREAKEVEN_FLOOR_TP1_RATIO de la distance entrée->TP1 (la cible technique
# déjà utilisée par _effective_tp_stages), avec un plancher absolu BREAKEVEN_FLOOR_
# MIN_PCT pour ne jamais déclencher sur un setup au TP1 très serré, où une fraction
# de sa distance serait plus étroite que le bruit de marché normal.
BREAKEVEN_FLOOR_TP1_RATIO = 0.5
BREAKEVEN_FLOOR_MIN_PCT = 0.08


def _breakeven_floor_threshold(target_price: float | None, entry_price: float | None) -> float | None:
    """Seuil de gain (fraction, ex. ``0.08`` = +8%) au-delà duquel le point mort se
    verrouille -- ``None`` si aucun prix d'entrée valide (jamais un calcul sur une
    donnée absente)."""
    if not entry_price or entry_price <= 0:
        return None
    stage1_pct = _effective_tp_stages(target_price, entry_price)[0]
    return max(BREAKEVEN_FLOOR_TP1_RATIO * stage1_pct, BREAKEVEN_FLOOR_MIN_PCT)


# 20/07 -- confirmation TEMPORELLE du plus-haut (remplace le plafond de vitesse
# HIGH_WATER_JUMP_CAP_MULTIPLE du 19/07, revue croisée Gemini round 7). Le plafond de
# vitesse avait lui-même un vrai défaut, trouvé par Gemini : brider l'AMPLEUR du saut
# autorisé par cycle pénalise aussi bien une mèche qu'un vrai mouvement parabolique
# légitime (une vraie bougie de découverte de prix peut faire +50% en un seul cycle) --
# la largeur du mouvement n'est structurellement PAS le bon signal pour distinguer les
# deux. La DURÉE l'est : une mèche isolée (bot d'arbitrage, manipulation ponctuelle sur
# pool peu liquide) ne dure jamais plus de quelques secondes/dizaines de secondes ; un
# vrai mouvement parabolique, si. Un nouveau plus-haut n'est donc ratché dans le stop
# suiveur qu'après être resté au-dessus du dernier plus-haut CONFIRMÉ pendant au moins
# HIGH_WATER_CONFIRMATION_SECONDS -- son AMPLEUR n'est jamais plafonnée (une fois
# confirmé, le plus-haut RÉEL de toute la fenêtre est ratché d'un coup, pas juste le
# prix de l'instant de confirmation).
#
# Durée en SECONDES, pas en nombre de cycles -- le pipeline momentum a deux boucles de
# gestion de position à des cadences différentes (heartbeat ~15 min, WebSocket ~30s,
# #196) : "2 cycles" n'a aucun sens commun entre les deux (30s vs 30 min), une durée
# absolue si. 75s = milieu de la fourchette 60-90s proposée par la revue croisée
# (assez pour laisser un bot d'arbitrage se désengager, assez court pour ne pas
# retarder la confirmation d'un vrai pump de façon perceptible à l'échelle des cycles
# de gestion).
HIGH_WATER_CONFIRMATION_SECONDS = 75


def _advance_high_water(
    confirmed_high_water: float,
    pending_high_water: float | None,
    pending_since: str | None,
    price: float,
    now: datetime,
) -> tuple[float, float | None, str | None]:
    """``(nouveau plus-haut confirmé, plus-haut en attente, horodatage de la
    candidature)`` pour UN cycle. Corrige un vrai risque (19/07, Gemini round 6) : ARIA
    relit un prix SPOT (DexScreener, dernière transaction) à chaque cycle pour la
    gestion de position -- une seule lecture instantanée anormale (mèche, bot
    d'arbitrage, erreur de slippage d'un gros acheteur) peut figer un plus-haut fictif
    dans ``high_water`` -- le ratchet ne redescend JAMAIS, donc le stop suiveur
    resterait durablement calé sur un prix qui n'a peut-être existé qu'un instant.

    Mécanique : tant que ``price`` reste au-dessus du dernier plus-haut CONFIRMÉ, une
    candidature reste "ouverte" (``pending_high_water``/``pending_since``), mise à jour
    au RÉEL maximum observé pendant qu'elle est ouverte. Dès qu'elle a tenu au moins
    ``HIGH_WATER_CONFIRMATION_SECONDS``, elle est confirmée d'un coup (le plus-haut
    RÉEL de toute la fenêtre, pas juste le prix de cet instant) et le plus-haut confirmé
    ratche. Si ``price`` retombe SOUS le dernier plus-haut confirmé à un moment
    quelconque, la candidature en cours est abandonnée entièrement (preuve qu'elle
    n'était pas soutenue) -- une nouvelle candidature repart de zéro si le prix
    redépasse plus tard.

    N'affecte QUE l'état ``high_water`` (le ratchet) -- la comparaison de déclenchement
    du stop utilise toujours le ``price`` RÉEL, jamais une valeur en attente de
    confirmation (une lecture aberrante à la BAISSE déclenche donc bien le stop si elle
    franchit le seuil -- choix délibéré, plus prudent pour du capital simulé de réagir à
    un signal ambigu que de l'ignorer)."""
    if price <= confirmed_high_water:
        return confirmed_high_water, None, None

    if pending_high_water is None or not pending_since:
        return confirmed_high_water, price, now.isoformat()

    pending_high_water = max(pending_high_water, price)
    try:
        elapsed = (now - datetime.fromisoformat(pending_since)).total_seconds()
    except ValueError:
        return confirmed_high_water, price, now.isoformat()

    if elapsed >= HIGH_WATER_CONFIRMATION_SECONDS:
        return pending_high_water, None, None
    return confirmed_high_water, pending_high_water, pending_since

# 17/07 -- demande opérateur explicite : réduire de moitié le bruit Telegram de l'alerte de
# suivi périodique (#197, une par cycle heartbeat -- ~15 min -- tant qu'une position reste
# ouverte). Fenêtre glissante par le TEMPS écoulé (pas un compteur de cycles) : robuste si la
# cadence heartbeat change un jour sans qu'il faille retoucher cette constante.
TRACKING_ALERT_MIN_INTERVAL_MINUTES = 30

# 17/07 -- demande opérateur explicite après une perte réelle (BRIAN rachetée 2 fois de
# suite après deux stop suiveur, -18 561 $ cumulés sur 3 entrées) : re-achat bloqué par
# défaut sauf signal EXTRÊME. Assoupli le 19/07 (décision opérateur explicite, suite à
# l'observation directe du portefeuille réel) : "achat unique pour les positions EN COURS
# [seulement] -- ça ne me dérange pas de rouvrir une position si il n'en existe pas déjà
# une, si un nouveau point d'entrée se profile". La seule protection contre une double
# détention reste ``has_open`` (jamais deux positions SIMULTANÉES sur le même contrat) --
# une fois clôturée, un contrat redevient un candidat comme un autre, même barre que
# n'importe quelle entrée normale (déjà passée avant d'atteindre ce point du pipeline).
# Le wash-trading/décoy type BRIAN reste couvert par deux gardes DURS distincts et non
# retirés ici (`momentum_blacklist.py`, plafond ratio volume24h/liquidité) -- construits
# spécifiquement pour ce pattern, jamais dépendants de ce gate de re-entrée.

_POS_FIELDS = (
    "id", "contract", "symbol", "cost_usd", "entry_price", "qty",
    "target_price", "invalidation_price", "opened_at", "status",
    "exit_price", "closed_at", "pnl_usd", "pnl_pct", "close_reason",
    "high_water_price", "tp_stage_hit", "initial_qty", "realized_pnl_partial",
    "category", "entry_security_json", "chain", "thesis", "close_notes",
    "entry_atr_pct", "pending_high_water", "pending_high_water_since",
    "strategy", "entry_liquidity_usd", "breakeven_locked", "entry_regime",
)

_ADDED_COLUMNS = [
    ("high_water_price", "REAL"),
    ("tp_stage_hit", "INTEGER NOT NULL DEFAULT 0"),
    ("initial_qty", "REAL"),
    ("realized_pnl_partial", "REAL NOT NULL DEFAULT 0"),
    # #187 -- surveillance continue + plafond de concentration (voir paper_trader_risk.py)
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("entry_security_json", "TEXT"),
    # #194 -- pivot momentum multi-chaînes, chaque position se souvient de sa chaîne
    # (Base historiquement implicite -- défaut 'base' pour les positions déjà ouvertes)
    ("chain", "TEXT NOT NULL DEFAULT 'base'"),
    # #197 (15/07) -- VCResult.these (analyse VC complète, déjà calculée par
    # analyze_vc_with_context) persistée à l'ouverture -- avant ce chantier, jamais
    # transmise ni sauvegardée : seuls les niveaux chiffrés (prix/cible/invalidation)
    # survivaient. Objectif opérateur explicite : la session cloud doit pouvoir vérifier
    # après coup, en base, POURQUOI ARIA est entrée -- pas seulement à quel prix.
    ("thesis", "TEXT"),
    # 17/07 -- demande opérateur explicite : chaque VENTE (pas seulement l'achat) doit se
    # justifier avec des chiffres concrets, pour maximiser la donnée exploitable à des fins
    # de calibration -- pas juste un tag court ("stop suiveur"/"invalidation") déjà utilisé
    # par du code/des tests existants (jamais touché ici), un texte séparé qui explique le
    # POURQUOI avec les niveaux réels. Alimenté à chaque clôture totale ET à chaque prise de
    # profit partielle (dans ce dernier cas, sur la ligne encore ouverte -- dernière note en
    # date, pas un historique cumulé).
    ("close_notes", "TEXT"),
    # 19/07 -- ATR (Average True Range) en % du prix d'entrée, calculé UNE SEULE FOIS à
    # l'ouverture par momentum_entry.evaluate_momentum_entry (mêmes candles que la
    # décision d'entrée -- jamais recalculé en cours de détention). ``NULL`` pour toute
    # position ouverte avant ce chantier, ou par un analyzer qui ne le fournit pas (ex.
    # l'ancien pilote VC-thesis) -- le stop suiveur retombe alors sur TRAIL_STOP_PCT
    # (pourcentage fixe), jamais une valeur inventée.
    ("entry_atr_pct", "REAL"),
    # 20/07 -- confirmation temporelle du plus-haut (remplace le clamp de vitesse
    # HIGH_WATER_JUMP_CAP_MULTIPLE, cf. _advance_high_water) : un nouveau plus-haut
    # candidat, pas encore confirmé (le prix doit rester au-dessus du dernier plus-haut
    # CONFIRMÉ pendant HIGH_WATER_CONFIRMATION_SECONDS avant de ratcher). NULL = aucune
    # candidature en cours (comportement par défaut, jamais une valeur inventée).
    ("pending_high_water", "REAL"),
    ("pending_high_water_since", "TEXT"),
    # 20/07 -- Formule B (discipline de sortie VC, cf. VC_MIN_LIQUIDITY_FLOOR_USD/
    # VC_LIQUIDITY_DROP_INVALIDATION_PCT/VC_TAKE_SEED_MULTIPLE ci-dessus). "momentum" par
    # défaut -- comportement inchangé (stop suiveur ATR + TP par tiers) pour TOUTE
    # position déjà ouverte ou toute nouvelle position dont l'analyzer ne fournit pas ce
    # champ explicitement. entry_liquidity_usd : liquidité du pool à l'entrée, réutilise
    # pool_liquidity_usd déjà transmis pour le sizing (aucun nouvel appel réseau) --
    # référence pour détecter une chute structurelle en cours de détention.
    ("strategy", "TEXT NOT NULL DEFAULT 'momentum'"),
    ("entry_liquidity_usd", "REAL"),
    # 20/07 -- Breakeven Hard Floor (cf. _breakeven_floor_threshold ci-dessus). 0/1 --
    # une fois passé à 1, ne redescend JAMAIS (verrou irrévocable, vérifié par test).
    # 0 par défaut, jamais une valeur inventée pour une position ouverte avant ce
    # chantier (comportement inchangé : le point mort ne se verrouille pas tant que le
    # prix n'a pas réellement touché le seuil flash APRÈS l'activation de ce correctif).
    ("breakeven_locked", "INTEGER NOT NULL DEFAULT 0"),
    # 20/07 -- Regime Switch dynamique (cf. market_sentiment.resolve_meta_regime).
    # Méta-régime macro AU MOMENT DE L'OUVERTURE -- ``NULL`` pour toute position
    # ouverte avant ce chantier ou tout analyzer qui ne le fournit pas (ex. l'ancien
    # pilote VC-thesis) -- traité comme "neutre" par le ratchet en gestion, jamais un
    # régime inventé.
    ("entry_regime", "TEXT"),
]

# 19/07 -- migration à chaud DÉDIÉE pour paper_position_archive (voir _ensure_tables) --
# cette table était créée complète dès l'origine (jamais de colonne ajoutée après coup
# avant ce jour), doit maintenant rester en parité EXACTE avec _POS_FIELDS/_ADDED_COLUMNS
# ci-dessus sur toute base déjà existante.
_ARCHIVE_ADDED_COLUMNS = [
    ("entry_atr_pct", "REAL"),
    ("pending_high_water", "REAL"),
    ("pending_high_water_since", "TEXT"),
    ("strategy", "TEXT NOT NULL DEFAULT 'momentum'"),
    ("entry_liquidity_usd", "REAL"),
    ("breakeven_locked", "INTEGER NOT NULL DEFAULT 0"),
    ("entry_regime", "TEXT"),
]

# Migration à chaud de `paper_state` (#186, 15/07) -- même patron idempotent que
# `_ADDED_COLUMNS` ci-dessus. Plus haut d'équité jamais atteint, utilisé par
# risk_guard.py pour le coupe-circuit de drawdown (jamais NULL après le premier
# appel de `get_equity_high_water_mark` -- initialisé au capital de départ).
_STATE_ADDED_COLUMNS = [
    ("equity_high_water_mark", "REAL"),
    # 17/07 -- horodatage de la dernière alerte de suivi périodique envoyée (voir
    # TRACKING_ALERT_MIN_INTERVAL_MINUTES) -- NULL tant qu'aucune n'a encore été envoyée.
    ("last_tracking_alert_at", "TEXT"),
    # 18/07 -- décision opérateur explicite : remplace le protocole 30j/7j/14j par une
    # boucle d'ENTRAÎNEMENT hebdomadaire (voir WEEKLY_CYCLE_DAYS/run_weekly_reset ci-dessous).
    # Numéro du cycle courant, incrémenté à chaque reset -- jamais NULL après le premier
    # appel de _ensure_tables (démarre à 1, même valeur par défaut que la colonne SQL).
    ("cycle_number", "INTEGER NOT NULL DEFAULT 1"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since(opened_at: str | None) -> float | None:
    """Durée de détention en heures depuis ``opened_at`` (ISO), pour les notes de sortie
    (17/07) -- ``None`` si absent/invalide, jamais une valeur inventée."""
    if not opened_at:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 3600.0
    except ValueError:
        return None


def _duration_phrase(opened_at: str | None) -> str:
    hours = _hours_since(opened_at)
    if hours is None:
        return "durée de détention inconnue"
    return f"détenue {hours:.1f}h" if hours < 24 else f"détenue {hours / 24:.1f}j"


def _num(v) -> float | None:
    """Parse défensif d'un prix éventuellement '$1,234.5' → float, ou None."""
    try:
        if v is None:
            return None
        s = str(v).replace("$", "").replace(",", "").strip().split()[0]
        return float(s)
    except (ValueError, IndexError, TypeError):
        return None


def _row_to_pos(row: tuple) -> dict:
    return dict(zip(_POS_FIELDS, row))


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_position (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                symbol TEXT,
                cost_usd REAL NOT NULL,
                entry_price REAL NOT NULL,
                qty REAL NOT NULL,
                target_price REAL,
                invalidation_price REAL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                exit_price REAL,
                closed_at TEXT,
                pnl_usd REAL,
                pnl_pct REAL,
                close_reason TEXT,
                high_water_price REAL,
                tp_stage_hit INTEGER NOT NULL DEFAULT 0,
                initial_qty REAL,
                realized_pnl_partial REAL NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '',
                entry_security_json TEXT,
                chain TEXT NOT NULL DEFAULT 'base',
                thesis TEXT,
                close_notes TEXT
            )
            """
        )
        # Migration à chaud : ajoute les colonnes de gestion de position aux DB existantes
        # (SQLite ne les crée pas si la table préexiste). Idempotent, non destructif.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_position)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE paper_position ADD COLUMN {name} {ddl}")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                starting_capital REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        state_existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_state)")).fetchall()
        }
        for name, ddl in _STATE_ADDED_COLUMNS:
            if name not in state_existing:
                await db.execute(f"ALTER TABLE paper_state ADD COLUMN {name} {ddl}")
        await db.execute(
            "INSERT OR IGNORE INTO paper_state (id, starting_capital, created_at) VALUES (1, ?, ?)",
            (STARTING_CAPITAL_USD, _now()),
        )
        # 18/07 -- verdict par semaine (une ligne par cycle clos par run_weekly_reset).
        # Jamais de DELETE/UPDATE destructif ailleurs que le upsert du reset lui-même --
        # c'est le vrai track record du protocole hebdo, doit survivre indéfiniment.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_weekly_cycle (
                cycle_number INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                target_equity REAL NOT NULL,
                start_capital REAL NOT NULL,
                end_equity REAL,
                return_pct REAL,
                validated INTEGER,
                closed_trades INTEGER,
                win_rate REAL
            )
            """
        )
        # 18/07 -- historique COMPLET jamais détruit : contrairement à reset_portfolio()
        # (DROP TABLE, destructif par design), run_weekly_reset() archive ICI chaque
        # position de la semaine (ouverte-puis-force-close comprise) avant de vider la
        # table live -- le track record hebdo reste consultable pour toujours. Types
        # copiés un-à-un depuis paper_position (jamais générés dynamiquement -- l'affinité
        # TEXT de SQLite convertirait silencieusement un nombre en chaîne si le mapping
        # se trompait), colonnes dans le même ordre que _POS_FIELDS pour que l'INSERT...
        # SELECT de run_weekly_reset reste un simple alignement positionnel.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_position_archive (
                archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_number INTEGER NOT NULL,
                id INTEGER,
                contract TEXT,
                symbol TEXT,
                cost_usd REAL,
                entry_price REAL,
                qty REAL,
                target_price REAL,
                invalidation_price REAL,
                opened_at TEXT,
                status TEXT,
                exit_price REAL,
                closed_at TEXT,
                pnl_usd REAL,
                pnl_pct REAL,
                close_reason TEXT,
                high_water_price REAL,
                tp_stage_hit INTEGER,
                initial_qty REAL,
                realized_pnl_partial REAL,
                category TEXT,
                entry_security_json TEXT,
                chain TEXT,
                thesis TEXT,
                close_notes TEXT,
                entry_atr_pct REAL
            )
            """
        )
        # 19/07 -- même patron de migration à chaud que paper_position/paper_state
        # ci-dessus : cette table est créée COMPLÈTE dès la première fois (pas de
        # colonnes ajoutées incrémentalement avant ce jour), donc jamais eu besoin
        # d'une liste de colonnes additives -- mais _POS_FIELDS (partagé avec
        # paper_position pour l'INSERT...SELECT positionnel de run_weekly_reset)
        # vient de gagner entry_atr_pct, et cette table doit rester en parité EXACTE
        # avec _POS_FIELDS sur toute base déjà existante (le CREATE TABLE IF NOT
        # EXISTS ci-dessus ne touche jamais une table déjà créée -- bug réel trouvé
        # en faisant tourner la suite complète : sqlite3.OperationalError sur
        # run_weekly_reset() dès que la table archive préexistait sans cette
        # colonne).
        archive_existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_position_archive)")).fetchall()
        }
        for name, ddl in _ARCHIVE_ADDED_COLUMNS:
            if name not in archive_existing:
                await db.execute(f"ALTER TABLE paper_position_archive ADD COLUMN {name} {ddl}")
        await db.commit()


async def starting_capital() -> float:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT starting_capital FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    return float(row[0]) if row else STARTING_CAPITAL_USD


async def reset_portfolio(starting: float = STARTING_CAPITAL_USD, *, created_at: str | None = None) -> None:
    """Repart à neuf (nouveau run de preuve). DESTRUCTIF : à déclencher explicitement par
    l'opérateur, jamais par une boucle automatique."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DROP TABLE IF EXISTS paper_position")
        await db.execute("DROP TABLE IF EXISTS paper_state")
        await db.commit()
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET starting_capital = ?, created_at = ?, equity_high_water_mark = ? WHERE id = 1",
            (starting, created_at or _now(), starting),
        )
        await db.commit()


async def get_equity_high_water_mark() -> float:
    """Plus haut d'équité jamais atteint (#186, coupe-circuit de drawdown). Initialisé
    au capital de départ tant qu'aucune équité supérieure n'a encore été observée --
    jamais NULL après cet appel (les DB migrées ont la colonne mais pas la valeur)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT equity_high_water_mark FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return await starting_capital()


async def set_equity_high_water_mark(value: float) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET equity_high_water_mark = ? WHERE id = 1", (value,),
        )
        await db.commit()


async def get_last_tracking_alert_at() -> str | None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_tracking_alert_at FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_last_tracking_alert_at(value: str) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET last_tracking_alert_at = ? WHERE id = 1", (value,),
        )
        await db.commit()


async def get_open_positions() -> list[dict]:
    await _ensure_tables()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE status = 'open' ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def get_closed_positions(limit: int = 500) -> list[dict]:
    await _ensure_tables()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            # `id DESC` en tie-break (#186) : `closed_at` (résolution microseconde) peut
            # coïncider entre deux clôtures rapprochées dans un même tick/test -- l'ordre
            # d'insertion reste le signal fiable de récence dans ce cas, notamment pour le
            # comptage de pertes consécutives de risk_guard.evaluate_portfolio_risk.
            f"SELECT {cols} FROM paper_position WHERE status = 'closed' ORDER BY closed_at DESC, id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def list_positions_for_contract(contract: str, limit: int = 100) -> list[dict]:
    """Toutes les positions papier (ouvertes + clôturées) d'un contrat, récentes d'abord.

    Alimente le « dossier par token ». La clé contrat est stockée EN MINUSCULES pour
    Base/Robinhood mais dans sa CASSE D'ORIGINE pour Solana (18/07, bug réel : un
    ``.lower()`` uniforme corrompait toute adresse base58 avant qu'elle atteigne
    GoPlus/RugCheck -- cf. ``momentum_entry.normalize_contract_case``/``open_position``
    ci-dessous). Cette fonction ne connaît pas la chaîne de l'appelant -- recherche
    donc insensible à la casse (``LOWER(contract) = ?``) plutôt que de supposer une
    normalisation qu'elle ne peut pas reproduire elle-même.
    """
    await _ensure_tables()
    contract = (contract or "").lower()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE LOWER(contract) = ? ORDER BY id DESC LIMIT ?",
            (contract, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def _get_open(contract: str) -> dict | None:
    """Recherche insensible à la casse -- même raison que ``list_positions_for_contract``
    ci-dessus (pas de paramètre ``chain`` ici pour reconstruire la vraie normalisation)."""
    contract = (contract or "").lower()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE LOWER(contract) = ? AND status = 'open' LIMIT 1",
            (contract,),
        ) as cur:
            row = await cur.fetchone()
    return _row_to_pos(row) if row else None


async def has_open(contract: str) -> bool:
    return (await _get_open(contract)) is not None


async def _has_prior_close(contract: str) -> bool:
    """Le contrat a-t-il déjà eu AU MOINS une position clôturée (gain ou perte, peu
    importe la raison -- stop suiveur, invalidation, palier de profit, re-scan sécurité) ?
    Réutilise ``list_positions_for_contract`` (aucune requête dupliquée) -- distinct de
    ``has_open`` qui ne regarde que le présent, jamais l'historique."""
    positions = await list_positions_for_contract(contract)
    return any(p["status"] == "closed" for p in positions)


async def cash_available() -> float:
    """Cash = capital de départ − coût des positions ouvertes + P&L réalisé des clôturées
    + P&L réalisé des prises de profit PARTIELLES sur des positions encore ouvertes (le
    coût restant de ``cost_usd`` est déjà réduit proportionnellement par ``reduce_position``,
    donc seul le profit au-delà de la base de coût doit être rajouté ici)."""
    start = await starting_capital()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(realized_pnl_partial), 0) "
            "FROM paper_position WHERE status = 'open'"
        ) as cur:
            open_cost, open_partial = await cur.fetchone()
        async with db.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_position WHERE status = 'closed'"
        ) as cur:
            realized = (await cur.fetchone())[0] or 0.0
    return float(start) - float(open_cost or 0.0) + float(realized) + float(open_partial or 0.0)


async def open_position(
    contract: str,
    symbol: str,
    entry_price: float,
    *,
    target_price: float | None = None,
    invalidation_price: float | None = None,
    alloc_usd: float | None = None,
    category: str = "",
    entry_security_json: str = "",
    chain: str = "base",
    thesis: str | None = None,
    pool_liquidity_usd: float | None = None,
    entry_atr_pct: float | None = None,
    strategy: str = "momentum",
    entry_regime: str | None = None,
) -> dict | None:
    """Ouvre une position FICTIVE au prix d'entrée réel. Refuse si déjà ouverte, plafond de
    positions atteint, coupe-circuit de risque armé, prix invalide, cash insuffisant, ou
    plafond de concentration de ``category`` dépassé sans place suffisante (#187, voir
    paper_trader_risk.py -- l'alloc est RÉDUITE pour tenir sous le plafond quand la place
    restante est significative, sinon la position est skippée). ``chain`` (#194, pivot
    momentum multi-chaînes) persiste la chaîne d'origine pour que la gestion ultérieure de
    la position (prix, re-scan) sache quelle chaîne interroger. ``thesis`` (#197, 15/07) :
    raisonnement VC complet (``VCResult.these``) persisté tel quel -- pourquoi ARIA entre,
    pas seulement à quel prix. La persistance prime sur l'affichage Telegram : sauvegardée
    ICI, indépendamment de tout notifier/topic configuré ou non. Retourne la position ou
    None.

    Casse du contrat (18/07, bug réel) : préservée pour Solana (base58, la casse fait
    partie de la valeur), lowercase pour Base/Robinhood (hex EVM, comme avant) --
    ``momentum_entry.normalize_contract_case``. Stocker une adresse Solana corrompue
    aurait rendu tout re-scan/prix ultérieur (``paper_trader_risk.py``) inopérant sur
    la vraie chaîne, silencieusement.

    ``pool_liquidity_usd`` (19/07, revue croisée Gemini) : liquidité RÉELLE du pool
    ciblé -- utilisée pour réduire ``alloc`` si l'impact de prix de CET ordre sur CE
    pool ferait tomber le R/R structurel sous son plancher (``risk_guard.
    cap_alloc_to_price_impact``). ``None`` par défaut -- comportement inchangé pour
    tout appelant qui ne le fournit pas (ex. l'ancien pilote VC-thesis, dormant).

    ``entry_atr_pct`` (19/07, revue croisée Gemini) : ATR (volatilité) en % du prix
    d'entrée, calculé une seule fois à l'ouverture -- persisté tel quel, utilisé par la
    gestion de position (stop suiveur adaptatif) plutôt que ``TRAIL_STOP_PCT`` fixe.
    ``None`` par défaut -- comportement inchangé (stop suiveur à pourcentage fixe) pour
    tout appelant qui ne le fournit pas."""
    await _ensure_tables()
    from aria_core.momentum_entry import normalize_contract_case

    contract = normalize_contract_case(contract, chain)
    if not contract or not entry_price or entry_price <= 0:
        return None
    if await has_open(contract):
        return None
    if len(await get_open_positions()) >= MAX_POSITIONS:
        return None

    # #186 -- chokepoint de sécurité en profondeur : vérifié ICI (pas seulement dans
    # run_paper_cycle) pour couvrir TOUT appelant présent ou futur (ex. commande manuelle,
    # futur pilote de capital réel réutilisant cette même fonction), pas seulement le cycle
    # heartbeat actuel.
    from aria_core import risk_guard

    blocked, reason = risk_guard.blocks_new_entries()
    if blocked:
        logger.info("open_position: refusé par risk_guard (%s)", reason)
        return None

    start = await starting_capital()
    cash = await cash_available()
    alloc = alloc_usd if alloc_usd is not None else ALLOC_PCT * start
    # #186 -- plafond de risque : ne réduit jamais alloc au-delà de sa valeur d'entrée,
    # jamais un bonus. Sans invalidation_price connue, inchangé (stop suiveur seul garde-fou).
    alloc = risk_guard.size_position_by_risk(alloc, entry_price, invalidation_price, start)
    # 19/07 -- plafond auto-calibré par impact de prix (revue croisée Gemini) : réduit
    # encore alloc si CET ordre sur CE pool précis ferait tomber le R/R structurel sous
    # son plancher -- fail-open sans pool_liquidity_usd/target/invalidation connus (même
    # doctrine que size_position_by_risk juste au-dessus).
    alloc = risk_guard.cap_alloc_to_price_impact(
        alloc, entry_price, target_price, invalidation_price, pool_liquidity_usd,
    )
    alloc = min(alloc, cash)
    if alloc <= 0:
        return None

    if category:
        from aria_core import paper_trader_risk as risk

        opens = await get_open_positions()
        already = risk.category_exposure_usd(category, opens)
        alloc = risk.fit_alloc_to_concentration_cap(
            category=category,
            alloc=alloc,
            already_deployed_usd=already,
            starting_capital=start,
            min_alloc=ALLOC_PCT * start * risk.MIN_CONCENTRATION_ALLOC_FRACTION,
        )
        if alloc <= 0:
            return None

    qty = alloc / entry_price
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO paper_position
              (contract, symbol, cost_usd, entry_price, qty, target_price,
               invalidation_price, opened_at, status, high_water_price, initial_qty,
               category, entry_security_json, chain, thesis, entry_atr_pct,
               strategy, entry_liquidity_usd, entry_regime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (contract, symbol or "", alloc, entry_price, qty, target_price, invalidation_price,
             _now(), entry_price, qty, category or "", entry_security_json or None,
             (chain or "base").lower(), thesis, entry_atr_pct,
             strategy or "momentum", pool_liquidity_usd, entry_regime),
        )
        await db.commit()
        pid = cur.lastrowid
    return await _get_open(contract) or {"id": pid, "contract": contract}


async def close_position(
    contract: str, exit_price: float, *, reason: str = "manuel", notes: str | None = None,
) -> dict | None:
    """Ferme une position FICTIVE au prix de sortie réel et enregistre le P&L. ``reason``
    reste un tag court stable (comparé par égalité ailleurs/dans les tests) ; ``notes``
    (17/07) porte la justification chiffrée complète -- séparés pour ne jamais casser un
    appelant qui dépend du tag exact.

    ``pnl_usd`` final = P&L de la dernière tranche + ``realized_pnl_partial`` déjà
    accumulé par d'éventuelles prises de profit partielles (19/07, bug réel trouvé sur
    la position #21) : ``portfolio_summary()`` ne lit ``realized_pnl_partial`` QUE pour
    les positions encore ``open`` -- une fois ``closed``, seul ``pnl_usd`` compte dans
    l'agrégat de capital. Sans cette addition, le P&L des paliers de prise de profit
    déjà réalisés disparaissait silencieusement du capital total pile au moment de la
    clôture finale. ``realized_pnl_partial`` reste inchangé sur la ligne (part du P&L
    total venue des paliers antérieurs, toujours visible séparément)."""
    await _ensure_tables()
    pos = await _get_open(contract)
    if not pos or not exit_price or exit_price <= 0:
        return None
    proceeds = pos["qty"] * exit_price
    final_leg_pnl = proceeds - pos["cost_usd"]
    pnl_usd = final_leg_pnl + (pos.get("realized_pnl_partial") or 0.0)
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    closed_at = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_position
               SET status = 'closed', exit_price = ?, closed_at = ?, pnl_usd = ?,
                   pnl_pct = ?, close_reason = ?, close_notes = ?
             WHERE id = ?
            """,
            (exit_price, closed_at, pnl_usd, pnl_pct, reason, notes, pos["id"]),
        )
        await db.commit()
    return {**pos, "status": "closed", "exit_price": exit_price, "closed_at": closed_at,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "close_reason": reason, "close_notes": notes}


async def reduce_position(
    contract: str, exit_price: float, sell_qty: float, *, stage: int,
    reason: str = "prise de profit", notes: str | None = None,
) -> dict | None:
    """Prise de profit PARTIELLE : vend une fraction de la position et garde le reste
    ouvert avec une base de coût réduite proportionnellement (même ``entry_price``, moins
    de ``qty``/``cost_usd``). Le P&L de la tranche vendue est accumulé dans
    ``realized_pnl_partial`` -- il reste visible dans ``cash_available``/``portfolio_summary``
    sans attendre la clôture complète de la position. ``notes`` (17/07) : justification
    chiffrée de CETTE prise partielle, persistée sur la ligne encore ouverte (remplace la
    précédente -- dernière note en date, pas un historique cumulé)."""
    await _ensure_tables()
    pos = await _get_open(contract)
    if not pos or not exit_price or exit_price <= 0 or sell_qty <= 0:
        return None
    sell_qty = min(sell_qty, pos["qty"])
    frac = sell_qty / pos["qty"] if pos["qty"] else 0.0
    sold_cost = pos["cost_usd"] * frac
    proceeds = sell_qty * exit_price
    pnl_usd = proceeds - sold_cost
    new_qty = pos["qty"] - sell_qty
    new_cost = pos["cost_usd"] - sold_cost
    new_realized_partial = (pos.get("realized_pnl_partial") or 0.0) + pnl_usd
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_position
               SET qty = ?, cost_usd = ?, realized_pnl_partial = ?, tp_stage_hit = ?, close_notes = ?
             WHERE id = ?
            """,
            (new_qty, new_cost, new_realized_partial, stage, notes, pos["id"]),
        )
        await db.commit()
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    return {
        **pos, "sold_qty": sell_qty, "exit_price": exit_price, "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct, "close_reason": reason, "close_notes": notes, "remaining_qty": new_qty,
        "tp_stage_hit": stage,
    }


async def _update_high_water(
    position_id: int, price: float,
    pending_high_water: float | None = None, pending_since: str | None = None,
) -> None:
    """``pending_high_water``/``pending_since`` (20/07) persistent la candidature de
    plus-haut en attente de confirmation temporelle (cf. ``_advance_high_water``) --
    ``None`` (défaut, rétrocompatible) efface toute candidature en cours."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET high_water_price = ?, pending_high_water = ?, "
            "pending_high_water_since = ? WHERE id = ?",
            (price, pending_high_water, pending_since, position_id),
        )
        await db.commit()


async def _lock_breakeven_floor(position_id: int) -> None:
    """Verrouille le point mort (Breakeven Hard Floor, cf. ``_breakeven_floor_
    threshold``) -- irrévocable, jamais réinitialisé ailleurs (aucune fonction
    UPDATE ne remet ``breakeven_locked`` à 0)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET breakeven_locked = 1 WHERE id = ?", (position_id,),
        )
        await db.commit()


async def portfolio_summary(*, price_lookup=None) -> dict:
    """Photo du portefeuille : cash, valeur totale (marquée au marché si price_lookup),
    rendement %, P&L réalisé/latent, taux de réussite. ``price_lookup(contract)`` async → prix."""
    start = await starting_capital()
    opens = await get_open_positions()
    closed = await get_closed_positions(limit=100_000)
    realized = (
        sum((p["pnl_usd"] or 0.0) for p in closed)
        + sum((p.get("realized_pnl_partial") or 0.0) for p in opens)
    )
    cash = start - sum(p["cost_usd"] for p in opens) + realized

    open_value = 0.0
    unrealized = 0.0
    for p in opens:
        price = None
        if price_lookup is not None:
            try:
                price = await price_lookup(p["contract"])
            except Exception:  # noqa: BLE001 — un prix indispo n'arrête pas la photo
                price = None
        value = p["qty"] * price if (price and price > 0) else p["cost_usd"]
        open_value += value
        unrealized += value - p["cost_usd"]

    equity = cash + open_value
    ret_pct = (equity / start - 1.0) * 100.0 if start else 0.0
    wins = [p for p in closed if (p["pnl_usd"] or 0.0) > 0]
    win_rate = (len(wins) / len(closed) * 100.0) if closed else None
    return {
        "starting": start,
        "cash": cash,
        "equity": equity,
        "return_pct": ret_pct,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "open_positions": len(opens),
        "closed_trades": len(closed),
        "win_rate": win_rate,
    }


# ── Alertes FICTIVES (opérateur) — toujours estampillées SIMULATION ──────────────────

def format_buy_alert(pos: dict) -> str:
    name = pos.get("symbol") or (pos.get("contract") or "")[:10]
    # 17/07 -- demande opérateur explicite : voir le % du capital de départ (STARTING_
    # CAPITAL_USD, jamais l'équité courante -- c'est exactement la base sur laquelle
    # chaque position est dimensionnée, cf. run_paper_cycle) engagé par CETTE position,
    # pas seulement le montant brut en $.
    cost = pos.get("cost_usd") or 0.0
    pct_of_capital = (cost / STARTING_CAPITAL_USD * 100.0) if STARTING_CAPITAL_USD else 0.0
    lines = [
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"ACHAT FICTIF {name}",
        f"Contrat {pos.get('contract', '')}",
        f"Entrée {pos['entry_price']:.6g} · taille {cost:,.0f} $ ({pct_of_capital:.1f}% du capital de départ)",
    ]
    if pos.get("target_price"):
        lines.append(f"Cible {pos['target_price']:.6g}")
    if pos.get("invalidation_price"):
        lines.append(f"Invalidation {pos['invalidation_price']:.6g}")
    # #197 (15/07) -- la thèse VC (pourquoi ARIA entre, pas seulement à quel prix) était
    # calculée mais jamais montrée. Affichée ici tronquée (lisibilité Telegram mobile) --
    # le texte COMPLET, lui, est toujours persisté tel quel en base (thesis, cf.
    # open_position), jamais tronqué là où ça compte pour la vérification après coup.
    thesis = (pos.get("thesis") or "").strip()
    if thesis:
        lines.append(f"Thèse : {thesis[:500]}")
    if pos.get("contract"):
        lines.append(f"DexScreener : {token_url(pos['contract'], chain=pos.get('chain') or 'base')}")
    lines.append("Aucun argent réel — preuve de performance en cours.")
    return "\n".join(lines)


def format_position_tracking_alert(
    tracked: list[dict], *, cash: float | None = None, equity: float | None = None,
) -> str:
    """Suivi PÉRIODIQUE des positions déjà ouvertes (#197, 15/07) -- pas seulement à
    l'achat/la vente. ``tracked`` : liste de dicts {contract, symbol, entry_price, price,
    qty, cost_usd}, une entrée par position ENCORE ouverte à la fin du cycle (les
    positions fermées CE tour sont déjà couvertes par format_sell_alert, pas dupliquées
    ici). Liste vide -> chaîne vide (rien à envoyer, l'appelant ne notifie pas).

    ``cash``/``equity`` (17/07) : trouvé en conditions réelles -- l'en-tête affichait
    "portefeuille papier 1 M$" en dur sur CHAQUE alerte, quelle que soit la valeur RÉELLE
    du moment (déjà 998 415 $ après la première perte) -- l'opérateur ne pouvait pas savoir
    combien il restait sans aller consulter /feedback ou /ledger à part. Optionnels
    (``None`` -> ancien libellé générique, dégradation honnête plutôt qu'un chiffre
    inventé si l'appelant ne les calcule pas)."""
    if not tracked:
        return ""
    if equity is not None and cash is not None:
        header = (
            f"🧪 SIMULATION — suivi positions ouvertes "
            f"(portefeuille papier : équité {equity:,.0f} $, cash {cash:,.0f} $)"
        )
    else:
        header = "🧪 SIMULATION — suivi positions ouvertes (portefeuille papier 1 M$)"
    lines = [header]
    for t in tracked:
        name = t.get("symbol") or (t.get("contract") or "")[:10]
        entry = t.get("entry_price") or 0.0
        price = t.get("price") or 0.0
        qty = t.get("qty") or 0.0
        cost = t.get("cost_usd") or 0.0
        value = qty * price
        pnl = value - cost
        pnl_pct = (price / entry - 1.0) * 100.0 if entry else 0.0
        sign = "+" if pnl >= 0 else ""
        # 17/07 -- demande opérateur explicite : capital investi + % du capital de départ
        # (STARTING_CAPITAL_USD, la base fixe sur laquelle chaque position est dimensionnée
        # à l'ouverture -- jamais l'équité courante, qui bougerait après coup et ne
        # représenterait plus fidèlement la taille décidée AU MOMENT de l'achat).
        pct_of_capital = (cost / STARTING_CAPITAL_USD * 100.0) if STARTING_CAPITAL_USD else 0.0
        lines.append(
            f"{name} : {price:.6g} ({sign}{pnl_pct:.1f}%) · P&L latent {sign}{pnl:,.0f} $ · "
            f"capital {cost:,.0f} $ ({pct_of_capital:.1f}% du capital de départ)"
        )
        if t.get("contract"):
            lines.append(f"  {token_url(t['contract'], chain=t.get('chain') or 'base')}")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def format_sell_alert(closed: dict) -> str:
    name = closed.get("symbol") or (closed.get("contract") or "")[:10]
    pnl = closed.get("pnl_usd") or 0.0
    pct = closed.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    lines = [
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"VENTE FICTIVE {name} ({closed.get('close_reason', '')})",
        f"Sortie {closed['exit_price']:.6g} · P&L {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%)",
    ]
    notes = (closed.get("close_notes") or "").strip()
    if notes:
        lines.append(f"Pourquoi : {notes}")
    if closed.get("contract"):
        lines.append(f"DexScreener : {token_url(closed['contract'], chain=closed.get('chain') or 'base')}")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def format_partial_exit_alert(partial: dict) -> str:
    name = partial.get("symbol") or (partial.get("contract") or "")[:10]
    pnl = partial.get("pnl_usd") or 0.0
    pct = partial.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    lines = [
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"PRISE DE PROFIT PARTIELLE FICTIVE {name} ({partial.get('close_reason', '')})",
        f"Sortie {partial['exit_price']:.6g} · {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%) sur la tranche vendue",
        f"Position restante : {partial.get('remaining_qty', 0):.6g} unités",
    ]
    notes = (partial.get("close_notes") or "").strip()
    if notes:
        lines.append(f"Pourquoi : {notes}")
    if partial.get("contract"):
        lines.append(f"DexScreener : {token_url(partial['contract'], chain=partial.get('chain') or 'base')}")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def format_summary(summary: dict) -> str:
    wr = summary.get("win_rate")
    wr_str = f"{wr:.0f}%" if wr is not None else "n/a"
    return "\n".join([
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"Valeur totale : {summary['equity']:,.0f} $ ({summary['return_pct']:+.2f}%)",
        f"Cash {summary['cash']:,.0f} $ · {summary['open_positions']} positions ouvertes",
        f"Réalisé {summary['realized_pnl']:+,.0f} $ · latent {summary['unrealized_pnl']:+,.0f} $",
        f"Trades clôturés {summary['closed_trades']} · réussite {wr_str}",
        "Aucun argent réel — track record de preuve.",
    ])


# ── Défauts prod (réseau/LLM), injectables en test ───────────────────────────────────

async def _default_pair_lookup(contract: str, *, chain: str = "base"):
    """17/07 -- factorisé hors de ``_default_price_lookup`` pour que la boucle de gestion
    des positions ouvertes puisse réutiliser la MÊME paire DexScreener à la fois pour le
    prix courant ET le re-scan du ratio volume/liquidité (``paper_trader_risk.
    rescan_open_position``), sans dupliquer l'appel réseau. Renvoie ``None`` si aucune
    paire liquide n'est trouvée -- jamais une paire inventée.

    19/07 -- même correctif que ``momentum_entry._best_pair`` (bug réel, position
    PLAZM #21 == en fait ESHARE) : ``fetch_token_pairs`` renvoie TOUTE paire
    impliquant ``contract``, y compris comme simple QUOTE du pool d'un AUTRE token
    -- sans filtre sur ``PairSnapshot.base_address``, cette fonction pouvait
    retourner le prix/volume/liquidité d'un token totalement différent (celui qui
    utilise ``contract`` comme quote d'un pool plus liquide que le sien). C'est
    CETTE fonction qui alimente le suivi périodique Telegram des positions
    ouvertes -- le prix erroné affiché pour la position #21 (~0,0176 au lieu du
    vrai prix ESHARE, ~5,84$) en découlait directement, pas seulement l'entrée."""
    from aria_core.services.dexscreener import fetch_token_pairs

    contract_lower = (contract or "").strip().lower()
    pairs = await fetch_token_pairs(contract, chain=chain)
    own_pairs = [p for p in pairs if (p.base_address or "").lower() == contract_lower]
    if not own_pairs:
        return None
    return max(own_pairs, key=lambda p: p.liquidity_usd)


async def _default_price_lookup(contract: str, *, chain: str = "base") -> float | None:
    """Généralisé multi-chaînes (#194) -- DexScreener directement (déjà multi-chaînes,
    services/dexscreener.py) plutôt que scan_base_token (spécifique Base, et surtout
    bien plus lourd : honeypot + TA + mint-authority complets pour juste un prix de
    suivi). ``chain`` par défaut ``"base"`` -- comportement inchangé pour tout appelant
    qui ne le précise pas."""
    best = await _default_pair_lookup(contract, chain=chain)
    if best is None:
        return None
    return best.price_usd if best.price_usd > 0 else None


# 20/07 -- #173, revue croisée : le reset hebdomadaire force-clôturait chaque position
# encore ouverte sur un SEUL tick spot instantané (``_default_price_lookup``) --
# vulnérable à une mèche isolée survenant pile au moment du reset (même classe de
# risque déjà traitée ailleurs pour la gestion continue -- anti-mèche du stop suiveur,
# Breakeven Hard Floor -- mais jamais pour CET événement ponctuel précis). Fenêtre
# courte : le reset est hebdomadaire, pas besoin d'un historique long, juste résister
# à UN tick aberrant.
_RESET_PRICE_CANDLE_WINDOW = 5
_RESET_PRICE_MIN_CANDLES = 3


async def _robust_close_price(contract: str, chain: str, pair) -> float | None:
    """Prix de clôture ROBUSTE pour le reset hebdomadaire (#173) -- médiane des
    ``_RESET_PRICE_CANDLE_WINDOW`` dernières bougies OHLCV (même cascade à 5 étages
    que le pipeline momentum, ``momentum_entry._fetch_candles`` -- jamais un second
    client dupliqué) plutôt qu'un tick spot unique : une mèche isolée sur UNE bougie
    ne domine pas une médiane sur plusieurs. Sous ``_RESET_PRICE_MIN_CANDLES`` bougies
    exploitables (chandelles absentes/invalides) -> ``None``, l'appelant retombe alors
    sur le prix spot déjà en main (``pair.price_usd``, zéro appel réseau
    supplémentaire) -- jamais pire que le comportement historique, jamais bloquant."""
    if pair is None or not pair.pair_address:
        return None
    from aria_core import momentum_entry

    try:
        candles = await momentum_entry._fetch_candles(
            pair.pair_address, chain, contract=contract, pair=pair,
        )
    except Exception:  # noqa: BLE001 — jamais bloquant, l'appelant dégrade vers le spot
        return None
    closes = sorted(
        c.close for c in candles[-_RESET_PRICE_CANDLE_WINDOW:] if c.close and c.close > 0
    )
    if len(closes) < _RESET_PRICE_MIN_CANDLES:
        return None
    mid = len(closes) // 2
    if len(closes) % 2 == 1:
        return closes[mid]
    return (closes[mid - 1] + closes[mid]) / 2.0


async def _default_analyzer(contract: str) -> dict | None:
    """Signal d'un contrat à partir de la VRAIE analyse VC. Retourne action + niveaux."""
    from aria_core.skills.vc_analysis import analyze_vc_with_context
    from aria_core import paper_trader_risk as risk

    result, ctx = await analyze_vc_with_context(contract)
    action = "BUY" if getattr(result, "recommandation", "") == "BUY" else "HOLD"
    price = ctx.best_pair.price_usd if ctx.best_pair else None
    target = _num(getattr(result, "cible", None)) or (ctx.ta_entry.cible if ctx.ta_entry else None)
    inval = _num(getattr(result, "invalidation", None)) or (
        ctx.ta_entry.invalidation if ctx.ta_entry else None
    )
    category = risk.derive_category(ctx.launchpad, bonding_phase=ctx.bonding_phase)
    entry_snapshot = await risk.capture_entry_snapshot(contract, ctx)
    return {
        "action": action,
        "symbol": ctx.best_pair.base_symbol if ctx.best_pair else "",
        "price": price,
        "target": target,
        "invalidation": inval,
        "category": category,
        "entry_security_json": entry_snapshot.to_json(),
        # #197 (15/07) -- VCResult.these était déjà calculée ici mais jamais remontée :
        # perdue dès la sortie de cette fonction. Remontée jusqu'à open_position() par
        # run_paper_cycle ci-dessous.
        "these": getattr(result, "these", "") or "",
        # 20/07 -- Formule B : cette pipeline (safety_screen/vc_analysis, fondamentaux +
        # sécurité, jamais Fibonacci/RSI) source des positions "vc_thesis" -- sortie sans
        # stop suiveur, invalidation fondamentale (liquidité), cf. paper_trader.py. Aucune
        # position n'est ouverte via ce chemin sur le test 1M$ en cours (défaut momentum,
        # cf. _momentum_candidates_and_chain_map ci-dessous) -- infrastructure prête pour
        # quand la poche VC 85% reprendra.
        "strategy": "vc_thesis",
        # ``liquidity_usd`` -- référence pour l'invalidation fondamentale en cours de
        # détention (chute structurelle vs. entrée). None si aucune paire résolue -- jamais
        # une donnée inventée, le check % ci-dessous est alors simplement fail-open (seul
        # le plancher absolu reste actif).
        "liquidity_usd": ctx.best_pair.liquidity_usd if ctx.best_pair else None,
    }


async def _momentum_candidates_and_chain_map(*, limit: int = 20) -> tuple[list[str], dict[str, str]]:
    """#194, pivot momentum -- source de candidats par défaut pour CE TEST (remplace
    ``candidate_ranking.top_candidates()`` UNIQUEMENT comme défaut de ``run_paper_cycle``
    quand ni ``candidates`` ni ``analyzer`` ne sont fournis par l'appelant -- ``screened_pool``/
    la poche VC 85% ne sont ni modifiés ni moins utilisés ailleurs, décision opérateur
    explicite et réversible). Renvoie la liste de contrats (contrat garde sa forme
    ``list[str]`` historique, inchangée pour le reste de la boucle) + la table
    contrat→chaîne pour l'analyzer momentum ci-dessous."""
    from aria_core import momentum_entry

    found = await momentum_entry.discover_momentum_candidates()
    chain_by_contract = {c["contract"]: c["chain"] for c in found}
    return [c["contract"] for c in found[:limit]], chain_by_contract


def _default_momentum_analyzer(
    chain_by_contract: dict[str, str], weekly_context: dict | None = None,
    current_regime: str | None = None,
):
    """Ferme sur la table contrat→chaîne construite au sourcing (#194) -- garde la
    signature ``analyzer(contract)`` historique inchangée, aucun appelant existant
    (tests, autres pilotes) n'est affecté. ``weekly_context`` (18/07)/``current_regime``
    (20/07, Regime Switch), tous deux optionnels : calculés UNE FOIS par cycle par
    l'appelant (cf. ``_run_paper_cycle_locked``), transmis tels quels à chaque candidat
    -- jamais un recalcul par candidat."""
    from aria_core import momentum_entry

    async def analyzer(contract: str) -> dict | None:
        chain = chain_by_contract.get(contract, "base")
        return await momentum_entry.evaluate_momentum_entry(
            contract, chain, weekly_context=weekly_context, current_regime=current_regime,
        )

    return analyzer


# ── Cycle d'entraînement hebdomadaire (18/07, remplace le protocole 30j/7j/14j) ──────

async def get_current_cycle_number() -> int:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT cycle_number FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 1


async def cycle_started_at() -> str:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT created_at FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else _now()


def weekly_target_equity(start_capital: float) -> float:
    return start_capital * WEEKLY_TARGET_MULTIPLIER


async def weekly_cycle_due() -> bool:
    """Vrai si ``WEEKLY_CYCLE_DAYS`` se sont écoulés depuis le début du cycle courant
    (``paper_state.created_at``). Jamais anticipé, même si l'objectif est déjà atteint --
    une boucle d'entraînement RÉPÉTÉE, pas une porte de sortie qu'on franchit une fois."""
    started = await cycle_started_at()
    try:
        started_dt = datetime.fromisoformat(started)
    except ValueError:
        return False
    if started_dt.tzinfo is None:
        started_dt = started_dt.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - started_dt
    return elapsed.total_seconds() >= WEEKLY_CYCLE_DAYS * 86400


async def run_weekly_reset(*, price_lookup=None) -> dict:
    """Bilan + reset du cycle hebdomadaire (décision opérateur explicite, 18/07) --
    remplace intégralement le protocole 30j/7j/14j comme méthode d'ENTRAÎNEMENT et de
    DÉCISION vers le capital réel : ARIA repart à 1M$ CHAQUE semaine, objectif +10%
    (1,1M$) VALIDÉ chaque semaine, que la précédente ait réussi ou non.

    Contrairement à ``reset_portfolio`` (DROP TABLE, destructif par design, réservé à un
    déclenchement opérateur explicite), cette fonction ne détruit JAMAIS l'historique :
    1. force-clôture mark-to-market (prix RÉEL, jamais inventé -- dégrade sur le coût
       d'entrée si le prix est introuvable) de toute position encore ouverte -- une
       semaine se juge sur elle-même, rien ne reste "à cheval" sur la suivante ;
    2. photo finale (``portfolio_summary``, equity == cash après l'étape 1) -> verdict
       ``validated`` = équité finale >= objectif ;
    3. archive TOUT l'historique de la semaine dans ``paper_position_archive`` (jamais
       perdu) puis vide la table live -- la prochaine semaine démarre à 0 position ;
    4. enregistre le verdict dans ``paper_weekly_cycle`` (track record permanent, une
       ligne par semaine, jamais réécrit après coup sauf par cette fonction elle-même) ;
    5. repart à neuf : capital 1M$, horodatage, plus-haut d'équité, cycle_number+1 ;
    6. lève le coupe-circuit de risque dédié (``risk_guard``) -- fraîche semaine, fraîche
       discipline, jamais un ancien palier dur qui bloquerait la semaine suivante.
    """
    await _ensure_tables()
    price_lookup = price_lookup or _default_price_lookup
    using_default_price_lookup = price_lookup is _default_price_lookup
    cycle_number = await get_current_cycle_number()
    started_at = await cycle_started_at()
    start_capital = await starting_capital()
    target_equity = weekly_target_equity(start_capital)

    force_closed: list[dict] = []
    for pos in await get_open_positions():
        price = None
        price_source = "indisponible"
        try:
            if using_default_price_lookup:
                chain = pos.get("chain") or "base"
                pair = await _default_pair_lookup(pos["contract"], chain=chain)
                robust = await _robust_close_price(pos["contract"], chain, pair)
                if robust and robust > 0:
                    price = robust
                    price_source = "médiane bougies (anti-mèche, #173)"
                elif pair is not None and pair.price_usd and pair.price_usd > 0:
                    price = pair.price_usd
                    price_source = "spot (bougies indisponibles)"
            else:
                price = await price_lookup(pos["contract"])
                price_source = "de marché" if (price and price > 0) else "indisponible"
        except Exception:  # noqa: BLE001 — un prix indispo ne bloque jamais le reset
            price = None
        exit_price = price if (price and price > 0) else pos["entry_price"]
        closed = await close_position(
            pos["contract"], exit_price,
            reason="reset_hebdomadaire",
            notes=(
                f"Clôture forcée -- fin du cycle #{cycle_number} ({_duration_phrase(pos.get('opened_at'))}), "
                f"prix {price_source if (price and price > 0) else 'indisponible, valorisé au coût d’entrée'}."
            ),
        )
        if closed:
            force_closed.append(closed)

    summary = await portfolio_summary()
    end_equity = summary["equity"]
    return_pct = summary["return_pct"]
    validated = end_equity >= target_equity
    ended_at = _now()

    async with aiosqlite.connect(DB_PATH) as db:
        cols = ", ".join(_POS_FIELDS)
        await db.execute(
            f"INSERT INTO paper_position_archive (cycle_number, {cols}) "
            f"SELECT ?, {cols} FROM paper_position",
            (cycle_number,),
        )
        await db.execute("DELETE FROM paper_position")
        await db.execute(
            """
            INSERT INTO paper_weekly_cycle
              (cycle_number, started_at, ended_at, target_equity, start_capital,
               end_equity, return_pct, validated, closed_trades, win_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cycle_number) DO UPDATE SET
              ended_at = excluded.ended_at, target_equity = excluded.target_equity,
              start_capital = excluded.start_capital, end_equity = excluded.end_equity,
              return_pct = excluded.return_pct, validated = excluded.validated,
              closed_trades = excluded.closed_trades, win_rate = excluded.win_rate
            """,
            (cycle_number, started_at, ended_at, target_equity, start_capital,
             end_equity, return_pct, int(validated), summary["closed_trades"], summary["win_rate"]),
        )
        next_cycle = cycle_number + 1
        await db.execute(
            "UPDATE paper_state SET starting_capital = ?, created_at = ?, "
            "equity_high_water_mark = ?, cycle_number = ?, last_tracking_alert_at = NULL "
            "WHERE id = 1",
            (STARTING_CAPITAL_USD, ended_at, STARTING_CAPITAL_USD, next_cycle),
        )
        await db.commit()

    # Fraîche semaine, fraîche discipline -- import local (risk_guard importe déjà
    # paper_trader, jamais l'inverse au niveau module, cf. open_position ci-dessus).
    from aria_core import risk_guard

    risk_guard.resume_new_entries(by="weekly_reset_auto")

    return {
        "cycle_number": cycle_number,
        "started_at": started_at,
        "ended_at": ended_at,
        "start_capital": start_capital,
        "target_equity": target_equity,
        "end_equity": end_equity,
        "return_pct": return_pct,
        "validated": validated,
        "closed_trades": summary["closed_trades"],
        "win_rate": summary["win_rate"],
        "force_closed": len(force_closed),
        "next_cycle_number": next_cycle,
    }


def format_weekly_cycle_report(report: dict) -> str:
    wr = report.get("win_rate")
    wr_str = f"{wr:.0f}%" if wr is not None else "n/a"
    verdict = "✅ VALIDÉ" if report["validated"] else "❌ non atteint"
    lines = [
        "🧪 SIMULATION — bilan hebdomadaire (cycle d'entraînement 1M$)",
        f"Semaine #{report['cycle_number']} : {verdict} (objectif {report['target_equity']:,.0f} $)",
        f"Départ {report['start_capital']:,.0f} $ → clôture {report['end_equity']:,.0f} $ "
        f"({report['return_pct']:+.2f}%)",
        f"Trades clôturés {report['closed_trades']} · réussite {wr_str}",
    ]
    if report.get("force_closed"):
        lines.append(f"{report['force_closed']} position(s) encore ouverte(s) clôturée(s) au prix du marché.")
    lines.append(
        f"Nouvelle semaine #{report['next_cycle_number']} : capital remis à "
        f"{STARTING_CAPITAL_USD:,.0f} $, 0 position. Aucun argent réel."
    )
    return "\n".join(lines)


async def run_paper_cycle(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
    depeg_check=None,
    skip_position_management: bool = False,
) -> dict:
    """Un tour de simulation, appliquant les VRAIS rapports :
      1. positions ouvertes : surveillance de sécurité continue (#187) puis gestion par
         stop suiveur + prise de profit échelonnée (voir ``TRAIL_STOP_PCT``/``TP_STAGES``/
         ``_effective_tp_stages`` -- TP1 ancré sur le target technique de la position quand
         connu, TP2/TP3 fixes au-dessus pour le moonbag) — protège les gains acquis sans
         couper le potentiel restant, au lieu d'une sortie binaire 100 % cible OU 100 %
         invalidation ;
      2. nouveaux achats : sur les candidats classés avec un signal d'ACHAT réel (bloqué si
         USDC est dépeg, #187), ouvre une position fictive et émet une alerte d'achat fictive.
    Tout est injectable (candidates/analyzer/price_lookup/notifier/depeg_check) → testable
    hors-ligne, sans appel réseau caché.
    Aucune exécution réelle, jamais un ordre : de la simulation.

    ``skip_position_management`` (#196, défaut ``False`` -- comportement historique
    inchangé) : saute l'étape 1 (re-scan sécurité + stop suiveur/TP sur les positions déjà
    ouvertes) -- réservé au service websocket momentum, déclenché bien plus souvent
    (~30s) que le cycle heartbeat normal (15 min), pour ne pas re-scanner GoPlus/Blockscout
    sur chaque position ouverte à chaque poussée. L'étape 1ter (photo de risque
    portefeuille, #186) reste TOUJOURS exécutée -- l'étape 2 (nouvelles entrées) en dépend
    (plafond/coupe-circuit), quel que soit l'appelant.

    Toute exécution passe par ``_run_cycle_lock`` (#196) -- jamais deux cycles en
    parallèle (heartbeat + websocket), qui liraient sinon le capital/le nombre de
    positions ouvertes avant que l'un des deux n'écrive (double-allocation possible).
    """
    async with _run_cycle_lock:
        return await _run_paper_cycle_locked(
            candidates=candidates,
            analyzer=analyzer,
            price_lookup=price_lookup,
            notifier=notifier,
            max_new=max_new,
            depeg_check=depeg_check,
            skip_position_management=skip_position_management,
        )


async def _run_paper_cycle_locked(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
    depeg_check=None,
    skip_position_management: bool = False,
) -> dict:
    """Corps réel de ``run_paper_cycle`` -- appelé UNIQUEMENT sous ``_run_cycle_lock``,
    jamais directement (pas de garde-fou de concurrence sinon)."""
    await _ensure_tables()
    price_lookup = price_lookup or _default_price_lookup
    # #194 -- le défaut sait suivre la chaîne persistée d'une position (multi-chaînes) ;
    # tout price_lookup INJECTÉ (tests, ou le pipeline momentum qui fournit le sien via
    # une fermeture propre) garde son contrat d'appel historique à un seul argument.
    using_default_price_lookup = price_lookup is _default_price_lookup
    actions: dict = {"opened": [], "closed": [], "partial": [], "checked": 0, "tracked": []}
    # #197 (15/07) -- suivi périodique : une entrée par position encore ouverte à la fin
    # du cycle (prix courant déjà récupéré ci-dessous, aucun appel réseau supplémentaire).
    tracked: list[dict] = []

    # 20/07 -- Regime Switch dynamique : méta-régime résolu UNE SEULE FOIS par cycle
    # (pure lecture DB locale, ``market_sentiment.resolve_meta_regime()``, zéro appel
    # réseau) -- réutilisé à la fois par la gestion des positions déjà ouvertes
    # ci-dessous (ratchet vers le régime le plus prudent) et par le sourcing de
    # nouvelles entrées plus bas (``_default_momentum_analyzer``). Import hissé HORS du
    # try (pas seulement l'appel) pour que ``market_sentiment`` reste toujours lié dans
    # ce scope, même si la résolution elle-même échoue -- les usages plus bas de
    # ``market_sentiment.more_cautious_meta_regime``/``META_REGIME_NEUTRAL`` ne
    # dépendent alors jamais du chemin de succès. Best-effort, jamais bloquant : une
    # panne dégrade vers "neutre" (comportement historique inchangé).
    from aria_core.skills import market_sentiment

    try:
        current_regime = await market_sentiment.resolve_meta_regime()
    except Exception as exc:  # noqa: BLE001 — jamais bloquant, dégrade vers "neutre"
        logger.info("paper_cycle: méta-régime indisponible (%s) -- neutre par défaut", exc)
        current_regime = market_sentiment.META_REGIME_NEUTRAL

    # 1) Gérer les positions ouvertes : d'abord une surveillance continue de SÉCURITÉ
    #    (#187 -- honeypot/ownership apparus après l'entrée, jamais vérifiés qu'une seule
    #    fois avant), qui prime sur toute gestion par prix ; puis stop suiveur (ne se
    #    relâche jamais) et prise de profit échelonnée sur ce qui reste ouvert.
    #    #196 -- sautée si ``skip_position_management`` (service websocket momentum,
    #    déclenché bien plus souvent que le cycle heartbeat normal) : ne re-scanne pas
    #    GoPlus/Blockscout sur chaque position ouverte à chaque poussée de candidat.
    from aria_core import paper_trader_risk as risk

    if not skip_position_management:
        for p in await get_open_positions():
            actions["checked"] += 1
            # 17/07 -- avec le price_lookup PAR DÉFAUT, la paire DexScreener est
            # récupérée UNE SEULE FOIS et réutilisée à la fois pour le prix et pour le
            # re-scan du ratio volume/liquidité ci-dessous (jamais un second appel
            # réseau dupliqué). Un price_lookup INJECTÉ (tests, pipeline momentum) ne
            # fournit pas cette paire -- le check ratio est alors simplement sauté
            # (dégradation honnête, cf. paper_trader_risk.rescan_open_position).
            pair = None
            try:
                if using_default_price_lookup:
                    pair = await _default_pair_lookup(p["contract"], chain=p.get("chain") or "base")
                    price = pair.price_usd if pair and pair.price_usd and pair.price_usd > 0 else None
                else:
                    price = await price_lookup(p["contract"])
            except Exception:  # noqa: BLE001
                price = None

            try:
                security_flag = await risk.rescan_open_position(p, pair=pair)
            except Exception as exc:  # noqa: BLE001 — la surveillance ne doit jamais casser le cycle
                logger.info("paper_cycle: re-scan sécurité %s échoué (%s)", p["contract"], exc)
                security_flag = None
            if security_flag:
                # Position paper -> fermeture automatique sans risque, ça teste la RÉACTION.
                # Avec du capital RÉEL ceci deviendrait une ALERTE seule (doctrine
                # wallet_guard -- jamais de vente automatique sans confirmation opérateur),
                # voir paper_trader_risk.py.
                exit_price = price if (price and price > 0) else p["entry_price"]
                sec_notes = (
                    f"Re-scan sécurité déclenché en cours de détention ({_duration_phrase(p.get('opened_at'))}) : "
                    + "; ".join(security_flag["reasons"])
                    + " -- fermeture immédiate (position fictive, teste la réaction)."
                )
                closed = await close_position(
                    p["contract"], exit_price, reason="sécurité re-scan", notes=sec_notes,
                )
                if closed:
                    actions["closed"].append(closed)
                    actions.setdefault("security_alerts", []).append(security_flag)
                    if notifier:
                        try:
                            alert = format_sell_alert(closed) + "\n⚠️ " + "; ".join(security_flag["reasons"])
                            await notifier(alert)
                        except Exception:  # noqa: BLE001
                            pass
                continue

            if not price or price <= 0:
                continue

            # #197 -- provisoire : retiré ci-dessous si la position se clôture (totalement)
            # dans ce même tour, pour ne jamais dupliquer avec format_sell_alert.
            tracked.append({
                "contract": p["contract"], "symbol": p["symbol"], "entry_price": p["entry_price"],
                "qty": p["qty"], "cost_usd": p["cost_usd"], "price": price, "chain": p.get("chain") or "base",
            })

            # 20/07 -- Formule B (discipline de sortie VC, cf. VC_MIN_LIQUIDITY_FLOOR_USD/
            # VC_LIQUIDITY_DROP_INVALIDATION_PCT/VC_TAKE_SEED_MULTIPLE plus haut) --
            # branche ENTIÈREMENT séparée de la gestion momentum ci-dessous (stop suiveur
            # ATR + TP par tiers), jamais atteinte pour "strategy" == "momentum" (défaut,
            # comportement historique inchangé).
            if (p.get("strategy") or "momentum") == "vc_thesis":
                entry_price = p["entry_price"]
                entry_liq = p.get("entry_liquidity_usd")
                current_liq = pair.liquidity_usd if pair is not None else None

                liquidity_invalidated = False
                liq_reason = ""
                if current_liq is not None:
                    if current_liq < VC_MIN_LIQUIDITY_FLOOR_USD:
                        liquidity_invalidated = True
                        liq_reason = (
                            f"liquidité tombée sous le plancher absolu "
                            f"({current_liq:,.0f}$ < {VC_MIN_LIQUIDITY_FLOOR_USD:,.0f}$)"
                        )
                    elif (
                        entry_liq and entry_liq > 0
                        and current_liq < entry_liq * VC_LIQUIDITY_DROP_INVALIDATION_PCT
                    ):
                        liquidity_invalidated = True
                        drop_pct = (1 - current_liq / entry_liq) * 100.0
                        liq_reason = (
                            f"liquidité en chute de {drop_pct:.0f}% depuis l'entrée "
                            f"({entry_liq:,.0f}$ -> {current_liq:,.0f}$)"
                        )

                if liquidity_invalidated:
                    exit_gain_pct = (price / entry_price - 1.0) * 100.0 if entry_price else 0.0
                    exit_notes = (
                        f"Invalidation fondamentale VC : {liq_reason} -- thèse invalidée "
                        f"({exit_gain_pct:+.1f}% vs entrée), sortie complète, "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price, reason="invalidation fondamentale (liquidité)",
                        notes=exit_notes,
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    continue

                target = p.get("target_price")
                if target and price >= target:
                    exit_gain_pct = (price / entry_price - 1.0) * 100.0 if entry_price else 0.0
                    exit_notes = (
                        f"Cible complète de la thèse VC atteinte ({price:.6g} >= {target:.6g}, "
                        f"{exit_gain_pct:+.1f}% vs entrée) -- clôture complète, "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price, reason="cible thèse VC", notes=exit_notes,
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    continue

                # "Take Seed" -- UNE SEULE sortie partielle, dès que la position double,
                # récupère EXACTEMENT la mise initiale (``cost_usd``). ``tp_stage_hit``
                # réutilisé comme simple marqueur booléen (0/1) -- cette branche ne
                # rejoint jamais la boucle de paliers momentum ci-dessous, aucun risque
                # de collision de sémantique.
                already_seeded = bool(p.get("tp_stage_hit"))
                gain_mult = (price / entry_price) if entry_price else 0.0
                if not already_seeded and gain_mult >= VC_TAKE_SEED_MULTIPLE:
                    cost_usd = p["cost_usd"]
                    sell_qty = min(cost_usd / price, p["qty"]) if price > 0 else 0.0
                    if sell_qty > 0:
                        seed_notes = (
                            f"Take Seed : position à {gain_mult:.1f}x l'entrée -- vente de "
                            f"{sell_qty:.6g} (récupère la mise initiale {cost_usd:,.0f}$), "
                            f"reste couru sans stop vers la cible complète de la thèse."
                        )
                        partial = await reduce_position(
                            p["contract"], price, sell_qty, stage=1,
                            reason="take seed 2x", notes=seed_notes,
                        )
                        if partial:
                            actions["partial"].append(partial)
                            if notifier:
                                try:
                                    await notifier(format_partial_exit_alert(partial))
                                except Exception:  # noqa: BLE001
                                    pass
                continue

            trail_pct = _effective_trail_pct(p.get("entry_atr_pct"))
            prev_high_water = p.get("high_water_price") or p["entry_price"]
            prev_pending = p.get("pending_high_water")
            prev_pending_since = p.get("pending_high_water_since")
            high_water, pending_hw, pending_since = _advance_high_water(
                prev_high_water, prev_pending, prev_pending_since, price, datetime.now(timezone.utc),
            )
            if (
                high_water != prev_high_water
                or pending_hw != prev_pending
                or pending_since != prev_pending_since
            ):
                await _update_high_water(p["id"], high_water, pending_hw, pending_since)

            # 20/07 -- Breakeven Hard Floor (cf. constantes/docstring ci-dessus) : lit le
            # prix INSTANTANÉ de ce cycle (pas high_water, qui peut encore être en attente
            # de confirmation) -- un seul cycle où le prix touche le seuil flash suffit à
            # verrouiller, indépendamment du sort de la candidature high_water en cours.
            entry_price = p["entry_price"]
            flash_threshold = _breakeven_floor_threshold(p.get("target_price"), entry_price)
            breakeven_locked = bool(p.get("breakeven_locked"))
            if not breakeven_locked and entry_price and flash_threshold is not None:
                if price >= entry_price * (1.0 + flash_threshold):
                    breakeven_locked = True
                    await _lock_breakeven_floor(p["id"])

            trailing_stop = high_water * (1 - trail_pct)
            invalidation = p.get("invalidation_price")
            active_stop = trailing_stop
            stop_source = "stop suiveur"
            if invalidation and invalidation > active_stop:
                active_stop = invalidation
                stop_source = "invalidation"
            if breakeven_locked and entry_price and entry_price > active_stop:
                active_stop = entry_price
                stop_source = "point mort verrouillé"

            if active_stop and price <= active_stop:
                exit_gain_pct = (price / p["entry_price"] - 1.0) * 100.0 if p["entry_price"] else 0.0
                if stop_source == "stop suiveur":
                    peak_gain_pct = (high_water / p["entry_price"] - 1.0) * 100.0 if p["entry_price"] else 0.0
                    trail_origin = "adapté à l'ATR" if p.get("entry_atr_pct") else "fixe"
                    exit_notes = (
                        f"Stop suiveur déclenché : plus haut {high_water:.6g} ({peak_gain_pct:+.1f}% vs entrée), "
                        f"retracement de {trail_pct * 100:.0f}% ({trail_origin}) depuis ce sommet a activé la "
                        f"protection -- sortie {price:.6g} ({exit_gain_pct:+.1f}% net vs entrée), "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    close_reason = "stop suiveur"
                elif stop_source == "point mort verrouillé":
                    threshold_pct = (flash_threshold or 0.0) * 100.0
                    exit_notes = (
                        f"Point mort verrouillé (Breakeven Hard Floor) : le prix a touché au moins "
                        f"+{threshold_pct:.0f}% à un moment de la détention (seuil flash, indépendant "
                        f"de la confirmation temporelle du plus haut) -- le stop a été remonté "
                        f"irrévocablement au prix d'entrée {entry_price:.6g} -- sortie {price:.6g} "
                        f"({exit_gain_pct:+.1f}% net vs entrée), {_duration_phrase(p.get('opened_at'))}."
                    )
                    close_reason = "breakeven hard floor"
                else:
                    exit_notes = (
                        f"Invalidation technique atteinte : prix {price:.6g} <= seuil {invalidation:.6g} "
                        f"({exit_gain_pct:+.1f}% vs entrée) -- thèse invalidée, sortie immédiate, "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    close_reason = "invalidation"
                closed = await close_position(
                    p["contract"], price,
                    reason=close_reason,
                    notes=exit_notes,
                )
                if closed:
                    actions["closed"].append(closed)
                    if notifier:
                        try:
                            await notifier(format_sell_alert(closed))
                        except Exception:  # noqa: BLE001 — l'alerte ne casse pas le cycle
                            pass
                continue  # position fermée, rien d'autre à évaluer ce tour

            # Prise de profit échelonnée : vend une fraction de la quantité INITIALE à chaque
            # palier de gain franchi. Dernier palier (ou reliquat négligeable) -> clôture complète.
            # ``stages`` (19/07) : TP1 ancré sur le target technique de CETTE position si
            # connu et cohérent, sinon repli TP_STAGES fixe -- cf. _effective_tp_stages().
            initial_qty = p.get("initial_qty") or p["qty"]
            stage_hit = int(p.get("tp_stage_hit") or 0)
            remaining_qty = p["qty"]
            entry_price = p["entry_price"]
            gain_pct = (price / entry_price - 1.0) if entry_price else 0.0
            # 20/07 -- Regime Switch : le régime EFFECTIF pour la sortie ratche vers le
            # plus prudent entre celui observé à l'entrée et celui observé maintenant --
            # jamais un assouplissement, même si le marché est redevenu plus optimiste
            # depuis (cf. docstring de _apply_regime_to_tp_stages/more_cautious_meta_regime).
            effective_exit_regime = market_sentiment.more_cautious_meta_regime(
                p.get("entry_regime"), current_regime,
            )
            stages = _apply_regime_to_tp_stages(
                _effective_tp_stages(p.get("target_price"), entry_price), effective_exit_regime,
            )

            while stage_hit < len(stages) and gain_pct >= stages[stage_hit]:
                stage_hit += 1
                sell_qty = min(initial_qty * TP_STAGE_FRACTION, remaining_qty)
                is_last_stage = stage_hit >= len(stages) or remaining_qty - sell_qty <= TP_QTY_EPSILON
                stage_target_pct = stages[stage_hit - 1] * 100.0
                if is_last_stage:
                    tp_notes = (
                        f"Dernier palier de profit {stage_hit}/{len(stages)} atteint "
                        f"(+{gain_pct * 100:.0f}% vs entrée, seuil visé +{stage_target_pct:.0f}%) -- "
                        f"clôture du reliquat, {_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price,
                        reason=f"palier {stage_hit}/{len(stages)} (clôture)", notes=tp_notes,
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    break

                partial_pct = TP_STAGE_FRACTION * 100.0
                remaining_after_pct = max(0.0, 100.0 - stage_hit * TP_STAGE_FRACTION * 100.0)
                partial_notes = (
                    f"Palier de profit {stage_hit}/{len(stages)} atteint "
                    f"(+{gain_pct * 100:.0f}% vs entrée, seuil visé +{stage_target_pct:.0f}%) -- "
                    f"prise de {partial_pct:.0f}% de la position initiale, "
                    f"~{remaining_after_pct:.0f}% restant en jeu."
                )
                partial = await reduce_position(
                    p["contract"], price, sell_qty, stage=stage_hit,
                    reason=f"palier {stage_hit}/{len(stages)}", notes=partial_notes,
                )
                if partial:
                    actions["partial"].append(partial)
                    remaining_qty = partial["remaining_qty"]
                    if notifier:
                        try:
                            await notifier(format_partial_exit_alert(partial))
                        except Exception:  # noqa: BLE001
                            pass

        # 1bis) Suivi périodique des positions ENCORE ouvertes (#197, 15/07) -- pas seulement
        # à l'achat/la vente. Retire celles fermées CE tour (déjà couvertes par
        # format_sell_alert, jamais dupliquées). Un seul message consolidé, pas un par
        # position (évite le bruit Telegram) -- persistance en base (thesis, prix, contrat)
        # prime de toute façon sur cet affichage, qui reste best-effort.
        closed_contracts_this_cycle = {c["contract"] for c in actions["closed"]}
        tracked = [t for t in tracked if t["contract"] not in closed_contracts_this_cycle]
        actions["tracked"] = tracked
        if tracked and notifier:
            # Équité/cash RÉELS (17/07) -- réutilise le prix déjà récupéré cette boucle pour
            # chaque position (``t["price"]``), aucun nouvel appel réseau ; ``cash_available``
            # est une simple lecture DB (déjà utilisée ailleurs), jamais un doublon de calcul.
            tracking_cash = tracking_equity = None
            try:
                tracking_cash = await cash_available()
                open_value = sum((t.get("qty") or 0.0) * (t.get("price") or 0.0) for t in tracked)
                tracking_equity = tracking_cash + open_value
            except Exception:  # noqa: BLE001 -- l'alerte degrade au libelle generique, jamais fatale
                pass
            # 17/07 -- réduit le bruit Telegram de moitié : n'envoie que si le dernier
            # envoi remonte à au moins TRACKING_ALERT_MIN_INTERVAL_MINUTES. Ne bloque jamais
            # une vraie alerte d'achat/vente (celles-ci ont leur propre notifier plus haut,
            # jamais soumises à cette fenêtre) -- seul ce suivi périodique est throttlé.
            should_notify = True
            try:
                last_at = await get_last_tracking_alert_at()
                if last_at:
                    elapsed_min = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).total_seconds() / 60.0
                    should_notify = elapsed_min >= TRACKING_ALERT_MIN_INTERVAL_MINUTES
            except Exception:  # noqa: BLE001 -- en cas de doute, on notifie (dégradation douce)
                should_notify = True
            msg = format_position_tracking_alert(tracked, cash=tracking_cash, equity=tracking_equity)
            if msg and should_notify:
                try:
                    await notifier(msg)
                    await set_last_tracking_alert_at(_now())
                except Exception:  # noqa: BLE001 — l'alerte ne casse pas le cycle
                    pass

    # 1ter) Photo du risque portefeuille (#186) -- une seule fois par cycle, APRÈS la gestion
    # des positions déjà ouvertes (qui doit continuer normalement même coupe-circuit armé) et
    # AVANT toute tentative d'ouverture. Met à jour le plus haut d'équité persisté, arme le
    # coupe-circuit dédié si un palier dur est franchi pour la première fois.
    from aria_core import risk_guard

    risk_state = await risk_guard.evaluate_portfolio_risk(price_lookup=price_lookup)
    actions["risk_state"] = risk_state
    if risk_state.newly_triggered_hard and notifier:
        try:
            await notifier(risk_guard.format_hard_circuit_breaker_alert(risk_state))
        except Exception:  # noqa: BLE001 — l'alerte ne casse pas le cycle
            pass
    elif risk_state.newly_triggered_soft and notifier:
        try:
            await notifier(risk_guard.format_soft_drawdown_alert(risk_state))
        except Exception:  # noqa: BLE001
            pass

    if risk_state.blocked:
        # Palier dur (ou pause globale) : aucune NOUVELLE entrée ce tour -- les positions
        # déjà ouvertes ont déjà été gérées normalement ci-dessus (étape 1).
        return actions

    # 18/07 -- décision opérateur explicite ("la rendre plus intelligente") : contexte de
    # rythme du cycle hebdomadaire (jour X/7, équité vs objectif +10%), calculé UNE FOIS
    # par cycle et réutilisant risk_state.equity déjà calculé ci-dessus (aucun appel
    # réseau supplémentaire). Transmis au pipeline momentum (tie-breaker + garde de
    # sécurité LLM) -- best-effort, jamais bloquant pour le cycle de trading lui-même.
    weekly_context: dict | None = None
    try:
        cap = await starting_capital()
        target = weekly_target_equity(cap)
        started_dt = datetime.fromisoformat(await cycle_started_at())
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        elapsed_days = (datetime.now(timezone.utc) - started_dt).total_seconds() / 86400.0
        progress_pct = (risk_state.equity / cap - 1.0) * 100.0 if cap else 0.0
        # 18/07 (suite, revue croisée) -- distance à l'objectif en points de %, en plus
        # des dollars bruts : un LLM manipule plus fiablement un ratio de progression
        # ("encore 0.5 pt avant l'objectif") qu'une soustraction mentale entre deux
        # grands nombres. positif = encore du chemin, <=0 = objectif déjà atteint/dépassé.
        target_pct = (WEEKLY_TARGET_MULTIPLIER - 1.0) * 100.0
        weekly_context = {
            "cycle_number": await get_current_cycle_number(),
            "day": min(WEEKLY_CYCLE_DAYS, int(elapsed_days) + 1),
            "days_total": WEEKLY_CYCLE_DAYS,
            "equity": risk_state.equity,
            "target_equity": target,
            "progress_pct": progress_pct,
            "remaining_pct": target_pct - progress_pct,
        }
    except Exception as exc:  # noqa: BLE001 — jamais bloquant, dégrade en absence de contexte
        logger.info("paper_cycle: contexte de rythme hebdo indisponible (%s)", exc)
        weekly_context = None

    # 2) Ouvrir de nouvelles positions depuis les candidats classés (signal d'achat réel) --
    #    sauf si USDC est dépeg (#187) : le pricing de tout ce portefeuille suppose un USD
    #    stable, on bloque les NOUVELLES entrées (les positions déjà ouvertes ne sont pas
    #    touchées) tant que le dépeg n'est pas résorbé.
    # #194 -- pivot momentum multi-chaînes : quand NI candidates NI analyzer ne sont
    # fournis (le cas réel du heartbeat, run_paper_cycle(notifier=...) sans arguments),
    # remplace le défaut candidate_ranking.top_candidates()/_default_analyzer (VC-thesis,
    # poche 85%) par le pipeline momentum pour CE TEST -- décision opérateur explicite,
    # réversible, screened_pool/safety_screen non touchés. Tout appelant qui fournit
    # SON PROPRE candidates ou analyzer garde le comportement historique inchangé.
    if candidates is None and analyzer is None:
        candidates, _momentum_chain_by_contract = await _momentum_candidates_and_chain_map(limit=20)
        analyzer = _default_momentum_analyzer(
            _momentum_chain_by_contract, weekly_context=weekly_context, current_regime=current_regime,
        )
    elif candidates is None:
        from aria_core.skills.candidate_ranking import top_candidates

        candidates = [c.contract for c in await top_candidates(20)]

    # Rien à acheter -> pas la peine de vérifier le dépeg (évite un appel réseau inutile
    # à chaque cycle, y compris quand aucun candidat n'est proposé ce tour).
    depeg_pct = None
    depegged = False
    if candidates:
        depeg_check = depeg_check or risk.usdc_depeg_pct
        try:
            depeg_pct = await depeg_check()
        except Exception as exc:  # noqa: BLE001
            logger.info("paper_cycle: vérif dépeg USDC échouée (%s)", exc)
            depeg_pct = None
        depegged = depeg_pct is not None and depeg_pct > risk.USDC_DEPEG_THRESHOLD_PCT
    actions["usdc_depeg_pct"] = depeg_pct
    actions["depeg_blocked"] = depegged

    if depegged:
        logger.warning(
            "paper_cycle: USDC dépeg %.2f%% (> seuil %.2f%%) -- nouvelles entrées bloquées ce cycle",
            (depeg_pct or 0.0) * 100, risk.USDC_DEPEG_THRESHOLD_PCT * 100,
        )
        return actions

    analyzer = analyzer or _default_analyzer
    # On ne re-rentre pas un nom qu'on vient de SORTIR ce tour (évite le churn : une sortie
    # sur stop suiveur/dernier palier exige un nouveau signal au tour suivant, pas un rachat
    # immédiat).
    closed_this_cycle = {c["contract"] for c in actions["closed"]}
    start = await starting_capital()
    # #186 -- palier souple : réduit de moitié l'allocation des NOUVELLES entrées (jamais
    # les positions déjà ouvertes) via ``risk_state.alloc_multiplier``, composé plus bas
    # avec le sizing par risque/ATR (ou son repli à paliers fixes). open_position applique
    # ENSUITE son propre plafond de risque par trade (défense en profondeur, cf.
    # size_position_by_risk).

    # Funnel par cycle (mandat #192, 16/07) : agrège POURQUOI chaque candidat évalué
    # n'a pas mené à un achat. Sans ça, une panne prolongée du seul garde-fou dur
    # (GoPlus, aucun repli -- cf. momentum_entry.py) produit exactement le même
    # symptôme observable (zéro nouvelle position) qu'un marché réellement sans
    # candidat valable -- indiscernables sans lire les logs applicatifs un par un,
    # ce qui va à l'encontre de l'objectif diagnostique du test 1M$ (comprendre
    # COMMENT ARIA trade, pas juste SI elle trade). Additif pur : ne change aucun
    # comportement de décision, seulement la visibilité. Le champ ``hold_reason``
    # (momentum_entry.py) alimente ce compteur ; un analyzer qui ne le fournit pas
    # (ex. le pilote VC-thesis historique, ``_default_analyzer``) tombe dans le
    # seau générique "unspecified", sans erreur.
    funnel: dict[str, int] = {}
    opened = 0
    for contract in candidates:
        if opened >= max_new:
            break
        if len(await get_open_positions()) >= MAX_POSITIONS:
            break
        if contract in closed_this_cycle:
            continue
        if await has_open(contract):
            continue
        try:
            sig = await analyzer(contract)
        except Exception as exc:  # noqa: BLE001 — une analyse qui plante n'arrête pas le cycle
            logger.info("paper_cycle: analyse %s échouée (%s)", contract, exc)
            funnel["analyzer_error"] = funnel.get("analyzer_error", 0) + 1
            continue
        if not sig:
            funnel["no_price_data"] = funnel.get("no_price_data", 0) + 1
            continue
        if sig.get("action") != "BUY":
            reason_code = sig.get("hold_reason") or "unspecified"
            funnel[reason_code] = funnel.get(reason_code, 0) + 1
            continue

        # 19/07 -- assoupli (décision opérateur explicite, cf. commentaire sur l'ancien
        # REENTRY_RR_MIN ci-dessus) : un contrat déjà clôturé redevient un candidat comme
        # un autre dès qu'un nouveau signal BUY se profile -- aucune barre supplémentaire.
        # Note informative seule (traçabilité de la thèse), jamais un filtre.
        if await _has_prior_close(contract):
            sig.setdefault("reasons", []).append(
                "re-entrée -- ce contrat a déjà eu une position clôturée précédemment"
            )

        price = sig.get("price")
        if not price:
            try:
                if using_default_price_lookup:
                    price = await price_lookup(contract, chain=sig.get("chain") or "base")
                else:
                    price = await price_lookup(contract)
            except Exception:  # noqa: BLE001
                price = None
        if not price or price <= 0:
            continue
        # 18/07 -- décision opérateur explicite ("plus agressive" = plus gros sur les
        # MEILLEURS setups, pas plus gros partout). 19/07 -- potential_score
        # (conviction_research.py) : None si la diligence fondamentale n'a rien
        # trouvé/est désactivée -- fail-open sur inconnu, ne bloque jamais le bonus
        # technique seul. volume_confirmed (momentum_entry._check_volume_confirmation,
        # revue croisée Gemini) : False -> malus de conviction, None/True -> aucun effet.
        #
        # 20/07 -- sizing HYBRIDE risque-cible/ATR (revue croisée Gemini round 7) :
        # quand ``entry_atr_pct`` est connu, le budget de risque du palier de
        # conviction (``conviction_risk_budget_pct``) est divisé par la largeur RÉELLE
        # du stop suiveur pour CE token (même fonction ``_effective_trail_pct`` que la
        # gestion de position -- jamais une largeur recalculée séparément, qui
        # pourrait diverger). Repli sur l'ancien système à paliers fixes
        # (``conviction_size_multiplier``) si ``entry_atr_pct`` est inconnu (analyzer
        # qui ne le fournit pas, ex. l'ancien pilote VC-thesis dormant) -- jamais un
        # budget de risque calculé sur une largeur de stop inventée.
        #
        # 20/07 (suite, bug réel trouvé en répondant à une question opérateur sur la
        # proportionnalité à la market cap) : le plafond ne doit PAS être le maximum
        # absolu (5 %) pour TOUS les paliers -- un ceiling partagé laissait un signal
        # MODÉRÉ ou FAIBLE sur un stop serré atteindre la même mise qu'un signal FORT
        # (dès que le stop tombe sous ~20 %/10 % respectivement), inversant l'intention
        # même des paliers de conviction. Le plafond de CHAQUE palier doit rester celui
        # de l'ancien système à paliers fixes (5 %/3.5 %/2 %) -- ``conviction_mult``
        # calculé une seule fois ci-dessous et réutilisé pour les DEUX chemins (plafond
        # du sizing par risque/ATR, ET multiplicateur direct du repli) garantit que le
        # nouveau système ne peut jamais dépasser ce que l'ancien aurait donné pour ce
        # MÊME palier -- seulement réduire en dessous, jamais égaliser vers le haut.
        risk_budget_pct = risk_guard.conviction_risk_budget_pct(
            sig.get("rr"), sig.get("align_score"), fundamental_score=sig.get("potential_score"),
            volume_confirmed=sig.get("volume_confirmed"),
        )
        conviction_mult = risk_guard.conviction_size_multiplier(
            sig.get("rr"), sig.get("align_score"), fundamental_score=sig.get("potential_score"),
            volume_confirmed=sig.get("volume_confirmed"),
        )
        entry_atr_pct = sig.get("entry_atr_pct")
        if risk_budget_pct is not None and entry_atr_pct:
            trail_pct = _effective_trail_pct(entry_atr_pct)
            base_alloc_usd = risk_guard.size_by_risk_budget(
                risk_budget_pct, trail_pct, start,
                ceiling_usd=conviction_mult * ALLOC_PCT * start,
            )
        else:
            base_alloc_usd = ALLOC_PCT * start * conviction_mult
        # 18/07 (suite, "frein à main" validé après revue) -- une fois l'objectif hebdo
        # déjà atteint, réduit de moitié les NOUVELLES entrées (jamais à zéro) : protège
        # le gain acquis sans jamais bloquer un setup exceptionnel doublement vérifié.
        # Règle DÉTERMINISTE (risk_guard), jamais confiée au LLM -- cf. discussion
        # opérateur 18/07 (séparation des rôles : le garde de sécurité détecte des
        # pièges, il ne dimensionne jamais une position). ``risk_state.alloc_multiplier``
        # (palier souple #186) et ce sizing par risque/ATR sont deux dampeners
        # orthogonaux (portefeuille vs. par-trade) -- toujours composés multiplicativement.
        pacing_mult = risk_guard.weekly_pacing_size_multiplier(weekly_context)
        # 20/07 -- Regime Switch : divise par 2 en régime macro Peur confirmé (préserve
        # le capital quand la liquidité se regroupe sur les gros actifs) -- même point
        # de composition que pacing_mult ci-dessus, 1.0 par défaut (Neutre/Euphorie).
        regime_mult = risk_guard.regime_size_multiplier(sig.get("regime"))
        entry_alloc_usd = base_alloc_usd * risk_state.alloc_multiplier * pacing_mult * regime_mult

        # 20/07 -- re-vérification de fraîcheur juste avant l'exécution (revue croisée
        # Gemini, cf. _fresh_rr/_execution_rr_still_valid plus haut) : ``price``
        # ci-dessus a été capturé tout au début de l'évaluation (avant honeypot/
        # concentration holders/cascade OHLCV/jusqu'à 2 appels LLM séquentiels) --
        # sur un token volatile, plusieurs secondes peuvent s'être écoulées. On
        # recalcule le R/R au prix RÉEL plutôt que de rejeter sur un simple % de
        # mouvement (root cause détaillée dans le commentaire de _fresh_rr) -- un
        # setup toujours bon au prix frais s'exécute, un setup dégradé passe au tour
        # suivant (jamais forcé sur une donnée obsolète ou un R/R qui ne tient plus).
        try:
            if using_default_price_lookup:
                fresh_price = await price_lookup(contract, chain=sig.get("chain") or "base")
            else:
                fresh_price = await price_lookup(contract)
        except Exception:  # noqa: BLE001 — une panne réseau ne doit jamais planter le cycle
            fresh_price = None
        fresh_rr = _fresh_rr(fresh_price, sig.get("target"), sig.get("invalidation"))
        if not _execution_rr_still_valid(sig.get("rr"), fresh_rr):
            funnel["price_stale_at_execution"] = funnel.get("price_stale_at_execution", 0) + 1
            continue
        # ``fresh_price`` est garanti valide ici dans le fonctionnement réel (``_fresh_rr``
        # renvoie None sur un prix manquant/invalide, donc ``_execution_rr_still_valid``
        # aurait déjà fail-closed ci-dessus) -- ce garde protège seulement contre un
        # ``_execution_rr_still_valid`` explicitement neutralisé (tests dédiés au sizing,
        # sans rapport avec ce garde précis), jamais atteint en production.
        if fresh_price and fresh_price > 0:
            price = fresh_price

        pos = await open_position(
            contract,
            sig.get("symbol", ""),
            price,
            target_price=sig.get("target"),
            invalidation_price=sig.get("invalidation"),
            alloc_usd=entry_alloc_usd,
            category=sig.get("category", ""),
            entry_security_json=sig.get("entry_security_json", ""),
            chain=sig.get("chain") or "base",
            # bug trouvé le 17/07 : ``sig.get("these")`` seul ne couvrait que l'ancien
            # analyseur VC-thesis (_default_analyzer, clé "these") -- l'analyseur momentum
            # (#194, evaluate_momentum_entry) construit une vraie liste "reasons" (setup
            # golden pocket/RSI, alignement technique, R/R) mais ne pose jamais "these",
            # donc `thesis` restait silencieusement None sur tous les trades momentum.
            thesis=sig.get("these") or "; ".join(sig.get("reasons") or []) or None,
            pool_liquidity_usd=sig.get("liquidity_usd"),
            entry_atr_pct=sig.get("entry_atr_pct"),
            # 20/07 -- Formule B : la discipline de sortie appliquée dépend de la pipeline
            # d'ENTRÉE réelle (cf. commentaire sur VC_MIN_LIQUIDITY_FLOOR_USD), jamais un
            # flag indépendant. "momentum" par défaut -- comportement inchangé pour tout
            # analyzer qui ne fournit pas ce champ.
            strategy=sig.get("strategy") or "momentum",
            # 20/07 -- Regime Switch : régime macro à l'entrée, verrouillé pour la vie
            # de la position (ratchet en gestion, cf. plus bas).
            entry_regime=sig.get("regime"),
        )
        if pos:
            opened += 1
            actions["opened"].append(pos)
            if notifier:
                try:
                    await notifier(format_buy_alert(pos))
                except Exception:  # noqa: BLE001
                    pass

    if funnel:
        actions["momentum_funnel"] = funnel
        logger.info("paper_cycle funnel (nouvelles entrées, %d candidats) : %s", len(candidates), funnel)
        # 19/07 -- persiste ce cycle pour un cumul consultable dans le temps
        # (momentum_funnel_log.py) : sans ça, ce funnel n'existait QUE dans les logs
        # applicatifs, jamais accumulé -- répond à la proposition d'ARIA elle-même
        # ("on log pendant 48h le compteur par étape... preuve avant opinion").
        # Best-effort : une panne d'écriture ne doit jamais casser un cycle de
        # trading réel pour une simple persistance de télémétrie.
        try:
            await momentum_funnel_log.record_funnel(funnel)
        except Exception as exc:  # noqa: BLE001
            logger.warning("paper_cycle: persistance du funnel échouée (%s)", exc)

    return actions
