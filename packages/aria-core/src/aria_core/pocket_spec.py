"""Pocket deployment contract -- the SINGLE source of truth for what must be
decided before a trading pocket can go live.

Why this exists (07/08, operator request): "meme toi si je te dit de deployer
un agent il faut que tu puisse remplir ce gabarit pour etre sur de rien
oublier, avec des erreur si une case a etait oublier". A checklist a human
reads is not a guardrail -- nothing forces a session (human or Claude) to
open it before writing code. This module turns the checklist into a
MECHANICAL gate: an incomplete spec produces blocking errors, and
``assert_deployable`` raises rather than letting a half-configured pocket
reach production.

Design rule that matters most: this file is the ONLY place the field list
lives. The operator-facing HTML template is a VIEW of ``SPEC_FIELDS`` (see
``as_template_rows``), never a second hand-maintained list -- two parallel
lists silently diverge, which is exactly the failure this project hit on
2026-08-07 (the v8-watch cadence documented as "2h" in both CLAUDE.md and
run.sh while the real crontab had been `*/30` for days).

Deliberately NOT a guardrail file in the CLAUDE.md sense (it never moves
money, never signs, never touches `permission_mode`/`wallet_guard`/
`outgoing_pause`): it only refuses to consider a pocket configuration
complete. The real money guardrails stay exactly where they are.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Timeframes -- crossed across the THREE providers of the OHLCV cascade.
#
# Operator rule (07/08): "si une api a des contrainte alors on annule pour
# tous le monde". A granularity only one provider serves is a trap, not a
# setting: the moment the primary provider rate-limits (which it does -- our
# own pipeline saturates it), the pocket silently runs on whatever the
# fallback serves instead. That is exactly how two v9 tokens ran on 15-minute
# candles for days while configured for 30.
#
# Each table below records what the cascade REALLY asks for, read from the
# code on 2026-08-07 -- deliberately not the provider's theoretical API
# surface, because an unused capability serves no pocket.
# ---------------------------------------------------------------------------

# GeckoTerminal, primary source. Aggregates confirmed against the official
# CoinGecko documentation read directly (docs.coingecko.com, "Pool OHLCV"):
# minute 1/5/15, hour 1/4/12, day 1. Also confirmed against production data:
# over 12h, 342 reads served at 15 min, 61 at 5 min, and 0 out of 140 at
# 30 min -- a rate limit would hit every granularity alike, that imbalance
# only fits a rejected parameter. (`services/ohlcv.py`)
_GECKOTERMINAL_MINUTES: frozenset[int] = frozenset({1, 5, 15, 60, 240, 720, 1440})

# Mobula -- MEASURED live on 2026-08-07, one call per granularity, checking
# the real spacing between consecutive candles rather than trusting the
# parameter echoed back. This table used to read `{15, 30}`, built from what
# the cascade HAPPENS TO ASK FOR rather than what the provider can do; that
# error alone shrank the "safe" intersection to a single value and nearly got
# Mobula removed. It is in fact the most capable source of the five.
#
# Verified: 1m->1min, 5m->5min, 15m->15min, 30m->30min, 1h->60min,
# 4h->240min, 6h->360min, 12h->720min, 1d->1440min.
#
# DELIBERATELY EXCLUDED -- 480 (8h): Mobula accepts the parameter, returns 60
# candles and no error, but the real spacing is 60 MINUTES. It silently
# serves hourly data under an 8-hour label. Same class of trap as the
# GeckoTerminal 30-min case, and worse: no `degraded` flag can catch it,
# because the provider claims success. Never route 8h here.
_MOBULA_MINUTES: frozenset[int] = frozenset({1, 5, 15, 30, 60, 240, 360, 720, 1440})

# Codex.io -- `_SCALPING_LADDER = ("15","30")` + `_STANDARD_LADDER =
# ("1D","240","60")` in `services/codex.py`. Live-tested 2026-08-07: 241 real
# candles returned. Its constraint is BUDGET, not reliability
# (`_MONTHLY_REQUEST_CAP = 9_500`, of which 4 000 reserved for scalping),
# against ~21 000 calls/month of real cascade traffic -- so it must be called
# LAST, only when a cheaper provider cannot serve the granularity.
_CODEX_MINUTES: frozenset[int] = frozenset({15, 30, 60, 240, 1440})

# CoinMarketCap, standard cascade only -- live-tested 2026-08-07: 500 candles,
# the deepest history of any provider. Hour-scale and above (no sub-hourly),
# so it never enters a scalping decision.
_COINMARKETCAP_MINUTES: frozenset[int] = frozenset({60, 240, 720, 1440})

# NOT LISTED as OHLCV sources -- both WORK, but only above an activity floor
# our candidates never reach. Measured 2026-08-07 on the same pair of targets:
#   - DexPaprika: 0 candles on a small pool, real 30-min candles on a 6.5M$
#     liquidity pool (90M$/24h). Its valid intervals, straight from its own
#     error message: 1m 5m 10m 15m 30m 1h 6h 12h 24h -- no 8h, and "1d" is
#     rejected in favour of "24h". Operator decision: dropped from the
#     cascade ("dexpaprika a l'air chiant retire la") -- momentum candidates
#     are small caps by definition, so it would answer empty every time.
#   - Dune: 48 candles on WETH, 0 on the small v9 token, `available=True` in
#     BOTH cases -- the empty answer is indistinguishable from a real one.
# The lesson worth keeping: a first test on a single small pool made both
# look dead. They are not; they are simply useless at our size. Never
# conclude a provider is broken from one sample.
#
# The DexScreener synthesis is also absent: ~5 approximate price points,
# never real candles -- a deliberately degraded last resort, not a source.

# The intersection: granularities every REAL provider can serve.
ROBUST_TIMEFRAME_MINUTES: tuple[int, ...] = tuple(sorted(
    _GECKOTERMINAL_MINUTES & _MOBULA_MINUTES & _CODEX_MINUTES
))

# Cost ordering, cheapest first -- what the adaptive cascade below follows.
#
# GeckoTerminal leads: throttled (4.0s) but effectively unmetered, and it is
# already the pipeline's workhorse. Mobula comes SECOND deliberately
# (operator, 07/08: "il faut mettre mobula dans les autres cascades en
# deuxieme ou plus pour eviter de tirer dessus sur d'autres timeframes") --
# it carries 36.5% of real traffic and is the only 30-min source, so burning
# it where GeckoTerminal can do the job would spend the safety net for
# nothing. It still ends up FIRST at 30 min, simply because routing by
# capability leaves no one else ahead of it there.
#
# Correction (07/08, real billing dashboard): Mobula's Free plan DOES carry a
# real monthly quota -- 10,000 credits/month (confirmed at docs.mobula.io/pricing),
# and the account was already at ~300% of it (30K used, $10.02 in overage credits
# at $0.0005/credit) at the time this was checked. An earlier version of this
# comment claimed "no monthly cap wired anywhere" -- that was wrong; it conflated
# the 2000-candles-per-request figure in mobula.py with the absence of a monthly
# quota. Whether overage is actually billed automatically or is a grace buffer
# before hard blocking is NOT documented publicly by Mobula -- unresolved, check
# the account's own Billing/Invoices tab if it matters again. Either way Mobula is
# not the "cheap, unmetered" provider this ordering used to assume -- CoinMarketCap
# and Codex are not the only ones with a real cap, Mobula has one too. No code-level
# budget guard exists for it yet (unlike blockscout_credit_budget.py) -- backlogged,
# not built here.
_PROVIDER_COST_ORDER: tuple[str, ...] = (
    "geckoterminal", "mobula", "coinmarketcap", "codex",
)

_PROVIDER_MINUTES: dict[str, frozenset[int]] = {
    "geckoterminal": _GECKOTERMINAL_MINUTES,
    "mobula": _MOBULA_MINUTES,
    "coinmarketcap": _COINMARKETCAP_MINUTES,
    "codex": _CODEX_MINUTES,
}


def providers_for_timeframe(minutes: int, *, scalping: bool = True) -> tuple[str, ...]:
    """The cascade to actually run for this granularity, cheapest first.

    Operator rule (07/08): "si les timeframes disponibles que sur mobula sont
    cochés alors elle doit être seule dans la cascade -- il faut faire de
    l'adaptatif pour pas surcharger les api à usage limité".

    A provider that CANNOT serve the requested granularity is never called:
    it would spend quota to return nothing, then fall through anyway. That is
    exactly what ran for days on the 30-minute tokens -- every cycle paid a
    GeckoTerminal call that could not possibly succeed before landing on
    Mobula. At 30 min this returns ("mobula", "codex"); at 5 min,
    ("geckoterminal",) alone.

    ``scalping`` drops the hour-scale-only providers: mixing day-scale candles
    into a scalping RSI corrupts the read with no visible error.
    """
    ordered = []
    for name in _PROVIDER_COST_ORDER:
        if minutes not in _PROVIDER_MINUTES[name]:
            continue
        if scalping and name == "coinmarketcap":
            continue  # no sub-hourly granularity, never a scalping source
        ordered.append(name)
    return tuple(ordered)

# Served by the PRIMARY provider but not by every fallback: usable, but the
# pocket degrades to another granularity whenever GeckoTerminal is
# unavailable. Allowed with an explicit warning, never silently.
DEGRADABLE_TIMEFRAME_MINUTES: tuple[int, ...] = tuple(sorted(
    _GECKOTERMINAL_MINUTES - set(ROBUST_TIMEFRAME_MINUTES)
))

# Every value any wired provider serves -- used only to tell "your provider
# can't do this at all" apart from "only a fallback can".
VALID_TIMEFRAME_MINUTES: tuple[int, ...] = tuple(sorted(
    _GECKOTERMINAL_MINUTES | _MOBULA_MINUTES | _CODEX_MINUTES | _COINMARKETCAP_MINUTES
))


def timeframe_support(minutes: int) -> dict[str, bool]:
    """Which wired providers can serve this granularity. Reporting helper."""
    return {
        "geckoterminal": minutes in _GECKOTERMINAL_MINUTES,
        "mobula": minutes in _MOBULA_MINUTES,
        "coinmarketcap": minutes in _COINMARKETCAP_MINUTES,
        "codex": minutes in _CODEX_MINUTES,
    }


# ---------------------------------------------------------------------------
# Chains -- same crossing rule, applied to the two HARD guardrails.
#
# This axis matters more than timeframes: a granularity mismatch degrades the
# signal, a chain gap removes a SAFETY CHECK entirely. Both maps read from the
# code on 2026-08-07.
# ---------------------------------------------------------------------------

# Honeypot check (`_DEXSCREENER_TO_GOPLUS_CHAIN_ID` in momentum_entry.py).
# An uncovered chain is already rejected there ("chain_not_covered").
_GOPLUS_CHAINS: frozenset[str] = frozenset({"base", "solana", "robinhood", "ethereum"})

# Holder concentration / contract data (`CHAIN_IDS` in services/blockscout.py).
# 14 EVM chains -- "robinhood" added #308 (16/08, real chain_id=4663 sourced
# from docs.robinhood.com/chain/connecting, closes the exact structural
# guardrail gap this comment used to flag) -- notably still NOT solana (not
# EVM, no Blockscout).
_BLOCKSCOUT_CHAINS: frozenset[str] = frozenset({
    "base", "ethereum", "arbitrum", "optimism", "polygon", "celo", "gnosis",
    "scroll", "zksync", "rootstock", "unichain", "soneium", "mode", "robinhood",
})

# Chains where BOTH hard guardrails can actually run. Anywhere else, at least
# one mandatory safety check is structurally unavailable -- which is a
# different problem from it merely failing on a given token.
FULLY_GUARDED_CHAINS: tuple[str, ...] = tuple(sorted(_GOPLUS_CHAINS & _BLOCKSCOUT_CHAINS))


def chain_support(chain: str) -> dict[str, bool]:
    """Which hard guardrails cover this chain. Reporting helper."""
    c = (chain or "").strip().lower()
    return {
        "goplus_honeypot": c in _GOPLUS_CHAINS,
        "blockscout_holders": c in _BLOCKSCOUT_CHAINS,
    }


def _validate_chains(value: str) -> str | None:
    """Blocks a chain where a HARD guardrail has no coverage at all.

    Directly relevant to two standing project notes: the 15/07 decision
    allowing "Base/Solana/Robinhood, no limit" for the paper test, and the
    Monad diligence that stalled on exactly this (Blockscout has no official
    deployment there). Solana and Robinhood pass the honeypot check but have
    no holder-concentration source -- fine for paper, never for real capital.
    """
    chains = [c.strip().lower() for c in value.replace(";", ",").split(",") if c.strip()]
    if not chains:
        return "aucune chaîne indiquée"

    problems: list[str] = []
    for c in chains:
        support = chain_support(c)
        if not any(support.values()):
            problems.append(f"{c} n'est couverte par AUCUN garde-fou dur")
        elif not support["goplus_honeypot"]:
            problems.append(f"{c} n'a pas de contrôle honeypot (garde-fou obligatoire)")
        elif not support["blockscout_holders"]:
            problems.append(
                f"{c} n'a pas de source de concentration des détenteurs "
                f"(GoPlus couvre le honeypot, Blockscout non déployé) -- "
                f"acceptable en paper, jamais en capital réel"
            )
    if problems:
        guarded = "/".join(FULLY_GUARDED_CHAINS)
        return "; ".join(problems) + f". Chaînes entièrement couvertes : {guarded}."
    return None


class SpecIncomplete(RuntimeError):
    """Raised by ``assert_deployable`` when at least one blocking error remains."""


@dataclass(frozen=True)
class SpecError:
    """One unresolved point. ``blocking=True`` forbids deployment."""

    key: str
    message: str
    blocking: bool = True

    def __str__(self) -> str:  # pragma: no cover -- display only
        return f"[{'BLOQUANT' if self.blocking else 'avertissement'}] {self.key} : {self.message}"


@dataclass(frozen=True)
class SpecField:
    """One decision that has to be made before a pocket goes live.

    ``required``  -- absent value = blocking error.
    ``guardrail`` -- non-negotiable; may never be answered with a disabling
                     value (see ``_DISABLING_ANSWERS``), even deliberately.
    ``validate``  -- optional extra check, returns an error message or None.
    """

    key: str
    section: str
    label: str
    help: str
    required: bool = True
    guardrail: bool = False
    validate: Callable[[str], str | None] | None = None


# Answers that read as "this point is turned off". Accepted on an optional
# field (a pocket legitimately may not use staged take-profit), never on a
# guardrail.
_DISABLING_ANSWERS = frozenset({"non", "no", "false", "0", "aucun", "aucune", "désactivé", "desactive", "off"})


def _validate_timeframe(value: str) -> str | None:
    """Blocks a granularity NO provider can serve.

    07/08 -- rewritten once the cascade became adaptive. The previous version
    blocked anything GeckoTerminal could not serve, which was correct while
    the cascade order was FIXED: a 30-minute pocket then paid a doomed
    GeckoTerminal call every cycle before landing on Mobula at 15 min. With
    ``providers_for_timeframe`` routing by capability, 30 min is now served
    natively by Mobula in FIRST position, so the blanket ban is obsolete --
    keeping it would forbid a granularity that genuinely works.

    What remains blocking is a granularity with no provider at all. A
    single-provider granularity is allowed but flagged (see ``validate``):
    it works, with no fallback if that one provider is down.
    """
    try:
        minutes = int(float(value.strip().replace("min", "").replace("m", "")))
    except (ValueError, AttributeError):
        return f"valeur illisible ({value!r}) -- attendu un nombre de minutes"

    if not providers_for_timeframe(minutes, scalping=False):
        served = "/".join(str(t) for t in VALID_TIMEFRAME_MINUTES)
        return (
            f"{minutes} min n'est servi par AUCUNE source OHLCV câblée. "
            f"Granularités réellement servies : {served} min."
        )
    return None


def _validate_positive(value: str) -> str | None:
    try:
        if float(value) <= 0:
            return "doit être strictement positif"
    except ValueError:
        return f"valeur numérique attendue, reçu {value!r}"
    return None


def _validate_fraction(value: str) -> str | None:
    """A share of capital: expressed as a fraction (0.05 = 5%), never a percent."""
    try:
        f = float(value)
    except ValueError:
        return f"valeur numérique attendue, reçu {value!r}"
    if not 0 < f <= 1:
        return f"attendu une fraction entre 0 et 1 (0.05 = 5%), reçu {f}"
    return None


SPEC_FIELDS: tuple[SpecField, ...] = (
    # -- 01 identity ---------------------------------------------------------
    SpecField("wallet", "Identité", "wallet",
              "Nom interne unique, clé dans les 7 tables de la base."),
    SpecField("objective", "Identité", "objectif en une phrase",
              "Ce que la poche cherche à capturer. Repris en tête de la thèse de "
              "CHAQUE position (voir PocketSpec.thesis_prefix) -- c'est ce qui "
              "permet de juger la poche contre son intention, des semaines après."),
    SpecField("starting_capital", "Identité", "STARTING_CAPITAL_USD",
              "Capital fictif de départ.", validate=_validate_positive),
    SpecField("gate", "Identité", "ARIA_<NOM>_ENABLED",
              "Interrupteur dédié, toujours OFF par défaut.", guardrail=True),
    SpecField("parent_gate", "Identité", "gate parent",
              "Interrupteur dont la poche dépend en plus du sien."),
    # -- 02 sourcing ---------------------------------------------------------
    SpecField("sourcing", "Sourcing", "source des candidats",
              "Watchlist fixe ou découverte automatique -- jamais les deux."),
    SpecField("chains", "Sourcing", "chaînes autorisées",
              "Croisées contre les deux garde-fous durs (honeypot + concentration). "
              "Solana à désactiver avant tout capital réel.",
              validate=_validate_chains),
    SpecField("max_candidates", "Sourcing", "MAX_CANDIDATES_PER_CYCLE",
              "Borne directement le coût réseau par passage.", validate=_validate_positive),
    # -- 03 candles ----------------------------------------------------------
    SpecField("timeframe", "Bougies", "timeframe_min",
              "Granularité des bougies. Validée contre les agrégats réellement "
              "acceptés par GeckoTerminal.", guardrail=True, validate=_validate_timeframe),
    SpecField("cycle_interval", "Bougies", "intervalle du cycle",
              "Fréquence de passage de l'agent, en minutes.", validate=_validate_positive),
    SpecField("min_candles", "Bougies", "MIN_CANDLES_FOR_SIGNAL",
              "Historique minimum avant d'oser un signal.", validate=_validate_positive),
    SpecField("closed_candle_only", "Bougies", "bougie non close exclue",
              "Toujours retirer la dernière bougie -- deux faux signaux déjà "
              "causés par un calcul sur bougie en formation.", guardrail=True),
    # -- 04/05 signal --------------------------------------------------------
    SpecField("entry_signal", "Entrée", "condition d'entrée",
              "Ce qui déclenche l'achat, avec ses seuils chiffrés."),
    SpecField("indicators", "Entrée", "indicateurs utilisés",
              "Liste explicite avec leur période -- un indicateur non listé n'est pas calculé."),
    SpecField("anti_chase", "Entrée", "MAX_CHASE_PCT",
              "Refus d'acheter un prix déjà parti au-dessus de la bougie de signal."),
    # -- 06 hard gates -------------------------------------------------------
    SpecField("honeypot", "Garde-fous", "honeypot (GoPlus)",
              "Seul garde-fou multi-chaînes obligatoire.", guardrail=True),
    SpecField("holder_concentration", "Garde-fous", "concentration des détenteurs",
              "Échoue en mode fermé si la donnée manque.", guardrail=True),
    SpecField("min_liquidity", "Garde-fous", "liquidité minimale",
              "Plancher en dollars.", guardrail=True, validate=_validate_positive),
    SpecField("wash_trading", "Garde-fous", "wash trading",
              "Ratio volume/liquidité anormal confirmé sur fenêtre glissante.", guardrail=True),
    SpecField("blacklist", "Garde-fous", "liste noire",
              "Bannissement permanent des arnaques et pertes répétées."),
    # -- 07 sizing -----------------------------------------------------------
    SpecField("sizing_mode", "Sizing", "mode de sizing",
              "Pourcentage fixe du cash ou budget de risque piloté par le R/R."),
    SpecField("alloc_pct", "Sizing", "ALLOC_PCT",
              "Part du capital par position, en fraction.", validate=_validate_fraction),
    SpecField("risk_cap", "Sizing", "RISK_CAP_PCT",
              "Perte maximale du capital total sur une position.",
              guardrail=True, validate=_validate_fraction),
    SpecField("price_impact_cap", "Sizing", "plafond d'impact de prix",
              "La position ne doit jamais déplacer le prix du pool au-delà du seuil.",
              guardrail=True),
    SpecField("max_positions", "Sizing", "MAX_POSITIONS",
              "Positions simultanées.", validate=_validate_positive),
    SpecField("fees", "Sizing", "frais simulés",
              "Swap + impact, appliqués à l'entrée et à la sortie."),
    # -- 08 exit -------------------------------------------------------------
    SpecField("trailing_stop", "Sortie", "stop suiveur",
              "Pourcentage fixe ou piloté par l'ATR.", guardrail=True),
    SpecField("take_profit", "Sortie", "prise de profit étagée",
              "Mettre 'aucune' si le stop suiveur est la seule sortie.", required=False),
    SpecField("invalidation", "Sortie", "invalidation ATR",
              "Plancher sous lequel la thèse est cassée.", required=False),
    SpecField("stagnation", "Sortie", "timeout de stagnation",
              "Sortie si le prix ne bouge pas assez dans le délai.", required=False),
    SpecField("max_hold", "Sortie", "durée maximale de détention",
              "Fermeture forcée.", required=False),
    SpecField("exclusive_manager", "Sortie", "gestionnaire exclusif",
              "Si la poche gère ses sorties, la boucle générique doit l'ignorer "
              "explicitement -- invariant à verrouiller par un test."),
    # -- 09 circuit breaker / cycle -----------------------------------------
    SpecField("kill_switch", "Coupe-circuit", "kill-switch /stop",
              "Vérifié à chaque cycle, en mode fermé sur tout ce qui touche l'argent.",
              guardrail=True),
    SpecField("circuit_breaker", "Coupe-circuit", "coupe-circuit dédié",
              "Perte maximale et pertes consécutives avant gel."),
    SpecField("weekly_reset", "Coupe-circuit", "cycle hebdomadaire",
              "Remise à zéro : clôture au prix réel, archivage, redémarrage."),
    # -- 10 traceability -----------------------------------------------------
    SpecField("scan_log", "Traçabilité", "journal de chaque évaluation",
              "Toutes les évaluations, motif de rejet inclus -- pas seulement les achats.",
              guardrail=True),
    SpecField("provenance", "Traçabilité", "provenance des données",
              "Fournisseur, granularité réellement servie, drapeau degraded.",
              guardrail=True),
    SpecField("thesis", "Traçabilité", "thèse écrite à l'achat",
              "Phrase lisible expliquant le déclenchement, stockée avec la position."),
    SpecField("monitoring", "Traçabilité", "surveillance périodique",
              "Alerte sur anomalie dans les DEUX sens : perte anormale et gain "
              "anormalement rapide."),
)


@dataclass
class PocketSpec:
    """A filled-in template. ``values`` maps ``SpecField.key`` -> operator answer."""

    name: str = ""
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return (self.values.get(key) or "").strip()

    def thesis_prefix(self) -> str:
        """The pocket's stated objective, formatted as the prefix every one of
        its positions carries in its ``thesis`` field.

        07/08 -- operator asked what the "objectif en une phrase" box actually
        did; honest answer at the time was "nothing, it only shows up in the
        export". Now it is the pocket's own line in the trade journal: the
        ``thesis`` column is what made it possible to prove, on 2026-08-07,
        that all 4 post-restart v8 trades came from the bootstrap tier and
        none from the divergence tier. A pocket whose objective is only
        written on a form cannot be judged against it afterwards.
        """
        objective = self.get("objective")
        if not objective:
            return f"[{self.name or 'poche'}]"
        return f"[{self.name or 'poche'} — {objective}]"


def validate(spec: PocketSpec) -> list[SpecError]:
    """Every unresolved point, blocking ones first. Empty list = deployable.

    Never raises on a malformed answer -- a bad value is reported as an error,
    which is the whole point of the gate.
    """
    errors: list[SpecError] = []

    if not spec.name.strip():
        errors.append(SpecError("name", "la poche n'a pas de nom"))

    for f in SPEC_FIELDS:
        value = spec.get(f.key)
        if not value:
            if f.guardrail:
                errors.append(SpecError(f.key, f"garde-fou non confirmé -- {f.help}"))
            elif f.required:
                errors.append(SpecError(f.key, f"non renseigné -- {f.help}"))
            else:
                errors.append(SpecError(
                    f.key, f"non renseigné (optionnel) -- {f.help}", blocking=False,
                ))
            continue
        if f.guardrail and value.lower() in _DISABLING_ANSWERS:
            errors.append(SpecError(
                f.key,
                f"un garde-fou ne peut pas être désactivé (réponse {value!r})",
            ))
            continue
        if f.validate is not None:
            problem = f.validate(value)
            if problem:
                errors.append(SpecError(f.key, problem))

    # Non-blocking notice: a granularity the primary provider serves but some
    # fallback does not. Legal, but the pocket WILL change granularity the
    # moment GeckoTerminal rate-limits -- which it does under our own load.
    tf = spec.get("timeframe")
    if tf:
        try:
            minutes = int(float(tf.replace("min", "").replace("m", "")))
        except ValueError:
            minutes = None
        cascade = providers_for_timeframe(minutes, scalping=False) if minutes else ()
        if len(cascade) == 1:
            errors.append(SpecError(
                "timeframe",
                f"{minutes} min n'est servi que par {cascade[0]} : aucun repli si "
                f"cette source tombe, la poche s'arrête sur cette granularité. "
                f"Acceptable si c'est un choix conscient ; sinon préférer "
                f"{'/'.join(str(t) for t in ROBUST_TIMEFRAME_MINUTES)} min "
                f"(servi par toutes les sources).",
                blocking=False,
            ))

    errors.sort(key=lambda e: not e.blocking)
    return errors


def blocking_errors(spec: PocketSpec) -> list[SpecError]:
    return [e for e in validate(spec) if e.blocking]


def assert_deployable(spec: PocketSpec) -> None:
    """Fail-closed gate. Raises ``SpecIncomplete`` listing every blocking point.

    Call this BEFORE writing a pocket's code or flipping its gate -- the whole
    reason this module exists is that a checklist nobody is forced to open
    stops no one.
    """
    blocking = blocking_errors(spec)
    if not blocking:
        return
    detail = "\n".join(f"  - {e.key} : {e.message}" for e in blocking)
    raise SpecIncomplete(
        f"Poche {spec.name or '(sans nom)'} non déployable -- "
        f"{len(blocking)} point(s) bloquant(s) :\n{detail}"
    )


def completion(spec: PocketSpec) -> tuple[int, int]:
    """``(answered, total)`` -- for a progress indicator, never a gate."""
    answered = sum(1 for f in SPEC_FIELDS if spec.get(f.key))
    return answered, len(SPEC_FIELDS)


def as_template_timeframes() -> list[dict[str, object]]:
    """Every granularity the operator template may offer, with its real status.

    07/08 -- operator: "tout ceci doit etre referencer pour interargir avec le
    futur template". The HTML must never carry its own hand-written timeframe
    table: it would drift from the code the same way CLAUDE.md drifted from
    the real crontab. It renders THIS, so correcting a provider table here
    updates the operator-facing form automatically.

    ``status`` is what the form should show:
      ``safe``      -- served by every active source, real fallbacks exist
      ``limited``   -- works, but few or no fallback if that source is down
      ``forbidden`` -- no source serves it truthfully (today: 8h, which
                       Mobula answers with hourly candles and no error)
    """
    rows: list[dict[str, object]] = []
    for minutes in sorted(set(VALID_TIMEFRAME_MINUTES) | {480}):
        cascade = providers_for_timeframe(minutes, scalping=False)
        if not cascade:
            status = "forbidden"
        elif minutes in ROBUST_TIMEFRAME_MINUTES:
            status = "safe"
        else:
            status = "limited"
        rows.append({
            "minutes": minutes,
            "label": _timeframe_label(minutes),
            "status": status,
            "providers": list(cascade),
            "scalping_providers": list(providers_for_timeframe(minutes)),
            "note": _TIMEFRAME_NOTES.get(minutes, ""),
        })
    return rows


def timeframe_support_names(minutes: int) -> tuple[str, ...]:
    """Providers able to serve this granularity, unordered. Used by the
    pipeline to skip a stage that structurally cannot answer."""
    return tuple(name for name, mins in _PROVIDER_MINUTES.items() if minutes in mins)


def mobula_period(minutes: int) -> str | None:
    """Mobula's own ``period`` string for a granularity, or ``None`` if it
    cannot serve it truthfully.

    Returns ``None`` for 480 (8h) on purpose: Mobula answers that one with
    hourly candles and no error, so asking is worse than not asking.
    """
    if minutes not in _MOBULA_MINUTES:
        return None
    if minutes < 60:
        return f"{minutes}m"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    return f"{minutes // 60}h"


def _timeframe_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    if minutes % 1440 == 0:
        return f"{minutes // 1440} j"
    return f"{minutes // 60} h"


# Traps worth stating on the form itself rather than in a comment nobody opens.
_TIMEFRAME_NOTES: dict[int, str] = {
    480: "Aucune source ne sert le 8 h. Mobula accepte le paramètre et renvoie "
         "60 bougies sans erreur, mais l'écart réel mesuré est de 60 min — il "
         "sert de l'horaire sous une étiquette 8 h.",
    30: "GeckoTerminal ne sert pas le 30 min : la cascade part directement sur "
        "Mobula, qui le sert nativement.",
    360: "Servi par Mobula uniquement — aucun repli si cette source tombe.",
}


def as_template_rows() -> list[dict[str, object]]:
    """The field list in a form the operator-facing HTML template renders.

    Exists so the template is a VIEW of this module rather than a second
    hand-maintained list that would silently drift from it.
    """
    return [
        {
            "key": f.key, "section": f.section, "label": f.label,
            "help": f.help, "required": f.required, "guardrail": f.guardrail,
        }
        for f in SPEC_FIELDS
    ]


def parse_export(text: str) -> PocketSpec:
    """Reads back the template's own text export (``[x] label = value``).

    Tolerant by design: an unrecognized line is skipped rather than raising --
    the export is meant to survive a copy/paste through Telegram or a PDF.
    """
    by_label = {f.label: f.key for f in SPEC_FIELDS}
    spec = PocketSpec()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Poche") and ":" in line:
            spec.name = line.split(":", 1)[1].strip()
            continue
        if not line.startswith("[") or "]" not in line:
            continue
        body = line.split("]", 1)[1].strip()
        if "=" not in body:
            continue
        label, value = body.split("=", 1)
        key = by_label.get(label.strip())
        if key:
            spec.values[key] = value.strip()
    return spec
