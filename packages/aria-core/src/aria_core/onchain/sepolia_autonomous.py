"""Autonomous Sepolia cycle — ARIA decides AND executes alone, no Telegram
click, on testnet ONLY. Explicit and repeated operator decision (08/07): "the
Sepolia test needs to be the hardest test she's ever passed, so that once she
reaches the real market, it's simple for her to say yes or no" — Sepolia
specifically serves to observe UNFILTERED behavior (hesitation, errors,
degradation) before anything reaches real capital.

Structural difference from the rest of the onchain dome: this module NEVER
calls wallet_guard.escalate_spend/resolve_spend — a completely separate path,
so that the shared Telegram guardrail (used by everything that will one day
touch real capital) stays intact and unmodified. Nothing here applies to
mainnet: send_anchor_transaction locks chain_id=84532
(aria_core.onchain.sepolia_wallet), and this module adds its OWN lock
(sepolia_autonomous_enabled) on top of sepolia_wallet_enabled.

Triple gate (defense in depth), all three must be true, none active by
default:
  1. ARIA_SEPOLIA_WALLET_ENABLED   — the Sepolia wallet exists (key readable).
  2. ARIA_SEPOLIA_AUTONOMOUS_ENABLED — autonomy (no Telegram click) is armed.
  3. ARIA_ONCHAIN_ANCHOR_ENABLED + ARIA_LEDGER_ADDRESS — a ledger contract is configured.

Kill-switch: every cycle re-reads outgoing_pause.is_paused() — the existing
Telegram /stop freezes this cycle exactly like it freezes tweets/ACP/scheduled
jobs. No parallel mechanism.

Sizing (Kelly) and the decision use REAL market data (same VC analysis client
as paper_trader/weekly_training) — but Sepolia has no real DEX pool for an
arbitrary Base token (testnet, no indexed liquidity). The execution artefact
is therefore an autonomous onchain anchoring of the decision record (real
signature, real gas, real nonce, real RPC failures) — exactly what a testnet
allows validating according to the operator's own reasoning: "a software
engineering test, not a trading-strategy validation." The Kelly amount is
computed on a fictional rehearsal capital (REHEARSAL_NOTIONAL_USD) so that the
sizing discipline itself is rehearsed, even though no real ETH changes hands.

Telemetry: EVERY cycle is logged (BUY, HOLD, ERROR, SKIP) — never only the
successes. This is the operator's explicit request: "if she hesitates I want
to know, if she screws up I want to know, if she gets fed up I want to know."
Honestly translated into measurable telemetry: decision latency (hesitation =
abnormally slow vs. its own recent average), raw errors, and a local circuit
breaker that arms after consecutive failures then cleanly re-evaluates on the
next cycle.

Test swap (09/07, explicit operator decision "real swap on Sepolia, test
asset"): on a BUY decision, in addition to the decision anchor above, an
INDEPENDENT real swap attempt (wrap/approve/exactInputSingle,
``sepolia_wallet.send_test_swap_transaction``) is logged if
``ARIA_SEPOLIA_SWAP_ENABLED``. Small fixed amount (``TEST_SWAP_AMOUNT_WEI``),
never sized by Kelly — this swap does NOT concern the actually-analyzed
candidate token (nonexistent on this testnet) but the configured test pair: it
validates the execution mechanism, not a market thesis. A swap failure never
erases the success of the decision anchor, and vice versa — two independent
artefacts from the same cycle.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

REHEARSAL_NOTIONAL_USD = 10_000.0   # fictional rehearsal capital — never real funds
KELLY_SAFETY_FACTOR = 0.5           # half-Kelly (standard tempering vs. full-Kelly, too volatile)
KELLY_CAP = 0.20                    # hard cap, even if the raw computation exceeds it
KELLY_MIN_SAMPLE = 5                # below this number of closed BUYs, insufficient sample -> conservative fraction
KELLY_FALLBACK_FRACTION = 0.01

MAX_AUTONOMOUS_TX_PER_DAY = 12      # sanity cap (RPC/faucet), not a financial risk cap
CANDIDATE_COOLDOWN_HOURS = 6        # doesn't re-analyze the same contract before this delay (pool rotation)
LATENCY_BASELINE_SAMPLE = 20
LATENCY_HESITATION_MULTIPLE = 2.0
CONSECUTIVE_ERROR_CIRCUIT_BREAKER = 4

TEST_SWAP_AMOUNT_WEI = 200_000_000_000_000  # ~0.0002 testnet ETH, fixed mechanical amount — never Kelly

_LOG_COLS = (
    "cycle_at", "contract", "symbol", "decision", "reasoning_excerpt",
    "latency_ms", "hesitant", "kelly_fraction", "kelly_size_usd", "tx_hash", "error", "outcome",
    "swap_tx", "swap_error",
)
_ADDED_LOG_COLS = ("swap_tx", "swap_error")


def sepolia_autonomous_enabled() -> bool:
    """Dedicated gate, on top of sepolia_wallet_enabled: the wallet can exist
    without autonomy being armed — two separate decisions, two separate
    flags."""
    from aria_core.onchain.sepolia_wallet import sepolia_wallet_enabled

    if not sepolia_wallet_enabled():
        return False
    return os.environ.get("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def kelly_fraction(
    win_rate: float | None, avg_win_pct: float | None, avg_loss_pct: float | None,
) -> float:
    """Kelly fraction (half-Kelly, capped) from the REAL BUY calibration
    statistics (vc_predictions.compute_metrics). Never a fabricated confidence:
    missing or degenerate data (no positive edge) -> fixed conservative
    fraction."""
    if win_rate is None or avg_win_pct is None or avg_loss_pct is None:
        return KELLY_FALLBACK_FRACTION
    if avg_win_pct <= 0 or avg_loss_pct >= 0:
        return KELLY_FALLBACK_FRACTION
    b = avg_win_pct / abs(avg_loss_pct)
    if b <= 0:
        return KELLY_FALLBACK_FRACTION
    raw = win_rate - (1.0 - win_rate) / b
    tempered = raw * KELLY_SAFETY_FACTOR
    return max(0.0, min(KELLY_CAP, tempered))


async def _kelly_fraction_from_history(*, min_sample: int = KELLY_MIN_SAMPLE) -> tuple[float, dict]:
    """Reads the real calibration (vc_predictions.metrics) and derives the Kelly fraction from it."""
    from aria_core import vc_predictions

    m = await vc_predictions.metrics()
    buy_count = m.get("buy_count", 0)
    if buy_count < min_sample:
        return KELLY_FALLBACK_FRACTION, {
            "sample": buy_count, "sufficient": False, "hit_rate": m.get("hit_rate"),
        }
    f = kelly_fraction(m.get("hit_rate"), m.get("avg_win_pct"), m.get("avg_loss_pct"))
    return f, {
        "sample": buy_count, "sufficient": True, "hit_rate": m.get("hit_rate"),
        "avg_win_pct": m.get("avg_win_pct"), "avg_loss_pct": m.get("avg_loss_pct"),
    }


def _num(v) -> float | None:
    """Defensive parse of a price possibly formatted '$1,234.5' -> float, or
    None (same logic as paper_trader/weekly_training/simulate_lifecycle — no
    shared client to duplicate here, just a pure utility already repeated
    identically elsewhere)."""
    try:
        if v is None:
            return None
        s = str(v).replace("$", "").replace(",", "").strip().split()[0]
        return float(s)
    except (ValueError, IndexError, TypeError):
        return None


async def _default_analyzer(contract: str) -> dict | None:
    """Same real VC analysis as paper_trader (analyze_vc_with_context), but
    keeps the thesis (raw reasoning) for behavioral telemetry — paper_trader
    doesn't need it, this module does ("if she hesitates, if she gets it
    wrong, I want to know")."""
    from aria_core.skills.vc_analysis import analyze_vc_with_context

    result, ctx = await analyze_vc_with_context(contract)
    action = "BUY" if getattr(result, "recommandation", "") == "BUY" else "HOLD"
    price = ctx.best_pair.price_usd if ctx.best_pair else None
    target = _num(getattr(result, "cible", None)) or (ctx.ta_entry.cible if ctx.ta_entry else None)
    inval = _num(getattr(result, "invalidation", None)) or (
        ctx.ta_entry.invalidation if ctx.ta_entry else None
    )
    return {
        "action": action,
        "symbol": ctx.best_pair.base_symbol if ctx.best_pair else "",
        "price": price,
        "target": target,
        "invalidation": inval,
        "these": getattr(result, "these", None),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sepolia_autonomous_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_at TEXT NOT NULL,
                contract TEXT,
                symbol TEXT,
                decision TEXT NOT NULL,
                reasoning_excerpt TEXT,
                latency_ms REAL,
                hesitant INTEGER NOT NULL DEFAULT 0,
                kelly_fraction REAL,
                kelly_size_usd REAL,
                tx_hash TEXT,
                error TEXT,
                outcome TEXT NOT NULL
            )
            """
        )
        cursor = await db.execute("PRAGMA table_info(sepolia_autonomous_log)")
        existing = {row[1] for row in await cursor.fetchall()}
        for col in _ADDED_LOG_COLS:
            if col not in existing:
                await db.execute(f"ALTER TABLE sepolia_autonomous_log ADD COLUMN {col} TEXT")
        await db.commit()


async def _insert_log(db: aiosqlite.Connection, **fields) -> None:
    """``hesitant`` is NOT NULL (0/1 boolean column): always coerced, never
    explicit NULL (SQLite only applies the DEFAULT if the column is omitted,
    not if NULL is provided explicitly)."""
    values = tuple(
        int(bool(fields.get(c))) if c == "hesitant" else fields.get(c) for c in _LOG_COLS
    )
    placeholders = ", ".join("?" for _ in _LOG_COLS)
    await db.execute(
        f"INSERT INTO sepolia_autonomous_log ({', '.join(_LOG_COLS)}) VALUES ({placeholders})",
        values,
    )
    await db.commit()


async def _recent_latencies(db: aiosqlite.Connection, limit: int = LATENCY_BASELINE_SAMPLE) -> list[float]:
    cursor = await db.execute(
        "SELECT latency_ms FROM sepolia_autonomous_log WHERE outcome = 'ok' "
        "AND latency_ms IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [r[0] for r in rows if r[0] is not None]


async def _consecutive_errors(db: aiosqlite.Connection) -> int:
    """Counts the most recent consecutive errors. Stops at the first
    non-error result -> the circuit breaker cleanly re-evaluates from the very
    next cycle after tripping (the SKIP it logs itself is not an "error")."""
    cursor = await db.execute("SELECT outcome FROM sepolia_autonomous_log ORDER BY id DESC LIMIT 50")
    rows = await cursor.fetchall()
    count = 0
    for (outcome,) in rows:
        if outcome == "error":
            count += 1
        else:
            break
    return count


async def _recently_decided_contracts(db: aiosqlite.Connection, *, hours: int = CANDIDATE_COOLDOWN_HOURS) -> set[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    cursor = await db.execute(
        "SELECT DISTINCT contract FROM sepolia_autonomous_log WHERE cycle_at >= ? AND contract IS NOT NULL",
        (cutoff,),
    )
    rows = await cursor.fetchall()
    return {r[0] for r in rows if r[0]}


async def _todays_tx_count(db: aiosqlite.Connection) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM sepolia_autonomous_log WHERE cycle_at >= ? AND tx_hash IS NOT NULL",
        (cutoff,),
    )
    row = await cursor.fetchone()
    return int(row[0] or 0)


async def run_autonomous_cycle(
    *,
    candidates=None,
    analyzer=None,
    anchor_sender=None,
    swap_sender=None,
    notifier=None,
) -> dict:
    """One round of the autonomous Sepolia rehearsal. Fail-closed at every
    stage (see the triple gate in the module docstring). Logs EVERY round —
    BUY, HOLD, ERROR, SKIP — never only the successes: this is the
    observability the operator asked for."""
    await _ensure_table()

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}
    if not sepolia_autonomous_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core.onchain.anchor import anchor_enabled, ledger_address

    contract_ledger = ledger_address()
    if not anchor_enabled() or not contract_ledger:
        return {"outcome": "skipped_no_ledger"}

    async with aiosqlite.connect(DB_PATH) as db:
        if await _consecutive_errors(db) >= CONSECUTIVE_ERROR_CIRCUIT_BREAKER:
            await _insert_log(
                db, cycle_at=_now(), decision="SKIP", outcome="circuit_breaker_open",
                reasoning_excerpt=(
                    f"{CONSECUTIVE_ERROR_CIRCUIT_BREAKER} échecs consécutifs — coupe-circuit "
                    "local armé pour ce cycle ; nouvelle tentative automatique au suivant."
                ),
            )
            if notifier:
                try:
                    await notifier(
                        "🔴 Rehearsal Sepolia autonome — coupe-circuit armé "
                        f"({CONSECUTIVE_ERROR_CIRCUIT_BREAKER} échecs consécutifs, testnet, "
                        "aucune valeur réelle). Nouvelle tentative automatique au cycle suivant."
                    )
                except Exception:  # noqa: BLE001
                    pass
            return {"outcome": "circuit_breaker_open"}

        if await _todays_tx_count(db) >= MAX_AUTONOMOUS_TX_PER_DAY:
            await _insert_log(db, cycle_at=_now(), decision="SKIP", outcome="skipped_rate_cap")
            return {"outcome": "skipped_rate_cap"}

        skip_contracts = await _recently_decided_contracts(db)
        recent_latencies = await _recent_latencies(db)

    if candidates is None:
        from aria_core.skills.candidate_ranking import top_candidates

        candidates = [c.contract for c in await top_candidates(20)]
    candidates = [c for c in candidates if c not in skip_contracts]
    if not candidates:
        async with aiosqlite.connect(DB_PATH) as db:
            await _insert_log(db, cycle_at=_now(), decision="SKIP", outcome="skipped_no_candidate")
        return {"outcome": "skipped_no_candidate"}

    contract = candidates[0]
    analyzer = analyzer or _default_analyzer

    started = time.monotonic()
    error_text: str | None = None
    sig: dict | None = None
    try:
        sig = await analyzer(contract)
    except Exception as exc:  # noqa: BLE001 — a failed analysis must be logged, never break the heartbeat
        error_text = str(exc)[:500]
    latency_ms = (time.monotonic() - started) * 1000.0

    baseline = (sum(recent_latencies) / len(recent_latencies)) if len(recent_latencies) >= 5 else None
    hesitant = bool(baseline and latency_ms > baseline * LATENCY_HESITATION_MULTIPLE)

    async with aiosqlite.connect(DB_PATH) as db:
        if error_text is not None:
            await _insert_log(
                db, cycle_at=_now(), contract=contract, decision="ERROR",
                latency_ms=latency_ms, hesitant=hesitant, error=error_text, outcome="error",
            )
            return {"outcome": "error", "error": error_text, "contract": contract, "hesitant": hesitant}

        if not sig or sig.get("action") != "BUY":
            await _insert_log(
                db, cycle_at=_now(), contract=contract, symbol=(sig or {}).get("symbol"),
                decision="HOLD", reasoning_excerpt=(sig or {}).get("these"),
                latency_ms=latency_ms, hesitant=hesitant, outcome="ok",
            )
            return {"outcome": "hold", "contract": contract, "hesitant": hesitant}

        fraction, _kelly_ctx = await _kelly_fraction_from_history()
        size_usd = round(REHEARSAL_NOTIONAL_USD * fraction, 2)

        record = {
            "contract": contract,
            "action": "BUY",
            "kelly_fraction": fraction,
            "entry_price": sig.get("price"),
            "target": sig.get("target"),
            "invalidation": sig.get("invalidation"),
            "ts": _now(),
        }

        tx_hash: str | None = None
        try:
            if anchor_sender is None:
                from aria_core.onchain.attestation import merkle_root
                from aria_core.onchain.sepolia_wallet import SEPOLIA_CHAIN_ID, send_anchor_transaction

                root = merkle_root([record])
                tx_hash = send_anchor_transaction(
                    contract=contract_ledger, root=root, chain_id=SEPOLIA_CHAIN_ID,
                )
            else:
                tx_hash = anchor_sender(record)
        except Exception as exc:  # noqa: BLE001 — a failed broadcast must surface in telemetry, never break the heartbeat
            error_text = str(exc)[:500]

        # Test swap — independent of the anchor: never sized by Kelly, never
        # on the real candidate token (nonexistent on this testnet). A failure
        # here doesn't erase the anchor's success above, and vice versa.
        swap_tx: str | None = None
        swap_error_text: str | None = None
        from aria_core.onchain.sepolia_wallet import sepolia_swap_enabled

        if sepolia_swap_enabled():
            try:
                if swap_sender is None:
                    from aria_core.onchain.sepolia_wallet import (
                        SEPOLIA_CHAIN_ID,
                        send_test_swap_transaction,
                    )

                    swap_result = send_test_swap_transaction(
                        amount_in_wei=TEST_SWAP_AMOUNT_WEI, chain_id=SEPOLIA_CHAIN_ID,
                    )
                else:
                    swap_result = swap_sender()
                swap_tx = swap_result.get("swap_tx") if swap_result else None
            except Exception as exc:  # noqa: BLE001 — a failed broadcast must surface in telemetry, never break the heartbeat
                swap_error_text = str(exc)[:500]

        outcome = "ok" if tx_hash else "error"
        await _insert_log(
            db, cycle_at=_now(), contract=contract, symbol=sig.get("symbol"),
            decision="BUY", reasoning_excerpt=sig.get("these"), latency_ms=latency_ms,
            hesitant=hesitant, kelly_fraction=fraction, kelly_size_usd=size_usd,
            tx_hash=tx_hash, error=error_text, outcome=outcome,
            swap_tx=swap_tx, swap_error=swap_error_text,
        )

    if notifier:
        try:
            if tx_hash:
                swap_line = (
                    f"\nSwap de test (paire test, pas le candidat) : tx {swap_tx}"
                    if swap_tx
                    else (f"\nSwap de test échoué : {swap_error_text}" if swap_error_text else "")
                )
                await notifier(
                    "🧪 Rehearsal Sepolia autonome — décision exécutée SANS validation Telegram "
                    "(testnet, aucune valeur réelle)\n"
                    f"{sig.get('symbol') or contract[:10]} · Kelly {fraction * 100:.1f}% "
                    f"({size_usd:,.0f} $ fictifs) · tx {tx_hash}{swap_line}"
                )
            else:
                await notifier(
                    "⚠️ Rehearsal Sepolia autonome — décision BUY prise mais diffusion échouée : "
                    f"{error_text}"
                )
        except Exception:  # noqa: BLE001
            pass

    return {
        "outcome": outcome, "contract": contract, "tx_hash": tx_hash,
        "kelly_fraction": fraction, "kelly_size_usd": size_usd, "hesitant": hesitant,
        "swap_tx": swap_tx, "swap_error": swap_error_text,
    }


async def autonomous_status() -> dict:
    """PUBLIC aggregated statistics (counts only, never an individual contract
    outside the last decision) for the cockpit — same policy as
    track-record/exam-status."""
    from aria_core.onchain.sepolia_wallet import get_address, get_balance_eth

    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM sepolia_autonomous_log")).fetchone())[0]
        tx_count = (await (await db.execute(
            "SELECT COUNT(*) FROM sepolia_autonomous_log WHERE tx_hash IS NOT NULL"
        )).fetchone())[0]
        errors = (await (await db.execute(
            "SELECT COUNT(*) FROM sepolia_autonomous_log WHERE outcome = 'error'"
        )).fetchone())[0]
        hesitations = (await (await db.execute(
            "SELECT COUNT(*) FROM sepolia_autonomous_log WHERE hesitant = 1"
        )).fetchone())[0]
        swap_tx_count = (await (await db.execute(
            "SELECT COUNT(*) FROM sepolia_autonomous_log WHERE swap_tx IS NOT NULL"
        )).fetchone())[0]
        swap_errors = (await (await db.execute(
            "SELECT COUNT(*) FROM sepolia_autonomous_log WHERE swap_error IS NOT NULL"
        )).fetchone())[0]
        last_row = await (await db.execute(
            "SELECT cycle_at, symbol, decision, outcome, tx_hash FROM sepolia_autonomous_log "
            "ORDER BY id DESC LIMIT 1"
        )).fetchone()
        breaker_open = (await _consecutive_errors(db)) >= CONSECUTIVE_ERROR_CIRCUIT_BREAKER

    last = None
    if last_row:
        last = {
            "at": last_row[0], "symbol": last_row[1], "decision": last_row[2],
            "outcome": last_row[3], "tx_hash": last_row[4],
        }
    from aria_core.onchain.sepolia_wallet import sepolia_swap_enabled

    return {
        "enabled": sepolia_autonomous_enabled(),
        "cycles_total": total,
        "tx_count": tx_count,
        "error_count": errors,
        "hesitation_count": hesitations,
        "circuit_breaker_open": breaker_open,
        "last": last,
        "wallet_address": get_address(),
        "wallet_balance_eth": get_balance_eth(),
        "swap_enabled": sepolia_swap_enabled(),
        "swap_tx_count": swap_tx_count,
        "swap_error_count": swap_errors,
    }
