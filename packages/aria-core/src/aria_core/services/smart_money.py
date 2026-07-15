"""Wallet-tracker smart-money — lecture seule, additif, jamais un déclencheur.

Méthode sourcée dans AGENTS.md : le smart money est un **comportement
mesurable**, pas une identité ou une taille de wallet. On analyse les
principaux holders d'un token (hors LP connu) pour repérer une convergence
sur les critères croisés documentés :
- cohérence dans le temps (pas un coup de chance) ;
- entrées précoces + tailles contrôlées (pas un seul apport massif) ;
- sorties disciplinées (vend par tranches, pas un dump total) ;
- concentration multi-wallets (plusieurs wallets indépendants convergent).

Faux signaux explicitement écartés : wash-trading (aller-retour avec la même
contrepartie), wallets contrat (équipe/vesting/LP), et l'absence de données
n'est jamais remplacée par une supposition (cf. AGENTS.md).

Ce module ne produit qu'un **signal de confirmation/contexte** — la règle
absolue « ne jamais copy-trader » s'applique : ce n'est jamais un déclencheur.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import fmean

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services.blockscout import (
    UNAVAILABLE,
    AddressInfo,
    BlockscoutClient,
    TokenHoldersResult,
    TokenTransfer,
)
from aria_core.services.wallet_scoring_weights import WEIGHTS

logger = logging.getLogger(__name__)

_MAX_WALLETS_DEFAULT = 8
_EARLY_ENTRY_WINDOW_SECONDS = 3 * 24 * 3600  # 3 jours après création de la paire
_LARGEST_BUY_SHARE_MAX = 0.7  # au-delà, l'entrée est jugée "massive", pas "contrôlée"
_WASH_TRADING_COUNTERPARTY_SHARE = 0.6
_MIN_TRANSFERS_FOR_WASH_CHECK = 3
_ZERO_ADDRESS = "0x" + "0" * 40

# Prix par tx_hash exact (14/07, complément pool+OHLCV -- cf. _hash_based_price) :
# stablecoins reconnus PAR ADRESSE DE CONTRAT (jamais par symbole -- un token
# peut usurper un symbole "USDC"), pour transformer un ratio de deux jambes
# on-chain en prix USD sans dépendre du pool/OHLCV. Base UNIQUEMENT pour ce
# chantier (adresses vérifiées une à une contre Blockscout le 14/07) --
# chaîne absente du dict = registre vide = repli systématique sur pool+OHLCV,
# pas un manque silencieux (cf. _hash_based_price).
_STABLECOIN_ADDRESSES_BY_CHAIN: dict[str, set[str]] = {
    "base": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (natif, Circle)
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC (bridged)
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI (bridged)
        "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",  # USDT (bridged)
    },
}

# Exploit "wrap/unwrap" (15/07, revue Gemini) : un script qui wrap/unwrap du
# ETH<->WETH des centaines de fois pour quelques centimes de gas débloquerait
# artificiellement WEIGHTS.min_total_swaps sans jamais prendre de risque de
# trading. Détection bon marché et SANS ambiguïté (contrairement au registre
# de protocoles DeFi documenté plus bas, hors de portée) : le wrapped-native
# token de chaque chaîne a une adresse canonique UNIQUE, et deposit()/withdraw()
# émettent un Transfer standard depuis/vers l'adresse zéro (mint/burn) -- pas
# de faux positif possible. Chaîne absente du registre = pas de protection
# (comportement dégradé documenté, même politique que `_STABLECOIN_ADDRESSES_BY_CHAIN`).
_WRAPPED_NATIVE_ADDRESSES: frozenset[str] = frozenset({
    "0x4200000000000000000000000000000000000006",  # WETH -- Base (predeploy standard)
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH -- Ethereum mainnet
})


def _is_wrap_unwrap_leg(transfer: TokenTransfer) -> bool:
    addr = (transfer.token_address or "").lower()
    if addr not in _WRAPPED_NATIVE_ADDRESSES:
        return False
    return (transfer.from_address or "").lower() == _ZERO_ADDRESS or (transfer.to_address or "").lower() == _ZERO_ADDRESS


# Extension de l'exploit wrap/unwrap (15/07, revue Gemini suite) : un swap
# stable<->stable (USDC<->USDT/DAI, pool à frais infimes, risque directionnel
# quasi nul) permet le même padding de WEIGHTS.min_total_swaps que wrap/
# unwrap, sans passer par un mint/burn -- non couvert par `_is_wrap_unwrap_leg`.
# Réutilise le registre stablecoin DÉJÀ existant (`_STABLECOIN_ADDRESSES_BY_CHAIN`,
# construit pour le pricing par hash exact) -- aucun nouveau registre à
# maintenir, contrairement au cas LST/wrapped (stETH<->wstETH, WBTC<->tBTC,
# rETH<->wETH) qui resterait un vrai trou (registre de correspondance peg par
# peg, hors de portée de ce correctif -- documenté comme limite ci-dessous).
_ALL_RECOGNIZED_STABLECOINS: frozenset[str] = frozenset().union(*_STABLECOIN_ADDRESSES_BY_CHAIN.values())


def _is_recognized_stablecoin(token_address: str | None) -> bool:
    return (token_address or "").lower() in _ALL_RECOGNIZED_STABLECOINS


def _is_stable_to_stable_peg_swap(tx_hash: str, transfers_by_tx: dict[str, list[TokenTransfer]]) -> bool:
    """Vrai si TOUTES les jambes touchant le wallet dans cette transaction sont
    des stablecoins reconnus (achat ET vente d'un côté comme de l'autre) --
    un swap stable<->stable, pas un vrai pari directionnel. Une seule jambe
    stablecoin (ex. achat d'un memecoin PAYÉ en USDC) n'est jamais concernée --
    `len(legs) >= 2` exige au moins un aller ET un retour."""
    legs = transfers_by_tx.get(tx_hash, [])
    return len(legs) >= 2 and all(_is_recognized_stablecoin(t.token_address) for t in legs)


@dataclass
class WalletBehavior:
    address: str
    is_contract: bool | None = None
    buys: int = 0
    sells: int = 0
    distinct_days: int = 0
    coherent_over_time: bool = False
    early_and_controlled: bool = False
    disciplined_exit: bool | None = None  # None = pas assez de sorties pour juger
    wash_trading_suspected: bool = False
    available: bool = True
    error: str | None = None

    @property
    def criteria_met(self) -> int:
        return sum(
            [
                self.coherent_over_time,
                self.early_and_controlled,
                bool(self.disciplined_exit),
            ]
        )

    @property
    def is_smart_candidate(self) -> bool:
        return (
            self.available
            and not self.wash_trading_suspected
            and not self.is_contract
            and self.criteria_met >= 2
        )


@dataclass
class SmartMoneySignal:
    wallets_analyzed: int = 0
    smart_wallets: list[str] = field(default_factory=list)
    score_delta: int = 0
    flags: list[str] = field(default_factory=list)
    available: bool = True
    error: str | None = None


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _select_top_wallets(
    holders: TokenHoldersResult, *, lp_address: str | None, max_wallets: int
) -> list[str]:
    lp = (lp_address or "").lower()
    candidates = [
        h
        for h in holders.holders
        if (h.address or "").lower() not in {lp, _ZERO_ADDRESS, ""}
    ]
    candidates.sort(key=lambda h: h.percentage or -1.0, reverse=True)
    return [h.address for h in candidates[:max_wallets]]


def _dominant_counterparty_share(
    transfers: list[TokenTransfer],
    wallet: str,
    *,
    lp_address: str | None,
    extra_exclusions: set[str] | None = None,
) -> float:
    """Part des échanges (hors LP/pool, achats+ventes) concentrée sur une seule contrepartie.

    Le LP/pool est exclu du calcul : la quasi-totalité des achats/ventes DEX
    transitent par lui, donc le compter ferait passer n'importe quel early
    buyer pour un cas de wash-trading. En dessous de `_MIN_TRANSFERS_FOR_WASH_CHECK`
    échanges hors LP, il n'y a pas assez de données pour juger — pas de suspicion.

    ``extra_exclusions`` (#157, correction 14/07) : ensemble d'adresses
    supplémentaires à exclure (au-delà du seul ``lp_address``) -- nécessaire
    quand ``transfers`` couvre PLUSIEURS tokens (un seul pool/LP statique ne
    suffit plus, cf. `_build_dex_infrastructure_exclusions`). Paramètre optionnel,
    n'affecte pas l'appel historique token-centrique (`_analyze_wallet_behavior`).
    """
    wallet_l = wallet.lower()
    excluded = {(lp_address or "").lower()} | {a.lower() for a in (extra_exclusions or ())}
    counterparties: dict[str, int] = {}
    total = 0
    for t in transfers:
        other = t.to_address if t.from_address.lower() == wallet_l else t.from_address
        other = (other or "").lower()
        if not other or other in excluded:
            continue
        counterparties[other] = counterparties.get(other, 0) + 1
        total += 1
    if total < _MIN_TRANSFERS_FOR_WASH_CHECK:
        return 0.0
    return max(counterparties.values()) / total


def _analyze_wallet_behavior(
    wallet: str,
    transfers: list[TokenTransfer],
    *,
    is_contract: bool | None,
    pair_created_at_ms: int | None,
    lp_address: str | None,
) -> WalletBehavior:
    wallet_l = wallet.lower()
    buys = [t for t in transfers if (t.to_address or "").lower() == wallet_l]
    sells = [t for t in transfers if (t.from_address or "").lower() == wallet_l]

    days = {
        ts.date()
        for t in (buys + sells)
        if (ts := _parse_timestamp(t.timestamp)) is not None
    }

    coherent = len(days) >= 2 and (len(buys) + len(sells)) >= 2

    early_and_controlled = False
    if buys and pair_created_at_ms is not None:
        buy_times = [ts for t in buys if (ts := _parse_timestamp(t.timestamp)) is not None]
        pair_created_at = datetime.fromtimestamp(
            pair_created_at_ms / 1000, tz=buy_times[0].tzinfo if buy_times else None
        )
        earliest = min(buy_times) if buy_times else None
        if earliest is not None:
            elapsed = (earliest - pair_created_at).total_seconds()
            amounts = [b.amount for b in buys if b.amount is not None]
            largest_share = (max(amounts) / sum(amounts)) if amounts and sum(amounts) > 0 else None
            controlled_size = (
                len(buys) >= 2 and (largest_share is None or largest_share <= _LARGEST_BUY_SHARE_MAX)
            )
            early_and_controlled = 0 <= elapsed <= _EARLY_ENTRY_WINDOW_SECONDS and controlled_size

    disciplined_exit: bool | None = None
    if sells:
        disciplined_exit = len(sells) >= 2 or (len(sells) == 1 and len(buys) >= 1)

    wash_suspected = (
        _dominant_counterparty_share(buys + sells, wallet, lp_address=lp_address)
        >= _WASH_TRADING_COUNTERPARTY_SHARE
    )

    return WalletBehavior(
        address=wallet,
        is_contract=is_contract,
        buys=len(buys),
        sells=len(sells),
        distinct_days=len(days),
        coherent_over_time=coherent,
        early_and_controlled=early_and_controlled,
        disciplined_exit=disciplined_exit,
        wash_trading_suspected=wash_suspected,
        available=True,
        error=None,
    )


async def analyze_smart_money(
    token_address: str,
    holders: TokenHoldersResult,
    *,
    client: BlockscoutClient,
    lp_address: str | None = None,
    pair_created_at_ms: int | None = None,
    max_wallets: int = _MAX_WALLETS_DEFAULT,
) -> SmartMoneySignal:
    """Analyse lecture seule des top holders — signal de confirmation/contexte uniquement."""
    if not holders.available:
        return SmartMoneySignal(available=False, error=holders.error or UNAVAILABLE)

    wallets = _select_top_wallets(holders, lp_address=lp_address, max_wallets=max_wallets)
    if not wallets:
        return SmartMoneySignal(wallets_analyzed=0, available=True)

    token_l = token_address.lower()
    smart_wallets: list[str] = []
    unavailable_count = 0

    for wallet in wallets:
        info = await client.get_address_info(wallet)
        transfers_result = await client.get_token_transfers(wallet, limit=100)

        if not transfers_result.available:
            unavailable_count += 1
            continue

        matched = [
            t
            for t in transfers_result.transfers
            if (t.token_address or "").lower() == token_l
        ]

        behavior = _analyze_wallet_behavior(
            wallet,
            matched,
            is_contract=info.is_contract if info.available else None,
            pair_created_at_ms=pair_created_at_ms,
            lp_address=lp_address,
        )
        if behavior.is_smart_candidate:
            smart_wallets.append(wallet)

    flags: list[str] = []
    score_delta = 0

    if unavailable_count:
        flags.append(
            f"Smart-money : {unavailable_count}/{len(wallets)} wallet(s) non analysable(s) "
            f"({UNAVAILABLE})."
        )

    if len(smart_wallets) >= 2:
        score_delta = 8
        flags.append(
            f"Smart-money : {len(smart_wallets)} wallets parmi les top holders montrent un "
            "comportement convergent (cohérence temporelle, entrées échelonnées) — "
            "confirmation contextuelle, jamais un déclencheur."
        )
    elif len(smart_wallets) == 1:
        flags.append(
            "Smart-money : 1 seul wallet au comportement convergent détecté — "
            "concentration insuffisante pour confirmer (critère multi-wallets non atteint)."
        )

    return SmartMoneySignal(
        wallets_analyzed=len(wallets),
        smart_wallets=smart_wallets,
        score_delta=score_delta,
        flags=flags,
        available=True,
        error=None,
    )


# ============================================================================
# #157 -- évaluateur wallet-centrique multi-token ("smart wallet" maison)
#
# Extension du module ci-dessus : au lieu d'analyser les top holders d'UN
# token, on prend 1-3 adresses de wallet et on tire TOUT leur historique de
# trades à travers PLUSIEURS tokens (via `get_token_transfers` paginé côté
# `blockscout.py`), on le valorise (PnL FIFO) via GeckoTerminal, et on en
# dérive un score composite + un drapeau "suspect positif" séparé + une thèse
# LLM. Toujours un signal de confirmation/contexte -- jamais un déclencheur
# (même règle absolue que `analyze_smart_money` ci-dessus).
#
# Quatre couches (recherche sourcée, docs/aria-learning-inbox/
# 2026-07-14-recherche-equation-smart-wallet-scoring-157.md) :
#   1. Disqualifiants durs (wash-trading généralisé, wallet-contrat, wallets
#      "convergents" = même entité via réutilisation d'adresse de dépôt,
#      financement par un wallet malveillant connu).
#   2. Score composite (PnL/win-rate FIFO, Sortino, récurrence acheteur
#      précoce multi-lancements avec conditions techniques à l'entrée,
#      diversification, drawdown wallet).
#   3. Drapeau "suspect positif" séparé (jamais fondu dans le score moyen).
#   4. Journalisation prête pour calibration continue (pas de recalibration
#      construite maintenant, juste l'écriture).
# ============================================================================
#
# LIMITES STRUCTURELLES CONNUES (15/07, angles morts on-chain identifiés via
# revue externe croisée -- délibérément DOCUMENTÉES, pas corrigées, pour ne
# pas faire exploser la complexité du moteur FIFO central) :
#
# - DeFi (dépôt en collatéral / apport de liquidité) : `_analyze_wallet_multi_token`
#   traite TOUT transfert sortant d'un token suivi comme une jambe de vente
#   FIFO valorisée au marché (cf. `sells` ci-dessous, symétrique de `buys` par
#   construction -- ni l'un ni l'autre ne distingue "vendu" de "déplacé").
#   Un dépôt Aave (collatéral) ou Uniswap (LP) fait donc apparaître un PnL
#   réalisé fictif au moment du dépôt (rien n'a été vendu), et un retrait
#   ultérieur (le token revient) s'enregistre comme un rachat à un tout
#   nouveau prix d'entrée, déconnecté du prix réel initial. Pas de signal bon
#   marché et fiable pour distinguer un jeton de reçu (aToken/LP token) d'un
#   swap réel sans registre de protocoles codé en dur (charge de maintenance
#   permanente, faux positifs probables) -- non construit.
# - Ponts cross-chain : le scan multi-chaînes (`chain_clients`, clé composite
#   "{chaîne}:{adresse}") consolide un score PAR WALLET mais ne relie jamais
#   une sortie sur une chaîne à l'arrivée correspondante sur une autre. Un
#   bridge Ethereum->Arbitrum s'enregistre comme une vente FIFO côté source
#   (prix marché au transfert sortant) ET un rachat FIFO indépendant côté
#   destination (prix marché à l'arrivée) -- même défaut structurel que le
#   cas DeFi ci-dessus, avec en plus la difficulté de corréler deux jambes sur
#   deux jeux de données de chaînes différentes (montant net des frais de
#   pont, fenêtre de temps plausible, registre des contrats de pont connus).
#
# Impact commun aux deux : les trades FIFO fictifs ainsi créés polluent
# TOUTES les métriques dérivées de `cumulative_trades` (win_rate, PnL,
# Sortino, drawdown, tendance de santé) à égalité avec de vrais trades --
# pas une marge d'erreur isolée sur un seul chiffre. Population concernée :
# plus significative chez les wallets qui font aussi du yield/LP/multi-L2 que
# chez un pur trader memecoin Base -- pas négligeable pour autant chez une
# vraie "smart money" sérieuse. Aucune correction prévue à court terme --
# à rouvrir si un besoin business précis (ex. dossier funding, due diligence
# poussée sur un wallet donné) le justifie.
#
# SUITE (15/07, second passage -- revue croisée Gemini/ChatGPT/Grok + web
# search Sybil/Nansen/Arkham). Corrigés ce passage (cf. code + WEIGHTS) :
# exploit wrap/unwrap ETH<->WETH sur le seuil de swaps (`_is_wrap_unwrap_leg`),
# dilution du trim anti-chance par volume de trades (`robust_trim_pct`),
# dust/scam-pool via plancher de liquidité confirmée (`min_pool_liquidity_
# usd_for_pricing`), transparence sur la confiance du cost-basis
# (`price_confirmation_ratio`) et sur les ventes non appariées
# (`unmatched_sell_events`), diversification pondérée par capital en plus du
# comptage. Vérifiés et REJETÉS (déjà correctement gérés, pas un vrai trou) :
# division par zéro du Sortino (`_sortino_ratio` retourne déjà `None` si
# `downside` est vide, AVANT tout calcul de déviation -- le garde
# `downside_deviation == 0` qui suit est du code défensif mort, jamais
# atteignable, mais inoffensif) ; win rate non pondéré par la taille des
# pertes (déjà compensé par construction -- Sortino/PnL restent des axes
# séparés, jamais fondus avec le win rate, donc un "99% de gains + 1 perte
# catastrophique" reste visible ailleurs).
#
# Documentés, DÉLIBÉRÉMENT non corrigés ce passage (trop coûteux/complexes
# pour un correctif ponctuel, ou hors de portée d'un ajustement de seuil) :
#
# - Coordination Sybil / multi-wallets (revue Grok, LE plus important des
#   points non résolus) : un seul opérateur peut faire tourner des dizaines
#   de wallets qui passent chacun le seuil d'échantillon et performent de
#   façon coordonnée -- chaque wallet a un bon score individuel, et
#   collectivement ils biaisent le classement comparatif (percentiles) à
#   mesure que le pool de wallets suivis grossit. Le trim anti-chance n'y
#   change rien (un Sybil bien orchestré répartit ses outliers). Confirmé par
#   recherche externe (15/07) : c'est un problème structurel connu de toute
#   analyse wallet-by-wallet sans clustering d'entité -- Nansen/Arkham/
#   Chainalysis/TRM s'appuient sur le clustering par SOURCE DE FINANCEMENT
#   PARTAGÉE (même famille que notre `_pairwise_convergence` existant, cf.
#   Victor FC 2020) mais à l'échelle d'un GRAPHE sur toute la population
#   suivie, pas juste une comparaison pairwise entre les 1-3 wallets soumis
#   ENSEMBLE dans un seul appel -- notre version actuelle est donc la même
#   famille d'heuristique, juste bien plus étroite en portée. Les approches
#   les plus robustes (Chainalysis/TRM) utilisent désormais des graph neural
#   networks entraînés sur des clusters Sybil labellisés, nettement plus dur
#   à contourner qu'un clustering par heuristique seule -- hors de portée
#   d'un correctif ponctuel, un vrai chantier séparé si jamais entrepris.
# - Farming du seuil d'entrée / wash-trading léger (revue Grok) : au-delà du
#   wrap/unwrap déjà fermé ci-dessus, rien n'empêche un wallet de padder
#   `min_total_swaps` avec des allers-retours minuscules sur un VRAI token
#   liquide (slippage/frais réels à chaque tour, donc plus coûteux que le
#   wrap/unwrap, mais pas impossible). Piste bon marché identifiée mais pas
#   construite (recherche externe 15/07) : les wash-traders utilisent
#   typiquement des MONTANTS RONDS et un impact de prix quasi nul malgré le
#   volume -- un détecteur dédié serait un complément naturel à
#   `_dominant_counterparty_share` existant, banqué pour un futur passage.
# - Absence de benchmark marché (alpha vs beta, revue Grok) : un wallet qui
#   fait simplement du bêta pur (long BTC/ETH en marché haussier) peut sortir
#   d'excellents win rate/Sortino/PnL sans aucune compétence particulière --
#   le système mesure la qualité du footprint on-chain, pas la valeur ajoutée
#   par rapport au marché. Nécessiterait une série de rendement de référence
#   (BTC/ETH/indice DeFi) et un calcul d'alpha dédié -- une vraie
#   fonctionnalité à chiffrer séparément, pas un ajustement de seuil.
# - Gaming structurel des tests de robustesse (revue Grok) : un wallet peut
#   délibérément prendre ses pires trades en tout début d'activité (avant que
#   l'historique ne compte vraiment) pour "consommer" le budget du trim anti-
#   chance, ou structurer son activité pour que la 2e moitié de la courbe de
#   santé semble artificiellement meilleure. Plus facile juste au-dessus des
#   seuils minimums (30 trades pour le trim, 10 pour la courbe de santé) --
#   limite inhérente à tout seuil statique, pas un bug isolé corrigible.
# - MEV / arbitrage atomique / flash loans (revue Grok) : ces stratégies a
#   risque quasi nul peuvent produire un win rate et un Sortino excellents
#   (downside quasi inexistant par construction) et passent bien le trim
#   anti-chance (trades uniformément bons, pas d'outlier à retirer). Le
#   système les traite comme des trades normaux -- les distinguer exigerait
#   une détection d'atomicité/flash-loan au niveau de la transaction
#   (bytecode/call-trace), une donnée que Blockscout ne fournit pas
#   nativement -- hors de portée sans une nouvelle source de données dédiée.
# - Biais de survie du gate d'échantillon (revue ChatGPT) : le seuil
#   `min_wallet_age_days`/`min_total_swaps` sélectionne les wallets qui ont
#   SURVÉCU assez longtemps pour l'atteindre -- les wallets catastrophiques
#   meurent souvent avant, et les meilleurs traders peuvent changer de wallet
#   régulièrement (opsec). Le classement devient donc un classement des
#   wallets SURVIVANTS, pas nécessairement des meilleurs traders. Inhérent à
#   tout gate d'échantillon minimum -- pas un bug, un compromis assumé (même
#   doctrine que `docs/protocole-argent-reel.md` : échantillon minimum avant
#   de faire confiance, quitte à exclure des cas valides).
# - Choix méthodologique FIFO (revue ChatGPT) : toutes les métriques utilisent
#   un modèle FIFO unique pour assurer la COMPARABILITÉ entre wallets -- un
#   modèle LIFO/HIFO donnerait un PnL différent sur des séquences achat/vente
#   partielles répétées. Ce n'est pas un choix fiscal (aucune prétention de
#   conformité fiscale, seulement une mesure de performance comparable) --
#   assumé, pas un défaut.
# - Paradoxe du percentile / population de comparaison non représentative
#   (revue Gemini + ChatGPT) : le classement comparatif compare CE wallet aux
#   AUTRES wallets déjà passés par `/walletscore` -- pas un échantillon
#   représentatif du marché. Si l'outil devient massivement utilisé par des
#   amateurs, un trader moyen se retrouve artificiellement dans un haut
#   percentile ; si seuls des pros l'utilisent, l'inverse. Le percentile d'un
#   même wallet peut donc bouger dans le temps SANS qu'aucun de ses propres
#   trades n'ait changé -- uniquement parce que la démographie de la base
#   suivie a évolué. Un benchmark fixe (échantillon aléatoire représentatif
#   de la blockchain, ex. 5000 wallets actifs) réglerait le problème mais
#   coûterait cher (faire tourner ce même pipeline multi-appels-réseau sur des
#   milliers de wallets, en continu) -- non construit. `compared_against_n_
#   wallets` reste affiché à côté du percentile pour au moins signaler l'ordre
#   de grandeur de la population de comparaison (jamais caché).
# - Découpage chronologique par NOMBRE de trades pour la courbe de santé
#   (revue ChatGPT) : `_health_trend` compare la 1ère à la 2e moitié par
#   nombre de trades, pas par fenêtre calendaire -- un wallet actif 3 ans puis
#   dormant 1 an peut voir sa "tendance" dominée par une reprise récente
#   plutôt que refléter une vraie évolution de compétence. Un découpage par
#   fenêtre calendaire (milieu de la durée totale, pas du nombre de trades)
#   serait plus robuste à ce cas -- piste identifiée, pas construite ce
#   passage (refonte de la fonction, effet sur le comportement existant à
#   valider séparément).
#
# TROISIÈME PASSAGE (15/07, même soirée -- revue croisée round 2/3, Gemini x2
# + ChatGPT + Grok). Corrigés ce passage : swaps stable<->stable exclus du
# compteur de swaps (extension de l'exploit wrap/unwrap ci-dessus, cf.
# `_is_stable_to_stable_peg_swap`) ; métriques sur fenêtre récente
# (`_recent_window_metrics`, réponse au biais temporel -- ChatGPT) ; clarifié
# et verrouillé par test que le fail-open sur liquidité inconnue n'est jamais
# atteint par le vrai client GeckoTerminal (cf. commentaire sur
# `pool_liquid_enough` plus bas). Vérifié et REJETÉ (répété deux fois par
# Gemini, toujours faux contre le code) : la division par zéro du Sortino --
# `_sortino_ratio` retourne `None` dès que `downside` est vide, avant tout
# calcul de déviation, verrouillé par `test_no_losses_unavailable_not_infinite`.
#
# Documentés, DÉLIBÉRÉMENT non corrigés ce passage :
#
# - Paires LST/wrapped à corrélation quasi parfaite (revue Gemini) : au-delà
#   du stable<->stable maintenant fermé, WBTC<->tBTC, stETH<->wstETH,
#   rETH<->wETH permettent le même padding à coût/risque quasi nul. Pas de
#   registre existant à réutiliser ici (contrairement aux stablecoins) --
#   construire et maintenir un registre de correspondance peg par peg est le
#   même type de charge que le registre de protocoles DeFi déjà écarté plus
#   haut. Trou plus étroit qu'avant (le sous-cas stable<->stable, sans doute
#   le plus utilisé en pratique, est fermé), mais réel.
# - Dilution du trim anti-chance par micro-trades (revue Gemini, raffinement) :
#   `_robust_pnl_check` trie par PnL EN DOLLARS, pas par rendement en %. Un
#   attaquant qui veut faire sortir un trade légendaire (ex. +10 000% sur une
#   position minuscule) du trim doit padder avec des trades dont le PnL EN
#   DOLLARS est comparable ou supérieur -- pas de simples micro-trades à
#   quelques centimes, qui restent alors en dessous du trade légendaire dans
#   le tri et continuent de se faire trimmer les premiers. La vulnérabilité
#   réelle est donc plus étroite que "spammer des micro-trades gratuits" :
#   elle exige un trade légendaire lui-même de faible montant EN DOLLARS
#   malgré un pourcentage énorme, ET un déploiement de capital réel sur les
#   trades de padding pour dépasser ce montant -- un cas plus contraint, pas
#   éliminé pour autant. Piste de raffinement identifiée, pas construite : un
#   trim par écart-type/z-score (retirer les trades à plus de X écarts-types
#   de la médiane) serait insensible à l'axe $ vs % choisi, mais change la
#   méthodologie plus profondément (instabilité du z-score lui-même sur petit
#   échantillon à gérer) -- candidat pour un futur passage, pas ce soir.
# - Pondération égale par trade (pas par capital) de win_rate/trim/health_trend/
#   SORTINO (revue ChatGPT, précisé 15/07 -- revue externe : Sortino avait été
#   omis de cette liste par erreur, alors qu'il partage exactement le même
#   défaut, cf. ci-dessous) : seule la diversification a désormais une variante
#   pondérée par capital (cf. plus haut). Win rate, trim anti-chance, courbe de
#   santé ET Sortino restent comptés/calculés PAR TRADE en % de rendement --
#   un trade de 500 000$ pèse autant qu'un trade de 10$. Choix ASSUMÉ pour
#   win_rate/trim/health_trend (le comptage par trade mesure autre chose : la
#   capacité à trouver des gagnants sur des paris indépendants) -- mais pour
#   SORTINO spécifiquement, la conséquence est plus trompeuse qu'un simple
#   choix de méthodologie : un ratio présenté comme "rendement ajusté au
#   risque" peut afficher un chiffre POSITIF alors que le PnL réel en dollars
#   est NÉGATIF. Démonstration chiffrée vérifiée (5 trades, seuil minimum
#   `WEIGHTS.min_closed_trades_for_sortino` atteint) : 4 micro-trades à +100%
#   sur une mise de 1$ chacun (+4$ au total) + 1 trade majeur à -50% sur une
#   mise de 1000$ (-500$) -- PnL réel = -496$ (perte nette), mais
#   mean(return_i) = 0.7, downside_deviation = 0.5, Sortino = 1.4 (positif,
#   "honorable"). **Corrigé partiellement (15/07)** : `sortino_pnl_
#   contradiction` détecte et signale VISIBLEMENT le cas le plus flagrant et
#   vérifiable à coup sûr (contradiction de SIGNE entre Sortino et PnL réel,
#   jamais une nuance à interpréter), affiché en ATTENTION à côté du Sortino
#   -- mais ne corrige PAS le biais sous-jacent lui-même (un Sortino pondéré
#   par la taille de position, calculé sur la courbe de valeur du portefeuille
#   plutôt que sur les rendements unitaires, serait une refonte méthodologique
#   plus profonde -- non entreprise, même arbitrage que les autres métriques
#   non pondérées ci-dessus).
# - Manipulation du point de bascule de la courbe de santé (revue Grok,
#   précision sur la limite déjà notée) : au-delà du simple découpage par
#   nombre de trades plutôt que par fenêtre calendaire, un wallet peut
#   délibérément accélérer ou ralentir son activité pour placer le point de
#   bascule à un moment favorable de sa propre courbe de PnL -- un levier de
#   manipulation actif, pas seulement un angle mort passif. Même refonte
#   candidate que déjà notée (découpage calendaire), pas construite.
# - Coordination Sybil, absence de benchmark marché, gaming structurel des
#   tests de robustesse, MEV/arbitrage atomique, farming du seuil d'entrée,
#   asymétrie de couverture protocolaire : reconfirmés par la revue round 2/3
#   (Grok) comme toujours non résolus -- aucun élément nouveau qui changerait
#   l'évaluation déjà écrite plus haut, pas de duplication de l'entrée.
#
# QUATRIÈME PASSAGE (15/07, revue round 4 -- ChatGPT + Grok). Précision
# apportée (pas un nouveau mécanisme, une clarification de portée) :
#
# - Migrations de token (v1->v2), redénominations, fusions/splits, airdrops de
#   remplacement (revue ChatGPT) : vérifié -- ces événements ne créent PAS un
#   troisième mécanisme de trou, ils se ramènent aux DEUX catégories déjà
#   documentées ci-dessus selon leur implémentation on-chain : (a) migration
#   via un NOUVEAU contrat (cas le plus courant, ex. un v1 envoyé/brûlé +
#   un v2 reçu séparément) = exactement le même défaut que le dépôt DeFi/pont
#   cross-chain (deux jambes sur deux adresses de token différentes, jamais
#   reliées, PnL fictif des deux côtés) ; (b) redénomination/split SANS
#   changement d'adresse (réinterprétation du solde sur le même contrat) =
#   exactement le même défaut que le rebasing (déjà capté, sans être crédité,
#   par `unmatched_sell_events`). Documenté ici comme exemples concrets
#   supplémentaires des deux limites déjà écrites, pas une nouvelle limite.
# - Le drapeau "suspect positif" comme cible de manipulation inversée (revue
#   Grok) : parce que ce drapeau est VISIBLE et peut être lu comme un signal
#   fort, un acteur sophistiqué peut délibérément calibrer son activité pour
#   franchir simultanément les seuils sur ≥3 axes (win rate, Sortino,
#   diversification, récurrence) sans avoir de vrai edge -- le drapeau devient
#   alors lui-même un objectif à optimiser plutôt qu'un signal fiable. Limite
#   inhérente à tout indicateur seuil VISIBLE (le rendre visible sert la
#   transparence mais crée la cible) -- pas de parade sans le rendre plus
#   coûteux à déclencher artificiellement (ex. exiger une confirmation
#   indépendante), non construit.
# - Biais de sélection de la couche 2 (revue Grok) : la priorité "round-trip
#   confirmé -> récence -> nombre de trades" (`_select_tokens_for_deep_
#   analysis`) sous-représente structurellement, à un instant T (avant
#   `full_coverage=True`), les holders long-terme de nombreuses petites
#   positions au profit des traders très actifs sur peu de tokens -- pas un
#   bug, un ordre de priorité assumé (round-trip d'abord parce qu'une position
#   encore ouverte ne peut jamais produire de trade clôturé), mais un vrai
#   biais tant que la couverture n'est pas complète. Le scan incrémental
#   cumulatif finit par tout couvrir, mais un score consulté AVANT couverture
#   complète reste construit sur un sous-ensemble non représentatif -- déjà
#   partiellement divulgué (`full_coverage`/`tokens_scanned_cumulative`
#   affichés), pas éliminé pour autant.
#
# CINQUIÈME PASSAGE (15/07, revue Gemini -- audit final). Deux points, TRAITÉS
# DIFFÉREMMENT après vérification :
#
# - Distorsion FIFO sur fluctuations de supply HORS-TRANSACTION -- rebases
#   POSITIFS **ET NÉGATIFS** (renommage explicite demandé par Gemini, limite
#   déjà en partie gérée) : le cas positif (solde qui augmente sans transfert,
#   ex. rendement stETH) était déjà documenté et capté sans être crédité
#   (`unmatched_sell_events`). Le cas NÉGATIF (solde divisé sans transfert,
#   ex. rebase négatif AMPL-like) est le miroir exact et n'était PAS nommé
#   explicitement : la file d'attente FIFO continue de porter les jetons
#   "fantômes" (jamais purgés faute d'événement on-chain pour réagir), qui se
#   font consommer par une vente ultérieure à un prix d'achat obsolète -- un
#   trade économiquement neutre peut alors s'enregistrer comme un profit
#   fictif. Même famille de cause que le cas positif (solde qui change hors-
#   transaction), symétrique en direction. Documenté ici tel quel, non
#   corrigé -- même arbitrage que le reste des cas rebasing/DeFi/ponts.
# - "Effondrement par perte fictive" via dusting ciblé sur pool manipulé
#   (revue Gemini) -- VÉRIFIÉ COMME RÉEL contre le code : un pool créé juste
#   au-dessus du plancher de liquidité ($35k > $30k) avec un prix ponctuel
#   manipulé peut faire accepter un coût d'acquisition démesuré (OHLCV) sur
#   un token dusté, puis un prix de sortie normal/crashé clôture le trade en
#   perte fictive massive -- confirmé plausible ligne par ligne (le plancher
#   de liquidité seul ne protège QUE contre un pool durablement thin, pas
#   contre un pic de prix ponctuel sur un pool qui clarifie le plancher).
#   **Première piste de correctif testée et REJETÉE après vérification** :
#   réutiliser `_pool_is_plausible` (déjà existant, geckoterminal.py) pour
#   filtrer aussi ce cas -- ne fonctionne PAS ici : cette fonction renvoie
#   délibérément `True` (plausible) quand le volume 24h est nul ou quasi nul
#   ("un token légitime peut simplement n'avoir eu aucun trade récent", cf.
#   sa docstring) -- exactement le profil d'un pool de scam peu/jamais
#   tradé par personne d'autre que l'attaquant. Une règle de correction
#   robuste (comparer le prix d'une bougie précise à ses voisines temporelles
#   pour détecter un pic isolé, ou exiger une corroboration de marché
#   indépendante avant de faire confiance à un cost-basis OHLCV sur un
#   transfert non-swap) reste un vrai chantier de conception -- risque de
#   nouveaux faux positifs (un memecoin légitimement volatil, ou un retrait
#   CEX légitime dont la contrepartie n'est jamais le pool) non résolu ce
#   soir avec la rigueur que ce point mérite. **Non corrigé, signalé comme
#   la limite la plus sérieuse actuellement ouverte** (coût d'attaque ~50$ de
#   gas, déterministe, ciblable sur n'importe quel wallet suivi) -- à traiter
#   comme un chantier dédié, pas un correctif de fin de soirée.
# ============================================================================
#
# SIXIÈME PASSAGE (15/07, revue Gemini + Grok convergentes). Corrigés ce
# passage : immunité aux rug pulls (plancher de liquidité désormais
# ASYMÉTRIQUE -- gate uniquement les jambes d'achat, jamais les ventes, cf.
# commentaire sur `pool_liquid_enough`/`_price_lookup` plus haut -- bug réel
# dans le correctif #160, pas une simple limite résiduelle) ; pollution du
# percentile par des scores partiels (`_latest_scored_wallets` exclut
# désormais les fiches `full_coverage=False` de la population de comparaison).
# **Portée honnête du correctif rug-pull** : ne résout PAS tous les cas --
# seulement celui où la jambe d'ACHAT a un prix établi indépendamment de la
# liquidité actuelle (prix par tx_hash exact, cf. `TestRugPullAsymmetricFloor`).
# Si l'achat ET la vente dépendent tous deux du SEUL instantané de liquidité
# actuel du pool (majorité des jambes, pas de stablecoin dans la tx), l'achat
# reste bloqué par le plancher (comportement inchangé, protection anti-dust
# intacte) -- le trade ne se clôture alors toujours pas (FIFO exige les deux
# bords valorisés), donc la perte reste invisible dans ce sous-cas précis.
# Root cause partagée avec la vulnérabilité dusting ci-dessus : aucune donnée
# de liquidité HISTORIQUE (par timestamp) n'est disponible, seulement un
# instantané au moment du scan -- même limite structurelle, pas résolue.
#
# Documenté, non corrigé -- wash-trading en petit cluster coordonné (2-5
# wallets, revue Gemini + Grok convergentes) : le disqualifiant de couche 1
# (contrepartie unique ≥60%) et la convergence pairwise (même source de
# financement) sont tous deux CONTOURNABLES simultanément par un acteur qui
# répartit son volume de complaisance sur 2-4 CONTREPARTIES distinctes
# (ex. wallet A envoie 30% vers B, 30% vers C, 40% de trades légitimes --
# aucune contrepartie unique ne franchit 60%) tout en utilisant des sources de
# financement différentes ou étalées dans le temps pour chaque wallet du
# cluster (évite la convergence pairwise stricte). Chaque wallet passe alors
# individuellement tous les disqualifiants et le seuil de 100 swaps, entre
# dans le classement comparatif, et le cluster peut biaiser collectivement les
# percentiles ou faire lever le drapeau "suspect positif" de façon
# coordonnée. Niveau de coordination intermédiaire entre le wash-trading
# intra-wallet (déjà couvert) et le Sybil industriel à grande échelle (déjà
# documenté ci-dessus) -- même famille de trou (pas de clustering d'entité au-
# delà de la convergence pairwise), à fermer par le même chantier dédié si
# entrepris (pas un correctif de seuil ponctuel : élargir le seuil de 60% ou
# le nombre de wallets vérifiés en pairwise ne fait que déplacer la taille de
# cluster minimale requise pour contourner, jamais l'éliminer).
#
# SEPTIÈME PASSAGE (15/07, revue DeepSeek -- 4e IA externe). Un point corrige
# une sur-affirmation de mon propre commentaire (cf. `buy_blocked_thin_
# liquidity` plus haut -- gains fictifs symétriques par vente sur pool
# manipulé, dorénavant reformulé honnêtement). Les autres, vérifiés réels et
# nouveaux (pas de doublon avec les passages précédents) :
#
# - Drawdown/Sortino calculés SEULEMENT sur le PnL RÉALISÉ (`_max_drawdown_pct`/
#   `_sortino_ratio` ne lisent que `closed_trades`, jamais `open_position_
#   amount`) : un wallet qui porte une position ouverte massivement en perte
#   latente (achetée puis jamais revendue, donc jamais "réalisée") affiche un
#   drawdown nul ou très faible alors que son risque réel est énorme -- la
#   mesure de risque est structurellement optimiste tant qu'une position
#   reste ouverte. Corriger exigerait une vraie fonctionnalité de mark-to-
#   market (prix courant fiable par token ouvert + coût moyen pondéré de la
#   file FIFO restante + redéfinition de ce que "drawdown" mesure -- courbe
#   d'équité réalisée+latente plutôt que réalisée seule) : même famille de
#   chantier dédié que le benchmark alpha/Sybil déjà différés, pas un ajout
#   de seuil. Non construit.
# - `price_confirmation_ratio`/`price_confidence_low` mesurent la confiance
#   de MÉTHODE (prix par ratio stablecoin exact vs. repli OHLCV estimé), PAS
#   la résistance à la manipulation de marché -- un axe orthogonal. Une jambe
#   à 100% "confirmée" par hash exact reste vraie (ratio réellement exécuté
#   dans SA transaction), mais une jambe purement OHLCV peut être exacte
#   (marché sain) ou manipulée (pool à faible volume, cf. vulnérabilité
#   dusting déjà documentée) -- le drapeau ne distingue pas ces deux cas
#   parmi les jambes estimées. Documenté ici comme clarification de portée,
#   pas un nouveau mécanisme à corriger (la vulnérabilité sous-jacente est
#   déjà la dusting/pool-manipulé ci-dessus).
# - Élagage anti-chance et faux négatif sur un style légitimement concentré
#   (barbell/conviction sizing) : `_robust_pnl_check` trie par PnL en dollars
#   et retire les `robust_trim_pct` extrêmes des deux côtés avant de vérifier
#   que le reste est positif -- pensé pour neutraliser un coup de chance
#   isolé (cf. passages précédents), mais un trader dont l'edge réel VIENT
#   justement d'un petit nombre de gains extrêmes (quelques multi-baggers
#   assumés, beaucoup de petites pertes/positions coupées vite) peut voir ses
#   meilleurs trades légitimes trimmés et le reste artificiellement jugé "non
#   robuste" -- un faux négatif sur un style de trading réel, pas seulement
#   un vrai positif sur la chance. Distinguer "chance isolée" de "conviction
#   sizing assumé" exigerait un signal indépendant (ex. taille de position
#   pré-décidée, thèse documentée) que le simple historique on-chain ne
#   fournit pas -- non construit, tension assumée entre les deux lectures
#   possibles du même signal.
# - Plafond `max_tokens_analyzed`/couverture exhaustive (revue DeepSeek,
#   même angle que "biais de sélection de la couche 2" déjà documenté
#   QUATRIÈME PASSAGE) : vérifié -- déjà présenté comme une limite de
#   complétude explicite (`full_coverage`/`tokens_scanned_cumulative`
#   affichés dans le rapport, et depuis le correctif #172, `full_coverage=
#   False` exclut désormais le wallet de la population de comparaison
#   percentile). Pas un angle mort supplémentaire, la couverture partielle
#   est déjà divulguée et neutralisée là où elle compterait le plus (le
#   classement comparatif).
# ============================================================================
#
# CONSTAT DE PALIER (15/07) : à ce stade, les rounds successifs de revue
# externe reconfirment très majoritairement les mêmes limites structurelles
# déjà écrites (Sybil, benchmark marché, MEV, gaming des seuils/tests) plutôt
# que d'en révéler de nouvelles -- signal que le fond du sujet est correctement
# cartographié. Les items encore ouverts sont, par nature, des PROJETS séparés
# (clustering d'entité, série de rendement de référence, détection
# d'atomicité de transaction), pas des correctifs ponctuels supplémentaires --
# à rouvrir sur décision explicite si l'un d'eux devient prioritaire.
# ============================================================================
#
# HUITIÈME PASSAGE (15/07, revue Gemini + DeepSeek round 2). Un vrai bug
# corrigé (pas une limite résiduelle), un vrai angle mort documenté :
#
# - Gel des erreurs transitoires (revue Gemini) -- CORRIGÉ pour la couche la
#   plus impactante : une panne D'INFRASTRUCTURE GeckoTerminal (timeout/429/
#   erreur serveur, déjà retentée plusieurs fois par `_get_json` avant
#   d'abandonner) lors de la résolution de pool d'un token pouvait se figer en
#   cicatrice PERMANENTE -- le scan incrémental persistant (checkpoint) ne
#   re-tente un token déjà "vu" que si son activité on-chain a changé, jamais
#   sur la simple résolution d'une erreur API. Une coupure réseau ponctuelle
#   pendant UN scan en arrière-plan condamnait donc une jambe à rester
#   "sans prix" pour toujours dans les archives (`wallet_archived_trade`),
#   faussant durablement le PnL ET `price_confirmation_ratio` du wallet,
#   sans aucun moyen de correction automatique. Corrigé : `resolve_primary_
#   pool` distingue déjà, EN TEXTE, un verdict de DONNÉE ("aucun pool trouvé
#   pour ce token"/"aucun pool plausible...") d'une panne d'infrastructure
#   (préfixée par la constante `UNAVAILABLE` de `geckoterminal.py` dans
#   TOUS les cas d'échec `_get_json`) -- signal déjà présent, jamais exploité
#   jusqu'ici. `_analyze_wallet_multi_token` classe désormais chaque token en
#   échec de résolution (`transient_pricing_error_tokens`), et `score_wallets`
#   exclut ces tokens de `checkpoint.scanned_tokens` -- ils restent éligibles
#   à une nouvelle tentative au prochain appel, MÊME sans nouvelle activité
#   on-chain. **Portée honnête, PAS un correctif universel** : ne couvre que
#   la couche de résolution de POOL (GeckoTerminal), où le texte d'erreur
#   sépare proprement les deux cas. Les couches OHLCV (`services/ohlcv.py`,
#   client partagé avec `vc_predictions`/`weekly_training`/`pump_dump_
#   autopsy`) et CoinMarketCap (triangulation 3e couche) CONFLENT, elles,
#   panne transitoire et absence légitime de donnée sous LA MÊME convention
#   de préfixe (`f"{UNAVAILABLE} (pool absent)"`/`f"{UNAVAILABLE} (aucune
#   bougie...)"` ressemblent textuellement à une vraie panne) -- les
#   distinguer proprement exigerait soit un champ typé dédié threadé à
#   travers ces clients partagés (risque de régression sur leurs AUTRES
#   appelants), soit un filtrage fragile par sous-chaîne de diagnostic
#   jamais conçue pour cet usage. Le même mode de défaillance (gel
#   silencieux) reste donc possible si l'échec survient à CES couches plutôt
#   qu'à la résolution de pool -- résiduel plus étroit qu'avant (le point
#   d'entrée le plus fréquent est fermé), mais réel, documenté, pas corrigé.
#   3 nouveaux tests (dont un test de contraste : un token sans AUCUN pool,
#   verdict légitime, reste bien marqué "scanné" -- comportement historique
#   inchangé).
# - Biais de sélection induit par l'exclusion `price_confidence_low` (revue
#   DeepSeek round 2) -- DOCUMENTÉ, tension assumée, pas corrigé. Le
#   correctif #175 (exclure un wallet à confiance de prix basse de la
#   population de comparaison percentile) protège l'INTÉGRITÉ du percentile
#   des AUTRES wallets (éviter d'ancrer une comparaison sur des chiffres
#   potentiellement faussés par une estimation de prix peu fiable) -- mais
#   introduit mécaniquement un biais de SÉLECTION dans la population de
#   référence elle-même : un wallet qui trade des tokens peu liquides, sans
#   paire stablecoin directe, ou via un agrégateur/smart-account (routage qui
#   échappe à la détection `_hash_based_price`, cf. sa docstring) aura
#   STRUCTURELLEMENT un `price_confirmation_ratio` bas -- pas parce qu'il
#   triche ou performe mal, mais parce que SON style de trading produit
#   moins de jambes hash-exactes. Un tel wallet reste scoré (avec son propre
#   avertissement affiché), mais n'est plus jamais utilisé comme POINT DE
#   RÉFÉRENCE pour comparer d'autres wallets -- la population de comparaison
#   se resserre autour des wallets qui tradent via des paires stablecoin
#   directes, PAS autour d'un échantillon représentatif du "smart money" au
#   sens large. **Tension particulièrement pertinente pour la thèse même
#   d'ARIA** (sourcing de builders sur des microcaps Base souvent peu
#   liquides, cf. CLAUDE.md "Vision & stratégie") : ce sont précisément CES
#   traders-là qui risquent d'être sous-représentés dans le groupe de
#   référence. Vient s'ajouter au paradoxe du percentile déjà documenté
#   (round 2/3, population non représentative du marché) -- même famille de
#   limite, un axe de biais SUPPLÉMENTAIRE et distinct (style de trading,
#   pas seulement démographie des utilisateurs de l'outil). **Pas de
#   correctif de code proposé** : revenir sur l'exclusion #175 réintroduirait
#   directement le bug qu'elle corrigeait (ancrer un percentile sur des
#   chiffres non fiables) -- un arbitrage entre deux défauts connus, pas une
#   erreur à corriger dans un sens ou l'autre sans un mécanisme plus fin
#   (ex. pondérer la contribution d'un wallet à la population de comparaison
#   par sa confiance plutôt qu'un tout-ou-rien) -- chantier séparé si repris.
# ============================================================================
#
# NEUVIÈME PASSAGE (15/07, revue externe -- l'équation résumée à l'opérateur a
# elle-même été auditée ligne par ligne). Deux corrections apportées au CODE
# (percentile lissé + contradiction Sortino/PnL signalée, cf. plus haut), une
# affirmation externe vérifiée et RÉFUTÉE, un vrai angle mort documenté :
#
# - Diversification -- l'AXE est nommé "diversification" mais NE MESURE PAS
#   une largeur/dispersion de portefeuille (type Herfindahl/entropie) : `D =
#   diversification_profitable_tokens / diversification_total_tokens` est en
#   réalité un TAUX DE RÉUSSITE PAR TOKEN (combien de tokens distincts finissent
#   nets positifs), un axe plus proche d'un second win_rate que d'une mesure de
#   dispersion. Conséquence vérifiée : un wallet qui trade UN SEUL token,
#   profitable, obtient D=1 (score parfait) -- un wallet qui en trade 20 dont
#   15 profitables obtient D=0,75 (plus bas), alors qu'il est objectivement
#   PLUS diversifié. Le nom pousse donc, littéralement, à l'extrême
#   concentration plutôt qu'à l'éparpillement qu'il est censé récompenser.
#   Nuance vérifiée : `_suspect_positive_flag` (couche 3, distincte du
#   percentile/composite) exige DÉJÀ `diversification_total_tokens >=
#   WEIGHTS.suspect_diversification_min_tokens` avant de compter cet axe comme
#   "suspect" -- un garde-fou existe donc contre ce gaming précis, mais
#   UNIQUEMENT pour le drapeau "suspect positif", jamais pour l'axe
#   `percentile_diversification`/`composite_percentile` lui-même, qui reste
#   sans aucun plancher de nombre de tokens. Non corrigé (renommer l'axe ou lui
#   ajouter un plancher change le sens même de la métrique affichée depuis le
#   début de ce chantier -- décision de méthodologie, pas un ajustement de
#   seuil ponctuel).
# - Complétude de l'équation -- clarification (pas un bug) : `diversification_
#   capital_weighted_ratio` (#163) N'EST PAS combiné avec le ratio de comptage
#   ci-dessus dans une formule pondérée unique -- les deux restent deux champs
#   SÉPARÉS (même doctrine "axes jamais fondus" que tout le reste de ce
#   module) ; seul le ratio de COMPTAGE entre dans `percentile_diversification`/
#   `composite_percentile`, la variante pondérée par capital reste un
#   diagnostic d'AFFICHAGE seul (`_format_card_for_prompt`), jamais utilisée
#   dans le calcul du percentile.
# - RÉFUTÉ après vérification (revue externe) : l'affirmation qu'un PnL brut
#   "linéaire" ferait s'écraser le percentile de tous les autres wallets vers
#   0 dès qu'un seul wallet a un PnL démesuré. Vérifié contre `_percentile` :
#   c'est un percentile de RANG (compte les autres wallets strictement en
#   dessous / population), jamais une normalisation min-max ni un calcul sur
#   la magnitude brute -- un unique outlier à 10M$ ne change RIEN au
#   percentile des autres wallets (il ne compte que pour son propre rang, au
#   sommet). Cette classe de distorsion ("un extrême écrase tout le reste")
#   s'appliquerait à une moyenne/normalisation par la valeur, pas à un
#   percentile par rang -- non applicable ici.
# - Frais de gas jamais déduits du PnL (revue externe) -- vérifié réel, pas
#   déjà géré ailleurs : `ClosedTrade.pnl_usd` ne soustrait aucun coût de
#   transaction (`qty * (sell_price - buy_price)` seul) ; aucune donnée de gas
#   (gas_used/gas_price par jambe) n'est même récupérée dans ce module. Un
#   wallet qui accumule de nombreux micro-trades gagnants EN POURCENTAGE mais
#   dont chaque swap coûte plus cher en gas que le gain lui-même serait donc
#   présenté comme performant alors qu'il est gas-négatif en réalité. Non
#   corrigé : exigerait un appel réseau supplémentaire par transaction (reçu
#   de transaction, gas_used * gas_price) pour CHAQUE jambe FIFO -- un nouveau
#   type de donnée jamais fetché ici, coût réseau significatif sur un wallet
#   actif -- chantier séparé si jamais entrepris, pas un correctif ponctuel.
# ============================================================================
#
# DIXIÈME PASSAGE (15/07, revue externe -- 2 lots). Un vrai bug corrigé, trois
# fausses alertes vérifiées et RÉFUTÉES, deux nuances documentées :
#
# - **Historique de transferts tronqué sans signal (CORRIGÉ)** : `client.
#   get_token_transfers(wallet, limit=2000, max_pages=10, ...)` peut arrêter
#   la pagination alors que Blockscout avait ENCORE de la donnée
#   (`next_page_params` présent) -- un wallet très actif (plus de 2000
#   transferts ERC-20 vie entière) voyait ses transferts les plus anciens
#   silencieusement absents, avec un risque de biais sur TOUS les axes
#   (W/PnL/S/D) et le percentile, pas seulement `unmatched_sell_events`
#   (déjà documenté plus haut, mais qui ne dit pas SI l'historique lui-même
#   était complet). `TokenTransfersResult.truncated` (nouveau champ, défaut
#   `False`, rétrocompatible) distingue désormais "historique réellement
#   épuisé" (pas de `next_page_params`) de "arrêté avant la fin" (erreur
#   réseau/réponse malformée en cours de pagination, OU plafond max_pages/
#   limit atteint alors qu'il restait de la donnée) -- `card.transfer_
#   history_truncated` l'affiche en ATTENTION à côté du reste.
# - **RÉFUTÉ (revue externe) -- "évasion du trim par désynchronisation
#   d'unités"** : l'affirmation que le trim anti-chance (trié en $) laisserait
#   passer un micro-trade à rendement % extrême qui viendrait ensuite
#   "contaminer" le Sortino. Vérifié contre le code : `_robust_pnl_check`
#   (le trim) et `card.sortino` sont deux calculs INDÉPENDANTS sur la MÊME
#   liste de trades clôturés -- le trim ne filtre jamais les trades utilisés
#   pour Sortino/win_rate/PnL, c'est un verdict de robustesse à PART
#   (`robust_pnl_positive`), jamais un préfiltre. Il n'y a donc rien de
#   "laissé passer" par le trim vers le Sortino -- Sortino voit TOUJOURS
#   100% des trades, trim ou pas. **Le sous-jacent réel derrière cette
#   critique reste valide, lui** : un trade dust (ex. achat 0,10$, vente 10$,
#   +9900% de rendement, +9,90$ de PnL) peut à lui seul dominer mean(return_i)
#   et donc le Sortino -- même famille que "Sortino jamais pondéré par la
#   taille" déjà documenté (revue ChatGPT/#178), ce sous-cas dust/airdrop-like
#   en est un exemple concret supplémentaire, pas un 3e mécanisme.
# - **RÉFUTÉ -- division par zéro sur `return_i` si `buy_price<=0`** : déjà
#   gardé. `ClosedTrade.return_pct` retourne `None` explicitement si
#   `buy_price <= 0`, AVANT toute division -- jamais un crash ni un infini.
#   Un token reçu gratuitement (buy_price=0, ex. airdrop) et revendu produit
#   un `pnl_usd` positif correct (`qty * sell_price`, tout le produit de la
#   vente est un profit réel) mais un `return_pct=None` -- exclu du calcul de
#   Sortino, jamais une valeur aberrante qui s'y invite.
# - **RÉFUTÉ -- division par zéro du percentile sur population vide** : déjà
#   doublement gardé. `_apply_comparative_ranking` retourne tôt si `others`
#   est vide (`if not others: return`), ET `_percentile` lui-même revérifie
#   `if value is None or not population: return None` -- aucun chemin
#   n'atteint la division. Comportement documenté et VERROUILLÉ par un test
#   dédié (`test_first_wallet_ever_scored_has_no_comparison_population`) --
#   pas seulement un hasard de conception.
# - Documenté (nuance mineure, pas un bug) : le lissage des ex-æquo (#178)
#   suppose des ex-æquo l'EXCEPTION -- sur une population aux valeurs très
#   arrondies ou discrètes (ex. beaucoup de wallets à win_rate pile 0,5), les
#   ex-æquo peuvent devenir la NORME, rendant le percentile moins
#   discriminant (toujours correct, juste moins granulaire). Propriété
#   statistique inhérente au rang moyen sur petite population/valeurs
#   discrètes -- pas un défaut du code, aucune meilleure alternative
#   simple sans changer fondamentalement de méthode de classement.
# ============================================================================

# Tous les poids/seuils tunables de ce chantier vivent dans
# wallet_scoring_weights.py (isolé à la demande opérateur, 14/07 -- statut
# provisoire, cf. docstring de ce module pour la décision d'emplacement
# définitif en attente). Aucune valeur numérique en dur ici : toujours via
# WEIGHTS.<champ>.


def wallet_scoring_enabled() -> bool:
    return os.environ.get("ARIA_WALLET_SCORING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# Même convention que pump_dump_autopsy.py (DB_PATH lié une fois à l'import) --
# les tests isolent en monkeypatchant `smart_money.DB_PATH` (cf. test_aria_track_record.py
# pour le même patron sur screened_pool.DB_PATH).
DB_PATH = str(aria_db_path())


@dataclass
class ClosedTrade:
    """Une jambe achat->vente appariée en FIFO, valorisée en USD à chaque bord
    via GeckoTerminal. Jamais construite sans prix des deux côtés (cf. `_fifo_match`
    -- une jambe sans prix disponible est comptée à part, pas valorisée à zéro)."""

    token_address: str
    buy_ts: datetime
    sell_ts: datetime
    token_amount: float
    buy_price: float
    sell_price: float
    # Confiance du cost-basis (15/07, revue Gemini) : ``True`` si CE bord précis
    # a été valorisé par un ratio d'exécution exact (tx_hash + jambe stablecoin
    # dans la même transaction), ``False`` s'il retombe sur le prix de marché
    # OHLCV -- jamais l'inverse d'un jugement de qualité du trade lui-même,
    # uniquement de la CONFIANCE dans le prix utilisé pour le calculer (cf.
    # ``price_confirmation_ratio`` sur ``WalletScoreCard``).
    buy_price_exact: bool = False
    sell_price_exact: bool = False

    @property
    def pnl_usd(self) -> float:
        return self.token_amount * (self.sell_price - self.buy_price)

    @property
    def return_pct(self) -> float | None:
        if self.buy_price <= 0:
            return None
        return (self.sell_price - self.buy_price) / self.buy_price


@dataclass
class _TokenFIFOResult:
    token_address: str
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    unpriced_legs: int = 0
    open_position_amount: float = 0.0
    # Ventes dont la queue d'achats FIFO s'est épuisée avant d'être entièrement
    # consommée (15/07, revue Gemini) : signal possible d'un rendement de
    # rebase/DeFi (stETH, aTokens -- le solde augmente sans transfert entrant
    # équivalent) ou d'un achat antérieur à la fenêtre de transferts récupérée.
    # Jamais crédité comme profit (impossible de distinguer les deux cas sans
    # deviner) -- juste compté pour transparence, cf. `unmatched_sell_events`
    # sur `WalletScoreCard`.
    unmatched_sell_events: int = 0


def _fifo_match(
    token_address: str,
    buys: list[tuple[datetime, float, str]],
    sells: list[tuple[datetime, float, str]],
    price_lookup,
    *,
    exact_hashes: frozenset[str] = frozenset(),
) -> _TokenFIFOResult:
    """FIFO strict : chaque vente consomme les achats les plus anciens en premier.
    ``price_lookup(ts, tx_hash) -> float | None`` -- une jambe sans prix disponible
    des DEUX côtés (achat ET vente) est comptée dans ``unpriced_legs``, jamais
    valorisée à zéro ni ignorée silencieusement (doctrine facts-only).
    ``buys``/``sells`` portent le ``tx_hash`` d'origine de chaque jambe (14/07,
    prix par hash exact) -- reste synchrone : la résolution éventuelle d'un prix
    par hash (appel réseau) est faite EN AMONT par l'appelant, qui fournit un
    ``price_lookup`` déjà résolu (dict/fermeture), jamais dans cette fonction.
    ``exact_hashes`` (15/07, additif -- défaut vide, rétrocompatible avec tout
    appelant/test existant) : ensemble des tx_hash déjà résolus par un prix
    d'exécution EXACT (cf. ``hash_prices`` dans ``_analyze_wallet_multi_token``)
    -- sert uniquement à marquer ``ClosedTrade.buy_price_exact``/``sell_price_exact``,
    aucun effet sur le prix retenu lui-même (toujours celui renvoyé par
    ``price_lookup``)."""
    buy_queue: deque[list] = deque(sorted(([ts, amt, tx_hash] for ts, amt, tx_hash in buys), key=lambda b: b[0]))
    closed: list[ClosedTrade] = []
    unpriced = 0
    unmatched_sell_events = 0

    for sell_ts, sell_amount, sell_hash in sorted(sells, key=lambda s: s[0]):
        remaining = sell_amount
        while remaining > 1e-12 and buy_queue:
            buy_ts, buy_amount, buy_hash = buy_queue[0]
            matched = min(remaining, buy_amount)
            buy_price = price_lookup(buy_ts, buy_hash)
            sell_price = price_lookup(sell_ts, sell_hash)
            if buy_price is None or sell_price is None:
                unpriced += 1
            else:
                closed.append(
                    ClosedTrade(
                        token_address=token_address,
                        buy_ts=buy_ts,
                        sell_ts=sell_ts,
                        token_amount=matched,
                        buy_price=buy_price,
                        sell_price=sell_price,
                        buy_price_exact=buy_hash in exact_hashes,
                        sell_price_exact=sell_hash in exact_hashes,
                    )
                )
            buy_queue[0][1] -= matched
            if buy_queue[0][1] <= 1e-12:
                buy_queue.popleft()
            remaining -= matched
        # Vente sans achat correspondant en attente (queue épuisée) : ignorée --
        # ne peut pas être un trade ARIA-observable (le wallet a acquis le token
        # avant la fenêtre de transferts récupérée, ou via un mécanisme non-transfer
        # comme un mint direct) ; pas une jambe "sans prix", juste hors-scope FIFO.
        # Comptée (pas créditée) -- cf. `unmatched_sell_events` ci-dessus.
        if remaining > 1e-12:
            unmatched_sell_events += 1

    open_amount = sum(amt for _, amt, _ in buy_queue)
    return _TokenFIFOResult(
        token_address=token_address, closed_trades=closed, unpriced_legs=unpriced,
        open_position_amount=open_amount, unmatched_sell_events=unmatched_sell_events,
    )


def _sortino_ratio(returns: list[float]) -> float | None:
    """Ratio type Sortino sur les rendements par trade clôturé. Sous
    `WEIGHTS.min_closed_trades_for_sortino`, jugé trop bruité pour un wallet individuel
    (cf. research doc #157) -- indisponible, jamais un chiffre peu fiable présenté
    comme fiable. Aucune perte observée -> ratio non défini (pas un infini
    artificiel)."""
    if len(returns) < WEIGHTS.min_closed_trades_for_sortino:
        return None
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    downside_deviation = math.sqrt(fmean([r * r for r in downside]))
    if downside_deviation == 0:
        return None
    return fmean(returns) / downside_deviation


def _max_drawdown_pct(closed_trades: list[ClosedTrade]) -> float | None:
    """Drawdown appliqué à la valeur CUMULÉE réalisée du wallet lui-même (pas au
    marché) -- pic de PnL cumulé atteint vs. pire retracement depuis ce pic,
    trades triés chronologiquement par date de vente."""
    if not closed_trades:
        return None
    ordered = sorted(closed_trades, key=lambda t: t.sell_ts)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered:
        cumulative += t.pnl_usd
        peak = max(peak, cumulative)
        if peak > 0:
            max_dd = max(max_dd, (peak - cumulative) / peak)
    return max_dd


def _avg_holding_period_days(closed_trades: list[ClosedTrade]) -> float | None:
    """Durée moyenne de détention (achat -> vente) en jours, sur les trades
    clôturés -- signal de conviction vs. rotation rapide, méthodologie sourcée
    (recherche externe 15/07 : "an increasing share of coins held over longer
    durations... indicates strong conviction", cf. docs/aria-learning-inbox).
    Aucune donnée réseau supplémentaire : buy_ts/sell_ts sont déjà dans chaque
    ``ClosedTrade``, ce calcul est gratuit."""
    if not closed_trades:
        return None
    days = [(t.sell_ts - t.buy_ts).total_seconds() / 86_400 for t in closed_trades]
    return fmean(days)


def _wallet_age_days(all_flat_transfers: list[TokenTransfer]) -> float | None:
    """Ancienneté du wallet -- du PREMIER transfert observé (dans la fenêtre
    récupérée, cf. limites de pagination Blockscout) à MAINTENANT. Un wallet
    inactif depuis un moment reste "âgé" (l'ancienneté mesure depuis combien de
    temps il existe/trade, pas depuis combien de temps il est actif)."""
    timestamps = [ts for t in all_flat_transfers if (ts := _parse_timestamp(t.timestamp)) is not None]
    if not timestamps:
        return None
    return (datetime.now(timezone.utc) - min(timestamps)).total_seconds() / 86_400


def _count_total_swaps(all_flat_transfers: list[TokenTransfer], wallet: str) -> int:
    """Nombre total de transferts touchant le wallet (achat OU vente) dans la
    fenêtre récupérée -- mesure d'activité brute, distincte du nombre de
    trades CLÔTURÉS (qui exige un achat ET une vente appariés en FIFO).

    Exclut les jambes de wrap/unwrap ETH<->WETH (15/07, revue Gemini, cf.
    `_is_wrap_unwrap_leg`) et les swaps stable<->stable (15/07, revue Gemini
    suite, cf. `_is_stable_to_stable_peg_swap`) -- sinon un script de
    wrapping/peg-swapping répété débloquerait WEIGHTS.min_total_swaps sans
    jamais avoir pris de risque de trading réel."""
    wallet_l = wallet.lower()
    by_tx: dict[str, list[TokenTransfer]] = {}
    touching: list[TokenTransfer] = []
    for t in all_flat_transfers:
        if (t.to_address or "").lower() == wallet_l or (t.from_address or "").lower() == wallet_l:
            touching.append(t)
            by_tx.setdefault(t.tx_hash, []).append(t)

    return sum(
        1 for t in touching
        if not _is_wrap_unwrap_leg(t) and not _is_stable_to_stable_peg_swap(t.tx_hash, by_tx)
    )


def _robust_pnl_check(closed_trades: list[ClosedTrade], *, trim_pct: float, min_required: int) -> bool | None:
    """Robustesse anti-chance (15/07, décision opérateur ; corrigé le même jour
    après revue externe croisée Gemini/ChatGPT/Grok) : retire un POURCENTAGE
    (``trim_pct`` de chaque extrémité, pas un compte fixe) des trades les
    MEILLEURS et les PIRES par PnL, puis vérifie si le PnL restant reste
    positif. Un compte fixe se dilue à mesure que l'échantillon grossit (10
    trades sur 30 = 33% retiré, mais seulement 0.05% sur 20 000 trades) -- un
    pourcentage scale avec N et empêche de noyer un unique trade "chanceux"
    derrière assez de micro-trades insignifiants pour le faire sortir du
    top-N absolu retiré. Ne s'applique QUE si le wallet a au moins
    ``min_required`` trades clôturés (sinon le retrait viderait ou
    déséquilibrerait un échantillon déjà petit) -- ``None`` explicite plutôt
    qu'un chiffre sur un reste non significatif."""
    if len(closed_trades) < min_required:
        return None
    ordered = sorted(closed_trades, key=lambda t: t.pnl_usd)
    trim_count = max(1, round(len(ordered) * trim_pct)) if trim_pct > 0 else 0
    if trim_count * 2 >= len(ordered):
        return None  # le retrait viderait ou inverserait l'échantillon -- jamais un résultat dessus
    trimmed = ordered[trim_count:-trim_count] if trim_count > 0 else ordered
    if not trimmed:
        return None
    return sum(t.pnl_usd for t in trimmed) > 0


def _health_trend(
    closed_trades: list[ClosedTrade], *, min_required: int, stable_band_pct: float,
) -> str | None:
    """Courbe de santé dans le temps (15/07) : compare le PnL moyen par trade
    de la seconde moitié CHRONOLOGIQUE (triée par date de vente) à la
    première -- "amélioration" (nettement meilleure), "dégradation" (nettement
    pire), ou "stable" (écart sous ``stable_band_pct`` -- pas un bruit
    présenté comme un signal). ``None`` sous ``min_required`` trades (signal
    jugé trop bruité sur un petit échantillon, même doctrine que Sortino)."""
    if len(closed_trades) < min_required:
        return None
    ordered = sorted(closed_trades, key=lambda t: t.sell_ts)
    mid = len(ordered) // 2
    first_half, second_half = ordered[:mid], ordered[mid:]
    if not first_half or not second_half:
        return None
    first_avg = fmean(t.pnl_usd for t in first_half)
    second_avg = fmean(t.pnl_usd for t in second_half)
    reference = max(abs(first_avg), abs(second_avg), 1e-9)
    delta = (second_avg - first_avg) / reference
    if delta > stable_band_pct:
        return "amélioration"
    if delta < -stable_band_pct:
        return "dégradation"
    return "stable"


def _recent_window_metrics(
    closed_trades: list[ClosedTrade], *, window_days: int,
) -> tuple[float | None, float | None, int]:
    """Biais temporel (15/07, revue ChatGPT) : un wallet excellent 3 ans puis
    dégradé depuis 6 mois garde un win_rate/Sortino/PnL historiques excellents
    très longtemps -- la courbe de santé (`_health_trend`) aide (2e moitié vs
    1ère) mais ne corrige PAS le score principal, qui reste calculé sur tout
    l'historique. Calcule win_rate/PnL réalisé sur les SEULS trades clôturés
    (vente) dans les ``window_days`` derniers jours -- en COMPLÉMENT, jamais en
    remplacement des métriques historiques complètes (mêmes cumulative_trades,
    juste un sous-ensemble récent). Renvoie ``(None, None, 0)`` si aucun trade
    clôturé dans la fenêtre -- jamais un chiffre sur un vide."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    recent = [t for t in closed_trades if t.sell_ts >= cutoff]
    if not recent:
        return None, None, 0
    wins = sum(1 for t in recent if t.pnl_usd > 0)
    return wins / len(recent), sum(t.pnl_usd for t in recent), len(recent)


def _group_transfers_by_token(transfers: list[TokenTransfer], *, chain: str = "base") -> dict[str, list[TokenTransfer]]:
    """Clé composite ``"{chain}:{adresse}"`` (#157 multi-chaînes, 14/07) --
    jamais l'adresse seule, pour que deux tokens de même adresse hexadécimale
    sur deux chaînes EVM différentes (espaces d'adresses indépendants) ne
    soient jamais fusionnés par erreur. Comportement historique inchangé pour
    tout appelant à une seule chaîne (``chain="base"`` par défaut)."""
    grouped: dict[str, list[TokenTransfer]] = {}
    for t in transfers:
        addr = (t.token_address or "").lower()
        if not addr:
            continue
        grouped.setdefault(f"{chain}:{addr}", []).append(t)
    return grouped


def _build_dex_infrastructure_exclusions(
    grouped: dict[str, list[TokenTransfer]], wallet: str,
) -> set[str]:
    """#157, correction 14/07 -- bug réel trouvé en relecture : généraliser
    `_dominant_counterparty_share` à TOUS les tokens du wallet en n'excluant
    QU'UN SEUL pool (comme le fait le chemin token-centrique existant) crée un
    faux positif quasi systématique. Un wallet actif sur `WEIGHTS.max_tokens_analyzed`
    tokens distincts passe par PLUSIEURS pools/routeurs DEX différents (Base a
    plusieurs DEX actifs -- Uniswap V3, Aerodrome, etc.) ; si le calcul généralisé
    n'exclut qu'une seule adresse, un routeur/pool revenant souvent comme
    contrepartie disqualifierait à tort la plupart des traders actifs normaux.

    Heuristique retenue, volontairement générale (ne nécessite AUCUNE adresse de
    routeur/pool codée en dur, donc s'adapte automatiquement à n'importe quelle
    infra DEX présente ou future sur Base) : une contrepartie qui revient sur au
    moins `WEIGHTS.wash_trading_infra_min_distinct_tokens` tokens DISTINCTS est
    structurellement une brique d'infrastructure (pool ou routeur -- les deux
    sont mécaniquement partagés entre de nombreuses paires par construction),
    PAS un partenaire de wash-trading (typiquement lié à UN seul token/schéma
    coordonné). Complète (ne remplace pas) l'exclusion du pool résolu par token
    -- cf. `_analyze_wallet_multi_token` / `resolve_primary_pool`, qui couvre en
    plus le cas d'un wallet dont l'historique ne porte encore que sur un seul
    token (pas assez de récurrence pour que cette heuristique déclenche seule).
    """
    wallet_l = wallet.lower()
    tokens_per_counterparty: dict[str, set[str]] = {}
    for token_addr, transfers in grouped.items():
        for t in transfers:
            other = t.to_address if (t.from_address or "").lower() == wallet_l else t.from_address
            other = (other or "").lower()
            if not other or other == wallet_l:
                continue
            tokens_per_counterparty.setdefault(other, set()).add(token_addr)
    return {
        addr
        for addr, tokens in tokens_per_counterparty.items()
        if len(tokens) >= WEIGHTS.wash_trading_infra_min_distinct_tokens
    }


_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _select_tokens_for_deep_analysis(
    grouped: dict[str, list[TokenTransfer]], *, wallet: str = "", cap: int = WEIGHTS.max_tokens_analyzed,
) -> tuple[list[str], int, int]:
    """Trie par (round-trip achat+vente présent, récence du dernier transfert,
    nombre de trades) décroissant -- plafonne à ``cap`` tokens analysés en
    profondeur (décision opérateur, #157). Renvoie (adresses sélectionnées, nb
    total de tokens distincts trouvés, nb ignorés par le plafond) -- l'appelant
    DOIT logger explicitement si le 3e élément est > 0, jamais une troncature
    silencieuse.

    ``wallet`` (15/07, correctif réel) : la priorité "récence seule" biaisait
    systématiquement l'échantillon vers des positions ENCORE OUVERTES chez les
    wallets très actifs (le token le plus récent est, par construction, plus
    souvent un achat pas encore revendu) -- un round-trip achat+vente ne peut
    JAMAIS se former sur une position ouverte, donc le plafond de `cap` tokens
    se remplissait parfois entièrement de positions non clôturables, laissant
    win rate/PnL/Sortino "indisponible" même sur un wallet très actif avec de
    vrais trades clôturés ailleurs dans son historique. Les tokens avec un
    round-trip confirmé (au moins un transfert entrant ET sortant) passent
    désormais en premier ; la récence/fréquence ne départage plus qu'en cas
    d'égalité, dans chaque groupe. ``wallet=""`` (défaut) préserve le
    comportement historique (aucun round-trip jamais détecté, tri par récence
    pure) -- rétrocompatible pour tout appelant qui ne connaît pas le wallet.
    """
    wallet_l = wallet.lower()

    def _has_round_trip(token_transfers: list[TokenTransfer]) -> bool:
        if not wallet_l:
            return False
        has_buy = any((t.to_address or "").lower() == wallet_l for t in token_transfers)
        has_sell = any((t.from_address or "").lower() == wallet_l for t in token_transfers)
        return has_buy and has_sell

    def _sort_key(item: tuple[str, list[TokenTransfer]]):
        _addr, token_transfers = item
        timestamps = [ts for t in token_transfers if (ts := _parse_timestamp(t.timestamp)) is not None]
        latest = max(timestamps) if timestamps else _EPOCH_UTC
        return (_has_round_trip(token_transfers), latest, len(token_transfers))

    ranked = sorted(grouped.items(), key=_sort_key, reverse=True)
    selected = [addr for addr, _ in ranked[:cap]]
    total = len(grouped)
    skipped = max(0, total - cap)
    return selected, total, skipped


def _is_informed_entry(ohlcv, entry_ts: datetime) -> bool:
    """Qualifie une entrée précoce d'"informée" (volume faible + figure chartiste
    juste avant l'achat) vs "rapide/FOMO" (aucun signal technique particulier) --
    raffinement demandé par l'opérateur, réutilise `ta_levels`/`candlestick_patterns`
    tels quels, aucune nouvelle heuristique de détection."""
    from aria_core.skills import candlestick_patterns

    entry_epoch = int(entry_ts.timestamp())
    window = [c for c in ohlcv.candles if c.ts <= entry_epoch]
    if len(window) < 3:
        return False
    window = window[-WEIGHTS.technical_entry_lookback_candles:]
    entry_candle = window[-1]
    prior = window[:-1]
    avg_prior_volume = fmean([c.volume for c in prior]) if prior else 0.0
    low_volume = avg_prior_volume > 0 and entry_candle.volume < avg_prior_volume
    patterns = candlestick_patterns.detect_patterns(window)
    pattern_just_before = any(p.index >= len(window) - 2 for p in patterns)
    return low_volume and pattern_just_before


@dataclass
class _MultiTokenResult:
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    unpriced_legs: int = 0
    early_entry_tokens: list[str] = field(default_factory=list)
    informed_entry_tokens: list[str] = field(default_factory=list)
    pool_lookup_errors: int = 0
    gecko_dexscreener_gap_tokens: list[str] = field(default_factory=list)
    cmc_recovered_tokens: list[str] = field(default_factory=list)
    resolved_pool_addresses: set[str] = field(default_factory=set)
    thin_liquidity_tokens: list[str] = field(default_factory=list)  # 15/07, revue Gemini -- défense anti-dust/scam-pool
    unmatched_sell_events: int = 0  # 15/07, revue Gemini -- transparence rebasing, cf. `_TokenFIFOResult`
    # Gel des erreurs transitoires (15/07, revue Gemini -- angle mort couche 2/3) :
    # clé composite ("{chaîne}:{adresse}") des tokens dont la résolution de pool
    # GeckoTerminal a échoué ce passage pour une cause D'INFRASTRUCTURE (timeout/
    # 429/erreur serveur -- déjà retentée plusieurs fois par `_get_json` avant
    # d'abandonner) plutôt qu'un verdict de DONNÉE ("aucun pool trouvé pour ce
    # token", légitime). Utilisé par `score_wallets` pour ne JAMAIS marquer un tel
    # token comme définitivement "scanné" dans le checkpoint incrémental -- sinon
    # une simple coupure réseau ponctuelle se fige en cicatrice permanente sur le
    # score du wallet (le scan incrémental ne re-tente un token déjà vu QUE si son
    # activité on-chain a changé, jamais sur une simple résolution d'erreur API).
    transient_pricing_error_tokens: set[str] = field(default_factory=set)


async def _hash_based_price(
    client: BlockscoutClient | None,
    tx_hash: str,
    token_address: str,
    wallet: str,
    *,
    chain: str,
) -> float | None:
    """Prix USD d'une jambe déduit du ratio réellement exécuté dans SA
    transaction (14/07, complément de ``resolve_primary_pool``+``get_ohlcv``+
    ``price_at``) -- vérité d'exécution, pas une approximation par bougie à un
    timestamp arrondi. Méthode : ratio entre le montant du token ciblé et un
    montant stablecoin, l'un et l'autre sur une jambe de la MÊME transaction
    qui touche directement ``wallet`` (``from``/``to``) -- pas un décodage du
    log ``Swap`` brut (cf. rapport [VPS Secondaire] 14/07 : la preuve de
    faisabilité de Task A utilisait déjà ce ratio de transferts, pas les
    montants bruts du log).

    Repli sur ``None`` (jamais une exception) dans tous les cas où le prix ne
    peut pas être établi SANS deviner :
    - chaîne sans registre stablecoin connu (cf. ``_STABLECOIN_ADDRESSES_BY_CHAIN``,
      Base uniquement pour ce chantier) ou client Blockscout absent ;
    - transaction indisponible (timeout/429/erreur -- déjà géré par ``_get_json``) ;
    - aucune jambe du token ciblé touchant le wallet dans cette tx (swap routé
      via un agrégateur/smart-account qui redirige la sortie ailleurs -- pattern
      réel constaté le 14/07 sur un wallet de test, PAS un cas rare marginal) ;
    - aucune jambe stablecoin touchant le wallet dans cette tx (swap token<->token
      non-stable, ou sortie multi-hop non-stable) -- repli attendu pour la
      majorité des jambes, pas un cas d'erreur ;
    - PLUSIEURS jambes du token ciblé OU plusieurs jambes stablecoin touchant
      le wallet dans la même tx (tx composite/batch ambiguë) -- jamais un choix
      arbitraire (même doctrine que ``_fifo_match`` : jamais valorisé à zéro ni
      deviné) ;
    - montant token nul/négatif (garde de division).
    """
    stables = _STABLECOIN_ADDRESSES_BY_CHAIN.get(chain)
    if not stables or client is None or not tx_hash:
        return None

    result = await client.get_transaction_token_transfers(tx_hash)
    if not result.available:
        return None

    wallet_l = wallet.lower()
    token_l = token_address.lower()
    token_amount: float | None = None
    stable_amount: float | None = None
    for t in result.transfers:
        if wallet_l not in ((t.from_address or "").lower(), (t.to_address or "").lower()):
            continue
        if not t.amount:
            continue
        addr = (t.token_address or "").lower()
        if addr == token_l:
            if token_amount is not None:
                return None  # jambe token ambiguë -- jamais deviner laquelle
            token_amount = t.amount
        elif addr in stables:
            if stable_amount is not None:
                return None  # jambe stable ambiguë -- jamais deviner laquelle
            stable_amount = t.amount

    if not token_amount or token_amount <= 0 or not stable_amount:
        return None
    return stable_amount / token_amount


async def _analyze_wallet_multi_token(
    wallet: str,
    transfers_by_token: dict[str, list[TokenTransfer]],
    *,
    gecko,
    chain_clients: dict[str, BlockscoutClient] | None = None,
) -> _MultiTokenResult:
    """``transfers_by_token`` est keyé par une clé composite ``"{chaîne}:{adresse}"``
    (cf. ``_group_transfers_by_token``, #157 multi-chaînes 14/07) -- jamais
    l'adresse token seule, pour ne jamais fusionner par erreur deux tokens
    d'adresses identiques sur deux chaînes différentes (espaces d'adresses
    indépendants par construction EVM). ``chain_clients`` (14/07, prix par
    tx_hash exact) : registre chaîne -> client Blockscout, utilisé pour
    interroger le bon client lors du lookup ``_hash_based_price`` par token
    (déjà connu par chaîne via la clé composite) ; ``None``/registre incomplet
    pour une chaîne dégrade proprement vers pool+OHLCV pour tous ses tokens
    (même politique que l'absence de client dans ``_hash_based_price``)."""
    from aria_core.services.coinmarketcap import CMC_NETWORK_SLUGS
    from aria_core.services.coinmarketcap import get_ohlcv as _cmc_get_ohlcv
    from aria_core.services.coinmarketcap import resolve_primary_pool as _cmc_resolve_primary_pool
    from aria_core.services.dexscreener import has_any_pair as _dexscreener_has_any_pair
    from aria_core.services.geckoterminal import GECKO_NETWORK_SLUGS
    from aria_core.services.geckoterminal import UNAVAILABLE as _gecko_unavailable

    wallet_l = wallet.lower()
    chain_clients = chain_clients or {}
    result = _MultiTokenResult()

    for composite_key, token_transfers in transfers_by_token.items():
        chain, _, token_addr = composite_key.partition(":")
        network = GECKO_NETWORK_SLUGS.get(chain, "base")

        buys = [
            (ts, t.amount, t.tx_hash)
            for t in token_transfers
            if (t.to_address or "").lower() == wallet_l and t.amount and (ts := _parse_timestamp(t.timestamp)) is not None
        ]
        sells = [
            (ts, t.amount, t.tx_hash)
            for t in token_transfers
            if (t.from_address or "").lower() == wallet_l and t.amount and (ts := _parse_timestamp(t.timestamp)) is not None
        ]
        if not buys:
            continue

        # Résout le VRAI pool du token (pas le contrat token lui-même -- deux
        # choses différentes en AMM, cf. `resolve_primary_pool`). Sert à la fois
        # la valorisation OHLCV et l'exclusion wash-trading multi-token (#157,
        # correction 14/07). ``network`` (#157 multi-chaînes, 14/07) : interroge
        # la BONNE chaîne GeckoTerminal, jamais Base en dur pour un token trouvé
        # sur Ethereum/BNB.
        pool_meta = await gecko.resolve_primary_pool(token_addr, network=network)
        # Défense anti-dust/scam-pool (15/07, revue Gemini) : un pool résolu
        # mais dont la liquidité CONFIRMÉE est sous le plancher n'est pas assez
        # fiable pour valoriser un PnL réel (pool trivialement manipulable --
        # ex. token de dust envoyé par un scammeur avec une "liquidité"
        # artificielle sur un pool minuscule). ``reserve_usd is None`` reste
        # fail-open -- POINT VÉRIFIÉ EXPLICITEMENT (15/07, revue Gemini suite,
        # objection "et si la liquidité est *inconnue* juste après le
        # déploiement d'un scam, avant l'indexation ?") : `GeckoTerminalClient.
        # resolve_primary_pool` (vrai client, cf. geckoterminal.py) ne renvoie
        # JAMAIS `None` pour une réserve manquante -- `reserve_in_usd` absent
        # de la réponse API retombe sur `0.0` (`float(attrs.get(...) or 0.0)`),
        # qui échoue DÉJÀ le plancher. Le cas `None` n'est donc atteignable
        # QUE par un double de test/une interface alternative qui ne
        # renseigne pas ce champ -- jamais par le vrai chemin de production.
        # Le fail-open reste un filet de sécurité d'INTERFACE (rétrocompat
        # tests existants), pas une faille de sécurité active. Verrouillé par
        # `test_missing_reserve_data_defaults_to_zero_not_none` (geckoterminal).
        pool_liquid_enough = pool_meta.available and (
            pool_meta.reserve_usd is None or pool_meta.reserve_usd >= WEIGHTS.min_pool_liquidity_usd_for_pricing
        )
        if pool_meta.available:
            # Adresse de pool NUE (jamais préfixée par la chaîne) : comparée
            # telle quelle à des adresses de contrepartie brutes dans
            # `_hard_disqualifiers`/`_dominant_counterparty_share` -- une
            # collision fortuite entre chaînes (espaces d'adresses EVM
            # indépendants, ~2^160) est négligeable, pas un vrai risque.
            # Ajouté même si trop peu liquide pour la valorisation -- reste une
            # vraie brique d'infra DEX pour l'exclusion wash-trading.
            result.resolved_pool_addresses.add(pool_meta.pool_address.lower())
            # Paradoxe "immunité aux rug pulls" (15/07, revue Gemini -- BUG réel
            # confirmé dans le correctif #160) : la liquidité CONFIRMÉE ci-dessus
            # est un instantané pris AU MOMENT DU SCAN, pas historique. Un token
            # acheté quand le pool avait 100k$ puis victime d'un rug pull (pool
            # effondré à 1k$ au moment du scan) verrait sa VENTE bloquée par le
            # même plancher que celui pensé pour bloquer le dust à l'ACHAT --
            # la perte réelle du rug pull disparaîtrait alors des statistiques
            # au lieu d'être comptabilisée (l'inverse exact de l'objectif anti-
            # dust). L'OHLCV est donc TOUJOURS récupéré dès que le pool est
            # résolu ; seul `pool_liquid_enough` gate désormais la confiance
            # côté ACHAT dans `_price_lookup` ci-dessous (jamais la vente) --
            # une vente n'est jamais exploitable pour fabriquer un gain (elle ne
            # fait que révéler un prix réel, éventuellement mauvais), donc rien
            # à protéger de ce côté.
            ohlcv = await gecko.get_ohlcv(pool_meta.pool_address, network=network)
            if not pool_liquid_enough:
                result.thin_liquidity_tokens.append(token_addr)
        else:
            # `pool_liquid_enough` vaut toujours False ici (`pool_meta.available`
            # est son premier facteur) -- mais ça ne veut PAS dire "trop peu
            # liquide", ça veut dire "GeckoTerminal n'a trouvé AUCUN pool du
            # tout", un cas DIFFÉRENT géré séparément ci-dessous (triangulation
            # DexScreener/CMC). Si CMC recouvre un prix, `buy_blocked_thin_
            # liquidity` (calculé après ce bloc) ne doit JAMAIS bloquer les
            # achats sur cette base -- seul un pool GeckoTerminal RÉSOLU mais
            # confirmé trop thin doit bloquer l'achat.
            ohlcv = None
            # Gel des erreurs transitoires (15/07, revue Gemini) : `pool_meta.error`
            # distingue déjà, en texte, un verdict de DONNÉE ("aucun pool trouvé
            # pour ce token"/"aucun pool plausible...", cf. `resolve_primary_pool`)
            # d'une panne D'INFRASTRUCTURE (`_get_json` préfixe TOUJOURS ces
            # dernières par la constante `UNAVAILABLE` -- timeout/429/erreur
            # serveur/réponse malformée, déjà retentées plusieurs fois avant
            # d'abandonner). Seule la 2e catégorie doit empêcher ce token d'être
            # marqué "scanné" dans le checkpoint incrémental (cf. `score_wallets`)
            # -- un vrai "pas de pool" reste, lui, définitivement couvert (rien à
            # re-tenter, le verdict ne changera pas tout seul).
            if pool_meta.error is not None and pool_meta.error.startswith(_gecko_unavailable):
                result.transient_pricing_error_tokens.add(composite_key)
            # Triangulation (#157, 14/07) : GeckoTerminal n'a pas résolu de
            # pool -- avant de conclure "token illiquide", on croise avec
            # DexScreener. `True` = écart réel entre les deux sources
            # (DexScreener voit une paire que GeckoTerminal rate -- signal
            # à creuser, pas un défaut du wallet) ; `False`/`None` (aucune
            # paire confirmée, ou vérification elle-même indisponible)
            # n'ajoute rien de plus que ce que `pool_lookup_errors` dit déjà.
            if await _dexscreener_has_any_pair(token_addr, chain=chain) is True:
                result.gecko_dexscreener_gap_tokens.append(token_addr)

            # 3e couche (#157, 14/07) : CoinMarketCap tente sa PROPRE
            # résolution de pool, INDÉPENDAMMENT du résultat DexScreener
            # ci-dessus -- le diagnostic "écart entre sources" et la
            # tentative de pricing CMC ne sont pas la même chose. Même
            # quand DexScreener confirme une paire (`True`), il ne fournit
            # aucun prix historique (pas de méthode OHLCV dans ce client)
            # -- CMC est quand même tenté, sinon le token reste non-valorisé
            # alors qu'une paire est confirmée exister.
            cmc_network = CMC_NETWORK_SLUGS.get(chain, "base")
            cmc_pool = await _cmc_resolve_primary_pool(token_addr, network_slug=cmc_network)
            if cmc_pool.available:
                cmc_ohlcv = await _cmc_get_ohlcv(cmc_pool.pool_address, network_slug=cmc_network)
                if cmc_ohlcv.available and cmc_ohlcv.candles:
                    ohlcv = cmc_ohlcv
                    result.cmc_recovered_tokens.append(token_addr)

        # Prix par tx_hash exact (14/07) : tenté pour chaque tx_hash DISTINCT de
        # ce token, dans l'ordre chronologique (cohérent avec le FIFO qui suit),
        # plafonné à WEIGHTS.max_hash_priced_legs_per_token -- jamais une boucle
        # non bornée sur un wallet très actif. Au-delà du plafond, les jambes
        # restantes retombent directement sur pool+OHLCV (ci-dessous), jamais un
        # abandon silencieux du reste du token.
        chain_client = chain_clients.get(chain)
        seen_hashes: set[str] = set()
        ordered_hashes: list[str] = []
        for ts, _amt, tx_hash in sorted(buys + sells, key=lambda leg: leg[0]):
            if tx_hash and tx_hash not in seen_hashes:
                seen_hashes.add(tx_hash)
                ordered_hashes.append(tx_hash)

        hash_prices: dict[str, float] = {}
        for tx_hash in ordered_hashes[: WEIGHTS.max_hash_priced_legs_per_token]:
            price = await _hash_based_price(chain_client, tx_hash, token_addr, wallet, chain=chain)
            if price is not None:
                hash_prices[tx_hash] = price

        if (ohlcv is None or not ohlcv.available or not ohlcv.candles) and not hash_prices:
            result.unpriced_legs += len(buys) + len(sells)
        else:
            from aria_core.services.geckoterminal import price_at

            # Plancher asymétrique (15/07, revue Gemini -- immunité aux rug
            # pulls) : `buy_tx_hashes` identifie les jambes d'ACHAT -- seules
            # celles-ci sont bloquées si le pool GeckoTerminal a été RÉSOLU
            # mais confirmé trop peu liquide (``pool_meta.available and not
            # pool_liquid_enough`` -- PAS juste ``not pool_liquid_enough``,
            # qui vaut aussi True quand GeckoTerminal n'a trouvé AUCUN pool
            # du tout, un cas différent où CMC peut avoir recouvré un prix
            # valide qu'il ne faut alors jamais bloquer). Une jambe de VENTE
            # utilise l'OHLCV même si la liquidité actuelle du pool est sous
            # le plancher (rug pull confirmé après un achat légitime) -- ce
            # choix reste correct pour le cas qu'il vise (bloquer la vente
            # aussi ferait juste réintroduire l'ancien bug d'immunité rug-pull
            # dans l'autre sens). PRÉCISION (15/07, revue DeepSeek -- corrige
            # une sur-affirmation de ce commentaire) : ça ne veut PAS dire que
            # cette lecture est à l'abri de toute manipulation -- un prix de
            # VENTE lu sur un pool à la liquidité manipulée (pump ponctuel
            # plutôt que dump) peut tout aussi bien gonfler un PnL réalisé de
            # façon fictive. C'est le miroir exact de la vulnérabilité dusting
            # déjà documentée plus bas (perte fictive), symétrique côté gain --
            # ni l'un ni l'autre n'est corrigé, cf. bloc de limites.
            buy_tx_hashes = {b_hash for _ts, _amt, b_hash in buys}
            buy_blocked_thin_liquidity = pool_meta.available and not pool_liquid_enough

            def _price_lookup(
                ts, tx_hash, _ohlcv=ohlcv, _hash_prices=hash_prices,
                _buy_hashes=buy_tx_hashes, _blocked=buy_blocked_thin_liquidity,
            ):
                cached = _hash_prices.get(tx_hash)
                if cached is not None:
                    return cached
                if _ohlcv is None or not _ohlcv.available or not _ohlcv.candles:
                    return None
                if tx_hash in _buy_hashes and _blocked:
                    return None
                return price_at(_ohlcv, int(ts.timestamp()))

            fifo = _fifo_match(token_addr, buys, sells, _price_lookup, exact_hashes=frozenset(hash_prices))
            result.closed_trades.extend(fifo.closed_trades)
            result.unpriced_legs += fifo.unpriced_legs
            result.unmatched_sell_events += fifo.unmatched_sell_events

        if pool_meta.available and pool_meta.created_at:
            earliest_buy_ts = min(ts for ts, _amt, _hash in buys)
            elapsed = (earliest_buy_ts - pool_meta.created_at).total_seconds()
            amounts = [a for _, a, _ in buys]
            largest_share = (max(amounts) / sum(amounts)) if amounts and sum(amounts) > 0 else None
            controlled = largest_share is None or largest_share <= _LARGEST_BUY_SHARE_MAX
            if 0 <= elapsed <= _EARLY_ENTRY_WINDOW_SECONDS and controlled:
                result.early_entry_tokens.append(token_addr)
                if ohlcv is not None and ohlcv.available and ohlcv.candles and _is_informed_entry(ohlcv, earliest_buy_ts):
                    result.informed_entry_tokens.append(token_addr)
        else:
            # Diagnostic DexScreener (`gecko_dexscreener_gap_tokens`) et
            # tentative CMC (`cmc_recovered_tokens`) sont déjà traités plus haut,
            # au moment où l'échec GeckoTerminal est constaté -- ce compteur
            # reste Gecko-only par construction (compte tout token sans pool
            # Gecko résolu, que CMC ait ou non récupéré un prix ensuite).
            result.pool_lookup_errors += 1

    return result


async def _funding_source(client: BlockscoutClient, wallet: str) -> tuple[str | None, bool]:
    """Première entrée native trouvée dans l'historique borné du wallet -- une
    BORNE, jamais garantie la vraie première transaction (Blockscout n'offre pas
    de tri "plus ancien d'abord" bon marché, vérifié en direct). Renvoie
    (source ou None, historique_tronqué)."""
    result = await client.get_transactions_bounded(wallet, max_pages=WEIGHTS.funding_source_max_pages)
    if not result.available:
        return None, False
    wallet_l = wallet.lower()
    dated = [
        (t, ts)
        for t in result.transactions
        if (t.to_address or "").lower() == wallet_l
        and (t.value_native or 0) > 0
        and (ts := _parse_timestamp(t.timestamp)) is not None
    ]
    if not dated:
        return None, result.truncated
    earliest_t, _ = min(dated, key=lambda pair: pair[1])
    source = (earliest_t.from_address or "").lower()
    return (source or None), result.truncated


def _pairwise_convergence(addresses: list[str], funding_sources: dict[str, str]) -> list[tuple[str, str]]:
    """Wallets soumis ENSEMBLE partageant la même source de financement initiale
    (heuristique de réutilisation d'adresse de dépôt, Victor FC 2020) -- signal
    croisé, jamais une éliminatoire automatique en dehors de ce contexte pairwise."""
    pairs: list[tuple[str, str]] = []
    for i in range(len(addresses)):
        for j in range(i + 1, len(addresses)):
            a, b = addresses[i].lower(), addresses[j].lower()
            fa, fb = funding_sources.get(a), funding_sources.get(b)
            if fa and fb and fa == fb:
                pairs.append((addresses[i], addresses[j]))
    return pairs


async def _ensure_wallet_scoring_tables() -> None:

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_score_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                scored_at TEXT NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )
        # Classement TVL dynamique des chaînes scannées (#157, 14/07) -- une
        # ligne par chaîne (PRIMARY KEY), remplacée en bloc à chaque
        # rafraîchissement réussi (cf. `refresh_chain_ranking_cache`), jamais
        # un journal append-only comme `wallet_score_log` ci-dessus.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_scoring_chain_ranking (
                chain TEXT PRIMARY KEY,
                tvl_usd REAL NOT NULL,
                rank INTEGER NOT NULL,
                refreshed_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def _log_wallet_score(wallet: str, report_json: str) -> None:
    """Couche 4 (#157) -- écriture pure, aucune logique de scoring n'en dépend.
    Permet une future recalibration contre le vrai track-record ARIA, non
    construite maintenant."""

    await _ensure_wallet_scoring_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_score_log (wallet, scored_at, report_json) VALUES (?, ?, ?)",
            (wallet.lower(), datetime.now(timezone.utc).isoformat(), report_json),
        )
        await db.commit()


async def _latest_scored_wallets(exclude_wallet: str) -> list[dict]:
    """Dernière fiche connue de chaque AUTRE wallet déjà noté (`wallet_score_log`,
    couche 4) -- une ligne par wallet, la plus récente. Base de comparaison du
    classement percentile (15/07) : jamais le wallet contre lui-même.

    Exclut les fiches `full_coverage=False` (15/07, revue Gemini -- pollution
    asymétrique du percentile) : un wallet scanné une seule fois, dont seuls
    quelques tokens prioritaires (récents/rentables, cf. `_select_tokens_for_
    deep_analysis`) ont été analysés en profondeur, produit un score
    temporairement plus favorable qu'un wallet à couverture complète -- le
    comparer sur un pied d'égalité fausse la distribution (un wallet
    moyennement actif mais entièrement couvert serait pénalisé face à des
    fantômes de scans partiels chanceux). Une fiche sans champ `full_coverage`
    du tout (format ancien, avant #157 suite) est traitée comme non couverte
    -- exclue par prudence, jamais un défaut de donnée qui s'invite dans la
    comparaison.

    Exclut aussi `price_confidence_low=True` (15/07, revue ChatGPT -- angle
    mort de comparabilité) : un wallet dont le cost-basis repose majoritairement
    sur des prix ESTIMÉS ne doit pas servir de référence pour juger un autre
    wallet dont les prix sont majoritairement CONFIRMÉS -- même doctrine que
    `full_coverage`, symétrique."""
    await _ensure_wallet_scoring_tables()
    exclude_l = exclude_wallet.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                """
                SELECT report_json FROM wallet_score_log w1
                WHERE wallet != ? AND scored_at = (
                    SELECT MAX(scored_at) FROM wallet_score_log w2 WHERE w2.wallet = w1.wallet
                )
                """,
                (exclude_l,),
            )
        ).fetchall()
    parsed: list[dict] = []
    for (report_json,) in rows:
        try:
            entry = json.loads(report_json)
        except (TypeError, ValueError):
            continue  # ligne corrompue/format ancien -- ignorée, jamais un crash du classement
        if not entry.get("full_coverage"):
            continue
        if entry.get("price_confidence_low"):
            # Angle mort de comparabilité (15/07, revue ChatGPT) : un wallet dont
            # le cost-basis repose majoritairement sur des prix ESTIMÉS (pas
            # confirmés par exécution exacte) ne doit pas polluer la population
            # de comparaison des AUTRES wallets -- même doctrine que full_coverage
            # ci-dessus (une fiche à qualité de données douteuse n'est pas une
            # référence fiable pour juger un autre wallet).
            continue
        parsed.append(entry)
    return parsed


def _diversification_ratio(entry: dict) -> float | None:
    total = entry.get("diversification_total_tokens")
    profitable = entry.get("diversification_profitable_tokens")
    if not total:
        return None
    return profitable / total


async def _apply_comparative_ranking(card: WalletScoreCard) -> None:
    """Classement comparatif (15/07, décision opérateur) : percentile de CE
    wallet parmi tous les AUTRES wallets déjà notés, par axe puis composite.
    Jamais un percentile sur une population vide -- `None` explicite, pas un
    50% par défaut qui suggérerait une comparaison qui n'a pas eu lieu.

    `composite_percentile` ne moyenne QUE les axes de performance/skill
    (win rate, Sortino, PnL, diversification) -- la durée de détention est un
    trait comportemental (conviction vs. rotation), pas un axe "meilleur si
    plus haut" sans ambiguïté (cf. recherche externe 15/07), donc affichée à
    part, jamais fondue dans la moyenne composite."""
    others = await _latest_scored_wallets(card.address)
    card.compared_against_n_wallets = len(others)
    if not others:
        return

    def _percentile(value: float | None, population: list[float]) -> float | None:
        """Percentile de rang MOYEN (15/07, revue externe -- lissage des
        ex-æquo) : un wallet dont la valeur est comptée seulement contre les
        AUTRES strictement inférieurs plaçait à tort tout wallet ex-æquo avec
        la majorité au 0e percentile (ex. beaucoup de wallets à win_rate=0.5
        pile) -- indiscernable d'un wallet réellement pire que tout le monde.
        Convention statistique standard (percentile de rang moyen, cf.
        `scipy.stats.percentileofscore(kind='mean')`) : les ex-æquo comptent
        pour une demi-position plutôt que zéro."""
        if value is None or not population:
            return None
        below = sum(1 for p in population if p < value)
        tied = sum(1 for p in population if p == value)
        return round(100.0 * (below + 0.5 * tied) / len(population), 1)

    win_rate_pop = [o["win_rate"] for o in others if o.get("win_rate") is not None]
    sortino_pop = [o["sortino"] for o in others if o.get("sortino") is not None]
    pnl_pop = [o["realized_pnl_usd"] for o in others if o.get("realized_pnl_usd") is not None]
    holding_pop = [o["avg_holding_period_days"] for o in others if o.get("avg_holding_period_days") is not None]
    diversification_pop = [r for o in others if (r := _diversification_ratio(o)) is not None]

    card.percentile_win_rate = _percentile(card.win_rate, win_rate_pop)
    card.percentile_sortino = _percentile(card.sortino, sortino_pop)
    card.percentile_pnl = _percentile(card.realized_pnl_usd, pnl_pop)
    card.percentile_holding_period = _percentile(card.avg_holding_period_days, holding_pop)
    card.percentile_diversification = _percentile(_diversification_ratio(asdict(card)), diversification_pop)

    skill_axes = [
        p for p in (
            card.percentile_win_rate, card.percentile_sortino, card.percentile_pnl, card.percentile_diversification,
        )
        if p is not None
    ]
    card.composite_percentile = round(fmean(skill_axes), 1) if skill_axes else None


# Plafond du classement TVL dynamique (#157, 14/07, décision opérateur) --
# aujourd'hui inerte (13 chaînes confirmées au total, toutes < 20), gardé
# générique si la liste confirmée grandit plus tard.
_MAX_RANKED_CHAINS = 20

# Repli si le cache TVL n'a jamais tourné (premier déploiement) ou si
# DefiLlama est indisponible -- jamais un /walletscore qui casse faute de
# classement à jour. "bnb" absent (retiré de blockscout.CHAIN_IDS, 14/07,
# Blockscout ne le sert pas).
_FALLBACK_SCAN_CHAINS: tuple[str, ...] = ("base", "ethereum")


async def refresh_chain_ranking_cache() -> bool:
    """Rafraîchit `wallet_scoring_chain_ranking` depuis le classement TVL
    DefiLlama (#157, 14/07) -- appelé par le heartbeat mensuel
    (`wallet_scoring_chain_ranking_refresh`), jamais par un scan `/walletscore`
    individuel. Sur échec DefiLlama, la table n'est JAMAIS vidée -- le dernier
    classement réussi continue de servir jusqu'au prochain rafraîchissement
    réussi. Retourne `True` si le cache a été mis à jour, `False` sinon."""
    from aria_core.services.defillama import fetch_chain_tvl_ranking

    ranking = await fetch_chain_tvl_ranking()
    if ranking is None:
        logger.warning("refresh_chain_ranking_cache: DefiLlama indisponible -- cache TVL inchangé")
        return False

    ranking = ranking[:_MAX_RANKED_CHAINS]
    refreshed_at = datetime.now(timezone.utc).isoformat()

    await _ensure_wallet_scoring_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM wallet_scoring_chain_ranking")
        await db.executemany(
            "INSERT INTO wallet_scoring_chain_ranking (chain, tvl_usd, rank, refreshed_at) VALUES (?, ?, ?, ?)",
            [(chain, tvl, rank, refreshed_at) for rank, (chain, tvl) in enumerate(ranking, start=1)],
        )
        await db.commit()

    logger.info("refresh_chain_ranking_cache: %s chaînes mises en cache (%s)", len(ranking), refreshed_at)
    return True


async def DEFAULT_SCAN_CHAINS() -> tuple[str, ...]:
    """Chaînes scannées par défaut par `/walletscore` -- lit le classement TVL
    en cache (#157, 14/07), trié par rang. Repli sur `_FALLBACK_SCAN_CHAINS`
    si le cache est vide (jamais tourné) OU inaccessible -- jamais une
    exception qui casse un scan faute de classement à jour."""
    try:
        await _ensure_wallet_scoring_tables()
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT chain FROM wallet_scoring_chain_ranking ORDER BY rank ASC"
            )
            rows = await cursor.fetchall()
    except Exception:
        logger.warning("DEFAULT_SCAN_CHAINS: cache TVL inaccessible -- repli sur %s", _FALLBACK_SCAN_CHAINS)
        return _FALLBACK_SCAN_CHAINS

    if not rows:
        return _FALLBACK_SCAN_CHAINS
    return tuple(row[0] for row in rows)


@dataclass
class HardDisqualifiers:
    is_contract: bool = False
    wash_trading_suspected: bool = False
    financed_by_known_malicious: bool = False
    financing_check_note: str | None = None  # info NON disqualifiante (ex. vérification GoPlus indisponible)
    reasons: list[str] = field(default_factory=list)

    @property
    def disqualified(self) -> bool:
        return self.is_contract or self.wash_trading_suspected or self.financed_by_known_malicious


async def _hard_disqualifiers(
    wallet: str,
    info: AddressInfo,
    all_transfers: list[TokenTransfer],
    funding_source: str | None,
    *,
    extra_exclusions: set[str] | None = None,
    goplus_client=None,
    funding_source_chain: str | None = None,
) -> HardDisqualifiers:
    reasons: list[str] = []

    is_contract = bool(info.is_contract) if info.available else False
    if is_contract:
        reasons.append("Wallet-contrat (équipe/vesting/LP), pas un trader individuel.")

    wash = (
        _dominant_counterparty_share(all_transfers, wallet, lp_address=None, extra_exclusions=extra_exclusions)
        >= _WASH_TRADING_COUNTERPARTY_SHARE
    )
    if wash:
        reasons.append("Wash-trading suspecté (échanges concentrés sur une seule contrepartie, tous tokens confondus).")

    # Financement par un wallet déjà connu comme malveillant -- GoPlus Malicious
    # Address API (AML), #157 (14/07). RÉSERVE HONNÊTE (research doc du 14/07,
    # vérifiée en direct, ÉTENDUE ce soir aux 13 chaînes du scan multi-chaînes) :
    # les 13 chain_id confirmés répondent tous "code: 1, ok" avec le même format
    # -- couverture FORMAT confirmée partout.
    #
    # Approfondi le même soir (2e test, adresses ACTIVES/connues cette fois --
    # WETH predeploy sur unichain/soneium/mode, token CELO natif, WRBTC sur
    # rootstock -- pas des adresses burn) sur celo/rootstock/unichain/soneium/
    # mode :
    # - `contract_address` reste "-1" (indéterminé) sur les 5 chaînes MÊME avec
    #   une adresse très active et connue -- ce n'était donc PAS un artefact du
    #   choix d'adresse burn du premier test : ce champ précis ne se résout
    #   simplement jamais sur ces 5 chaînes, quelle que soit l'adresse.
    # - MAIS `data_source`/`honeypot_related_address` (les champs réellement
    #   liés à l'analyse de sécurité, pas `contract_address`) se comportent
    #   différemment selon la chaîne : sur Unichain/Soneium/Mode, `data_source`
    #   passe de `""` (adresse burn) à `"GoPlus"` (adresse active) et
    #   `honeypot_related_address` de `"0"` à `"1"` -- une vraie analyse tourne
    #   sur ces 3 chaînes une fois qu'il y a de l'activité à analyser. Sur
    #   Celo/Rootstock, `data_source` reste `""` même avec une adresse active et
    #   connue -- aucun signe d'analyse engagée sur ces 2 chaînes, sur les deux
    #   adresses testées ce soir (burn et active).
    # - Réserve à garder explicite : une seule adresse active testée par chaîne
    #   ce soir, jamais une adresse effectivement flaggée malveillante nulle
    #   part -- ceci documente un INDICE de couverture (Unichain/Soneium/Mode
    #   probablement mieux couvertes que Celo/Rootstock), PAS une preuve
    #   définitive d'absence de données sur Celo/Rootstock.
    #
    # Conclusion pratique inchangée : filtre probabiliste supplémentaire,
    # jamais présenté comme exhaustif, quelle que soit la chaîne -- la densité
    # réelle des données malveillantes varie probablement par chaîne, avec un
    # indice de couverture plus faible sur Celo/Rootstock. Fail-closed strict :
    # une vérification indisponible reste "indisponible" (note informative, PAS
    # une disqualification, PAS non plus un faux négatif silencieux qui dirait
    # "non malveillant" sans le dire).
    #
    # `funding_source_chain` (#157, correction 14/07) : la chaîne où
    # `funding_source` a RÉELLEMENT été trouvé (cf. score_wallets) -- jamais
    # supposée Base par défaut désormais que le scan couvre 13 chaînes ; une
    # adresse de financement peut légitimement vivre sur une chaîne différente
    # de celle où le wallet trade. `CHAIN_IDS` (blockscout.py) est la SEULE
    # source de vérité pour traduire le nom de chaîne en chain_id GoPlus --
    # aucun registre dupliqué. Chaîne absente/inconnue : repli sur le défaut
    # de `get_address_security` (Base), jamais un chain_id inventé.
    financed_by_malicious = False
    financing_check_note: str | None = None
    if funding_source:
        if goplus_client is None:
            from aria_core.services.goplus import goplus_client as _default_goplus_client

            goplus_client = _default_goplus_client
        from aria_core.services.blockscout import CHAIN_IDS

        chain_id = CHAIN_IDS.get(funding_source_chain) if funding_source_chain else None
        if chain_id is not None:
            security = await goplus_client.get_address_security(funding_source, chain_id=str(chain_id))
        else:
            security = await goplus_client.get_address_security(funding_source)
        if security.available:
            financed_by_malicious = security.is_malicious
            if financed_by_malicious:
                reasons.append(
                    f"Financé par un wallet marqué à risque par GoPlus AML ({funding_source}, "
                    f"catégories : {', '.join(sorted(security.flags)) or 'non précisées'})."
                )
        else:
            financing_check_note = (
                f"Vérification GoPlus AML de la source de financement indisponible ({security.error}) "
                "-- disqualifiant non évalué, jamais un faux négatif silencieux."
            )

    return HardDisqualifiers(
        is_contract=is_contract,
        wash_trading_suspected=wash,
        financed_by_known_malicious=financed_by_malicious,
        financing_check_note=financing_check_note,
        reasons=reasons,
    )


@dataclass
class WalletScoreCard:
    address: str
    display_name: str | None = None  # ENS/Basename -- COSMÉTIQUE, jamais lu par aucun calcul de score
    available: bool = True
    error: str | None = None

    disqualified: bool = False
    disqualification_reasons: list[str] = field(default_factory=list)
    financing_check_note: str | None = None  # info NON disqualifiante (ex. vérification GoPlus AML indisponible)

    tokens_found: int = 0
    tokens_analyzed: int = 0
    tokens_skipped_capped: bool = False
    chains_scanned: list[str] = field(default_factory=list)  # chaînes où une activité réelle a été trouvée (#157, 14/07)
    # 15/07, revue externe -- historique tronqué par le plafond de pagination
    # Blockscout (2000 transferts/10 pages) : l'API avait ENCORE de la donnée
    # au-delà de ce qui a été récupéré (jamais quand l'historique est
    # réellement épuisé). Un wallet très actif peut donc manquer ses
    # transferts les plus anciens -- risque de biais sur TOUS les axes
    # (W/PnL/S/D) et le percentile, pas seulement `unmatched_sell_events`.
    transfer_history_truncated: bool = False

    # Scan incrémental persistant (#157 suite, 15/07) : `tokens_analyzed` ci-dessus
    # reste "analysés CETTE passe" -- ces deux champs donnent la vue cumulative
    # (couverture réelle du wallet au fil des appels successifs, cf.
    # wallet_scan_state.py). `full_coverage=True` = tous les tokens connus à ce
    # jour ont été vus au moins une fois ; un futur appel ne fait plus que
    # rafraîchir l'activité nouvelle depuis le dernier scan.
    tokens_scanned_cumulative: int = 0
    full_coverage: bool = False

    closed_trades_count: int = 0
    unpriced_legs: int = 0
    pool_lookup_errors: int = 0  # tokens sans pool GeckoTerminal résolu (#157, 14/07 -- diagnostic)
    gecko_dexscreener_gap_count: int = 0  # parmi eux, DexScreener voit une paire que GeckoTerminal a ratée (#157, 14/07)
    cmc_price_recovery_count: int = 0  # parmi eux, valorisés via CoinMarketCap après échec GeckoTerminal (#157, 14/07)
    # Défense anti-dust/scam-pool (15/07, revue Gemini) : tokens dont le pool a
    # été résolu mais dont la liquidité confirmée est sous
    # WEIGHTS.min_pool_liquidity_usd_for_pricing -- non valorisés (diagnostic
    # PAR PASSE, même convention que les deux compteurs ci-dessus).
    thin_liquidity_pricing_skipped_count: int = 0
    # Ventes dont la queue FIFO d'achats s'est épuisée (15/07, revue Gemini) --
    # signal possible de rebase/rendement DeFi jamais crédité comme profit,
    # juste compté pour transparence. Diagnostic PAR PASSE (pas cumulatif).
    unmatched_sell_events: int = 0
    # Gel des erreurs transitoires (15/07, revue Gemini) : parmi les tokens de
    # cette passe, combien ont échoué pour une cause d'infrastructure (jamais
    # marqués "scanné" -- retentés au prochain appel). Diagnostic PAR PASSE.
    transient_pricing_errors: int = 0
    win_rate: float | None = None
    realized_pnl_usd: float | None = None
    sortino: float | None = None
    # Contradiction Sortino/PnL (15/07, revue externe -- biais d'asymétrie de
    # taille) : `sortino` se calcule sur `return_i` (rendement EN %), jamais
    # pondéré par le capital engagé sur le trade -- un wallet peut afficher un
    # Sortino positif "honorable" (moyenne des % de rendement) alors que son
    # PnL réalisé EN DOLLARS est négatif (une grosse perte en $ mais petite en
    # %, plusieurs petits gains en % sur des mises minuscules). Ce drapeau
    # capture le cas le plus flagrant et vérifiable À COUP SÛR (contradiction
    # de SIGNE entre les deux, jamais une nuance à interpréter) -- il ne
    # corrige pas le biais sous-jacent (non pondéré par la taille, cf. bloc de
    # limites), il rend visible sa manifestation la plus trompeuse.
    sortino_pnl_contradiction: bool = False
    max_drawdown_pct: float | None = None
    avg_holding_period_days: float | None = None  # 15/07 -- conviction vs. rotation rapide (méthodologie sourcée)

    # Fenêtre récente (15/07, revue ChatGPT -- biais temporel) : en PLUS des
    # métriques historiques complètes ci-dessus, jamais à leur place.
    win_rate_recent: float | None = None
    realized_pnl_usd_recent: float | None = None
    recent_window_trades_count: int = 0

    # Confiance du cost-basis (15/07, revue Gemini) : part des jambes (achat +
    # vente) valorisées par un prix d'exécution EXACT plutôt que par le repli
    # marché OHLCV. Affiché À CÔTÉ du score (jamais en cachant win_rate/PnL),
    # même doctrine que `sample_size_sufficient` -- pas le masquage complet de
    # Sortino/robust_pnl/health_trend.
    price_confirmation_ratio: float | None = None
    price_confidence_low: bool = False

    diversification_profitable_tokens: int = 0
    diversification_total_tokens: int = 0
    # Diversification pondérée par capital (15/07, revue ChatGPT) : part du
    # capital total déployé qui a fini dans une position profitable -- complète
    # (remplace pas) le ratio de comptage ci-dessus, mesure la CONCENTRATION du
    # capital plutôt que la largeur des paris indépendants.
    diversification_capital_weighted_ratio: float | None = None

    early_entry_recurrence_count: int = 0
    informed_entry_count: int = 0

    funding_source: str | None = None
    funding_source_truncated: bool = False

    # Échantillon minimum + robustesse anti-chance + tendance dans le temps
    # (15/07, décision opérateur). Tous calculés sur `cumulative_trades`
    # (l'historique complet archivé, pas seulement ce lot) -- s'affinent au
    # fil des scans successifs, même doctrine que le reste du score cumulatif.
    wallet_age_days: float | None = None
    total_swaps: int = 0
    sample_size_sufficient: bool = False  # âge >= min_wallet_age_days ET swaps >= min_total_swaps
    robust_pnl_positive: bool | None = None  # None = pas assez de trades pour ce test
    health_trend: str | None = None  # "amélioration" / "stable" / "dégradation" / None (pas assez de trades)

    # Classement comparatif (15/07) : percentile de CE wallet parmi tous les
    # wallets déjà notés (wallet_score_log), par axe puis composite. None tant
    # qu'il n'y a pas d'autres wallets notés pour comparer (jamais un
    # percentile inventé sur une population vide/unitaire).
    percentile_win_rate: float | None = None
    percentile_sortino: float | None = None
    percentile_pnl: float | None = None
    percentile_diversification: float | None = None
    percentile_holding_period: float | None = None
    composite_percentile: float | None = None
    compared_against_n_wallets: int = 0

    suspect_positive: bool = False
    thesis: str | None = None


@dataclass
class WalletScoringReport:
    wallets: list[WalletScoreCard] = field(default_factory=list)
    convergence_pairs: list[tuple[str, str]] = field(default_factory=list)
    synthesis: str | None = None
    available: bool = True
    error: str | None = None


def _suspect_positive_flag(card: WalletScoreCard) -> bool:
    """Couche 3 (#157) -- SÉPARÉ du score composite, jamais fondu dans une
    moyenne. Vrai si le wallet dépasse un seuil statique sur au moins
    `WEIGHTS.suspect_positive_min_axes` axes indépendants simultanément. Seuils
    statiques de départ (pas de vrais percentiles tant qu'il n'y a pas
    d'historique ARIA -- cf. couche 4), révisables."""
    axes = 0
    if card.win_rate is not None and card.win_rate >= WEIGHTS.suspect_win_rate_min:
        axes += 1
    if card.sortino is not None and card.sortino >= WEIGHTS.suspect_sortino_min:
        axes += 1
    if (
        card.diversification_total_tokens >= WEIGHTS.suspect_diversification_min_tokens
        and card.diversification_profitable_tokens / card.diversification_total_tokens
        >= WEIGHTS.suspect_diversification_ratio_min
    ):
        axes += 1
    if card.early_entry_recurrence_count >= WEIGHTS.suspect_recurrence_min:
        axes += 1
    return axes >= WEIGHTS.suspect_positive_min_axes


_WALLET_THESIS_SYSTEM = (
    "Tu es ARIA. On te montre un ou plusieurs wallets déjà notés par un pipeline "
    "déterministe (FIFO PnL, Sortino, drawdown, durée moyenne de détention, "
    "récurrence d'entrée précoce -- "
    "AUCUN chiffre ci-dessous n'est de toi, tu synthétises, tu n'en inventes jamais "
    "un nouveau). Si une donnée est marquée indisponible, dis-le explicitement, ne "
    "la comble jamais. Rappel absolu : ce score sert de confirmation/contexte, "
    "jamais un signal de copy-trade -- ne recommande jamais d'imiter ces wallets. "
    "Réponds STRICTEMENT en JSON : {\"wallets\": [{\"address\": \"0x...\", "
    "\"thesis\": \"<3-5 phrases factuelles>\"}], \"synthesis\": \"<note globale si "
    "plusieurs wallets soumis ensemble, sinon chaîne vide>\"}"
)


def _format_card_for_prompt(card: WalletScoreCard) -> str:
    lines = [f"Wallet {card.address}" + (f" ({card.display_name})" if card.display_name else "")]
    if not card.available:
        lines.append(f"Données indisponibles : {card.error or UNAVAILABLE}")
        return "\n".join(lines)
    if card.disqualified:
        lines.append("DISQUALIFIÉ : " + "; ".join(card.disqualification_reasons))
    if card.financing_check_note:
        lines.append(card.financing_check_note)
    lines.append(
        f"Tokens tradés trouvés : {card.tokens_found} (analysés cette passe : {card.tokens_analyzed}"
        + (f", plafond de {WEIGHTS.max_tokens_analyzed} atteint -- {card.tokens_scanned_cumulative}/{card.tokens_found} couverts au total" if card.tokens_skipped_capped else "")
        + ")"
    )
    lines.append(
        "Couverture complète du portefeuille atteinte." if card.full_coverage
        else f"Scan progressif en cours ({card.tokens_scanned_cumulative}/{card.tokens_found} tokens couverts à ce jour) -- "
             "relancer /walletscore plus tard pour poursuivre et affiner la note."
    )
    if card.transfer_history_truncated:
        lines.append(
            "ATTENTION : historique de transferts tronqué par le plafond de pagination -- ce wallet est très "
            "actif, des transferts plus anciens que ceux récupérés existent peut-être encore et ne sont pas "
            "couverts (risque de biais sur le PnL/win rate/Sortino/diversification)."
        )
    lines.append(
        f"Trades clôturés valorisés (cumulé) : {card.closed_trades_count} (jambes sans prix cette passe : "
        f"{card.unpriced_legs}, tokens sans pool GeckoTerminal résolu cette passe : {card.pool_lookup_errors})"
    )
    if card.thin_liquidity_pricing_skipped_count:
        lines.append(
            f"Dont {card.thin_liquidity_pricing_skipped_count} token(s) avec un pool trop peu liquide pour faire "
            f"confiance à son prix (< ${WEIGHTS.min_pool_liquidity_usd_for_pricing:,.0f}) -- non valorisé(s), "
            "défense anti-dust/scam-pool."
        )
    if card.unmatched_sell_events:
        lines.append(
            f"{card.unmatched_sell_events} vente(s) dont la quantité dépasse ce qui a été acheté dans la fenêtre "
            "récupérée (rendement de rebase/DeFi possible, ou achat antérieur) -- jamais créditée comme profit."
        )
    if card.transient_pricing_errors:
        lines.append(
            f"{card.transient_pricing_errors} token(s) non couvert(s) cette passe suite à une panne d'API "
            "temporaire (timeout/rate-limit) -- retenté(s) automatiquement au prochain scan, jamais figé(s)."
        )
    if card.gecko_dexscreener_gap_count:
        lines.append(
            f"Dont {card.gecko_dexscreener_gap_count} avec une paire DexScreener trouvée que GeckoTerminal "
            "n'a pas résolue (écart entre sources, pas forcément un token illiquide)."
        )
    if card.cmc_price_recovery_count:
        lines.append(
            f"Dont {card.cmc_price_recovery_count} valorisé(s) via CoinMarketCap après échec GeckoTerminal "
            "(3e couche de pricing, #157)."
        )
    lines.append(f"Win rate : {card.win_rate:.0%}" if card.win_rate is not None else "Win rate : indisponible")
    lines.append(
        f"PnL réalisé : ${card.realized_pnl_usd:,.2f}"
        if card.realized_pnl_usd is not None
        else "PnL réalisé : indisponible"
    )
    lines.append(
        f"Sortino : {card.sortino:.2f}"
        if card.sortino is not None
        else "Sortino : indisponible (trop peu de trades clôturés ou aucune perte observée)"
    )
    if card.sortino_pnl_contradiction:
        lines.append(
            "ATTENTION : Sortino positif mais PnL réalisé négatif -- le Sortino se base sur le "
            "rendement en % par trade, jamais pondéré par la taille de la position (un petit gain "
            "en % sur une grosse perte en $ peut gonfler ce ratio) -- ne pas lire seul, croiser avec "
            "le PnL réalisé en dollars ci-dessus."
        )
    lines.append(
        f"Max drawdown (wallet) : {card.max_drawdown_pct:.0%}"
        if card.max_drawdown_pct is not None
        else "Max drawdown : indisponible"
    )
    lines.append(
        f"Diversification : {card.diversification_profitable_tokens}/{card.diversification_total_tokens} tokens profitables"
    )
    lines.append(
        f"Diversification pondérée par capital : {card.diversification_capital_weighted_ratio:.0%} du capital "
        "déployé a fini dans une position profitable"
        if card.diversification_capital_weighted_ratio is not None
        else "Diversification pondérée par capital : indisponible"
    )
    lines.append(
        f"Durée moyenne de détention : {card.avg_holding_period_days:.1f} jour(s)"
        if card.avg_holding_period_days is not None
        else "Durée moyenne de détention : indisponible"
    )
    if card.recent_window_trades_count:
        lines.append(
            f"Fenêtre récente ({WEIGHTS.recent_window_days}j, {card.recent_window_trades_count} trade(s) "
            f"clôturé(s)) : win rate {card.win_rate_recent:.0%}, PnL ${card.realized_pnl_usd_recent:,.2f} "
            "-- en complément de l'historique complet ci-dessus, jamais à sa place."
        )
    else:
        lines.append(f"Fenêtre récente ({WEIGHTS.recent_window_days}j) : aucun trade clôturé -- indisponible")
    lines.append(
        f"Récurrence acheteur précoce multi-lancements : {card.early_entry_recurrence_count} token(s) "
        f"(dont {card.informed_entry_count} avec conditions techniques jugées informées)"
    )
    lines.append(f"Suspect positif (multi-axes) : {'oui' if card.suspect_positive else 'non'}")
    lines.append(
        f"Échantillon suffisant pour un classement fiable ({WEIGHTS.min_wallet_age_days}j+/"
        f"{WEIGHTS.min_total_swaps}+ swaps) : {'oui' if card.sample_size_sufficient else 'non'} "
        f"(âge : {card.wallet_age_days:.0f}j, swaps : {card.total_swaps})"
        if card.wallet_age_days is not None
        else "Ancienneté du wallet : indisponible"
    )
    lines.append(
        f"Robustesse anti-chance (retrait des {WEIGHTS.robust_trim_pct:.0%} meilleurs ET "
        f"{WEIGHTS.robust_trim_pct:.0%} pires trades) : PnL restant "
        f"{'positif' if card.robust_pnl_positive else 'négatif'}"
        if card.robust_pnl_positive is not None
        else "Robustesse anti-chance : indisponible (pas assez de trades clôturés)"
    )
    lines.append(
        f"Tendance de santé dans le temps : {card.health_trend}"
        if card.health_trend is not None
        else "Tendance de santé dans le temps : indisponible (pas assez de trades clôturés)"
    )
    if card.price_confirmation_ratio is not None:
        lines.append(f"Confiance du cost-basis : {card.price_confirmation_ratio:.0%} des prix confirmés par exécution exacte")
        if card.price_confidence_low:
            lines.append(
                "Attention : une partie importante des prix d'entrée de ce wallet est estimée via les prix de "
                "marché historiques (transferts type CEX ou swaps complexes sans stablecoin direct) -- le PnL "
                "réel peut différer."
            )
    if card.compared_against_n_wallets > 0:
        lines.append(
            f"Classement comparatif (vs {card.compared_against_n_wallets} autre(s) wallet(s) suivi(s)) : "
            f"percentile composite {card.composite_percentile:.0f}e" if card.composite_percentile is not None
            else f"Classement comparatif : pas assez d'axes communs avec les {card.compared_against_n_wallets} "
                 "autre(s) wallet(s) suivi(s)"
        )
        if card.composite_percentile is not None and card.price_confidence_low:
            # Angle mort de comparabilité (15/07, revue ChatGPT) : le drapeau de
            # confiance basse vivait ailleurs dans le rapport, jamais rattaché au
            # chiffre du percentile lui-même -- un lecteur (humain ou LLM de
            # synthèse) pouvait présenter un excellent classement comme fiable
            # sans le relier à un cost-basis majoritairement estimé.
            lines.append(
                "ATTENTION : ce percentile repose majoritairement sur des prix estimés "
                f"(confiance du cost-basis {card.price_confirmation_ratio:.0%}, sous le seuil de "
                f"{WEIGHTS.min_price_confirmation_ratio:.0%}) -- à interpréter avec prudence."
            )
        if card.percentile_holding_period is not None:
            lines.append(f"Percentile durée de détention (contextuel, hors composite) : {card.percentile_holding_period:.0f}e")
    else:
        lines.append("Classement comparatif : indisponible (aucun autre wallet encore suivi pour comparer)")
    return "\n".join(lines)


async def _generate_thesis(
    cards: list[WalletScoreCard], convergence_pairs: list[tuple[str, str]], *, llm=None,
) -> str | None:
    if llm is None:
        from aria_core.llm import chat_with_context as llm

    from aria_core.runtime import settings
    from aria_core.spark_config import DEFAULT_MODEL_DEVELOP

    develop_model = (getattr(settings, "aria_llm_model_develop", None) or "").strip() or DEFAULT_MODEL_DEVELOP

    prompt_parts = [_format_card_for_prompt(c) for c in cards]
    if convergence_pairs:
        prompt_parts.append(
            "Wallets soumis ensemble partageant une source de financement initiale "
            "(suspects d'être la même entité) : "
            + ", ".join(f"{a} <-> {b}" for a, b in convergence_pairs)
        )
    prompt = "\n\n".join(prompt_parts)

    raw = await llm(prompt, _WALLET_THESIS_SYSTEM, max_tokens=800, model=develop_model, depth="wallet_scoring")
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    wallet_theses = {
        str(w.get("address", "")).lower(): str(w.get("thesis", ""))
        for w in (data.get("wallets") or [])
        if isinstance(w, dict)
    }
    for card in cards:
        card.thesis = wallet_theses.get(card.address.lower()) or None

    return str(data.get("synthesis") or "") or None


def _valid_address(address: str) -> bool:
    return bool(address) and address.startswith("0x") and len(address) == 42


async def score_wallets(
    addresses: list[str],
    *,
    client: BlockscoutClient | None = None,
    chains: dict[str, BlockscoutClient] | None = None,
    gecko,
    llm=None,
    goplus=None,
    max_tokens: int | None = None,
) -> WalletScoringReport:
    """Point d'entrée wallet-centrique (#157) : 1 à 3 adresses -> disqualifiants
    durs, score composite, drapeau suspect positif, thèse LLM. Toujours un
    signal de confirmation/contexte, jamais un déclencheur (même règle absolue
    que `analyze_smart_money`).

    Multi-chaînes EVM (#157, 14/07, décision opérateur explicite) : une même
    adresse 0x est valide sur toutes les chaînes EVM -- ARIA essaie chaque
    chaîne et CONSOLIDE en un seul score (pas un score par chaîne), plafond de
    tokens analysés appliqué globalement sur l'ensemble consolidé. ``chains``
    (dict chaîne -> client) permet d'injecter un registre explicite (tests, ou
    un sous-ensemble de chaînes) ; à défaut, ``client`` seul retombe sur un
    comportement mono-chaîne "base" STRICTEMENT inchangé (chemin historique,
    tous les tests existants) ; si ni l'un ni l'autre n'est fourni, le
    classement TVL dynamique (`DEFAULT_SCAN_CHAINS()`, #157 14/07 -- DefiLlama,
    rafraîchi mensuellement par le heartbeat, repli sur Base/Ethereum si le
    cache n'a jamais tourné) est utilisé. Solana n'est PAS EVM (chantier
    séparé, hors scope) -- jamais dans ce registre.

    PRÉCISION DE PORTÉE (15/07, revue ChatGPT -- incohérence relevée entre
    cette docstring et la limite "ponts cross-chain" documentée plus haut) :
    "consolidé" signifie ici que les trades/métriques de TOUTES les chaînes
    scannées sont agrégés dans UN SEUL jeu de chiffres (win_rate/PnL/Sortino/
    etc. mélangent les trades Base et Ethereum d'un même wallet, par exemple)
    -- PAS que le cost-basis d'UNE position suit une continuité à travers un
    bridge. Un achat sur Base puis un pont vers Arbitrum puis une vente sur
    Arbitrum (économiquement UN seul trade) est vu comme DEUX événements
    FIFO indépendants et non reliés (cf. limite "ponts cross-chain" plus
    haut) -- consolidation des MÉTRIQUES par wallet, jamais continuité du
    cost-basis à travers les bridges.
    """
    if not addresses:
        return WalletScoringReport(available=False, error="aucune adresse fournie")
    if len(addresses) > 3:
        return WalletScoringReport(available=False, error="maximum 3 adresses par appel")
    if not all(_valid_address(a) for a in addresses):
        return WalletScoringReport(available=False, error="adresse invalide -- attendu 0x + 40 caractères hexadécimaux")

    if chains is not None:
        chain_clients = chains
    elif client is not None:
        chain_clients = {"base": client}
    else:
        from aria_core.services.blockscout import get_blockscout_client

        chain_clients = {c: get_blockscout_client(c) for c in await DEFAULT_SCAN_CHAINS()}

    cards: list[WalletScoreCard] = []
    funding_sources: dict[str, str] = {}

    for wallet in addresses:
        card = WalletScoreCard(address=wallet)

        grouped: dict[str, list[TokenTransfer]] = {}
        chains_with_data: list[str] = []
        all_flat_transfers: list[TokenTransfer] = []
        primary_info: AddressInfo | None = None
        funding_source: str | None = None
        funding_truncated = False
        funding_source_chain: str | None = None
        any_chain_available = False
        last_error: str | None = None
        # 15/07, revue externe -- historique tronqué par le plafond de
        # pagination (2000 transferts/10 pages) : `TokenTransfersResult.
        # truncated` signale quand Blockscout avait ENCORE de la donnée
        # (`next_page_params`) alors qu'on a arrêté à cause du plafond, jamais
        # quand l'historique est réellement épuisé. Sans ce signal, un wallet
        # très actif (> 2000 transferts ERC-20 vie entière) verrait ses
        # premiers achats silencieusement absents du FIFO -- des ventes plus
        # tard dans l'historique deviendraient des `unmatched_sell_events` à
        # tort, biaisant potentiellement W/PnL/S/D et le percentile.
        transfers_truncated = False

        for chain, chain_client in chain_clients.items():
            transfers_result = await chain_client.get_token_transfers(
                wallet, limit=2000, max_pages=10, token_type="ERC-20",
            )
            if not transfers_result.available:
                last_error = transfers_result.error or UNAVAILABLE
                continue
            any_chain_available = True
            transfers_truncated = transfers_truncated or transfers_result.truncated
            if transfers_result.transfers:
                chains_with_data.append(chain)
                all_flat_transfers.extend(transfers_result.transfers)
            grouped.update(_group_transfers_by_token(transfers_result.transfers, chain=chain))

            if primary_info is None or not primary_info.available:
                info = await chain_client.get_address_info(wallet)
                if info.available:
                    primary_info = info

            if funding_source is None:
                fs, trunc = await _funding_source(chain_client, wallet)
                if fs:
                    funding_source, funding_truncated = fs, trunc
                    # Chaîne RÉELLE où funding_source a été trouvé (#157,
                    # correction 14/07) -- jamais supposée Base par défaut :
                    # une adresse de financement peut vivre sur une chaîne
                    # différente de celle où le wallet trade ses tokens.
                    funding_source_chain = chain

        if not any_chain_available:
            card.available = False
            card.error = last_error or UNAVAILABLE
            cards.append(card)
            continue

        card.display_name = primary_info.ens_domain_name if primary_info else None
        card.chains_scanned = chains_with_data
        card.transfer_history_truncated = transfers_truncated

        # Scan incrémental persistant (#157 suite, 15/07) : ne ré-analyse QUE les
        # tokens jamais vus, ou dont l'activité a évolué depuis le dernier scan
        # (nouveau transfert postérieur à `checkpoint.last_scan_at`) -- jamais les
        # 680 tokens d'un coup, jamais non plus une re-analyse inutile d'un token
        # déjà couvert et inchangé.
        from aria_core.services import wallet_scan_state

        checkpoint = await wallet_scan_state.get_checkpoint(wallet)
        total_found = len(grouped)

        def _needs_scan(key: str, transfers: list[TokenTransfer]) -> bool:
            if key not in checkpoint.scanned_tokens:
                return True
            if checkpoint.last_scan_at is None:
                return False
            return any(
                (ts := _parse_timestamp(t.timestamp)) is not None and ts > checkpoint.last_scan_at
                for t in transfers
            )

        pending = {k: v for k, v in grouped.items() if _needs_scan(k, v)}

        cap = max_tokens if max_tokens is not None else WEIGHTS.max_tokens_analyzed
        selected_tokens, _pending_total, skipped = _select_tokens_for_deep_analysis(pending, wallet=wallet, cap=cap)
        card.tokens_found = total_found
        card.tokens_analyzed = len(selected_tokens)
        card.tokens_skipped_capped = skipped > 0
        if card.tokens_skipped_capped:
            logger.info(
                "score_wallets: wallet %s -- plafond de %s tokens atteint (%s restants à couvrir, "
                "sélection par round-trip puis récence/nombre de trades, toutes chaînes confondues)",
                wallet, cap, skipped,
            )

        selected_transfers = {key: grouped[key] for key in selected_tokens}

        card.funding_source = funding_source
        card.funding_source_truncated = funding_truncated
        if funding_source:
            funding_sources[wallet.lower()] = funding_source

        # Analyse multi-token AVANT les disqualifiants durs : fournit les pools
        # réellement résolus par token, utilisés ci-dessous pour généraliser
        # l'exclusion wash-trading sans faux positif (#157, correction 14/07).
        multi = await _analyze_wallet_multi_token(wallet, selected_transfers, gecko=gecko, chain_clients=chain_clients)
        card.unpriced_legs = multi.unpriced_legs
        card.pool_lookup_errors = multi.pool_lookup_errors
        card.gecko_dexscreener_gap_count = len(multi.gecko_dexscreener_gap_tokens)
        card.cmc_price_recovery_count = len(multi.cmc_recovered_tokens)
        card.thin_liquidity_pricing_skipped_count = len(multi.thin_liquidity_tokens)
        card.unmatched_sell_events = multi.unmatched_sell_events
        card.transient_pricing_errors = len(multi.transient_pricing_error_tokens)

        # Persistance : ce lot remplace les trades archivés des tokens qu'il
        # couvre (le FIFO est recalculé en entier depuis l'historique complet du
        # token, jamais un append qui dupliquerait les mêmes trades historiques).
        batch_addresses = {key.partition(":")[2] for key in selected_tokens}
        await wallet_scan_state.replace_archived_trades(wallet, batch_addresses, multi.closed_trades)

        now = datetime.now(timezone.utc)
        # Gel des erreurs transitoires (15/07, revue Gemini) : un token dont la
        # résolution de pool a échoué CE PASSAGE pour une cause d'infrastructure
        # (`transient_pricing_error_tokens`) n'est JAMAIS marqué "scanné" -- il
        # reste éligible à une nouvelle tentative au prochain appel, même sans
        # nouvelle activité on-chain (`_needs_scan` le re-sélectionnera). Sans
        # ça, un simple timeout/429 ponctuel se serait figé en cicatrice
        # permanente (jambe jamais reprix, jamais retentée) dans les archives.
        new_scanned = checkpoint.scanned_tokens | (set(selected_tokens) - multi.transient_pricing_error_tokens)
        full_coverage_at = checkpoint.full_coverage_at
        if full_coverage_at is None and len(new_scanned) >= total_found:
            full_coverage_at = now
        await wallet_scan_state.save_checkpoint(
            wallet, scanned_tokens=new_scanned, last_scan_at=now,
            tokens_found_total=total_found, full_coverage_at=full_coverage_at,
        )
        card.tokens_scanned_cumulative = len(new_scanned)
        card.full_coverage = full_coverage_at is not None

        # Score final basé sur TOUS les trades clôturés jamais archivés pour ce
        # wallet (cumulatif), pas seulement ceux de ce lot -- la note s'affine au
        # fil des passages plutôt que de repartir de zéro à chaque appel.
        cumulative_trades = await wallet_scan_state.list_archived_trades(wallet)
        card.closed_trades_count = len(cumulative_trades)

        dex_exclusions = _build_dex_infrastructure_exclusions(grouped, wallet) | multi.resolved_pool_addresses
        disq = await _hard_disqualifiers(
            wallet, primary_info or AddressInfo(address=wallet, available=False), all_flat_transfers, funding_source,
            extra_exclusions=dex_exclusions, goplus_client=goplus, funding_source_chain=funding_source_chain,
        )
        card.disqualified = disq.disqualified
        card.disqualification_reasons = disq.reasons
        card.financing_check_note = disq.financing_check_note

        if cumulative_trades:
            wins = sum(1 for t in cumulative_trades if t.pnl_usd > 0)
            card.win_rate = wins / len(cumulative_trades)
            card.realized_pnl_usd = sum(t.pnl_usd for t in cumulative_trades)
            card.max_drawdown_pct = _max_drawdown_pct(cumulative_trades)
            returns = [r for t in cumulative_trades if (r := t.return_pct) is not None]
            card.sortino = _sortino_ratio(returns)
            card.sortino_pnl_contradiction = (
                card.sortino is not None and card.sortino > 0 and card.realized_pnl_usd < 0
            )
            card.avg_holding_period_days = _avg_holding_period_days(cumulative_trades)
            card.win_rate_recent, card.realized_pnl_usd_recent, card.recent_window_trades_count = (
                _recent_window_metrics(cumulative_trades, window_days=WEIGHTS.recent_window_days)
            )

            by_token: dict[str, float] = {}
            capital_by_token: dict[str, float] = {}
            for t in cumulative_trades:
                by_token[t.token_address] = by_token.get(t.token_address, 0.0) + t.pnl_usd
                capital_by_token[t.token_address] = (
                    capital_by_token.get(t.token_address, 0.0) + t.token_amount * t.buy_price
                )
            card.diversification_total_tokens = len(by_token)
            card.diversification_profitable_tokens = sum(1 for v in by_token.values() if v > 0)

            # Diversification pondérée par capital (15/07, revue ChatGPT) : le
            # ratio de comptage ci-dessus traite un pari de 5$ comme un pari de
            # 50 000$ -- un wallet peut gonfler artificiellement sa
            # diversification "comptée" via 200 positions minuscules pendant
            # qu'un seul gros pari domine réellement son capital. Complète
            # (remplace pas) le ratio de comptage -- les deux mesurent des
            # choses différentes (largeur des paris indépendants vs.
            # concentration réelle du capital), même doctrine "axes séparés,
            # jamais fondus" que le reste de ce module.
            total_capital = sum(capital_by_token.values())
            card.diversification_capital_weighted_ratio = (
                round(
                    sum(v for addr, v in capital_by_token.items() if by_token.get(addr, 0.0) > 0) / total_capital, 4,
                )
                if total_capital > 0
                else None
            )

            card.robust_pnl_positive = _robust_pnl_check(
                cumulative_trades,
                trim_pct=WEIGHTS.robust_trim_pct,
                min_required=WEIGHTS.robust_trim_min_closed_trades,
            )

            # Confiance du cost-basis (15/07, revue Gemini) : part des JAMBES
            # (achat + vente comptées séparément) valorisées par un prix
            # d'exécution exact plutôt que par le repli marché OHLCV.
            exact_legs = sum(
                (1 if t.buy_price_exact else 0) + (1 if t.sell_price_exact else 0) for t in cumulative_trades
            )
            total_legs = len(cumulative_trades) * 2
            card.price_confirmation_ratio = round(exact_legs / total_legs, 4) if total_legs else None
            card.price_confidence_low = (
                card.price_confirmation_ratio is not None
                and card.price_confirmation_ratio < WEIGHTS.min_price_confirmation_ratio
            )
            card.health_trend = _health_trend(
                cumulative_trades,
                min_required=WEIGHTS.health_trend_min_closed_trades,
                stable_band_pct=WEIGHTS.health_trend_stable_band_pct,
            )

        # Échantillon minimum (15/07, décision opérateur) : sur `all_flat_transfers`
        # (l'historique brut, pas seulement les trades clôturés) -- un wallet peut
        # être "jeune" ou "peu actif" indépendamment d'avoir des trades clôturés.
        card.wallet_age_days = _wallet_age_days(all_flat_transfers)
        card.total_swaps = _count_total_swaps(all_flat_transfers, wallet)
        card.sample_size_sufficient = (
            card.wallet_age_days is not None and card.wallet_age_days >= WEIGHTS.min_wallet_age_days
            and card.total_swaps >= WEIGHTS.min_total_swaps
        )

        card.early_entry_recurrence_count = len(multi.early_entry_tokens)
        card.informed_entry_count = len(multi.informed_entry_tokens)
        card.suspect_positive = _suspect_positive_flag(card)

        await _apply_comparative_ranking(card)

        cards.append(card)

    convergence_pairs = _pairwise_convergence(addresses, funding_sources)

    synthesis = None
    if any(c.available for c in cards):
        synthesis = await _generate_thesis(cards, convergence_pairs, llm=llm)

    for card in cards:
        try:
            await _log_wallet_score(card.address, json.dumps(asdict(card), default=str))
        except Exception:  # noqa: BLE001 -- le log ne doit jamais casser le scoring
            logger.warning("score_wallets: échec écriture wallet_score_log pour %s", card.address)

    return WalletScoringReport(
        wallets=cards, convergence_pairs=convergence_pairs, synthesis=synthesis, available=True, error=None,
    )
