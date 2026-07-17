"""Gestion de risque du portefeuille papier (#187) -- surveillance continue des positions
OUVERTES + plafond de concentration par catégorie.

Module séparé de ``paper_trader.py`` (au lieu d'y être ajouté en ligne) pour limiter la
surface de collision avec le travail parallèle sur ce fichier (#186) -- ``paper_trader.py``
n'y gagne que 2 colonnes DB additives (``category``, ``entry_security_json``) et 2 kwargs
optionnels sur ``open_position``, tout le reste vit ici.

Deux mécanismes, tous deux appelés depuis ``paper_trader.run_paper_cycle`` (aucune nouvelle
cadence heartbeat -- réutilise le cycle ``paper_trade_cycle`` existant, 180 min) :

1. SURVEILLANCE CONTINUE -- ``rescan_open_position`` compare l'état de sécurité ACTUEL
   d'une position (GoPlus honeypot/taxes + Blockscout vérification/ownership) contre
   l'instantané capturé À L'ENTRÉE (``capture_entry_snapshot``, réutilise les champs déjà
   calculés par ``scan_base_token`` -- aucun appel GoPlus/Blockscout dupliqué à l'entrée,
   seul ``read_owner`` est un appel nouveau car ``TokenScanContext`` n'a pas d'adresse
   owner). Ne ferme JAMAIS la position elle-même : renvoie un diagnostic, c'est l'appelant
   (``paper_trader.py``, seul détenteur de ``close_position``) qui décide.

   ⚠️ DOCTRINE CAPITAL RÉEL (wallet_guard.py) : en paper-trading, fermer automatiquement
   sur signal dur est sans risque -- ça teste la RÉACTION. Avec du capital RÉEL, ce
   mécanisme ne devrait JAMAIS déclencher une vente automatique : seulement une ALERTE
   Telegram avec confirmation opérateur obligatoire, exactement comme ``wallet_guard``
   l'impose déjà pour toute dépense ACP (``escalate_spend`` ne fait qu'alerter, seul un
   clic Telegram réel déclenche ``resolve_spend``). Si ce module est un jour branché sur
   un portefeuille réel, la fermeture automatique de ``run_paper_cycle`` doit être
   remplacée par le même patron d'escalade.

2. PLAFOND DE CONCENTRATION -- jamais plus de ``CONCENTRATION_CAP_PCT`` du capital de
   poche (``STARTING_CAPITAL_USD``, l'enveloppe fixe de la preuve, pas le sous-ensemble
   actuellement déployé -- une caisse à moitié vide ne doit pas se lire comme
   "diversifiée" juste parce qu'elle n'a que 2 positions du même type) concentré sur une
   seule catégorie ouverte simultanément. Une nouvelle entrée qui dépasserait le plafond
   est RÉDUITE en taille pour tenir exactement dessous ; si la place restante est trop
   faible pour une position significative (< 20 % de l'allocation normale), la position
   est SKIPPÉE plutôt qu'ouverte en position poussière.

   Catégorie = ``launchpad`` (déjà résolu par ``scan_base_token`` -- champ plus fin que
   ``network``, qui n'existe pas sur ``TokenScanContext``/ne varie pas dans ce portefeuille
   Base-only) suffixé ``-bonding`` si ``bonding_phase`` -- ex. ``virtuals_bonding``,
   ``virtuals_bonding-bonding``, ``clanker``, ``unknown``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields

logger = logging.getLogger(__name__)

# ── Plafond de concentration ──────────────────────────────────────────────────────────

CONCENTRATION_CAP_PCT = 0.40
# Sous ce seuil (fraction de l'allocation NORMALE d'une position), on skip plutôt que
# d'ouvrir une position poussière qui encombre le portefeuille pour un montant dérisoire.
MIN_CONCENTRATION_ALLOC_FRACTION = 0.2


def derive_category(launchpad: str | None, *, bonding_phase: bool = False) -> str:
    base = (launchpad or "unknown").strip() or "unknown"
    return f"{base}-bonding" if bonding_phase else base


def category_exposure_usd(category: str, open_positions: list[dict]) -> float:
    if not category:
        return 0.0
    return sum(
        float(p.get("cost_usd") or 0.0)
        for p in open_positions
        if (p.get("category") or "") == category
    )


def fit_alloc_to_concentration_cap(
    *,
    category: str,
    alloc: float,
    already_deployed_usd: float,
    starting_capital: float,
    min_alloc: float,
) -> float:
    """Renvoie l'allocation ajustée pour respecter ``CONCENTRATION_CAP_PCT`` de
    ``starting_capital`` sur ``category``, ou 0.0 si la place restante est trop faible
    (< ``min_alloc``) pour valoir la peine d'ouvrir une position."""
    if not category or starting_capital <= 0 or alloc <= 0:
        return alloc
    cap_usd = CONCENTRATION_CAP_PCT * starting_capital
    room = cap_usd - already_deployed_usd
    if room <= 0:
        return 0.0
    fitted = min(alloc, room)
    return fitted if fitted >= min_alloc else 0.0


# ── Instantané de sécurité à l'entrée + re-scan continu ───────────────────────────────

_RENOUNCED_OWNER_MARKERS = (
    "0x" + "0" * 40,
    "0x000000000000000000000000000000000000dead",
)


@dataclass
class EntrySecuritySnapshot:
    """État de sécurité au moment de l'OUVERTURE d'une position -- la référence contre
    laquelle ``rescan_open_position`` détecte un signal NOUVEAU (apparu après l'entrée),
    jamais un état absolu (un token peut légitimement avoir des taxes élevées dès le
    départ -- c'est le CHANGEMENT après ouverture qui est le signal dur)."""

    is_honeypot: bool | None = None
    cannot_sell: bool | None = None
    hidden_owner: bool | None = None
    can_take_back_ownership: bool | None = None
    contract_verified: bool | None = None
    owner_address: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | None) -> "EntrySecuritySnapshot | None":
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


async def capture_entry_snapshot(contract: str, ctx) -> EntrySecuritySnapshot:
    """Réutilise les champs déjà calculés par ``scan_base_token`` sur ``ctx`` (aucun appel
    GoPlus/Blockscout dupliqué) ; seul ``read_owner`` est un appel réseau nouveau, car
    ``TokenScanContext`` n'a pas d'adresse owner."""
    from aria_core.services.blockscout import blockscout_client

    owner, _err = await blockscout_client.read_owner(contract)
    return EntrySecuritySnapshot(
        is_honeypot=getattr(ctx, "is_honeypot", None),
        cannot_sell=getattr(ctx, "cannot_sell", None),
        hidden_owner=getattr(ctx, "hidden_owner", None),
        can_take_back_ownership=getattr(ctx, "can_take_back_ownership", None),
        contract_verified=getattr(ctx, "contract_verified", None),
        owner_address=owner,
    )


async def rescan_open_position(position: dict, *, pair=None) -> dict | None:
    """Re-vérifie une position OUVERTE contre son instantané d'entrée. Renvoie
    ``{"contract": ..., "reasons": [...]}`` si un signal dur NOUVEAU est détecté, sinon
    ``None``. Positions ouvertes avant ce mécanisme (pas d'``entry_security_json``) :
    aucune référence à comparer -- on ne réinvente pas une base, on saute silencieusement
    (dégradation honnête, jamais un signal fabriqué).

    ``pair`` (17/07, angle mort trouvé le même soir) : ``PairSnapshot`` DexScreener déjà
    récupéré par l'appelant (``paper_trader.py``, qui le récupère de toute façon pour
    connaître le prix courant -- jamais un second appel réseau dupliqué). ``None`` par
    défaut -- le check ratio volume/liquidité est alors simplement SAUTÉ (même doctrine
    de dégradation honnête que le reste de cette fonction), jamais un appel réseau
    autonome déclenché depuis ici. Sans ce check, un token pouvait entrer proprement
    (ratio sain à l'ouverture, cf. ``momentum_entry.py``) puis dériver vers un pool
    manipulé PENDANT la détention sans jamais être re-contrôlé -- le stop suiveur
    suivrait alors un prix de wash-trading en toute confiance."""
    snapshot = EntrySecuritySnapshot.from_json(position.get("entry_security_json"))

    contract = position["contract"]
    reasons: list[str] = []

    if pair is not None and pair.liquidity_usd and pair.liquidity_usd > 0:
        from aria_core.momentum_entry import MAX_VOLUME_TO_LIQUIDITY_RATIO

        volume_to_liq = (pair.volume_24h_usd or 0.0) / pair.liquidity_usd
        if volume_to_liq > MAX_VOLUME_TO_LIQUIDITY_RATIO:
            reasons.append(
                f"ratio volume 24h/liquidité extrême détecté en cours de détention "
                f"({volume_to_liq:.0f}x > {MAX_VOLUME_TO_LIQUIDITY_RATIO:.0f}x) -- "
                f"signal de wash-trading, absent ou non détecté à l'entrée"
            )

    if snapshot is None:
        return {"contract": contract, "reasons": reasons} if reasons else None

    from aria_core.services.goplus import goplus_client

    try:
        security = await goplus_client.get_token_security(contract)
    except Exception as exc:  # noqa: BLE001 — une panne GoPlus ne doit jamais planter le cycle
        logger.info("rescan_open_position: GoPlus %s échoué (%s)", contract, exc)
        security = None

    if security is not None and security.available:
        if security.is_honeypot and not snapshot.is_honeypot:
            reasons.append("honeypot détecté (absent à l'entrée)")
        if security.cannot_sell_all and not snapshot.cannot_sell:
            reasons.append("revente totale bloquée détectée (absente à l'entrée)")
        if security.hidden_owner and not snapshot.hidden_owner:
            reasons.append("owner caché détecté (absent à l'entrée)")
        if security.can_take_back_ownership and not snapshot.can_take_back_ownership:
            reasons.append("reprise de propriété possible détectée (absente à l'entrée)")

    from aria_core.services.blockscout import blockscout_client

    try:
        flags = await blockscout_client.check_contract_flags(contract)
    except Exception as exc:  # noqa: BLE001
        logger.info("rescan_open_position: Blockscout flags %s échoué (%s)", contract, exc)
        flags = None
    if flags is not None and flags.available and flags.is_verified is False and snapshot.contract_verified:
        reasons.append("contrat n'est plus vérifié (l'était à l'entrée)")

    try:
        owner_now, owner_err = await blockscout_client.read_owner(contract)
    except Exception as exc:  # noqa: BLE001
        logger.info("rescan_open_position: Blockscout read_owner %s échoué (%s)", contract, exc)
        owner_now, owner_err = None, str(exc)
    if owner_err is None and owner_now:
        was_renounced = (
            not snapshot.owner_address
            or snapshot.owner_address.lower() in _RENOUNCED_OWNER_MARKERS
        )
        if was_renounced and owner_now.lower() not in _RENOUNCED_OWNER_MARKERS:
            reasons.append(f"ownership repris par {owner_now} (renoncée ou inconnue à l'entrée)")

    if not reasons:
        return None
    return {"contract": contract, "reasons": reasons}


# ── Dépeg USDC -- bloque les NOUVELLES entrées, jamais les positions déjà ouvertes ────

USDC_DEPEG_THRESHOLD_PCT = 0.01  # 1 % d'écart au peg $1
USDC_COINGECKO_ID = "usd-coin"


async def usdc_depeg_pct() -> float | None:
    """Écart absolu au peg $1, ou ``None`` si le prix est indisponible -- fail-open : une
    panne CoinGecko ne bloque jamais le cycle (doctrine dôme), voir ``is_usdc_depegged``."""
    from aria_core.services.coingecko import coingecko_client

    try:
        result = await coingecko_client.get_simple_price([USDC_COINGECKO_ID], vs_currencies=["usd"])
    except Exception as exc:  # noqa: BLE001
        logger.info("usdc_depeg_pct: CoinGecko échoué (%s)", exc)
        return None
    if not result.available:
        return None
    price = result.prices.get(USDC_COINGECKO_ID, {}).get("usd")
    if not price or price <= 0:
        return None
    return abs(price - 1.0)


async def is_usdc_depegged() -> bool:
    pct = await usdc_depeg_pct()
    return pct is not None and pct > USDC_DEPEG_THRESHOLD_PCT
