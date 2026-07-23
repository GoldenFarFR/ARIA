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

from aria_core.momentum_entry import MAX_VOLUME_TO_LIQUIDITY_RATIO, _wash_trading_ratio_confirmed
from aria_core.skills.acp_onchain_scan import TokenScanContext
from aria_core.skills.mint_authority import SAFE_AUTHORITIES


def _mint_is_dev_controlled(ctx: TokenScanContext) -> bool:
    """True si un mint externe existe ET reste aux mains d'un dev (ou indéterminable).

    Neutralisé (retourne False) si l'autorité est renoncée, un launchpad connu, ou
    un contrat (timelock/multisig/émission). Fail-closed : un mint dont l'autorité
    n'a pas pu être résolue ('unknown' ou non renseignée) reste bloquant.
    """
    if ctx.has_mint is not True:
        return False
    return (ctx.mint_authority or "unknown") not in SAFE_AUTHORITIES


def _wash_trading_confirmed(ctx: TokenScanContext) -> bool:
    """22/07 -- item #1 (plan de renforcement post-stress-test) : réutilise TEL QUEL le
    détecteur de wash-trading du pipeline momentum (`MAX_VOLUME_TO_LIQUIDITY_RATIO` +
    `_wash_trading_ratio_confirmed`, fenêtre de confirmation soutenue partagée) --
    jamais une deuxième constante/logique qui pourrait diverger. Le crible VC n'avait
    jusqu'ici AUCUN garde-fou anti-manipulation de volume, alors que le scoring
    `_score_and_verdict` ne regarde que sécurité/liquidité/concentration. Chaîne fixée
    en dur à 'base' -- ce module (comme `acp_onchain_scan.py`) est scopé Base
    uniquement, la clé partagée (contrat, chaîne) avec le pipeline momentum est donc
    cohérente (même contrat = même réalité de marché, peu importe qui le scanne)."""
    if ctx.best_pair is None or not ctx.best_pair.liquidity_usd:
        return False
    volume_to_liq = (ctx.best_pair.volume_24h_usd or 0.0) / ctx.best_pair.liquidity_usd
    return _wash_trading_ratio_confirmed(ctx.contract, "base", volume_to_liq)

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
    liquidity_stability_confirmed: bool | None = None,
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

    ``liquidity_stability_confirmed`` (22/07, item #19 -- confirmation de stabilité
    temporelle, `skills/liquidity_stability.py`) : ``False`` si une chute de liquidité
    suspecte a été détectée depuis le dernier scan de ce même contrat (fenêtre
    récente) -- soft-fail (comportement de marché, pas un mécanisme confirmé dans le
    contrat, même famille que `wash_trading`). ``None`` (par défaut, ou premier scan
    jamais vu) -> aucun effet, jamais un rejet sur une absence de comparaison.
    """
    liq = ctx.best_pair.liquidity_usd if ctx.best_pair else 0.0
    reasons: list[str] = []

    # Calculés en premier (réutilisés par plusieurs barrières ci-dessous, jamais
    # recalculés deux fois avec un risque de divergence).
    mint_blocks = _mint_is_dev_controlled(ctx)
    wash_trading = _wash_trading_confirmed(ctx)

    # 22/07 -- item #5 (plan de renforcement) : le plancher de liquidité unique
    # pénalisait à tort un token dont le SCORE et le VERDICT sont déjà propres --
    # le risque scam/rug est alors déjà écarté par le scoring lui-même. Assoupli
    # SEULEMENT si tout le reste (score, verdict, mint) est irréprochable, jamais
    # un blanc-seing générique sur la liquidité (qui reste le défaut partout ailleurs).
    liquidity_low = ctx.best_pair is not None and liq < min_liquidity_usd
    liquidity_bypass = (
        liquidity_low
        and ctx.security_score >= min_score
        and ctx.lite_verdict == "SAFE"
        and not mint_blocks
    )

    # Barrières marché
    if not ctx.valid_address:
        reasons.append("adresse de contrat invalide")
    if ctx.best_pair is None:
        reasons.append("aucune paire DEX trouvée (illiquide ou inexistant)")
    if liquidity_low:
        if liquidity_bypass:
            reasons.append(
                f"liquidité faible ${liq:,.0f} < ${min_liquidity_usd:,.0f} -- tolérée "
                f"(score {ctx.security_score}/95, verdict SAFE, mint propre)"
            )
        else:
            reasons.append(f"liquidité ${liq:,.0f} < minimum ${min_liquidity_usd:,.0f}")
    if ctx.security_score < min_score:
        reasons.append(f"score de sécurité {ctx.security_score} < {min_score}")
    if ctx.lite_verdict != "SAFE":
        reasons.append(f"verdict de scan '{ctx.lite_verdict}' (SAFE requis)")
    if wash_trading:
        reasons.append(
            f"volume 24h/liquidité extrême et SOUTENU (> {MAX_VOLUME_TO_LIQUIDITY_RATIO:.0f}x) "
            "-- signal de wash-trading"
        )
    if liquidity_stability_confirmed is False:
        reasons.append(
            "chute de liquidité suspecte depuis le dernier scan de ce contrat "
            "-- possible manipulation synchronisée sur la fenêtre de scan"
        )

    # Barrières « le dev garde le pouvoir » (prioritaires)
    if require_verified and ctx.contract_verified is not True:
        reasons.append("contrat non vérifié (code opaque)")
    # Mint : bloquant seulement si un DEV en garde le contrôle. Un mint renoncé,
    # piloté par un launchpad connu (Virtuals/Flaunch...) ou par un contrat
    # (timelock/multisig/émission) est légitime -> neutralisé (cf. mint_authority).
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
    # 22/07 -- item #2 (plan de renforcement) : même famille que hidden_owner/
    # can_take_back_ownership -- un pouvoir CACHÉ que le dev garde sur le contrat,
    # jamais réparé par le temps ou par un meilleur score de liquidité -> échec dur.
    if ctx.slippage_modifiable is True:
        reasons.append("taxe/slippage modifiable après coup (GoPlus) — pouvoir dissimulé")
    # 22/07 -- trou trouvé en observant une position momentum RÉELLEMENT ouverte
    # (CNX, owner_change_balance jamais consulté nulle part) : pouvoir DISTINCT du
    # honeypot classique -- l'owner peut modifier directement le solde d'un wallet,
    # vecteur de perte totale, jamais réparé par le temps -> échec dur.
    if ctx.owner_change_balance is True:
        reasons.append("owner peut modifier le solde d'un wallet (GoPlus) — vecteur de perte totale")

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
        and (liq >= min_liquidity_usd or liquidity_bypass)
        and ctx.security_score >= min_score
        and ctx.lite_verdict == "SAFE"
        and not wash_trading
        and liquidity_stability_confirmed is not False
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
        and ctx.slippage_modifiable is not True
        and ctx.owner_change_balance is not True
    )
    if passed:
        reasons = [
            f"screené : score {ctx.security_score}/95, liquidité ${liq:,.0f}, "
            f"vérifié, holder max {ctx.top_holder_pct:.0f}%, verdict SAFE"
        ]
        # Jamais silencieux (item #5) : le franchissement du plancher de liquidité
        # reste visible même sur un passage réussi, pas seulement sur un rejet.
        if liquidity_bypass:
            reasons.append(
                f"liquidité faible ${liq:,.0f} < ${min_liquidity_usd:,.0f} tolérée "
                "(score/verdict/mint propres)"
            )

    # Échec DUR = un mécanisme MALVEILLANT confirmé dans le contrat lui-même (le code
    # ne "guérit" jamais avec le temps -- décision opérateur explicite, 10/07 : "si il y
    # a une super technologie mais des failles dans le contrat on jette, aucun risque,
    # il existe énormément d'autres projets"). PAS un échec dur : liquidité, paire DEX,
    # vérification du contrat, concentration des holders -- ce sont des ASPECTS
    # D'INVESTISSEMENT qui évoluent avec la maturité du projet (le même principe
    # s'applique à "comme tous les autres tokens", pas seulement bonding). Ces
    # candidats restent 'pending' (retry) : jamais bannis pour toujours sur un simple
    # état de marché du moment.
    hard_fail = (not passed) and (
        (not ctx.valid_address)
        or mint_blocks_confirmed(ctx)
        or (ctx.has_blacklist is True)
        or (ctx.has_disable_transfers is True)
        or (ctx.is_honeypot is True)
        or (ctx.cannot_sell is True)
        or (ctx.sell_tax is not None and ctx.sell_tax > DEFAULT_MAX_SELL_TAX)
        or (ctx.hidden_owner is True)
        or (ctx.can_take_back_ownership is True)
        or (ctx.slippage_modifiable is True)
        or (ctx.owner_change_balance is True)
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
