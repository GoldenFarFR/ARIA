"""Filtre de sécurité pour la niche 15% — tokens ENCORE en courbe de bonding.

Le pendant de ``safety_screen.py`` pour les candidats pré-graduation. Le filtre
standard exige TOUJOURS une paire DEX (``ctx.best_pair is not None``) : un token en
bonding, sans liquidité DEX **par construction** (la liquidité vit dans la courbe, pas
un pool Uniswap, jusqu'à la graduation), échouerait ce filtre à tort — pas un rejet
légitime, un faux négatif garanti.

Ce module réutilise EXACTEMENT le scan existant (``acp_onchain_scan.scan_base_token``,
qui résout déjà ``ctx.bonding_phase`` / ``ctx.bonding_progress`` / ``ctx.mint_authority``
/ ``ctx.dev_signal`` — cf. tâche #10 livrée le 09/07) et n'ajoute qu'un SEUIL adapté,
sans jamais exiger de liquidité DEX ni de honeypot GoPlus (signaux qui n'existent pas
avant graduation).

## Barrières (mêmes principes que ``safety_screen`` : « le dev garde-t-il le pouvoir ? »)

- **confirmées** (rejet définitif, ``hard_fail=True``) : mint aux mains d'un wallet de
  dev (``eoa``), blacklist, désactivation des transferts, verdict de scan ``DANGER``,
  comportement du dev jugé ``concern``.
- **données indisponibles** (échec mou, ``hard_fail=False`` — retry plus tard) :
  contrat non encore vérifié/inconnu, autorité du mint indéterminable (``unknown``),
  verdict ``CAUTION`` (progression de graduation encore faible ou peu de holders —
  pas un signal négatif confirmé, juste pas encore assez mûr).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aria_core.skills.acp_onchain_scan import TokenScanContext

# Mêmes autorités neutralisées que safety_screen.py (mint piloté par le protocole,
# renoncé, ou un contrat verrouillé/multisig — jamais un simple wallet de dev).
_MINT_AUTHORITY_OK = frozenset({"renounced", "launchpad", "contract"})

DEFAULT_MIN_SCORE = 55  # sous le seuil SAFE (70) du scan mais au-dessus de CAUTION pur


@dataclass(frozen=True)
class BondingScreenResult:
    """Verdict du filtre bonding pour un candidat, avec ses raisons factuelles."""

    contract: str
    passed: bool
    security_score: int
    verdict: str  # lite_verdict du scan : SAFE / CAUTION / DANGER
    bonding_progress: float | None
    reasons: list[str] = field(default_factory=list)
    hard_fail: bool = False


def bonding_safety_screen(
    ctx: TokenScanContext, *, min_score: int = DEFAULT_MIN_SCORE
) -> BondingScreenResult:
    """Décide si un candidat en bonding entre dans la niche 15% (``base-bonding``).

    Suppose que ``ctx`` vient de ``scan_base_token(contract, include_dev_behavior=True)``
    sur un contrat SANS paire DEX (sinon ``ctx.bonding_phase`` reste ``False`` et ce
    filtre rejette — utiliser ``safety_screen`` standard dans ce cas).
    """
    reasons: list[str] = []
    hard_reasons: list[str] = []
    soft_reasons: list[str] = []

    if not ctx.valid_address:
        hard_reasons.append("adresse de contrat invalide")
    if not ctx.bonding_phase:
        # Pas applicable : soit gradué (a une paire DEX -> safety_screen standard),
        # soit statut Virtuals non résolu (indisponibilité best-effort -> retry).
        soft_reasons.append("statut bonding non confirmé (ctx.bonding_phase=False)")

    if ctx.contract_verified is None:
        soft_reasons.append("vérification du contrat indisponible")
    elif ctx.contract_verified is False:
        hard_reasons.append("contrat non vérifié (code opaque)")

    mint_authority = ctx.mint_authority or "unknown"
    if ctx.has_mint is True:
        if mint_authority == "unknown":
            soft_reasons.append("autorité du mint indéterminable")
        elif mint_authority not in _MINT_AUTHORITY_OK:
            detail = ctx.mint_authority_detail or "le dev peut créer des tokens"
            hard_reasons.append(f"fonction mint contrôlée par un dev ({detail})")

    if ctx.has_blacklist is True:
        hard_reasons.append("fonction blacklist présente (le dev peut bloquer des ventes)")
    if ctx.has_disable_transfers is True:
        hard_reasons.append("désactivation des transferts possible (levier honeypot)")

    if ctx.lite_verdict == "DANGER":
        hard_reasons.append(f"verdict de scan 'DANGER' (score {ctx.security_score})")
    elif ctx.lite_verdict == "CAUTION":
        soft_reasons.append(f"verdict de scan 'CAUTION' (score {ctx.security_score} < {min_score})")
    elif ctx.security_score < min_score:
        soft_reasons.append(f"score de sécurité {ctx.security_score} < {min_score}")

    if ctx.dev_signal == "concern":
        hard_reasons.append("comportement du dev jugé 'concern' (cf. dev_wallet)")
    elif ctx.dev_signal in (None, "unknown"):
        soft_reasons.append("comportement du dev non résolu")

    reasons = [*hard_reasons, *soft_reasons]
    passed = not hard_reasons and not soft_reasons

    return BondingScreenResult(
        contract=ctx.contract,
        passed=passed,
        security_score=ctx.security_score,
        verdict=ctx.lite_verdict,
        bonding_progress=ctx.bonding_progress,
        reasons=reasons,
        hard_fail=bool(hard_reasons),
    )
