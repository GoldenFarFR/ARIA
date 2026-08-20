"""Read-only RugCheck.xyz client -- second security opinion for Solana tokens
that GoPlus doesn't yet cover (#207, 07/18), PLUS a richer shadow-only risk
snapshot for solana_pump_shadow.py (16/08, see ``get_token_report`` below).

Context verified live (real curl, not a guess): the momentum pipeline
(`momentum_entry._check_honeypot`) cautiously rejects (fail-closed) any token
for which GoPlus has NO data at all -- deliberately kept behavior (explicit
operator decision, 07/17: "Solana is a dangerous market ... she must stick to
safe tokens"). But on 3 freshly-launched Solana tokens (pump.fun, discovered
via the same DexScreener feed as the momentum pipeline): GoPlus
`token_security/solana` responds `{"code":1,"message":"OK","result":null}` on
all 3 -- not an outage, a genuine coverage gap. ARIA was therefore rejecting
these candidates FOR LACK OF DATA, never because a danger signal was confirmed.

RugCheck.xyz (public API, free, NO key required -- verified live, HTTP 200
with no authentication) has real coverage on these same 3 tokens, and on one
of them detected "Creator history of rugged tokens" (level "danger") -- a
signal that GoPlus, with its pure contract scan (honeypot/mint/freeze),
structurally cannot see (the CREATOR's history, not the contract's).
COMPLEMENTARY angle, never a GoPlus replacement.

Doctrine (never relaxed, applied in `momentum_entry._check_honeypot`): this
client is a SECOND OPINION, consulted ONLY when GoPlus explicitly has NO data
(`TokenSecurity.no_data=True`), never on a real GoPlus network outage (the
existing fail-closed stays unchanged in that case), and never on any chain
other than Solana (GoPlus already covers Base entirely, "as strict as on
Base" doctrine unchanged). A token must come back CONFIRMED clean by RugCheck
to pass -- if RugCheck itself doesn't have the data or is unavailable, the
fail-closed rejection stays unchanged (opens up coverage, never relaxes the guardrail).

Rate limit observed live (`x-rate-limit-limit`/`-remaining` headers, 07/18):
15 requests, undocumented window -- cautiously treated as 1 minute, throttle
set to ~4.5s/request to stay well under this cap. Re-confirmed live 16/08
(same header, same undocumented window) -- ``get_token_report`` below reuses
this SAME shared throttle rather than a second independent one, same "one
coordination point per provider" doctrine already applied elsewhere in this
codebase (see geckoterminal.py's shared adaptive throttle).

**16/08 addition -- shadow-only risk snapshot (``get_token_report``)**:
solana_pump_shadow.py logs (never blocks on) a richer per-signal snapshot
(score, named risks, top-holder %, creator wallet) via the fuller
``/tokens/{mint}/report`` endpoint -- distinct from ``get_report_summary``'s
lightweight ``/report/summary`` (sufficient for the real pipeline's simple
boolean gate, but missing topHolders/creator). Explicit operator decision
after 2-3 live comparison tests against the shadow's own already-closed
positions: RugCheck correctly flagged the one confirmed real LP-pull case
this session (top holder 33%, score 46, "High holder concentration") but ALSO
flagged two genuinely profitable positions (score 31, "Low Liquidity",
top holder ~80% -- likely the pool itself counted as a holder on a freshly
launched token, never confirmed either way) -- SHADOW MODE ONLY, never used
to filter/block a signal, logged purely for later correlation analysis.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rugcheck.xyz/v1"
UNAVAILABLE = "donnée RugCheck indisponible"

_MIN_INTERVAL_S = 4.5  # ~13/min, under the observed 15/min cap (unknown window)
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()


@dataclass
class RugCheckResult:
    """Solana second security opinion. `rugged`/`danger_risks` stay None/[] if
    RugCheck doesn't have the data -- never an invented True/False."""

    address: str
    available: bool = False
    error: str | None = None
    rugged: bool | None = None
    score_normalised: float | None = None
    danger_risks: list[str] = field(default_factory=list)

    @property
    def confirmed_clean(self) -> bool:
        """True only if RugCheck responded AND found neither `rugged` nor a
        "danger"-level risk -- never inferred from missing data (RugCheck
        unavailable => available=False => confirmed_clean=False, fail-closed)."""
        return self.available and self.rugged is False and not self.danger_risks


@dataclass
class RugCheckReport:
    """16/08 -- shadow-only risk snapshot, see module docstring. Distinct
    from ``RugCheckResult`` (the real pipeline's simple pass/fail gate):
    this carries the raw score/risks/top-holder/creator for logging and
    later correlation analysis, never a boolean verdict."""

    score_normalised: int | None = None
    risks: list[str] = field(default_factory=list)
    top_holder_pct: float | None = None
    # The deployer wallet -- neither GeckoTerminal nor DexPaprika expose this
    # field (verified live 16/08), RugCheck is the only one of the three
    # that does. Banked for a future per-creator behavior analysis, not
    # built yet.
    creator: str | None = None
    # 20/08 -- the top holders as (pct, owner) pairs, most-held first.
    # Added after a real finding: on a Solana fresh launch `top_holder_pct` is
    # almost always the POOL/bonding curve itself, not a wallet (verified live
    # on 6 real tokens, 5 of 6 had topHolders[0].owner == the pool address,
    # with the second real holder at 0.01-0.39%). So `top_holder_pct` measures
    # how much of the supply is still UNSOLD in the curve -- the inverse of
    # traction -- and NOT the holder concentration its name suggests. Callers
    # that know the pool address can now exclude it and read the real
    # concentration among actual wallets; the scalar above is kept unchanged
    # so every existing caller keeps its current behavior.
    top_holders: list[tuple[float, str | None]] = field(default_factory=list)
    available: bool = True
    error: str | None = None


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        elapsed = time.monotonic() - _last_call_at
        if elapsed < _MIN_INTERVAL_S:
            await asyncio.sleep(_MIN_INTERVAL_S - elapsed)
        _last_call_at = time.monotonic()


async def _get_json(url: str) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same policy as dexscreener.py."""
    await _throttle()
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("rugcheck: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("rugcheck: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("rugcheck: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Includes the case of an unknown mint/never scanned by RugCheck
            # (verified live: 400 "invalid length" on a malformed address -- a
            # valid but never-indexed mint would fall into this same generic
            # branch, never confused with a confirmed "clean").
            logger.warning("rugcheck: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def get_report_summary(mint: str) -> RugCheckResult:
    """Second security opinion for a Solana mint --
    `/tokens/{mint}/report/summary` (lightweight endpoint, sufficient for a
    boolean gate: `rugged` + "danger"-level risks). Best-effort, never
    blocking outside its explicit use in `momentum_entry._check_honeypot`."""
    addr = (mint or "").strip()
    if not addr:
        return RugCheckResult(address=addr, available=False, error="adresse vide")

    data, error = await _get_json(f"{BASE_URL}/tokens/{addr}/report/summary")
    if error is not None:
        return RugCheckResult(address=addr, available=False, error=error)
    if not isinstance(data, dict):
        return RugCheckResult(address=addr, available=False, error=UNAVAILABLE)

    risks = data.get("risks")
    danger_risks = (
        [
            str(r.get("name") or "risque non nommé")
            for r in risks
            if isinstance(r, dict) and str(r.get("level") or "").lower() == "danger"
        ]
        if isinstance(risks, list)
        else []
    )

    rugged = data.get("rugged")
    score = data.get("score_normalised")

    return RugCheckResult(
        address=addr,
        available=True,
        error=None,
        rugged=bool(rugged) if isinstance(rugged, bool) else None,
        score_normalised=float(score) if isinstance(score, (int, float)) else None,
        danger_risks=danger_risks,
    )


async def get_token_report(mint_address: str) -> RugCheckReport:
    """16/08 -- shadow-only richer risk snapshot for solana_pump_shadow.py,
    see module docstring. Uses the SAME shared throttle as
    ``get_report_summary`` (``_get_json`` above) -- never a second
    independent rate limiter for the same provider. Never raises."""
    addr = (mint_address or "").strip()
    if not addr:
        return RugCheckReport(available=False, error="adresse vide")

    data, error = await _get_json(f"{BASE_URL}/tokens/{addr}/report")
    if error is not None:
        return RugCheckReport(available=False, error=error)
    if not isinstance(data, dict):
        return RugCheckReport(available=False, error=UNAVAILABLE)

    score_normalised = data.get("score_normalised")
    score_normalised = int(score_normalised) if isinstance(score_normalised, (int, float)) else None

    risks: list[str] = []
    raw_risks = data.get("risks")
    if isinstance(raw_risks, list):
        for r in raw_risks:
            if isinstance(r, dict) and isinstance(r.get("name"), str):
                risks.append(r["name"])

    top_holder_pct: float | None = None
    top_holders: list[tuple[float, str | None]] = []
    raw_holders = data.get("topHolders")
    if isinstance(raw_holders, list) and raw_holders:
        first = raw_holders[0]
        if isinstance(first, dict) and isinstance(first.get("pct"), (int, float)):
            top_holder_pct = float(first["pct"])
        for h in raw_holders:
            if isinstance(h, dict) and isinstance(h.get("pct"), (int, float)):
                owner = h.get("owner")
                top_holders.append((float(h["pct"]), owner if isinstance(owner, str) else None))

    raw_creator = data.get("creator")
    creator = raw_creator if isinstance(raw_creator, str) and raw_creator else None

    return RugCheckReport(
        score_normalised=score_normalised, risks=risks,
        top_holder_pct=top_holder_pct, top_holders=top_holders, creator=creator,
        available=True, error=None,
    )
