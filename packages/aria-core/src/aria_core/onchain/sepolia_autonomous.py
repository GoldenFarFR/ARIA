"""Cycle autonome Sepolia — ARIA décide ET exécute seule, sans clic Telegram, sur testnet
UNIQUEMENT. Décision opérateur explicite et répétée (08/07) : « il faut que le Sepolia
soit le test le plus dur qu'elle ait passé, et qu'une fois arrivée dans le vrai marché,
ce soit simple pour elle de dire oui ou non » — Sepolia sert précisément à observer le
comportement NON FILTRÉ (hésitation, erreurs, dégradation) avant que quoi que ce soit
n'atteigne du capital réel.

Différence structurelle avec tout le reste du dôme onchain : ce module N'APPELLE JAMAIS
wallet_guard.escalate_spend/resolve_spend — chemin totalement séparé, pour que le
garde-fou Telegram partagé (utilisé par tout ce qui touchera un jour du capital réel)
reste intact et non modifié. Rien ici ne s'applique au mainnet : send_anchor_transaction
verrouille chain_id=84532 (aria_core.onchain.sepolia_wallet), et ce module ajoute son
PROPRE verrou (sepolia_autonomous_enabled) par-dessus sepolia_wallet_enabled.

Triple gate (défense en profondeur), les trois doivent être vrais, aucun actif par défaut :
  1. ARIA_SEPOLIA_WALLET_ENABLED   — le wallet Sepolia existe (clé lisible).
  2. ARIA_SEPOLIA_AUTONOMOUS_ENABLED — l'autonomie (sans clic Telegram) est armée.
  3. ARIA_ONCHAIN_ANCHOR_ENABLED + ARIA_LEDGER_ADDRESS — un contrat ledger est configuré.

Kill-switch : chaque cycle relit outgoing_pause.is_paused() — le /stop Telegram existant
gèle ce cycle exactement comme il gèle tweets/ACP/jobs planifiés. Pas de mécanisme parallèle.

Le sizing (Kelly) et la décision utilisent les VRAIES données marché (même client
d'analyse VC que paper_trader/weekly_training) — mais Sepolia n'a pas de pool DEX réel
pour un token Base arbitraire (testnet, aucune liquidité indexée). L'artefact d'exécution
est donc un ancrage onchain autonome de l'enregistrement de décision (signature réelle,
gas réel, nonce réel, échecs RPC réels) — exactement ce qu'un testnet permet de valider
selon la recherche même de l'opérateur : « un test d'ingénierie logicielle, pas une
validation de stratégie de trading ». Le montant Kelly est calculé sur un capital de
répétition fictif (REHEARSAL_NOTIONAL_USD) pour que la discipline de sizing elle-même
soit répétée, même si aucun ETH réel ne change de mains.

Télémétrie : CHAQUE cycle est journalisé (BUY, HOLD, ERROR, SKIP) — jamais seulement les
succès. C'est la demande explicite de l'opérateur : « si elle hésite je veux le savoir,
si elle chie je veux le savoir, si elle en a marre je veux le savoir ». Traduit
honnêtement en télémétrie mesurable : latence de décision (hésitation = anormalement
lente vs sa propre moyenne récente), erreurs brutes, et un coupe-circuit local qui
s'arme après des échecs consécutifs puis se ré-évalue proprement au cycle suivant.

Swap de test (09/07, décision opérateur explicite « swap réel sur Sepolia, actif de
test ») : sur une décision BUY, en plus de l'ancrage de décision ci-dessus, une tentative
INDÉPENDANTE de swap réel (wrap/approve/exactInputSingle, ``sepolia_wallet.
send_test_swap_transaction``) est journalisée si ``ARIA_SEPOLIA_SWAP_ENABLED``. Montant
fixe petit (``TEST_SWAP_AMOUNT_WEI``), jamais dimensionné par Kelly — ce swap ne porte PAS
sur le token candidat réellement analysé (inexistant sur ce testnet) mais sur la paire de
test configurée : il valide le mécanisme d'exécution, pas une thèse de marché. Échec du
swap n'efface jamais le succès de l'ancrage de décision, et inversement — deux artefacts
indépendants du même cycle.
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

REHEARSAL_NOTIONAL_USD = 10_000.0   # capital fictif de répétition — jamais un fonds réel
KELLY_SAFETY_FACTOR = 0.5           # demi-Kelly (tempérament standard face au plein-Kelly, trop volatile)
KELLY_CAP = 0.20                    # plafond dur, même si le calcul brut dépasse
KELLY_MIN_SAMPLE = 5                # sous ce nombre de BUY clôturés, échantillon insuffisant -> fraction conservatrice
KELLY_FALLBACK_FRACTION = 0.01

MAX_AUTONOMOUS_TX_PER_DAY = 12      # plafond de bon sens (RPC/faucet), pas un plafond de risque financier
CANDIDATE_COOLDOWN_HOURS = 6        # ne ré-analyse pas le même contrat avant ce délai (rotation du pool)
LATENCY_BASELINE_SAMPLE = 20
LATENCY_HESITATION_MULTIPLE = 2.0
CONSECUTIVE_ERROR_CIRCUIT_BREAKER = 4

TEST_SWAP_AMOUNT_WEI = 200_000_000_000_000  # ~0.0002 ETH testnet, montant fixe mécanique — jamais Kelly

_LOG_COLS = (
    "cycle_at", "contract", "symbol", "decision", "reasoning_excerpt",
    "latency_ms", "hesitant", "kelly_fraction", "kelly_size_usd", "tx_hash", "error", "outcome",
    "swap_tx", "swap_error",
)
_ADDED_LOG_COLS = ("swap_tx", "swap_error")


def sepolia_autonomous_enabled() -> bool:
    """Gate dédié, au-dessus de sepolia_wallet_enabled : le wallet peut exister sans que
    l'autonomie soit armée — deux décisions séparées, deux flags séparés."""
    from aria_core.onchain.sepolia_wallet import sepolia_wallet_enabled

    if not sepolia_wallet_enabled():
        return False
    return os.environ.get("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def kelly_fraction(
    win_rate: float | None, avg_win_pct: float | None, avg_loss_pct: float | None,
) -> float:
    """Fraction de Kelly (demi-Kelly, plafonnée) à partir des VRAIES statistiques de
    calibration BUY (vc_predictions.compute_metrics). Jamais de confiance inventée :
    données manquantes ou dégénérées (pas d'edge positif) -> fraction fixe conservatrice."""
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
    """Lit la calibration réelle (vc_predictions.metrics) et en dérive la fraction de Kelly."""
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
    """Parse défensif d'un prix éventuellement '$1,234.5' -> float, ou None (même
    logique que paper_trader/weekly_training/simulate_lifecycle — pas de client partagé
    à dupliquer ici, juste un utilitaire pur déjà répété à l'identique ailleurs)."""
    try:
        if v is None:
            return None
        s = str(v).replace("$", "").replace(",", "").strip().split()[0]
        return float(s)
    except (ValueError, IndexError, TypeError):
        return None


async def _default_analyzer(contract: str) -> dict | None:
    """Même analyse VC réelle que paper_trader (analyze_vc_with_context), mais conserve
    la thèse (raisonnement brut) pour la télémétrie comportementale — paper_trader n'en a
    pas besoin, ce module si (« si elle hésite, si elle se trompe, je veux le savoir »)."""
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
    """``hesitant`` est NOT NULL (colonne booléenne 0/1) : toujours coercée, jamais NULL
    explicite (SQLite n'applique le DEFAULT que si la colonne est omise, pas si NULL est
    fourni explicitement)."""
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
    """Compte les erreurs consécutives les plus récentes. S'arrête au premier résultat
    non-erreur -> le coupe-circuit se ré-évalue proprement dès le cycle suivant après
    s'être déclenché (le SKIP qu'il journalise lui-même n'est pas une "erreur")."""
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
    """Un tour du rehearsal autonome Sepolia. Fail-closed à chaque étage (voir le triple
    gate dans le docstring du module). Journalise CHAQUE tour — BUY, HOLD, ERROR, SKIP —
    jamais seulement les succès : c'est l'observabilité demandée par l'opérateur."""
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
    except Exception as exc:  # noqa: BLE001 — une analyse ratée doit être journalisée, jamais casser le heartbeat
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
        except Exception as exc:  # noqa: BLE001 — une diffusion ratée doit remonter dans la télémétrie, jamais casser le heartbeat
            error_text = str(exc)[:500]

        # Swap de test — indépendant de l'ancrage : jamais dimensionné par Kelly, jamais
        # sur le token candidat réel (inexistant sur ce testnet). Échec ici n'efface pas
        # le succès de l'ancrage ci-dessus, et inversement.
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
            except Exception as exc:  # noqa: BLE001 — une diffusion ratée doit remonter dans la télémétrie, jamais casser le heartbeat
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
    """Statistiques agrégées PUBLIQUES (comptages seulement, jamais un contrat individuel
    hors dernière décision) pour le cockpit — même politique que track-record/exam-status."""
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
