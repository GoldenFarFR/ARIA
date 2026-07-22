"""Classification de l'autorité d'un contrat — un mint n'est dangereux que si un DEV le contrôle.

Un token peut exposer une fonction ``mint`` externe sans être un piège : tout dépend
de QUI peut l'appeler.
  - **renounced** : owner = adresse morte (0x0 / 0x…dead) -> personne ne peut minter -> sûr ;
  - **launchpad** : le token a été déployé par un launchpad connu (Virtuals, Flaunch,
    Clanker, Zora...) -> l'autorité appartient au PROTOCOLE, jamais renoncée mais
    légitime (c'est le fonctionnement normal du launchpad) ;
  - **contract** : owner = un contrat (timelock / multisig / contrat d'émission) ->
    pouvoir verrouillé/partagé, pas un wallet de dev unique -> acceptable ;
  - **eoa** : owner = un wallet externe (une personne) -> le dev peut diluer à volonté
    -> DANGER ;
  - **unknown** : impossible de déterminer -> on reste prudent (fail-closed en aval).

Ce module est PUR (aucun réseau) : il classe à partir de faits déjà récoltés
(déployeur, owner, owner-est-un-contrat) et du registre ``knowledge/launchpads.yaml``.
Déterministe : mêmes faits -> même verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_LAUNCHPADS_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "launchpads.yaml"

# Adresses « mortes » = renoncement effectif (personne ne détient la clé).
_DEAD_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000042069420694206942069",
}


def _is_dead_address(addr: str) -> bool:
    """Reconnaît une adresse morte (renoncement) : liste connue OU motif zéros+dead."""
    a = (addr or "").strip().lower()
    if a in _DEAD_ADDRESSES:
        return True
    body = a[2:] if a.startswith("0x") else a
    if len(body) != 40:
        return False
    if body.endswith("dead") and set(body[:-4]) <= {"0"}:
        return True
    if body.startswith("dead") and set(body[4:]) <= {"0"}:
        return True
    return False

# Verdicts d'autorité où le mint est considéré NEUTRALISÉ (non contrôlé par un dev).
# Public (pas de préfixe _) : source unique importée par safety_screen.py (crible dur)
# ET acp_onchain_scan.py (malus de score) — corrige #164, un mint timelocké/launchpad/
# renoncé ne doit jamais être pénalisé deux fois avec deux listes qui pourraient diverger.
SAFE_AUTHORITIES = frozenset({"renounced", "launchpad", "contract"})


@dataclass(frozen=True)
class AuthorityVerdict:
    """Qui contrôle le contrat (et donc le mint), avec sa base factuelle."""

    kind: str  # renounced / launchpad / contract / eoa / unknown / na
    launchpad: str | None = None  # label du launchpad si reconnu
    owner: str | None = None
    detail: str = ""

    @property
    def mint_neutralized(self) -> bool:
        """True si un éventuel mint n'est PAS aux mains d'un dev (donc acceptable).

        ``na`` (aucun mint externe) est trivialement neutralisé : rien à contrôler.
        """
        return self.kind == "na" or self.kind in SAFE_AUTHORITIES


@lru_cache(maxsize=1)
def _launchpad_index() -> dict[str, str]:
    """Table adresse_déployeur (minuscule) -> label du launchpad."""
    idx: dict[str, str] = {}
    if not _LAUNCHPADS_PATH.is_file():
        return idx
    try:
        cfg: dict[str, Any] = yaml.safe_load(_LAUNCHPADS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return idx
    for _key, block in (cfg.get("launchpads") or {}).items():
        label = str((block or {}).get("label") or _key)
        for addr in (block or {}).get("addresses", []) or []:
            a = str(addr).strip().lower()
            if a.startswith("0x") and len(a) == 42:
                idx[a] = label
    return idx


def match_launchpad(creator_address: str | None) -> str | None:
    """Label du launchpad si le déployeur est connu, sinon None."""
    if not creator_address:
        return None
    return _launchpad_index().get(str(creator_address).strip().lower())


@lru_cache(maxsize=1)
def _norms_by_label() -> dict[str, dict]:
    """Table label du launchpad -> ses normes de tokenomics (bonding, alloc, liq...)."""
    out: dict[str, dict] = {}
    if not _LAUNCHPADS_PATH.is_file():
        return out
    try:
        cfg: dict[str, Any] = yaml.safe_load(_LAUNCHPADS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return out
    for _key, block in (cfg.get("launchpads") or {}).items():
        label = str((block or {}).get("label") or _key)
        out[label] = dict((block or {}).get("norms") or {})
    return out


def launchpad_norms(label: str | None) -> dict:
    """Normes de tokenomics d'un launchpad (dict vide si inconnu)."""
    if not label:
        return {}
    return _norms_by_label().get(label, {})


def is_bonding_launchpad(label: str | None) -> bool:
    """True si le launchpad fonctionne par courbe de bonding (liquidité exponentielle)."""
    return bool(launchpad_norms(label).get("bonding_curve"))


def classify_authority(
    *,
    has_mint: bool | None,
    creator_address: str | None = None,
    owner_address: str | None = None,
    owner_is_contract: bool | None = None,
) -> AuthorityVerdict:
    """Classe l'autorité du contrat à partir des faits on-chain déjà récoltés.

    Ordre de priorité :
      1. pas de mint externe -> ``na`` (rien à neutraliser) ;
      2. déployé par un launchpad connu -> ``launchpad`` (le protocole détient l'autorité) ;
      3. owner = adresse morte -> ``renounced`` ;
      4. owner = un contrat -> ``contract`` (verrouillé/multisig/émission) ;
      5. owner = un wallet (EOA) -> ``eoa`` (dev peut diluer -> danger) ;
      6. indéterminable -> ``unknown``.
    """
    if not has_mint:
        return AuthorityVerdict(kind="na", detail="pas de fonction mint externe")

    launchpad = match_launchpad(creator_address)
    if launchpad:
        return AuthorityVerdict(
            kind="launchpad",
            launchpad=launchpad,
            detail=f"déployé par {launchpad} (autorité du protocole, mint légitime)",
        )

    owner = (owner_address or "").strip().lower() or None
    if owner and _is_dead_address(owner):
        return AuthorityVerdict(
            kind="renounced", owner=owner, detail="propriété renoncée (owner = adresse morte)"
        )
    if owner and owner_is_contract is True:
        return AuthorityVerdict(
            kind="contract",
            owner=owner,
            detail="owner = contrat (timelock/multisig/émission, pas un wallet de dev)",
        )
    if owner and owner_is_contract is False:
        return AuthorityVerdict(
            kind="eoa",
            owner=owner,
            detail="owner = wallet externe (le dev peut créer des tokens)",
        )
    return AuthorityVerdict(kind="unknown", owner=owner, detail="autorité du mint indéterminable")
