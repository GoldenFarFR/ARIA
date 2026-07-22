"""Signal "sortie de liquidité déguisée" — wallets insiders hors du 'creator' labellisé.

`dev_wallet.py` ne surveille QUE le wallet déployeur explicitement identifié
(`creator_address`, via Blockscout) — un insider qui a reçu une part significative
de la distribution initiale (mint direct ou transfert du déployeur, jamais passé
par un DEX) peut revendre intégralement sa dotation sans qu'aucun signal existant
ne le capte, puisqu'il ne porte jamais l'étiquette "creator".

Repéré via `services/dune.py::get_insider_recipients` (table brute
`erc20_base.evt_transfer`, tous les transferts ERC-20 sur Base — contrairement à
`dex.trades` qui ne couvre que les trades DEX). Vérifié en conditions réelles
(22/07, contrat CNX) : le déployeur reçoit le mint initial depuis l'adresse zéro
puis distribue à plusieurs wallets tiers dans les heures/jours suivants —
exactement le pattern que ce module doit capter.

JUGE pur et déterministe, même doctrine que `dev_wallet.py` : produit un signal
pondéré (concern/neutral/unknown) qui NOURRIT le raisonnement d'ARIA — jamais un
rejet d'office (seuls honeypot/owner_change_balance restent des véto durs)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Un wallet doit avoir reçu au moins 1% de ce qu'a reçu le plus gros récipiendaire
# (typiquement le déployeur lui-même, qui porte le mint initial) pour être considéré
# comme une "allocation significative" — élimine le bruit des micro-transferts sans
# rapport avec une vraie distribution insider (poussière, remboursement de gas...).
_MIN_SHARE_OF_TOP_RECIPIENT = 0.01
# Détention actuelle en-dessous de ce seuil (%, même échelle que TokenHolder.percentage)
# = "n'a quasiment plus rien" — a revendu/transféré l'essentiel de sa dotation.
_NEAR_ZERO_HOLD_PCT = 0.05
# Nombre de wallets insiders examinés au maximum (le haut de la distribution, pas la
# longue traîne de destinataires anecdotiques).
_MAX_INSIDERS_EXAMINED = 10
# Fenêtre par défaut après la création de la paire — même horizon que le gate d'âge
# minimum du pipeline momentum (14 jours) : au-delà, une distribution devient une
# activité normale de marché, pas un signal de TGE.
_DEFAULT_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class InsiderWalletFacts:
    """Faits on-chain sur les destinataires directs de la distribution initiale
    (hors wallet 'creator', déjà couvert par dev_wallet.py)."""

    examined: int = 0
    flagged: list[str] = field(default_factory=list)  # adresses ayant quasi tout revendu
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class InsiderWalletVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_insider_wallets(facts: InsiderWalletFacts) -> InsiderWalletVerdict:
    """Jugement pondéré, jamais un couperet — même doctrine que judge_dev_wallet."""
    if not facts.available:
        return InsiderWalletVerdict(
            signal="unknown", points=[facts.error or "distribution initiale non analysable"],
        )
    if facts.examined == 0:
        return InsiderWalletVerdict(
            signal="neutral", points=["aucune distribution insider significative détectée (hors déployeur)"],
        )
    if not facts.flagged:
        return InsiderWalletVerdict(
            signal="neutral",
            points=[f"{facts.examined} wallet(s) ayant reçu une allocation directe, aucun n'a tout revendu"],
        )
    n = len(facts.flagged)
    return InsiderWalletVerdict(
        signal="concern",
        points=[
            f"{n}/{facts.examined} wallet(s) ayant reçu une allocation directe du déployeur/mint "
            "et ne détenant plus quasiment rien aujourd'hui (sortie possible, jamais visible sur "
            "le wallet 'creator' labellisé seul)"
        ],
    )


async def gather_insider_wallet_facts(
    contract: str,
    creator: str | None,
    *,
    pair_created_at_ms: int | None,
    lp_address: str | None = None,
    holders=None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    dune_module=None,
    client=None,
) -> InsiderWalletFacts:
    """Récolte best-effort les faits sur les wallets insiders (défensif, jamais bloquant).

    ``holders`` : `TokenHoldersResult` déjà en main (réutilisé — `scan_base_token`
    l'a déjà récupéré pour la concentration, aucun appel réseau supplémentaire).
    Si absent, indisponible ou vide → ``available=False`` (fail-closed sur inconnu,
    JAMAIS un "tout a été revendu" déduit d'une absence de donnée). ``dune_module``/
    ``client`` injectables pour les tests offline (défaut : `services.dune` /
    `blockscout_client`)."""
    if not creator:
        return InsiderWalletFacts(available=False, error="déployeur inconnu (fenêtre de distribution non bornable)")
    if pair_created_at_ms is None:
        return InsiderWalletFacts(available=False, error="date de création de la paire inconnue")

    if dune_module is None:
        from aria_core.services import dune as dune_module

    try:
        created = datetime.fromtimestamp(pair_created_at_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        return InsiderWalletFacts(available=False, error=f"horodatage de paire invalide ({exc})")

    window_start = (created - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (created + timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")

    result = await dune_module.get_insider_recipients(
        contract, creator, window_start=window_start, window_end=window_end,
        limit=_MAX_INSIDERS_EXAMINED + 5,
    )
    if not result.available:
        return InsiderWalletFacts(available=False, error=result.error)

    recipients = result.recipients
    if not recipients:
        return InsiderWalletFacts(examined=0, available=True)

    top_amount = recipients[0].total_received_raw
    if not top_amount or top_amount <= 0:
        return InsiderWalletFacts(examined=0, available=True)

    if holders is None:
        if client is None:
            from aria_core.services.blockscout import blockscout_client as client
        holders = await client.get_token_holders(contract)

    if holders is None or not getattr(holders, "available", False):
        return InsiderWalletFacts(
            available=False,
            error="holders actuels indisponibles : impossible de vérifier si la distribution initiale a été revendue",
        )

    current_pct: dict[str, float] = {}
    for h in holders.holders:
        addr = (h.address or "").lower()
        if addr:
            current_pct[addr] = float(h.percentage) if h.percentage is not None else 0.0

    dev = creator.lower()
    lp = (lp_address or "").lower()

    examined = 0
    flagged: list[str] = []
    for r in recipients[:_MAX_INSIDERS_EXAMINED]:
        addr = r.address.lower()
        if addr in (dev, lp, _ZERO_ADDRESS):
            continue  # déjà couvert par dev_wallet.py, ou pool LP / adresse zéro — pas un insider tiers
        if r.total_received_raw < top_amount * _MIN_SHARE_OF_TOP_RECIPIENT:
            continue  # allocation trop faible pour être significative
        examined += 1
        if current_pct.get(addr, 0.0) < _NEAR_ZERO_HOLD_PCT:
            flagged.append(r.address)

    return InsiderWalletFacts(examined=examined, flagged=flagged, available=True)
