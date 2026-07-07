"""Filtre de sécurité (« screen ») — le gardien du pool de contrats entraînables.

Transforme le résultat riche d'un scan (`TokenScanContext`) en un **verdict binaire**
« passe / ne passe pas », avec ses raisons factuelles. C'est la porte d'entrée du
pool de tokens dans lequel la boucle d'entraînement tire ses 20 candidats au sort.

## Honnêteté (extension du dôme)

- On ne dit JAMAIS « 100 % fiable ». Passer le filtre = **« aucun marqueur de scam
  détecté + liquidité suffisante + verdict de scan SAFE »**, pas une garantie. Un
  contrat propre techniquement peut rug plus tard (équipe, off-chain) : indétectable
  on-chain, on ne le prétend pas.
- Le filtre s'appuie sur le scoring EXISTANT du scan (`_score_and_verdict`), qui pénalise
  déjà honeypot / mint / concentration / liquidité faible via le `security_score`. Ici
  on ne fait qu'imposer un **seuil strict** par-dessus (défense en profondeur).
- Déterministe : mêmes faits de scan → même verdict de filtre.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aria_core.skills.acp_onchain_scan import TokenScanContext

# Seuils du pool entraînable (stricts par défaut : on veut du VRAIMENT tradeable,
# pas juste « pas un scam »). Ajustables par appel.
DEFAULT_MIN_LIQUIDITY_USD = 30_000.0
DEFAULT_MIN_SCORE = 70
# Au-delà, un seul wallet (hors LP/burn) peut faire s'effondrer le token en vendant.
DEFAULT_MAX_TOP_HOLDER_PCT = 30.0


@dataclass(frozen=True)
class ScreenResult:
    """Verdict du filtre de sécurité pour un contrat, avec ses raisons factuelles."""

    contract: str
    passed: bool
    security_score: int
    liquidity_usd: float
    verdict: str  # lite_verdict du scan : SAFE / CAUTION / DANGER
    reasons: list[str] = field(default_factory=list)


def safety_screen(
    ctx: TokenScanContext,
    *,
    min_liquidity_usd: float = DEFAULT_MIN_LIQUIDITY_USD,
    min_score: int = DEFAULT_MIN_SCORE,
    max_top_holder_pct: float = DEFAULT_MAX_TOP_HOLDER_PCT,
    require_verified: bool = True,
) -> ScreenResult:
    """Décide si un contrat entre dans le pool « screené ».

    Barrières PRIORITAIRES (le dev garde-t-il un pouvoir de nuisance ?), avant même
    de regarder le marché :
      - contrat **vérifié** (code public, sinon opaque) ;
      - pas de **mint** / **blacklist** / **désactivation des transferts** (leviers de
        rug/honeypot que le dev conserverait) ;
      - **concentration** : aucun wallet (hors LP/burn) au-dessus du seuil (sinon une
        seule baleine peut dumper).
    Puis les barrières de marché : adresse valide, paire DEX, liquidité, score, verdict
    SAFE. Passe seulement si TOUT est réuni. ``reasons`` liste chaque blocage
    (jamais un rejet opaque). Une donnée de sécurité **inconnue** bloque aussi : pour
    un pool d'entraînement, on n'inclut que ce qu'on peut confirmer (fail-closed).
    """
    liq = ctx.best_pair.liquidity_usd if ctx.best_pair else 0.0
    reasons: list[str] = []

    # Barrières marché
    if not ctx.valid_address:
        reasons.append("adresse de contrat invalide")
    if ctx.best_pair is None:
        reasons.append("aucune paire DEX trouvée (illiquide ou inexistant)")
    if ctx.best_pair is not None and liq < min_liquidity_usd:
        reasons.append(f"liquidité ${liq:,.0f} < minimum ${min_liquidity_usd:,.0f}")
    if ctx.security_score < min_score:
        reasons.append(f"score de sécurité {ctx.security_score} < {min_score}")
    if ctx.lite_verdict != "SAFE":
        reasons.append(f"verdict de scan '{ctx.lite_verdict}' (SAFE requis)")

    # Barrières « le dev garde le pouvoir » (prioritaires)
    if require_verified and ctx.contract_verified is not True:
        reasons.append("contrat non vérifié (code opaque)")
    if ctx.has_mint is True:
        reasons.append("fonction mint présente (le dev peut créer des tokens)")
    if ctx.has_blacklist is True:
        reasons.append("fonction blacklist présente (le dev peut bloquer des ventes)")
    if ctx.has_disable_transfers is True:
        reasons.append("désactivation des transferts possible (levier honeypot)")

    # Barrière concentration (baleine)
    if ctx.top_holder_pct is None:
        reasons.append("distribution des holders inconnue (non confirmable)")
    elif ctx.top_holder_pct > max_top_holder_pct:
        reasons.append(
            f"holder dominant {ctx.top_holder_pct:.0f}% > {max_top_holder_pct:.0f}% (risque de dump)"
        )

    passed = (
        ctx.valid_address
        and ctx.best_pair is not None
        and liq >= min_liquidity_usd
        and ctx.security_score >= min_score
        and ctx.lite_verdict == "SAFE"
        and (ctx.contract_verified is True or not require_verified)
        and ctx.has_mint is not True
        and ctx.has_blacklist is not True
        and ctx.has_disable_transfers is not True
        and ctx.top_holder_pct is not None
        and ctx.top_holder_pct <= max_top_holder_pct
    )
    if passed:
        reasons = [
            f"screené : score {ctx.security_score}/95, liquidité ${liq:,.0f}, "
            f"vérifié, holder max {ctx.top_holder_pct:.0f}%, verdict SAFE"
        ]

    return ScreenResult(
        contract=ctx.contract,
        passed=passed,
        security_score=ctx.security_score,
        liquidity_usd=liq,
        verdict=ctx.lite_verdict,
        reasons=reasons,
    )
