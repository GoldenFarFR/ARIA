"""Comportement du wallet du dev/équipe — builder engagé ou farmer ?

Le déployeur d'un token laisse une trace on-chain qui en dit long sur sa LÉGITIMITÉ.
Quatre dimensions (intuition opérateur), jugées AU CAS PAR CAS, jamais en binaire :

  1. **Détient-il ?** (`holds_pct`) — skin-in-the-game vs pression de vente. Zone
     saine relative : sur Virtuals la team ~15-20 % est normale ; un fondateur solo
     sans argent à 0 % n'est pas forcément de mauvaise foi ; mais >40 % hors norme =
     risque de dump.
  2. **A-t-il acheté avec son argent** (`acquired='bought'`) ou juste **auto-alloué**
     (`'allocation'`) ? Acheter = aligné (il a engagé du capital).
  3. **A-t-il vendu** (`sold_pct_of_received`) — pour **financer** le projet (petites
     tranches, sain) ou **extraire** (gros dump early, concern) ?
  4. **All-in ?** (achat + garde + peu/pas de vente) = conviction forte.

Le JUGE est PUR et déterministe ; il produit des OBSERVATIONS factuelles + un signal
pondéré (aligned / neutral / concern / unknown) qui NOURRIT le raisonnement d'ARIA —
il ne rejette pas d'office. La logique s'adapte à la taille du projet : une équipe
organisée qui n'engage rien est anormale ; un dev solo est excusé.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Repères RELATIFS (pas des couperets) : au-dessus, un examen s'impose.
_HIGH_HOLD_PCT = 40.0        # détention très élevée hors norme launchpad -> risque dump
_HEAVY_SELL_PCT = 50.0       # a vendu la majorité de ce qu'il a reçu -> extraction probable


@dataclass(frozen=True)
class DevWalletFacts:
    """Faits on-chain sur le wallet du déployeur (récoltés, jamais inventés)."""

    creator: str | None
    holds_pct: float | None = None            # % de supply détenu (hors LP/burn)
    acquired: str | None = None               # 'allocation' | 'bought' | 'mixed' | None
    sold_events: int = 0
    sold_pct_of_received: float | None = None  # part de ce qu'il a reçu qu'il a revendu
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DevWalletVerdict:
    """Jugement pondéré du comportement du dev, avec ses observations factuelles."""

    signal: str  # aligned / neutral / concern / unknown
    points: list[str] = field(default_factory=list)


def judge_dev_wallet(
    facts: DevWalletFacts,
    *,
    launchpad_team_norm: tuple[float, float] | None = None,
    team_is_large: bool | None = None,
) -> DevWalletVerdict:
    """Juge le comportement du dev au cas par cas. Retourne signal + observations.

    ``launchpad_team_norm`` : fourchette d'allocation team normale du launchpad (ex.
    (15, 20) pour Virtuals) — une détention DANS cette fourchette n'est pas un concern.
    ``team_is_large`` : indice de taille d'équipe (si connu) — une grande équipe qui
    n'engage rien est plus suspecte qu'un dev solo.
    """
    if not facts.available:
        return DevWalletVerdict(signal="unknown", points=[facts.error or "wallet du dev non analysable"])

    points: list[str] = []
    concern = 0
    aligned = 0

    hp = facts.holds_pct
    within_norm = (
        hp is not None and launchpad_team_norm is not None
        and launchpad_team_norm[0] <= hp <= launchpad_team_norm[1] * 1.25
    )

    if hp is None:
        points.append("détention du dev inconnue")
    elif hp == 0:
        points.append("le dev ne détient rien : peu de pression de vente, mais conviction à confirmer")
        if team_is_large:
            points.append("équipe apparemment organisée mais sans skin-in-the-game : incohérent")
            concern += 1
    elif within_norm:
        points.append(f"détient {hp:.1f}% (dans la norme du launchpad, aligné)")
        aligned += 1
    elif hp >= _HIGH_HOLD_PCT:
        points.append(f"détient {hp:.1f}% (très concentré : risque de dump)")
        concern += 1
    else:
        points.append(f"détient {hp:.1f}% (skin-in-the-game)")
        aligned += 1

    if facts.acquired == "bought":
        points.append("a ACHETÉ ses tokens avec son capital (aligné)")
        aligned += 1
    elif facts.acquired == "allocation":
        points.append("détention par auto-allocation (aucun capital engagé)")
    elif facts.acquired == "mixed":
        points.append("mélange d'allocation et d'achats")
        aligned += 1

    if facts.sold_pct_of_received is not None:
        sp = facts.sold_pct_of_received
        if sp >= _HEAVY_SELL_PCT:
            points.append(f"a revendu {sp:.0f}% de sa dotation (extraction probable)")
            concern += 2
        elif facts.sold_events >= 3 and sp < _HEAVY_SELL_PCT:
            points.append(f"ventes échelonnées ({facts.sold_events}x, {sp:.0f}%) : possible financement du dev")
        elif facts.sold_events == 1 and sp >= 25:
            points.append(f"un seul gros dégagement ({sp:.0f}%) : à surveiller")
            concern += 1
        elif sp == 0:
            points.append("n'a rien vendu (conviction)")
            aligned += 1

    # Signal pondéré (jamais un couperet — nourrit le jugement d'ARIA).
    if concern >= 2 and concern > aligned:
        signal = "concern"
    elif aligned >= 2 and concern == 0:
        signal = "aligned"
    elif concern > 0 and concern >= aligned:
        signal = "concern"
    else:
        signal = "neutral"
    return DevWalletVerdict(signal=signal, points=points)


_ZERO = "0x0000000000000000000000000000000000000000"

# 23/07 -- calibration en live (tâche #26) : contrat CNX (déjà scanné en prod)
# vérifié comme un pool Uniswap V4 (dexId "uniswap" sans plus de précision côté
# DexScreener). Sur V4, TOUS les swaps de TOUS les pools transitent par ce
# PoolManager SINGLETON, jamais par une adresse de pool dédiée comme sur V2/V3
# -- comparer `frm`/`to` au seul `lp_address` (l'identifiant logique de pool
# renvoyé par DexScreener) ne capture donc JAMAIS un achat/vente réel sur un
# pool V4, peu importe le token. Adresse officielle vérifiée (BaseScan, "Uniswap
# V4: Pool Manager") -- Base uniquement, cohérent avec le reste de ce module.
_UNISWAP_V4_POOL_MANAGER_BASE = "0x498581ff718922c3f8e6a244956af099b2652b2b"


async def gather_dev_wallet_facts(
    contract: str,
    creator: str | None,
    *,
    lp_address: str | None = None,
    client=None,
) -> DevWalletFacts:
    """Récolte best-effort les faits on-chain sur le déployeur (défensif, jamais bloquant).

    - **Détention** : part du déployeur dans la liste des holders du token.
    - **Achats / allocation / ventes** : classe les transferts du token impliquant le
      déployeur — reçu depuis le zéro/contrat = allocation ; reçu depuis le pool LP
      (ou le PoolManager Uniswap V4, cf. `_UNISWAP_V4_POOL_MANAGER_BASE`) = achat ;
      envoyé vers l'un des deux = vente.

    ``client`` injectable (défaut : blockscout_client) pour les tests offline. Toute
    indisponibilité -> ``available=False`` (le juge renverra 'unknown'). La classification
    reste une heuristique — calibrée en live le 23/07 contre un cas réel (CNX, pool V4)."""
    if not creator:
        return DevWalletFacts(creator=None, available=False, error="déployeur inconnu")
    if client is None:
        from aria_core.services.blockscout import blockscout_client as client

    dev = creator.lower()
    lp_candidates = {_UNISWAP_V4_POOL_MANAGER_BASE}
    if lp_address:
        lp_candidates.add(lp_address.lower())

    holds_pct: float | None = None
    try:
        holders = await client.get_token_holders(contract)
        if holders.available:
            for h in holders.holders:
                if (h.address or "").lower() == dev:
                    holds_pct = float(h.percentage) if h.percentage is not None else None
                    break
            else:
                holds_pct = 0.0  # absent des holders = ne détient pas
    except Exception as exc:  # noqa: BLE001
        return DevWalletFacts(creator=dev, available=False, error=f"holders indisponibles ({exc})")

    received = 0.0
    bought = 0.0
    sold = 0.0
    sold_events = 0
    try:
        transfers = await client.get_token_transfers(dev, limit=100)
        items = getattr(transfers, "transfers", None) or []
        for t in items:
            if (getattr(t, "token_address", "") or "").lower() != contract.lower():
                continue
            amt = getattr(t, "amount", None) or 0.0
            frm = (getattr(t, "from_address", "") or "").lower()
            to = (getattr(t, "to_address", "") or "").lower()
            if to == dev:  # le dev reçoit
                received += amt
                if frm in lp_candidates:
                    bought += amt
            elif frm == dev:  # le dev envoie
                if to in lp_candidates:
                    sold += amt
                    sold_events += 1
    except Exception:  # noqa: BLE001 — les transferts sont un bonus, pas bloquant
        pass

    if received > 0:
        if bought > 0 and bought < received:
            acquired = "mixed"
        elif bought > 0:
            acquired = "bought"
        else:
            acquired = "allocation"
    else:
        acquired = None

    sold_pct = round(100.0 * sold / received, 1) if received > 0 else None

    return DevWalletFacts(
        creator=dev,
        holds_pct=holds_pct,
        acquired=acquired,
        sold_events=sold_events,
        sold_pct_of_received=sold_pct,
        available=True,
    )
