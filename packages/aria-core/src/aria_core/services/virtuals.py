"""Virtuals Protocol read-only client (Base) — pre-bonding detection.

Core of ARIA's niche: indexing Virtuals tokens **still on the bonding
curve** (Strapi status ``UNDERGRAD`` = Prototype) before their graduation
(``AVAILABLE`` = Sentient, threshold 42,000 accumulated VIRTUAL). Public
Strapi endpoint, no key (see ``docs/recherche-launchpads-base-2026.md``).

No writes, no signing, no calls other than GET. Same error and degradation
policy as ``services/blockscout.py`` / ``services/coingecko.py``:
- 429: exponential backoff, 3 attempts max, then give up without blocking.
- Timeout / endpoint unavailable: 1 retry after 5s, then explicit fallback.
- **NEVER raises on a network error**: ``fetch_*`` return ``[]`` / ``None``.
- No missing data is ever replaced by a guess (facts-only): missing field
  → ``None``, never a fabricated value.

## Security dome (hostile external data)

ALL strings coming from the API (name, symbol, description, status,
addresses, social links, tokenomics, additional details) are **untrusted**
and go through ``_sanitize``/``_sanitize_structured``: stripping control
characters + **neutralizing angle brackets** ``<`` / ``>`` (replaced with
``‹`` / ``›``), so a hostile name/symbol can't forge a delimiter tag and
escape an untrusted zone downstream (anti prompt-injection — same helper as
``skills/vc_analysis.py``). Social links are additionally restricted to the
``http(s)`` scheme only.

``description``/``tokenomics``/``additional_details`` feed
``skills/vc_analysis.py``'s product diligence (audit 11/07): for a Virtuals
token, the team and tokenomics live on ITS OWN PAGE (virtuals.io), not
necessarily on an external site. This remains **declarative** text (the
project talking about itself) — never an independent verification, same
doctrine as the rest of the dome.

Warning: network blocked in the build environment: live calls fail and are
tested against fixtures; live wiring happens on the VPS (see the research
doc — confirmation `curl` + re-verification of the BaseScan addresses before
prod).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger(__name__)

# Token index (pre-bonding index). DO NOT confuse with the ACP registry
# `acpx.virtuals.io/api/agents` (service agents) — see research doc §5.
API_ROOT = "https://api.virtuals.io/api"
_VIRTUALS_ENDPOINT = f"{API_ROOT}/virtuals"

# Graduation threshold: 42,000 VIRTUAL (research doc -- constant kept for
# compat, see the REJECTION of this very premise documented just below).
# RE-INVESTIGATED on 11/07 (direct network access to api.virtuals.io +
# app.virtuals.io from the VPS, confirmed HTTP 200): the front-end JS bundle
# (`app.virtuals.io/assets/index-*.js`, read in plaintext, no auth) literally
# describes the 42,000 $VIRTUAL as the INITIAL LIQUIDITY provided by the
# creator at launch ("...I will provide 42,000 $VIRTUAL... as initial
# liquidity for the pool"), NOT a target of VIRTUAL "accumulated" through
# purchases. The "raised/42000" premise that motivated this constant was
# therefore already wrong to begin with, not just unconfirmed -- see the
# detailed comment on `_VIRTUAL_RAISED_KEYS` below for the rest of the
# investigation (why no field of the Strapi API can serve as a proxy).
GRADUATION_THRESHOLD_VIRTUAL = 42_000.0

UNAVAILABLE = "donnée Virtuals indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3

# Dome: same defenses as skills/vc_analysis.py (single choke point).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FIELD_MAX = 600

# "Still in bonding" statuses — strict ALLOWLIST (text form preferred over
# the numeric form inferred from the frontend; "1" = prototype). Everything
# else (AVAILABLE/graduated, unknown, None) → NOT in bonding (conservative).
_BONDING_STATUSES = frozenset({"UNDERGRAD", "PROTOTYPE", "1"})

# Fields carrying the VIRTUAL accumulated in the curve. VERIFIED LIVE on
# 10/07 (contract 0x6f8c2Eb5..., full payload inspected): NONE of these
# names exist in the real API response -- graduation_progress() therefore
# always returns None in practice today (honest degradation, never a
# fabricated figure -- see the "56.94%" displayed by the Virtuals UI, whose
# real formula isn't confirmed). Kept as a whitelist in case the API exposes
# them one day (or for a different token) -- zero cost, never a false
# positive since absent = None.
#
# RE-INVESTIGATED on 11/07, real network access confirmed (direct curl from
# the VPS, HTTP 200 on both `api.virtuals.io` AND `app.virtuals.io`) --
# verdict unchanged (still None), but the investigation went noticeably
# further than before:
#
# 1. List AND detail (`/api/virtuals` filtered status=UNDERGRAD, and
#    `/api/virtuals/{id}`) inspected on dozens of tokens genuinely in
#    bonding (e.g. CRASHCAT just launched: mcapInVirtual=8500,
#    holderCount=1; ORCHAD mid-bonding: mcapInVirtual=36000,
#    holderCount=3). The FULL LIST of the ~70 keys in the real payload was
#    enumerated -- none carries a percentage, a "raised" figure, or a curve
#    progression, under any name (`virtualTokenValue` included: it's just
#    `mcapInVirtual * 1e9`, a reformulation, not new data).
# 2. `totalValueLocked` (a candidate that looked promising) is UNUSABLE:
#    always `"0"` for a token still UNDERGRAD (verified including on
#    ORCHAD, which does have real holders and a real liquidityUsd) -- it's
#    only populated AFTER graduation (verified on real AVAILABLE tokens,
#    e.g. TOSHI: totalValueLocked=46897). Confirms this field tracks
#    post-graduation DEX liquidity, not the pre-graduation curve reserve.
# 3. `mcapInVirtual` is NOT an accumulated reserve: it's a derived
#    valuation (price x supply), not the raw amount of VIRTUAL deposited
#    into the bonding pool. Direct proof found by sorting ~200 tokens by
#    descending `mcapInVirtual` (`sort[0]=mcapInVirtual:desc`, still with
#    no reliable status filter -- point 5): a token still UNDERGRAD (CTDA,
#    mcapInVirtual=385,200) has a HIGHER mcap than several tokens already
#    AVAILABLE/graduated in the same snapshot (SOLACE=362,111,
#    NOX=356,254). No fixed mcap threshold therefore separates the two
#    groups -- definitively rules out any `mcapInVirtual / constant` ratio
#    as a progression proxy, including `/42000` already ruled out before
#    this session.
# 4. The front-end's JS bundle (`app.virtuals.io`, HTML + assets, read in
#    plaintext, no auth) confirms that the 42,000 $VIRTUAL is the INITIAL
#    LIQUIDITY of the pool at launch (exact UI text: "...I will provide
#    42,000 $VIRTUAL... as initial liquidity for the pool"), not a target
#    of accumulated VIRTUAL -- the very premise of the `raised/42000`
#    formula was wrong from the start, not just unconfirmed. The same
#    bundle contains the ABI of an on-chain `Bonding`/`BondingV2` contract
#    (Base) with a `Graduated(address token, address agentToken)` event
#    and modifiable parameters (`newGradThreshold` notably) -- the real
#    progression data is an on-chain state (curve reserve vs the contract's
#    real graduation threshold), never exposed by this discovery Strapi
#    API. Reading it properly would require an authenticated Base mainnet
#    RPC call (contract address + ABI decoding): no mainnet on-chain read
#    infrastructure exists in this repo today (only `onchain/sepolia_wallet.py`
#    exists, testnet, for wallet signing -- not a generic read client) and a
#    new mainnet RPC network egress point requires explicit operator
#    authorization (CLAUDE.md doctrine) before being wired -- not done this
#    segment, for lack of that confirmation.
# 5. Side effect found while digging into this point: the Strapi API's
#    `filters[status]=…` filter appears to be ignored server-side
#    regardless of the value tested (`AVAILABLE`, `SENTIENT`, `GRADUATED`,
#    `INITIALIZED` -- yet a real value observed in the data --, or a
#    bogus value): all 5 return exactly the same list sorted by creation
#    date, unfiltered. `build_graduated_url`/`fetch_graduated` were
#    therefore NOT doing real server-side filtering (the date sort masked
#    the problem in practice because recent creations are almost all
#    UNDERGRAD). No security risk observed (the only caller,
#    `base_crawler.discover_virtuals_graduated_tokens`, already excludes
#    entries with `token_address=None`, so unfiltered false-UNDERGRAD
#    entries were already eliminated downstream) -- FIXED on 11/07:
#    `fetch_prototypes` and `fetch_graduated` now filter themselves
#    client-side via `is_in_bonding` (same allowlist already tested, no new
#    parsing logic), see the `test_fetch_*_filters_out_*_client_side` tests
#    in `test_virtuals_client.py`. The URLs still carry `filters[status]=…`
#    in the request (zero cost, in case the API respects it one day) but
#    must no longer be considered filtering on their own.
# 6. RE-INVESTIGATED on 11/07 (continued), real Base mainnet RPC access
#    obtained this segment (`mainnet.base.org`, free, read-only -- no
#    signing, no wallet). Direct on-chain read attempted on the Bonding
#    contract documented as "medium confidence"
#    (`0xF66DeA7b3e897cD44A5a231c61B6B4423d613259`, see
#    `docs/recherche-launchpads-base-2026.md`): `tokenInfo(address)` on
#    this contract returns an EMPTY entry (every field zero/default) for a
#    real token genuinely in bonding (WOODY, factory `BONDING_V5`) --
#    CONFIRMS that this address is wrong or stale for Virtuals' current V5
#    version (several factory versions coexist, `BONDING_V5` observed in
#    the API payload). The correct Bonding V5 contract address was NOT
#    identified despite several leads (the token is an EIP-1167 clone
#    pointing to a generic ERC20 implementation, not the Bonding contract
#    itself; the token's `owner()` returns an EOA wallet, not a contract;
#    Blockscout Base doesn't yet index transactions of tokens this recent)
#    -- requires either BaseScan with a real account/API (search by
#    verified contract name), or direct confirmation from the Virtuals
#    team.
#
#    HOWEVER, a real constant was confirmed by direct reading of
#    `getReserves()` on the PAIR itself (address available directly in the
#    API payload under `preTokenPair`, without going through the Bonding
#    contract): the agent token's reserve (`reserve0`) starts at EXACTLY
#    1,000,000,000 (verified identical on 4 different freshly-launched
#    tokens -- WOODY, ASUAGENT, CRASHCAT, DBT). This is a real, verified
#    candidate for the "initial reserve" that was missing until now.
#
#    The graduation threshold read on the "medium confidence" contract
#    (`gradThreshold` = 125,000,000, via the `gradThreshold()` call) DOESN'T
#    HOLD: tested against the real `reserve0` of 5 tokens STILL marked
#    UNDERGRAD by the API (TIBBIR, CAS, AIXBT, REPPO, AIDOG -- all at
#    different progression stages), the formula
#    `(1e9 - reserve0) / (1e9 - 125e6)` systematically gives > 100%
#    (100.01% to 110.34%) even though these tokens are NOT graduated --
#    empirical proof that this 125M threshold does come from the wrong
#    contract.
#
#    RESOLVED on 11/07 (continued 2) -- the REAL active Bonding V5 contract
#    found by direct scanning of on-chain logs (`eth_getLogs` on the
#    `Graduated(address,address)` topic, in blocks of 10,000 on
#    `mainnet.base.org` -- the free RPC's range limit): a real `Graduated`
#    event genuinely found, emitted by
#    `0x1A540088125d00dD3990f9dA45CA0859af4d3B01`
#    (`TransparentUpgradeableProxy` EIP-1967, verified on Blockscout,
#    implementation `0xCceb278a2b3A0b6D32E47B9cD61D2bD1212C3Fab`). Confirmed
#    as the right contract: `tokenInfo()` there returns real, non-empty
#    data for the graduated token from that event (unlike the old "medium
#    confidence" address, empty for every real token).
#
#    Root cause of the wrong global threshold: in `BondingV5.sol`
#    (`launchpadv2/BondingV5.sol` of the `Virtual-Protocol/protocol-contracts`
#    repo, NOT `fun/Bonding.sol` read the previous time -- two generations
#    of contracts coexist in the same repo), the threshold is NO LONGER a
#    global constant but a mapping PER TOKEN: `mapping(address => uint256)
#    public tokenGradThreshold`. There's also `tokenFakeInitialVirtualLiq`
#    (per-token mapping) -- explains the `mcapInVirtual`/`reserve1`
#    divergence never cracked before this session: it's a virtual reserve
#    offset (pump.fun pattern), not an anomaly.
#
#    FORMULA EMPIRICALLY CONFIRMED on a real graduated token (not a
#    guess): `tokenGradThreshold(graduated token) = 168,316,831.68` and
#    `getReserves()` on its real pair gives `reserve0 = 163,037,404.96`
#    -- indeed `<= tokenGradThreshold`, exactly the graduation condition in
#    the source code (`newReserveA <= gradThreshold` in `_buy()`). The
#    same exact threshold (168,316,831.68) and
#    `tokenFakeInitialVirtualLiq = 8500` found identical on 2 fresh tokens
#    from the same contract (WOODY, CRASHCAT) -- consistent with a
#    deterministic calculation (`BondingConfig.calculateGradThreshold`)
#    rather than real per-token variance for a standard launch.
#
#    Validated formula: `progress = (1_000_000_000 - reserve0) /
#    (1_000_000_000 - tokenGradThreshold(token))`, where `reserve0` comes
#    from `getReserves()` on `pair` (readable directly via `preTokenPair`
#    in the Strapi API payload, or via `tokenInfo(token).pair` on the
#    contract).
#
#    REMAINING REAL LIMIT (honest, unresolved): several instances of the
#    Bonding V5 contract coexist -- `tokenInfo()`/`tokenGradThreshold()`
#    return EMPTY for a token not managed by THIS particular instance
#    (verified: TIBBIR, a real Base token still UNDERGRAD, returns zero on
#    both mappings of `0x1A540088...`). No field of the Strapi API
#    indicates which instance manages which token -- a future wiring
#    should either try several known addresses (best-effort, graceful
#    degradation to `None` if none recognizes the token -- honest partial
#    coverage rather than a hard blocker), or dynamically discover active
#    instances by scanning logs like this segment did (expensive, not
#    suited to a synchronous call per `/vc` analysis).
#
#    Network access used this segment (already authorized by the operator,
#    explicit green light 11/07): `mainnet.base.org` (public Base RPC,
#    read-only, no signing/wallet, no new dependency -- `web3.py` already
#    present in the project's deps).
_VIRTUAL_RAISED_KEYS = (
    "virtualRaised",
    "raisedVirtual",
    "virtual_raised",
    "bondingCurveVirtualReserve",
)


@dataclass
class VirtualToken:
    """An indexed Virtuals token. All strings are already sanitized."""

    name: str | None = None
    symbol: str | None = None
    status: str | None = None
    chain: str | None = None
    token_address: str | None = None
    pre_token_address: str | None = None
    # Bonding pair address (audit 11/07, `preTokenPair` in the API payload,
    # never captured before) -- direct read possible via `getReserves()`
    # without having to identify the Bonding contract itself. See
    # `services/base_onchain.py`.
    pair_address: str | None = None
    created_at: str | None = None
    mcap: float | None = None
    volume24h: float | None = None
    price_change24h: float | None = None
    holder_count: int | None = None
    description: str | None = None
    socials: list[dict] = field(default_factory=list)
    raw_status: str | None = None
    # Derived extras (beyond the strict minimum), facts-only:
    virtual_id: int | None = None  # entity's Strapi id (for build_token_url)
    virtual_raised: float | None = None  # accumulated VIRTUAL, if the API exposes it
    # Product diligence (audit 11/07): a Virtuals token's team/tokenomics
    # live on ITS OWN PAGE (virtuals.io), not necessarily on an external
    # site -- see `vc_analysis.py`. Exact shape not confirmed live (can be a
    # string or a structured object depending on the API): flattened into
    # readable text by `_sanitize_structured`, never analyzed further
    # (facts-only, no field fabricated if absent).
    tokenomics: str | None = None
    additional_details: str | None = None


# ----------------------------------------------------------------------
# Sanitization (dome)
# ----------------------------------------------------------------------
def _sanitize(text: object, max_len: int = _FIELD_MAX) -> str | None:
    """Neutralizes an external string. ``None`` stays ``None`` (facts-only).

    Strips control characters, neutralizes angle brackets ``<`` / ``>``
    (no legitimate use in on-chain metadata), truncates to ``max_len``.
    """
    if text is None:
        return None
    s = _CONTROL_CHARS_RE.sub("", str(text))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]


def _sanitize_structured(value: object, max_len: int = _FIELD_MAX) -> str | None:
    """Like ``_sanitize`` but also tolerates a structured object (dict/list).

    Some Virtuals fields (``tokenomics``, ``additionalDetails``) are
    sometimes a JSON object rather than a plain string -- shape not
    confirmed live (never inspected on a real payload). Flattened into
    readable ``key: value`` text at ONE level of depth only (never
    unbounded recursion on a hostile object); each key and value goes
    through ``_sanitize`` like any other external data. ``None`` or an
    empty object → ``None`` (facts-only, never a fabricated text).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _sanitize(value, max_len) or None
    if isinstance(value, dict):
        parts = [
            f"{_sanitize(k, 60)}: {_sanitize(v, 160)}"
            for k, v in value.items()
            if not isinstance(v, (dict, list))
        ]
        return _sanitize("; ".join(parts), max_len) or None
    if isinstance(value, list):
        parts = [_sanitize(v, 160) for v in value if not isinstance(v, (dict, list))]
        return _sanitize("; ".join(p for p in parts if p), max_len) or None
    return _sanitize(value, max_len) or None


# ----------------------------------------------------------------------
# Careful numeric coercion (never a guess)
# ----------------------------------------------------------------------
def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    if value is None or isinstance(value, bool) or isinstance(value, (list, dict)):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first(mapping: dict, *keys: str) -> object:
    """First non-``None`` value among ``keys`` (tolerates naming variants)."""
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


# ----------------------------------------------------------------------
# URL construction (public Strapi endpoints)
# ----------------------------------------------------------------------
def build_prototypes_url(chain: str = "BASE", page_size: int = 100, *, status: str = "UNDERGRAD") -> str:
    """URL of the index filtered by status (nominally), sorted by recency.

    ``status`` defaults to ``"UNDERGRAD"`` (still in bonding, unchanged
    historical behavior). ``build_graduated_url`` reuses this same function
    with ``status="AVAILABLE"`` (graduated tokens) — a single URL
    construction point, never duplicated.

    Warning: real diagnostic (11/07, direct network access to
    api.virtuals.io): ``filters[status]`` is IGNORED server-side regardless
    of the value sent (all return the same unfiltered list). The parameter
    is kept in the URL (in case the API respects it one day, zero cost) but
    does NOT actually filter — the real filtering happens client-side in
    ``VirtualsClient.fetch_prototypes`` / ``fetch_graduated`` via
    ``is_in_bonding``.

    Ex. ``…/api/virtuals?filters[status]=UNDERGRAD&filters[chain]=BASE&sort[0]=createdAt:desc&pagination[pageSize]=100``
    """
    try:
        size = int(page_size)
    except (TypeError, ValueError):
        size = 100
    size = max(1, min(size, 200))
    params = [
        ("filters[status]", str(status)),
        ("filters[chain]", str(chain).upper()),
        ("sort[0]", "createdAt:desc"),
        ("pagination[pageSize]", str(size)),
    ]
    return f"{_VIRTUALS_ENDPOINT}?{urlencode(params, safe='[]:$')}"


def build_graduated_url(chain: str = "BASE", page_size: int = 100) -> str:
    """URL of tokens that recently graduated (``AVAILABLE`` status requested).

    ``sort[0]=createdAt:desc`` stays sorted by the prototype's CREATION date
    (no graduation date exposed by the API) — the most recently created
    appear first, which in practice captures recent graduations without
    being a strict guarantee of graduation order.

    Warning: ``filters[status]=AVAILABLE`` is NOT applied server-side (see
    ``build_prototypes_url``): the URL alone doesn't return a filtered
    list. Use ``VirtualsClient.fetch_graduated`` (never this raw URL) to get
    a real list of graduated tokens — the real filtering happens client-side
    there.
    """
    return build_prototypes_url(chain=chain, page_size=page_size, status="AVAILABLE")


def build_token_url(virtual_id: object) -> str:
    """URL of a token's detail by its Strapi id (``…/api/virtuals/{id}``)."""
    return f"{_VIRTUALS_ENDPOINT}/{quote(str(virtual_id), safe='')}"


def build_token_by_address_url(token_address: str, chain: str = "BASE") -> str:
    """URL filtered by token address (``filters[tokenAddress][$eq]=0x…``).

    Address lowercased before filtering: real diagnostic (10/07, contracts
    0x6f8c2Eb5.../0xB455C23d... tested live) -- an EXACT filter (`$eq`) on
    a mixed-case address (the usual format copied from an explorer/wallet)
    doesn't match if the Virtuals API stores the address in lowercase,
    silently breaking ALL bonding detection (`_resolve_bonding_phase`) --
    falling back to the generic "no pair" analysis. EVM addresses are
    case-insensitive (the mixed case only exists for display checksums), so
    there's no information loss.
    """
    params = [
        ("filters[tokenAddress][$eq]", str(token_address).lower()),
        ("filters[chain]", str(chain).upper()),
    ]
    return f"{_VIRTUALS_ENDPOINT}?{urlencode(params, safe='[]:$')}"


def build_token_by_pretoken_url(pre_token_address: str, chain: str = "BASE") -> str:
    """URL filtered by PRE-TOKEN address (``filters[preToken][$eq]=0x…``).

    Real diagnostic (10/07, contract 0x6f8c2Eb5... tested live):
    ``tokenAddress`` stays ``null`` AS LONG AS a token hasn't graduated --
    this is structural, not a glitch. The contract address the operator
    sees during the bonding phase (the one displayed on virtuals.io, the
    one they paste into `/vc`) is stored in ``preToken``, never
    ``tokenAddress``. Without this fallback filter, ``fetch_by_address``
    could STRUCTURALLY never find a token still in bonding by its address --
    exactly the category ``_resolve_bonding_phase`` is meant to detect."""
    params = [
        ("filters[preToken][$eq]", str(pre_token_address).lower()),
        ("filters[chain]", str(chain).upper()),
    ]
    return f"{_VIRTUALS_ENDPOINT}?{urlencode(params, safe='[]:$')}"


# ----------------------------------------------------------------------
# Social link extraction (http/https only, sanitized)
# ----------------------------------------------------------------------
def _extract_socials(attrs: dict) -> list[dict]:
    """Verifiable social links only (``http(s)`` scheme), deduplicated.

    Tolerates the Strapi/Virtuals shapes encountered: ``socials`` as a dict
    (possibly nested, e.g. ``{"VERIFIED_LINKS": {"TWITTER": "https://…"}}``),
    as a list of ``{label,url}``, plus direct keys (``twitter``…). Any
    non-``http(s)`` URL (e.g. ``javascript:``) is rejected.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _add(label: object, url: object) -> None:
        if not isinstance(url, str):
            return
        candidate = url.strip()
        if not candidate.lower().startswith(("http://", "https://")):
            return
        clean_url = _sanitize(candidate, 300)
        if not clean_url or clean_url in seen:
            return
        seen.add(clean_url)
        out.append({"label": _sanitize(label, 40) or "", "url": clean_url})

    raw = attrs.get("socials")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, str):
                _add(key, value)
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    _add(sub_key, sub_value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _add(
                            _first(item, "label", "type", "name", "platform"),
                            _first(item, "url", "value", "href", "link"),
                        )
                    else:
                        _add("", item)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                _add(
                    _first(item, "label", "type", "name", "platform"),
                    _first(item, "url", "value", "href", "link"),
                )
            else:
                _add("", item)

    for key in ("twitter", "telegram", "website", "discord", "warpcast"):
        _add(key, attrs.get(key))

    return out[:12]


# ----------------------------------------------------------------------
# Parsing a response object (graceful degradation)
# ----------------------------------------------------------------------
def parse_virtual(raw: dict) -> VirtualToken | None:
    """Parses a Strapi object into a ``VirtualToken``. Never raises.

    Tolerates the ``{"id":…, "attributes": {…}}`` shape (Strapi v4) AND the
    flat shape. Non-dict raw → ``None``; missing field → ``None`` (facts-only).
    """
    if not isinstance(raw, dict):
        return None

    attributes = raw.get("attributes")
    attrs = attributes if isinstance(attributes, dict) else raw

    raw_status = _first(attrs, "status")

    virtual_raised = None
    for key in _VIRTUAL_RAISED_KEYS:
        virtual_raised = _safe_float(attrs.get(key))
        if virtual_raised is not None:
            break

    return VirtualToken(
        name=_sanitize(_first(attrs, "name"), 120),
        symbol=_sanitize(_first(attrs, "symbol", "ticker"), 20),
        status=_sanitize(raw_status, 40),
        chain=_sanitize(_first(attrs, "chain"), 20),
        token_address=_sanitize(_first(attrs, "tokenAddress", "token_address", "contractAddress"), 80),
        pre_token_address=_sanitize(_first(attrs, "preToken", "preTokenAddress", "pre_token_address"), 80),
        pair_address=_sanitize(_first(attrs, "preTokenPair", "preTokenPairAddress", "pre_token_pair"), 80),
        created_at=_sanitize(_first(attrs, "createdAt", "created_at"), 40),
        mcap=_safe_float(_first(attrs, "mcapInVirtual", "mcap", "marketCap", "market_cap")),
        volume24h=_safe_float(_first(attrs, "volume24h", "volume_24h", "volume")),
        price_change24h=_safe_float(
            _first(attrs, "priceChangePercent24h", "priceChange24h", "price_change_24h")
        ),
        holder_count=_safe_int(_first(attrs, "holderCount", "holder_count", "holders")),
        description=_sanitize(_first(attrs, "description"), _FIELD_MAX),
        socials=_extract_socials(attrs),
        raw_status=_sanitize(raw_status, 40),
        virtual_id=_safe_int(raw.get("id") if raw.get("id") is not None else attrs.get("id")),
        virtual_raised=virtual_raised,
        tokenomics=_sanitize_structured(_first(attrs, "tokenomics")),
        additional_details=_sanitize_structured(_first(attrs, "additionalDetails", "additional_details")),
    )


# ----------------------------------------------------------------------
# Facts-only predicates
# ----------------------------------------------------------------------
def is_in_bonding(token: VirtualToken) -> bool:
    """True only if the status is in the bonding ALLOWLIST.

    ``UNDERGRAD`` / ``PROTOTYPE`` / ``1`` → True. ``AVAILABLE`` (graduated),
    unknown or missing status → False (conservative: bonding is only
    asserted on a known status).
    """
    for candidate in (token.status, token.raw_status):
        if candidate is None:
            continue
        if str(candidate).strip().upper() in _BONDING_STATUSES:
            return True
    return False


def graduation_progress(token: VirtualToken) -> float | None:
    """Progress toward graduation (0.0-1.0) if derivable, otherwise ``None``.

    Ratio ``accumulated VIRTUAL / 42,000``. Returns ``None`` when the API
    doesn't expose the accumulated value (facts-only: no inference from
    mcap, which isn't the curve reserve -- verified live on 11/07: a token
    still in bonding can have a higher ``mcapInVirtual`` than tokens already
    graduated, so no ratio against a fixed constant is valid as a proxy).
    See the comment above ``_VIRTUAL_RAISED_KEYS`` for the full
    investigation detail (real payload enumerated, UI formula not
    reproducible, real on-chain mechanism out of this API's reach).
    """
    raised = token.virtual_raised
    if raised is None or raised < 0:
        return None
    return min(raised / GRADUATION_THRESHOLD_VIRTUAL, 1.0)


# ----------------------------------------------------------------------
# HTTP client (read-only)
# ----------------------------------------------------------------------
class VirtualsClient:
    """Async HTTP client, read-only, cautious throttle (public keyless API).

    Default ``httpx.AsyncClient`` (``trust_env=True``): the CA bundle and
    any proxy are picked up from the environment, like the other clients in
    the ``services/`` folder.
    """

    def __init__(self, endpoint: str = _VIRTUALS_ENDPOINT, *, min_interval: float = 0.5) -> None:
        self.endpoint = endpoint
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "virtuals: %s consecutive failures (last: %s) — no blocking, no escalation",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "virtuals: call failed (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, url: str) -> tuple[object | None, str | None]:
        """GET with our own error policy. Returns ``(data, error)``."""
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers={"accept": "application/json"})
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout Virtuals)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Virtuals)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Virtuals)"

            if response.status_code == 404:
                self._record_success()
                return None, "token Virtuals introuvable"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def fetch_prototypes(self, chain: str = "BASE", page_size: int = 100) -> list[VirtualToken]:
        """Index of tokens still in bonding. Always a list (``[]`` on error).

        ``is_in_bonding`` filter applied CLIENT-SIDE after receiving the
        response: real diagnostic (11/07, direct network access to
        api.virtuals.io) — the server-side ``filters[status]=…`` filter of
        ``build_prototypes_url`` is ignored regardless of the value tested
        (all return the same unfiltered list, sorted by creation date).
        Without this client-side filter, an already-graduated token would
        slip into the "still in bonding" index — see the detailed comment
        above ``_VIRTUAL_RAISED_KEYS``.
        """
        try:
            url = build_prototypes_url(chain=chain, page_size=page_size)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return []
            items = data.get("data")
            if not isinstance(items, list):
                return []
            tokens: list[VirtualToken] = []
            for item in items:
                token = parse_virtual(item)
                if token is not None and is_in_bonding(token):
                    tokens.append(token)
            return tokens
        except Exception as exc:  # ultimate degradation: never an outgoing exception
            logger.info("virtuals: fetch_prototypes unexpected failure — %s", exc)
            return []

    async def fetch_graduated(self, chain: str = "BASE", page_size: int = 100) -> list[VirtualToken]:
        """Tokens that recently graduated (``AVAILABLE`` status). Always a list.

        These tokens have real DEX liquidity (post-graduation): they join
        the STANDARD absorption pipeline (85% VC), not the bonding niche —
        see ``services/launchpad_discovery.py``.

        ``not is_in_bonding`` filter applied CLIENT-SIDE after receiving the
        response: real diagnostic (11/07, direct network access to
        api.virtuals.io) — the server-side ``filters[status]=…`` filter of
        ``build_graduated_url`` is ignored regardless of the value tested
        (``AVAILABLE``, ``SENTIENT``, ``GRADUATED``, ``INITIALIZED`` or a
        bogus value all return the same unfiltered list, sorted by creation
        date — never a real list of graduates). Without this client-side
        filter, a token still ``UNDERGRAD`` would slip into the "graduated"
        index and wrongly join the STANDARD pipeline — see the detailed
        comment above ``_VIRTUAL_RAISED_KEYS``.
        """
        try:
            url = build_graduated_url(chain=chain, page_size=page_size)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return []
            items = data.get("data")
            if not isinstance(items, list):
                return []
            tokens: list[VirtualToken] = []
            for item in items:
                token = parse_virtual(item)
                if token is not None and not is_in_bonding(token):
                    tokens.append(token)
            return tokens
        except Exception as exc:  # ultimate degradation: never an outgoing exception
            logger.info("virtuals: fetch_graduated unexpected failure — %s", exc)
            return []

    async def fetch_virtual(self, virtual_id: object) -> VirtualToken | None:
        """A token's detail by Strapi id. ``None`` on error (never an exception)."""
        try:
            url = build_token_url(virtual_id)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return None
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            return parse_virtual(payload)
        except Exception as exc:
            logger.info("virtuals: fetch_virtual unexpected failure — %s", exc)
            return None

    async def fetch_by_address(self, token_address: str, chain: str = "BASE") -> VirtualToken | None:
        """Virtuals token by contract address (what `/vc <contract>` receives,
        never the internal Strapi id). ``None`` on error or absence — never
        an exception.

        Tries ``tokenAddress`` first (graduated token). If nothing matches,
        retries via ``preToken`` (token still in bonding -- ``tokenAddress``
        is always ``null`` there, see ``build_token_by_pretoken_url``). A
        second network call only in this fallback case, never on the happy
        path."""
        try:
            url = build_token_by_address_url(token_address, chain=chain)
            data, error = await self._get_json(url)
            if error is None and isinstance(data, dict):
                items = data.get("data")
                if isinstance(items, list) and items:
                    return parse_virtual(items[0])

            url = build_token_by_pretoken_url(token_address, chain=chain)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return None
            items = data.get("data")
            if not isinstance(items, list) or not items:
                return None
            return parse_virtual(items[0])
        except Exception as exc:
            logger.info("virtuals: fetch_by_address unexpected failure — %s", exc)
            return None


virtuals_client = VirtualsClient()
