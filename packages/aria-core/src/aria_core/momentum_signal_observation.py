"""Read-after-decide observation layer for the momentum pipeline
(specs/016-momentum-signal-observation-layer). Captures, for every
candidate ``momentum_entry.evaluate_momentum_entry`` evaluates -- bought
AND rejected -- the three already-existing signal families (on-chain,
chart, social) as three separate blocks, the pipeline's real decision, and
forward price performance at five fixed horizons.

Deliberately NOT a new decision score: this module NEVER combines the
three families into one number, NEVER reads a value that momentum_entry.py
itself did not already compute for this specific decision, and NEVER
influences that decision -- ``capture_observation`` is called strictly
AFTER the real decision is made, in a best-effort try/except the caller
cannot observe (see ``evaluate_momentum_entry``'s wrapper in
momentum_entry.py). Full design rationale: specs/016-momentum-signal-
observation-layer/research.md and data-model.md.

Two tables, same pattern as ``dex_score_log.py``:
- ``momentum_signal_observation`` -- append-only, one row per evaluation.
- ``momentum_signal_forward_performance`` -- 5 rows per observation,
  created ``pending`` at insert time, the only mutable part of this design
  (updated in place as each horizon resolves).

A signal this module never got a real value for is recorded as
``{"available": false, "reason": "..."}`` -- never a silent zero/neutral
default (data-model.md's Validation rules). ``composite_pillars``/
``composite_score`` come from ``dex_composite_score.py`` but are only ever
present in ``core_result`` when the pipeline reached that stage (the
"BUY after conviction_research" branch) -- for every earlier rejection
they are correctly absent, not hidden. Two sub-signals named in the
target architecture (``holder_concentration_top10_pct``,
``smart_money_rescue_triggered``) are not currently exposed on
``evaluate_momentum_entry``'s return value at all -- they are therefore
recorded as ``not_instrumented`` (our pipeline's gap), never as a gate
outcome, until a future change (out of this feature's scope, FR-009)
surfaces them there; this module never reaches into momentum_entry.py's
internals to fetch them itself.

Unavailability vocabulary (obs-v2, 02/09) -- four reasons, never one:
``gate_not_reached`` (evaluation stopped upstream), ``computed_no_value``
(the stage ran, no value exists for this token), ``not_instrumented``
(the pipeline never forwards this field), ``excluded_by_design``
(deliberately withheld -- see ``technical_align_score``). Collapsing them,
as v1 did, makes a replay read our own blind spots as facts about a token.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services import dexscreener

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Bump only when the SHAPE of what this module captures changes (a
# sub-signal added/removed/restructured) -- never for an unrelated
# momentum_entry.py threshold tweak. Deliberate, explicit, documented in
# the commit that changes it (data-model.md's "signal_version scheme").
# obs-v2 (02/09): the unavailability vocabulary changed shape, so rows written
# before and after CANNOT be pooled in a replay. v1 collapsed four different
# meanings into "not_evaluated_this_gate"; v2 separates gate_not_reached /
# computed_no_value / not_instrumented / excluded_by_design. Bumping the
# version is the whole point of the column -- a replay that mixes the two
# would read v1's silence as v2's "computed, nothing found".
SIGNAL_VERSION = "obs-v2"

_HORIZONS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}

_OBSERVATION_DDL = """
CREATE TABLE IF NOT EXISTS momentum_signal_observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract TEXT NOT NULL,
    chain TEXT NOT NULL,
    symbol TEXT,
    decision_ts TEXT NOT NULL,
    decision_action TEXT NOT NULL,
    decision_reason TEXT,
    reference_price_usd REAL,
    signal_version TEXT NOT NULL,
    onchain_json TEXT NOT NULL,
    chart_json TEXT NOT NULL,
    social_json TEXT NOT NULL
)
"""
_FORWARD_DDL = """
CREATE TABLE IF NOT EXISTS momentum_signal_forward_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL,
    horizon TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    price_usd REAL,
    pct_change_vs_reference REAL,
    resolved_at TEXT,
    unavailable_reason TEXT
)
"""


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_OBSERVATION_DDL)
        await db.execute(_FORWARD_DDL)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mso_contract_chain_ts "
            "ON momentum_signal_observation (contract, chain, decision_ts)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mso_signal_version "
            "ON momentum_signal_observation (signal_version)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_msfp_status_due "
            "ON momentum_signal_forward_performance (status, due_at)"
        )
        await db.commit()


# Why an absent value needs FOUR distinct reasons and not one (02/09).
#
# ``not_evaluated_this_gate`` used to cover every absence, which made the four
# cases below indistinguishable in storage -- and they mean opposite things to
# anyone replaying this data later. The operator's standing principle applies
# directly: an absence of signal is not a negative signal. A replay that reads
# "R/R absent" as "R/R was bad" draws the wrong conclusion; one that reads
# "never instrumented" as "not computable for this token" blames the token for
# a gap in our own code.
#
# The distinction is derived MECHANICALLY, never from a maintained list of
# reason codes:
#   - the technical analysis ran iff ``core`` CONTAINS the "gp_low" key (the
#     key is written by every path that reaches ``detect_entry``, with None as
#     a legitimate value) -- so key presence proves execution, and its value
#     reports the outcome;
#   - a key present but None means the computation ran and produced nothing;
#   - a key absent while the analysis DID run means this code path does not
#     forward that field yet -- our gap, not the token's.
GATE_NOT_REACHED = "gate_not_reached"          # evaluation stopped upstream (blacklist, honeypot...)
COMPUTED_NO_VALUE = "computed_no_value"        # the gate ran; no value exists for this token
NOT_INSTRUMENTED = "not_instrumented"          # the pipeline never forwards this field (our gap)
EXCLUDED_BY_DESIGN = "excluded_by_design"      # deliberately omitted (see technical_align_score)


def _avail(value: object, data_timestamp: str) -> dict:
    return {"available": True, "value": value, "data_timestamp": data_timestamp}


def _unavail(reason: str) -> dict:
    return {"available": False, "reason": reason}


def _absent(core: dict, key: str, *, ran: bool) -> dict:
    """The reason a field carries no value, derived from the dict's own shape.

    ``ran`` says whether the producing stage executed at all. Given that, the
    presence of ``key`` in ``core`` separates "computed, nothing to report"
    from "this path never forwards it".
    """
    if not ran:
        return _unavail(GATE_NOT_REACHED)
    return _unavail(COMPUTED_NO_VALUE if key in core else NOT_INSTRUMENTED)


def _build_onchain_block(core: dict, decision_ts: str) -> dict:
    score = core.get("dex_security_score")
    breakdown = core.get("dex_security_breakdown")
    return {
        "composite_score": (
            _avail(score, decision_ts) if score is not None
            else _absent(core, "dex_security_score", ran="dex_security_score" in core)
        ),
        "composite_pillars": (
            _avail(breakdown, decision_ts) if breakdown is not None
            else _absent(core, "dex_security_breakdown", ran="dex_security_breakdown" in core)
        ),
        # NOT_INSTRUMENTED, not "not evaluated": these two are computed inside
        # evaluate_hard_gates but never placed on its return value, so they are
        # absent for EVERY token on EVERY path. Measured 02/09: the on-chain
        # block was available on 0 of 895 observations. Recording that as a
        # gate outcome would tell a replay the data was uncomputable for these
        # tokens -- it is our pipeline that drops it. Surfacing them is a
        # separate change; this only stops mislabelling the gap.
        "holder_concentration_top10_pct": _unavail(NOT_INSTRUMENTED),
        "smart_money_rescue_triggered": _unavail(NOT_INSTRUMENTED),
    }


def _build_chart_block(core: dict, decision_ts: str) -> dict:
    gp_low = core.get("gp_low")
    gp_high = core.get("gp_high")
    rsi_gap = core.get("rsi_gap")
    rr = core.get("rr")
    align_score = core.get("align_score")
    volume_confirmed = core.get("volume_confirmed")
    rvol_multiple = core.get("rvol_multiple")
    regime = core.get("regime")

    # Did the technical stage run at all? The "gp_low" KEY is written by every
    # path that reaches detect_entry (with None as a legitimate value), so its
    # presence -- not its value -- is the proof. Never a list of reason codes
    # to keep in sync with momentum_entry's control flow.
    ran = "gp_low" in core

    golden_pocket_present = gp_low is not None and gp_high is not None
    return {
        "golden_pocket_present": (
            _avail(golden_pocket_present, decision_ts)
            if (gp_low is not None or gp_high is not None or rr is not None)
            else _absent(core, "gp_low", ran=ran)
        ),
        "rsi_divergence_present": (
            _avail(rsi_gap is not None, decision_ts) if rsi_gap is not None
            else _absent(core, "rsi_gap", ran=ran)
        ),
        "risk_reward_ratio": (
            _avail(rr, decision_ts) if rr is not None else _absent(core, "rr", ran=ran)
        ),
        # NEVER _absent(): align_score's absence is a deliberate decision, not
        # a measurement. momentum_entry withholds it on the no_entry_signal
        # path because risk_guard reads a MISSING align_score as "caller
        # doesn't support this signal" and falls back to its 5% sizing tier
        # (item #221) -- forwarding it would turn an observability field into
        # a trading decision. Recording that as "computed_no_value" would tell
        # a future replay the analysis found nothing, which is false.
        "technical_align_score": (
            _avail(align_score, decision_ts) if align_score is not None
            else _unavail(EXCLUDED_BY_DESIGN)
        ),
        "rvol_confirmed": (
            _avail({"confirmed": volume_confirmed, "multiple": rvol_multiple}, decision_ts)
            if volume_confirmed is not None
            else _absent(core, "volume_confirmed", ran=ran)
        ),
        "market_regime": (
            _avail(regime, decision_ts) if regime else _absent(core, "regime", ran=ran)
        ),
    }


async def _read_signal_cascade_convergence(contract: str, chain: str) -> list[dict]:
    """Passive read-only lookup -- triggers no new scan, no write. First
    by-contract reader for this table (research.md §4: none existed
    before this feature). Best-effort: returns [] on any failure, never
    raises into the caller."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT source, signal, accelerating, detail, recorded_at "
                "FROM signal_cascade_convergence WHERE LOWER(contract) = LOWER(?) AND chain = ?",
                (contract, chain),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 -- passive read, never blocking
        logger.info("momentum_signal_observation: signal_cascade_convergence read failed (%s)", exc)
        return []


async def _build_social_block(core: dict, contract: str, chain: str, decision_ts: str) -> dict:
    potential_score = core.get("potential_score")
    cascade_rows = await _read_signal_cascade_convergence(contract, chain)
    return {
        "conviction_research_score": (
            _avail(potential_score, decision_ts) if potential_score is not None
            else _absent(core, "potential_score", ran="potential_score" in core)
        ),
        "signal_cascade_convergence": (
            _avail(
                [
                    {"source": r["source"], "signal": r["signal"], "accelerating": bool(r["accelerating"]), "detail": r["detail"]}
                    for r in cascade_rows
                ],
                cascade_rows[0]["recorded_at"],
            )
            if cascade_rows
            else _unavail("not_yet_scanned")
        ),
        # radar_x.py has no persisted state to read passively (verified
        # this session, research.md §4) -- always unavailable in this
        # feature's baseline, never a live scan triggered from here (would
        # violate FR-008: never add latency/dependency to the decision path).
        "radar_x_signal": _unavail("no_persisted_state"),
    }


async def capture_observation(contract: str, chain: str, core_result) -> None:
    """Called exactly once, right after ``_evaluate_momentum_entry_core``
    returns, from ``evaluate_momentum_entry``'s wrapper. Best-effort: any
    failure is logged and swallowed, never raised into the caller -- same
    posture as ``narrative_signal_shadow.record_evaluation`` at the same
    call site."""
    try:
        decision_ts = datetime.now(timezone.utc).isoformat()
        core = core_result if isinstance(core_result, dict) else {}

        # 02/09 -- found in production data: `evaluate_momentum_entry` returns
        # None when no tradeable pair exists for the token at all (its `best is
        # None` path). Recording that as "HOLD" turned an ABSENCE OF DATA into
        # what reads like a decision the pipeline made -- exactly the confusion
        # this whole layer exists to prevent (a HOLD means "looked, declined";
        # None means "could not even look"). 25 of the first 120 production
        # observations were this case, indistinguishable from a real HOLD.
        # Rows captured before this fix keep the ambiguous "HOLD"/NULL-reason
        # shape -- deliberately not rewritten (append-only).
        if core_result is None:
            decision_action, decision_reason = "NO_CANDIDATE_DATA", "no_tradeable_pair_found"
        else:
            decision_action = core.get("action") or "UNKNOWN"
            decision_reason = core.get("hold_reason") or (
                "; ".join(core.get("reasons") or []) if core.get("reasons") else None
            )
        reference_price_usd = core.get("price")

        onchain_json = json.dumps(_build_onchain_block(core, decision_ts))
        chart_json = json.dumps(_build_chart_block(core, decision_ts))
        social_json = json.dumps(await _build_social_block(core, contract, chain, decision_ts))

        await _ensure_tables()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "INSERT INTO momentum_signal_observation "
                "(contract, chain, symbol, decision_ts, decision_action, decision_reason, "
                " reference_price_usd, signal_version, onchain_json, chart_json, social_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    contract.lower(), chain, core.get("symbol"), decision_ts, decision_action,
                    decision_reason, reference_price_usd, SIGNAL_VERSION, onchain_json, chart_json, social_json,
                ),
            )
            observation_id = cur.lastrowid
            decision_dt = datetime.fromisoformat(decision_ts)
            for horizon, delta in _HORIZONS.items():
                due_at = (decision_dt + delta).isoformat()
                await db.execute(
                    "INSERT INTO momentum_signal_forward_performance "
                    "(observation_id, horizon, due_at, status) VALUES (?, ?, ?, 'pending')",
                    (observation_id, horizon, due_at),
                )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the real decision
        logger.info("momentum_signal_observation: capture_observation failed (%s)", exc)


async def resolve_due_forward_prices() -> int:
    """Cheapest-first funnel (Resource-Engineering doctrine): a local SQL
    filter before any network call, deduplicated by token, reusing the
    already-throttled ``dexscreener`` client -- never a new poller, never
    a fabricated price (research.md §3). Returns the count of rows
    resolved this cycle. Best-effort: never raises."""
    await _ensure_tables()
    now = datetime.now(timezone.utc)
    resolved = 0
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT f.id, f.observation_id, o.contract, o.chain, o.reference_price_usd "
                "FROM momentum_signal_forward_performance f "
                "JOIN momentum_signal_observation o ON o.id = f.observation_id "
                "WHERE f.status = 'pending' AND f.due_at <= ?",
                (now.isoformat(),),
            )
            due_rows = [dict(r) for r in await cur.fetchall()]

        if not due_rows:
            return 0

        # Rows with no reference price never needed a network call at all.
        no_ref_ids = [r["id"] for r in due_rows if r["reference_price_usd"] is None]
        priceable_rows = [r for r in due_rows if r["reference_price_usd"] is not None]

        price_by_token: dict[tuple[str, str], float | None] = {}
        for contract, chain in {(r["contract"], r["chain"]) for r in priceable_rows}:
            try:
                pairs = await dexscreener.fetch_token_pairs(contract, chain=chain)
                price_by_token[(contract, chain)] = pairs[0].price_usd if pairs else None
            except Exception as exc:  # noqa: BLE001 -- one token's failure never blocks the batch
                logger.info("momentum_signal_observation: price lookup failed for %s/%s (%s)", contract, chain, exc)
                price_by_token[(contract, chain)] = None

        resolved_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            for row_id in no_ref_ids:
                await db.execute(
                    "UPDATE momentum_signal_forward_performance "
                    "SET status='unavailable', unavailable_reason='no_reference_price', resolved_at=? WHERE id=?",
                    (resolved_at, row_id),
                )
                resolved += 1
            for r in priceable_rows:
                price = price_by_token.get((r["contract"], r["chain"]))
                if price is None:
                    await db.execute(
                        "UPDATE momentum_signal_forward_performance "
                        "SET status='unavailable', unavailable_reason='token_unpriceable', resolved_at=? WHERE id=?",
                        (resolved_at, r["id"]),
                    )
                else:
                    pct_change = (price - r["reference_price_usd"]) / r["reference_price_usd"] * 100
                    await db.execute(
                        "UPDATE momentum_signal_forward_performance "
                        "SET status='measured', price_usd=?, pct_change_vs_reference=?, resolved_at=? WHERE id=?",
                        (price, pct_change, resolved_at, r["id"]),
                    )
                resolved += 1
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocking
        logger.info("momentum_signal_observation: resolve_due_forward_prices failed (%s)", exc)
    return resolved


async def list_recent(limit: int = 50) -> list[dict]:
    """Most recent observations first -- read helper for tests/ad hoc queries."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM momentum_signal_observation ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ("onchain_json", "chart_json", "social_json"):
            try:
                d[key.replace("_json", "")] = json.loads(d[key])
            except (TypeError, ValueError):
                d[key.replace("_json", "")] = {}
        out.append(d)
    return out


async def forward_performance_for(observation_id: int) -> list[dict]:
    """Read helper: the 5 forward-performance rows for one observation."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM momentum_signal_forward_performance WHERE observation_id = ? ORDER BY due_at",
            (observation_id,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
