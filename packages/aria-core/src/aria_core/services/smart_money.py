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
from datetime import datetime, timezone
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


def _fifo_match(
    token_address: str,
    buys: list[tuple[datetime, float]],
    sells: list[tuple[datetime, float]],
    price_lookup,
) -> _TokenFIFOResult:
    """FIFO strict : chaque vente consomme les achats les plus anciens en premier.
    ``price_lookup(ts) -> float | None`` -- une jambe sans prix disponible des DEUX
    côtés (achat ET vente) est comptée dans ``unpriced_legs``, jamais valorisée à
    zéro ni ignorée silencieusement (doctrine facts-only)."""
    buy_queue: deque[list] = deque(sorted(([ts, amt] for ts, amt in buys), key=lambda b: b[0]))
    closed: list[ClosedTrade] = []
    unpriced = 0

    for sell_ts, sell_amount in sorted(sells, key=lambda s: s[0]):
        remaining = sell_amount
        while remaining > 1e-12 and buy_queue:
            buy_ts, buy_amount = buy_queue[0]
            matched = min(remaining, buy_amount)
            buy_price = price_lookup(buy_ts)
            sell_price = price_lookup(sell_ts)
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

    open_amount = sum(amt for _, amt in buy_queue)
    return _TokenFIFOResult(
        token_address=token_address, closed_trades=closed, unpriced_legs=unpriced, open_position_amount=open_amount,
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
    grouped: dict[str, list[TokenTransfer]], *, cap: int = WEIGHTS.max_tokens_analyzed,
) -> tuple[list[str], int, int]:
    """Trie par (récence du dernier transfert, nombre de trades) décroissant --
    plafonne à ``cap`` tokens analysés en profondeur (décision opérateur, #157).
    Renvoie (adresses sélectionnées, nb total de tokens distincts trouvés, nb
    ignorés par le plafond) -- l'appelant DOIT logger explicitement si le 3e
    élément est > 0, jamais une troncature silencieuse."""

    def _sort_key(item: tuple[str, list[TokenTransfer]]):
        _addr, token_transfers = item
        timestamps = [ts for t in token_transfers if (ts := _parse_timestamp(t.timestamp)) is not None]
        latest = max(timestamps) if timestamps else _EPOCH_UTC
        return (latest, len(token_transfers))

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


async def _analyze_wallet_multi_token(
    wallet: str,
    transfers_by_token: dict[str, list[TokenTransfer]],
    *,
    gecko,
) -> _MultiTokenResult:
    """``transfers_by_token`` est keyé par une clé composite ``"{chaîne}:{adresse}"``
    (cf. ``_group_transfers_by_token``, #157 multi-chaînes 14/07) -- jamais
    l'adresse token seule, pour ne jamais fusionner par erreur deux tokens
    d'adresses identiques sur deux chaînes différentes (espaces d'adresses
    indépendants par construction EVM)."""
    from aria_core.services.coinmarketcap import CMC_NETWORK_SLUGS
    from aria_core.services.coinmarketcap import get_ohlcv as _cmc_get_ohlcv
    from aria_core.services.coinmarketcap import resolve_primary_pool as _cmc_resolve_primary_pool
    from aria_core.services.dexscreener import has_any_pair as _dexscreener_has_any_pair
    from aria_core.services.geckoterminal import GECKO_NETWORK_SLUGS

    wallet_l = wallet.lower()
    result = _MultiTokenResult()

    for composite_key, token_transfers in transfers_by_token.items():
        chain, _, token_addr = composite_key.partition(":")
        network = GECKO_NETWORK_SLUGS.get(chain, "base")

        buys = [
            (ts, t.amount)
            for t in token_transfers
            if (t.to_address or "").lower() == wallet_l and t.amount and (ts := _parse_timestamp(t.timestamp)) is not None
        ]
        sells = [
            (ts, t.amount)
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
        if pool_meta.available:
            # Adresse de pool NUE (jamais préfixée par la chaîne) : comparée
            # telle quelle à des adresses de contrepartie brutes dans
            # `_hard_disqualifiers`/`_dominant_counterparty_share` -- une
            # collision fortuite entre chaînes (espaces d'adresses EVM
            # indépendants, ~2^160) est négligeable, pas un vrai risque.
            result.resolved_pool_addresses.add(pool_meta.pool_address.lower())
            ohlcv = await gecko.get_ohlcv(pool_meta.pool_address, network=network)
        else:
            ohlcv = None
            # Triangulation (#157, 14/07) : GeckoTerminal n'a pas résolu de pool --
            # avant de conclure "token illiquide", on croise avec DexScreener.
            # `True` = écart réel entre les deux sources (DexScreener voit une
            # paire que GeckoTerminal rate -- signal à creuser, pas un défaut du
            # wallet) ; `False`/`None` (aucune paire confirmée, ou vérification
            # elle-même indisponible) n'ajoute rien de plus que ce que
            # `pool_lookup_errors` dit déjà.
            if await _dexscreener_has_any_pair(token_addr, chain=chain) is True:
                result.gecko_dexscreener_gap_tokens.append(token_addr)

            # 3e couche (#157, 14/07) : CoinMarketCap tente sa PROPRE résolution
            # de pool, INDÉPENDAMMENT du résultat DexScreener ci-dessus -- le
            # diagnostic "écart entre sources" et la tentative de pricing CMC ne
            # sont pas la même chose. Même quand DexScreener confirme une paire
            # (`True`), il ne fournit aucun prix historique (pas de méthode OHLCV
            # dans ce client) -- CMC est quand même tenté, sinon le token reste
            # non-valorisé alors qu'une paire est confirmée exister.
            cmc_network = CMC_NETWORK_SLUGS.get(chain, "base")
            cmc_pool = await _cmc_resolve_primary_pool(token_addr, network_slug=cmc_network)
            if cmc_pool.available:
                cmc_ohlcv = await _cmc_get_ohlcv(cmc_pool.pool_address, network_slug=cmc_network)
                if cmc_ohlcv.available and cmc_ohlcv.candles:
                    ohlcv = cmc_ohlcv
                    result.cmc_recovered_tokens.append(token_addr)

        if ohlcv is None or not ohlcv.available or not ohlcv.candles:
            result.unpriced_legs += len(buys) + len(sells)
        else:
            from aria_core.services.geckoterminal import price_at

            def _price_lookup(ts, _ohlcv=ohlcv):
                return price_at(_ohlcv, int(ts.timestamp()))

            fifo = _fifo_match(token_addr, buys, sells, _price_lookup)
            result.closed_trades.extend(fifo.closed_trades)
            result.unpriced_legs += fifo.unpriced_legs

        if pool_meta.available and pool_meta.created_at:
            earliest_buy_ts = min(ts for ts, _ in buys)
            elapsed = (earliest_buy_ts - pool_meta.created_at).total_seconds()
            amounts = [a for _, a in buys]
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

    closed_trades_count: int = 0
    unpriced_legs: int = 0
    pool_lookup_errors: int = 0  # tokens sans pool GeckoTerminal résolu (#157, 14/07 -- diagnostic)
    gecko_dexscreener_gap_count: int = 0  # parmi eux, DexScreener voit une paire que GeckoTerminal a ratée (#157, 14/07)
    cmc_price_recovery_count: int = 0  # parmi eux, valorisés via CoinMarketCap après échec GeckoTerminal (#157, 14/07)
    win_rate: float | None = None
    realized_pnl_usd: float | None = None
    sortino: float | None = None
    max_drawdown_pct: float | None = None

    diversification_profitable_tokens: int = 0
    diversification_total_tokens: int = 0

    early_entry_recurrence_count: int = 0
    informed_entry_count: int = 0

    funding_source: str | None = None
    funding_source_truncated: bool = False

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
    "déterministe (FIFO PnL, Sortino, drawdown, récurrence d'entrée précoce -- "
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
        f"Tokens tradés trouvés : {card.tokens_found} (analysés en profondeur : {card.tokens_analyzed}"
        + (f", plafond de {WEIGHTS.max_tokens_analyzed} atteint" if card.tokens_skipped_capped else "")
        + ")"
    )
    lines.append(
        f"Trades clôturés valorisés : {card.closed_trades_count} (jambes sans prix : {card.unpriced_legs}, "
        f"tokens sans pool GeckoTerminal résolu : {card.pool_lookup_errors})"
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
    lines.append(
        f"Max drawdown (wallet) : {card.max_drawdown_pct:.0%}"
        if card.max_drawdown_pct is not None
        else "Max drawdown : indisponible"
    )
    lines.append(
        f"Diversification : {card.diversification_profitable_tokens}/{card.diversification_total_tokens} tokens profitables"
    )
    lines.append(
        f"Récurrence acheteur précoce multi-lancements : {card.early_entry_recurrence_count} token(s) "
        f"(dont {card.informed_entry_count} avec conditions techniques jugées informées)"
    )
    lines.append(f"Suspect positif (multi-axes) : {'oui' if card.suspect_positive else 'non'}")
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

        for chain, chain_client in chain_clients.items():
            transfers_result = await chain_client.get_token_transfers(
                wallet, limit=2000, max_pages=10, token_type="ERC-20",
            )
            if not transfers_result.available:
                last_error = transfers_result.error or UNAVAILABLE
                continue
            any_chain_available = True
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

        cap = max_tokens if max_tokens is not None else WEIGHTS.max_tokens_analyzed
        selected_tokens, found, skipped = _select_tokens_for_deep_analysis(grouped, cap=cap)
        card.tokens_found = found
        card.tokens_analyzed = len(selected_tokens)
        card.tokens_skipped_capped = skipped > 0
        if card.tokens_skipped_capped:
            logger.info(
                "score_wallets: wallet %s -- plafond de %s tokens atteint (%s trouvés, %s ignorés, "
                "sélection par récence/nombre de trades, toutes chaînes confondues)",
                wallet, cap, found, skipped,
            )

        selected_transfers = {key: grouped[key] for key in selected_tokens}

        card.funding_source = funding_source
        card.funding_source_truncated = funding_truncated
        if funding_source:
            funding_sources[wallet.lower()] = funding_source

        # Analyse multi-token AVANT les disqualifiants durs : fournit les pools
        # réellement résolus par token, utilisés ci-dessous pour généraliser
        # l'exclusion wash-trading sans faux positif (#157, correction 14/07).
        multi = await _analyze_wallet_multi_token(wallet, selected_transfers, gecko=gecko)
        card.closed_trades_count = len(multi.closed_trades)
        card.unpriced_legs = multi.unpriced_legs
        card.pool_lookup_errors = multi.pool_lookup_errors
        card.gecko_dexscreener_gap_count = len(multi.gecko_dexscreener_gap_tokens)
        card.cmc_price_recovery_count = len(multi.cmc_recovered_tokens)

        dex_exclusions = _build_dex_infrastructure_exclusions(grouped, wallet) | multi.resolved_pool_addresses
        disq = await _hard_disqualifiers(
            wallet, primary_info or AddressInfo(address=wallet, available=False), all_flat_transfers, funding_source,
            extra_exclusions=dex_exclusions, goplus_client=goplus, funding_source_chain=funding_source_chain,
        )
        card.disqualified = disq.disqualified
        card.disqualification_reasons = disq.reasons
        card.financing_check_note = disq.financing_check_note

        if multi.closed_trades:
            wins = sum(1 for t in multi.closed_trades if t.pnl_usd > 0)
            card.win_rate = wins / len(multi.closed_trades)
            card.realized_pnl_usd = sum(t.pnl_usd for t in multi.closed_trades)
            card.max_drawdown_pct = _max_drawdown_pct(multi.closed_trades)
            returns = [r for t in multi.closed_trades if (r := t.return_pct) is not None]
            card.sortino = _sortino_ratio(returns)

            by_token: dict[str, float] = {}
            for t in multi.closed_trades:
                by_token[t.token_address] = by_token.get(t.token_address, 0.0) + t.pnl_usd
            card.diversification_total_tokens = len(by_token)
            card.diversification_profitable_tokens = sum(1 for v in by_token.values() if v > 0)

        card.early_entry_recurrence_count = len(multi.early_entry_tokens)
        card.informed_entry_count = len(multi.informed_entry_tokens)
        card.suspect_positive = _suspect_positive_flag(card)

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
