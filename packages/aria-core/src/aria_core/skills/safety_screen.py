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

# Autorités où un mint externe est légitime (non contrôlé par un wallet de dev).
_MINT_AUTHORITY_OK = frozenset({"renounced", "launchpad", "contract"})


def _mint_is_dev_controlled(ctx: TokenScanContext) -> bool:
    """True si un mint externe existe ET reste aux mains d'un dev (ou indéterminable).

    Neutralisé (retourne False) si l'autorité est renoncée, un launchpad connu, ou
    un contrat (timelock/multisig/émission). Fail-closed : un mint dont l'autorité
    n'a pas pu être résolue ('unknown' ou non renseignée) reste bloquant.
    """
    if ctx.has_mint is not True:
        return False
    return (ctx.mint_authority or "unknown") not in _MINT_AUTHORITY_OK

# Seuils du pool entraînable (stricts par défaut : on veut du VRAIMENT tradeable,
# pas juste « pas un scam »). Ajustables par appel.
DEFAULT_MIN_LIQUIDITY_USD = 30_000.0
DEFAULT_MIN_SCORE = 70
# Au-delà, un seul wallet (hors LP/burn) peut faire s'effondrer le token en vendant.
DEFAULT_MAX_TOP_HOLDER_PCT = 30.0
# Au-delà, la taxe de vente rend la sortie trop coûteuse pour un pool entraînable
# (souvent le signe d'un token extractif / semi-honeypot).
DEFAULT_MAX_SELL_TAX = 0.15


@dataclass(frozen=True)
class ScreenResult:
    """Verdict du filtre de sécurité pour un contrat, avec ses raisons factuelles."""

    contract: str
    passed: bool
    security_score: int
    liquidity_usd: float
    verdict: str  # lite_verdict du scan : SAFE / CAUTION / DANGER
    reasons: list[str] = field(default_factory=list)
    # True si l'échec vient d'un signal NÉGATIF CONFIRMÉ (mint dev, blacklist,
    # concentration prouvée, liquidité réelle trop basse...). False si l'échec
    # tient UNIQUEMENT à une donnée indisponible (429, timeout, holders non
    # renvoyés) : dans ce cas ce n'est pas un « rejet pour toujours » — à réessayer.
    hard_fail: bool = False


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
    # Mint : bloquant seulement si un DEV en garde le contrôle. Un mint renoncé,
    # piloté par un launchpad connu (Virtuals/Flaunch...) ou par un contrat
    # (timelock/multisig/émission) est légitime -> neutralisé (cf. mint_authority).
    mint_blocks = _mint_is_dev_controlled(ctx)
    if mint_blocks:
        detail = ctx.mint_authority_detail or "le dev peut créer des tokens"
        reasons.append(f"fonction mint contrôlée par un dev ({detail})")
    if ctx.has_blacklist is True:
        reasons.append("fonction blacklist présente (le dev peut bloquer des ventes)")
    if ctx.has_disable_transfers is True:
        reasons.append("désactivation des transferts possible (levier honeypot)")

    # Barrières honeypot dynamiques (GoPlus, data-gated). None (non scanné / indisponible)
    # → aucun effet : comportement strictement inchangé sur les scans qui ne l'appellent pas.
    if ctx.is_honeypot is True:
        reasons.append("honeypot confirmé (GoPlus) — revente bloquée")
    if ctx.cannot_sell is True:
        reasons.append("vente totale impossible (GoPlus)")
    if ctx.sell_tax is not None and ctx.sell_tax > DEFAULT_MAX_SELL_TAX:
        reasons.append(
            f"taxe de vente {ctx.sell_tax * 100:.0f}% > {DEFAULT_MAX_SELL_TAX * 100:.0f}% (extractif)"
        )
    if ctx.hidden_owner is True:
        reasons.append("owner caché (GoPlus)")
    if ctx.can_take_back_ownership is True:
        reasons.append("reprise de propriété possible (GoPlus)")

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
        and not mint_blocks
        and ctx.has_blacklist is not True
        and ctx.has_disable_transfers is not True
        and ctx.top_holder_pct is not None
        and ctx.top_holder_pct <= max_top_holder_pct
        and ctx.is_honeypot is not True
        and ctx.cannot_sell is not True
        and (ctx.sell_tax is None or ctx.sell_tax <= DEFAULT_MAX_SELL_TAX)
        and ctx.hidden_owner is not True
        and ctx.can_take_back_ownership is not True
    )
    if passed:
        reasons = [
            f"screené : score {ctx.security_score}/95, liquidité ${liq:,.0f}, "
            f"vérifié, holder max {ctx.top_holder_pct:.0f}%, verdict SAFE"
        ]

    # Échec DUR = au moins un signal négatif CONFIRMÉ (pas une simple donnée absente).
    # Sert à l'absorbeur : un échec purement « données indisponibles » (429/timeout)
    # ne doit PAS bannir un token « pour toujours » — juste être réessayé plus tard.
    hard_fail = (not passed) and (
        (not ctx.valid_address)
        or (ctx.best_pair is None)
        or (ctx.best_pair is not None and liq < min_liquidity_usd)
        or (ctx.contract_verified is False)
        or mint_blocks_confirmed(ctx)
        or (ctx.has_blacklist is True)
        or (ctx.has_disable_transfers is True)
        or (ctx.top_holder_pct is not None and ctx.top_holder_pct > max_top_holder_pct)
        or (ctx.is_honeypot is True)
        or (ctx.cannot_sell is True)
        or (ctx.sell_tax is not None and ctx.sell_tax > DEFAULT_MAX_SELL_TAX)
        or (ctx.hidden_owner is True)
        or (ctx.can_take_back_ownership is True)
    )

    return ScreenResult(
        contract=ctx.contract,
        passed=passed,
        security_score=ctx.security_score,
        liquidity_usd=liq,
        verdict=ctx.lite_verdict,
        reasons=reasons,
        hard_fail=hard_fail,
    )


def mint_blocks_confirmed(ctx: TokenScanContext) -> bool:
    """True si le mint est CONFIRMÉ contrôlé par un dev (owner EOA) — pas 'unknown'.

    'unknown' (autorité non résolue à cause d'une indisponibilité) reste un échec
    MOU : on ne bannit pas définitivement, on réessaiera.
    """
    return ctx.has_mint is True and ctx.mint_authority == "eoa"
