"""Shared Telegram open/close notifier for scale-out-ladder shadow pockets.

**Why this exists (23/08).** Robinhood and Base's dedicated notify functions
(``_notify_robinhood``/``_notify_base`` in ``shadow_persistent.py``, hors git)
turned out ~95% byte-for-byte identical -- confirmed by a background audit
workflow the same evening. This module factors out the shared body into one
parametrized function, called with a small per-pocket config instead of two
near-duplicate ~110-line functions.

**Why ``solana_late_bonding_shadow``'s own notifier is deliberately NOT here.**
That pocket exits in one shot (no scale-out ladder), reads its own
``summary()`` for the aggregate instead of inline SQL, and has a milestone/
epoch system with no Robinhood/Base equivalent -- its divergence is
structural, not cosmetic. Forcing it into this shape would be exactly the
premature homogenization a background adversarial review flagged as a real
risk on a first draft of this refactor (a naive shared abstraction can make
two pockets' behavior quietly identical even after their thresholds diverge,
if the code path never actually reads the pocket-specific value it's supposed
to render). See that review's own worked example: this module is deliberately
tested against a fake pocket pair with DIFFERENT thresholds (not just today's
identical Robinhood/Base numbers), so any future hardcoded shortcut fails a
test instead of silently passing.

**Why ``send`` is injected rather than imported.** This module lives in the
repo (``aria_core``, covered by CI). The real Telegram client
(``telegram_notify.py``) lives beside ``shadow_persistent.py``, outside git
(see ``docs/registre-automatisations.md``) -- importing it directly here
would make this module untestable in CI. The caller (``shadow_persistent.py``)
passes ``telegram_notify.send`` at the call site instead.

**Regime-gate note.** This module does NOT include a shared regime-gate
(the market-wide entry sensor built the same evening for
``solana_late_bonding_shadow``). That mechanism's safety depends on reading
price via a free, gate-decision-independent, already-happening subscription
(Solana's ``bonding_ws_feed``, subscribed for every candidate regardless of
the gate's verdict) -- Robinhood/Base currently only have throttled REST
snapshots (``_snapshot_with_fallback``), which do not have that property.
Generalizing the regime-gate before that gap is closed for a given pocket
risks silently reintroducing the exact starvation bias the original sensor
was rebuilt to fix (cf. ``solana_late_bonding_shadow.py``'s own incident
comment). Deliberately deferred -- see ``docs/HANDOFF_PIPELINE_MOMENTUM.md``'s
23/08 entry on this decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Awaitable, Callable

import aiosqlite

NotifySendFn = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class PocketNotifyConfig:
    """One instance per pocket, owned by the caller (``shadow_persistent.py``).

    ``module`` must expose ``DB_PATH``/``TABLE``/``MAX_POOL_AGE_MINUTES``/
    ``SCALE_OUT_STEP_PCT``/``SCALE_OUT_SELL_FRACTION``/``TRAILING_STOP_PCT`` --
    the same shape ``robinhood_pump_shadow``/``base_momentum_shadow`` already
    have.
    """

    key: str
    label: str
    module: ModuleType
    dexscreener_chain_slug: str
    liquidity_floor_usd: float = 4000.0
    # Robinhood's floor was calibrated on its own 200 real closures (23/08);
    # Base's identical-looking number is inherited from Robinhood, unverified
    # for Base's own market -- see base_momentum_shadow.py's own comment on
    # MIN_LIQUIDITY_USD. This flag picks the disclaimer wording so a reader
    # never mistakes one pocket's real calibration for the other's borrowed
    # placeholder, even after the code is shared.
    liquidity_floor_calibrated: bool = False


@dataclass
class _NotifyState:
    last_notified_id: int | None = None
    notified_closes: set[int] = field(default_factory=set)


_NOTIFIED_CLOSES_MAX = 500
_STATE: dict[str, _NotifyState] = {}


def _state_for(cfg: PocketNotifyConfig) -> _NotifyState:
    return _STATE.setdefault(cfg.key, _NotifyState())


def _local_hms(iso_ts) -> str:
    if not iso_ts:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_ts)
    except Exception:  # noqa: BLE001 -- a label must never crash the notifier
        return "?"
    return dt.strftime("%H:%M:%S")


def _format_hold_duration(detected_at, closed_at) -> str:
    if not detected_at or not closed_at:
        return "inconnue"
    try:
        delta = datetime.fromisoformat(closed_at) - datetime.fromisoformat(detected_at)
    except Exception:  # noqa: BLE001
        return "inconnue"
    minutes = delta.total_seconds() / 60.0
    if minutes < 60:
        return f"{minutes:.1f} min"
    return f"{minutes / 60.0:.1f} h"


def exit_text(cfg: PocketNotifyConfig) -> str:
    """Derived from the code, never hardcoded -- a notification describing a
    rule that no longer runs is worse than no notification (lesson paid on
    late_bonding, 22/08: the word "paliers" stayed hardcoded after the ladder
    was retired, so the notif kept announcing it)."""
    m = cfg.module
    return (f"Sortie: paliers +{m.SCALE_OUT_STEP_PCT:.0f}% "
            f"(vend {m.SCALE_OUT_SELL_FRACTION*100:.0f}% du restant a chaque) "
            f"| trailing -{m.TRAILING_STOP_PCT:.0f}%")


async def aggregate(cfg: PocketNotifyConfig) -> str:
    """Recent-first, cumulative-second, debit last -- same reading order
    across every pocket using this notifier."""
    m = cfg.module
    try:
        async with aiosqlite.connect(m.DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT COUNT(*) n, "
                f"       AVG((COALESCE(realistic_final_multiplier, final_multiplier)-1)*100) pnl, "
                f"       SUM(COALESCE(realistic_final_multiplier, final_multiplier) > 1) wins "
                f"FROM {m.TABLE} WHERE exit_reason IS NOT NULL AND final_multiplier IS NOT NULL"
            )
            cum = dict(await cur.fetchone())
            cur = await db.execute(
                f"SELECT COUNT(*) n, "
                f"       AVG((COALESCE(realistic_final_multiplier, final_multiplier)-1)*100) pnl, "
                f"       SUM(COALESCE(realistic_final_multiplier, final_multiplier) > 1) wins "
                f"FROM (SELECT * FROM {m.TABLE} WHERE exit_reason IS NOT NULL "
                f"      AND final_multiplier IS NOT NULL ORDER BY id DESC LIMIT 30)"
            )
            rec = dict(await cur.fetchone())
            cur = await db.execute(f"SELECT COUNT(*) n FROM {m.TABLE} WHERE exit_reason IS NULL")
            ouvertes = (await cur.fetchone())["n"]
            depuis = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            cur = await db.execute(
                f"SELECT SUM(detected_at >= ?) ouv, SUM(last_checked_at >= ? AND exit_reason IS NOT NULL) clo "
                f"FROM {m.TABLE}", (depuis, depuis))
            debit = dict(await cur.fetchone())
    except Exception:  # noqa: BLE001 -- the aggregate must never kill the notification
        return ""

    out = ""
    if rec.get("n"):
        wr = f"{100.0*(rec['wins'] or 0)/rec['n']:.0f}%"
        out += f"\n{rec['n']} dernieres: winrate {wr}, PnL {rec['pnl'] or 0:+.1f}%"
    if cum.get("n"):
        wr = f"{100.0*(cum['wins'] or 0)/cum['n']:.0f}%"
        out += (f"\nCumul: {cum['n']} clot., winrate {wr}, {ouvertes} ouv."
                f", PnL {cum['pnl'] or 0:+.1f}%")
    out += f"\nDebit 1h: {debit.get('ouv') or 0} ouv., {debit.get('clo') or 0} clot."
    return out


async def notify_pocket(cfg: PocketNotifyConfig, kind: str, *, send: NotifySendFn) -> None:
    """Same DIFF approach as every sibling shadow notifier: the pocket module
    stays untouched, this compares row ids between passes. ``kind`` is
    "open" or "close". A failed notification never affects the pocket."""
    state = _state_for(cfg)
    m = cfg.module
    try:
        async with aiosqlite.connect(m.DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if kind == "open":
                if state.last_notified_id is None:
                    cur = await db.execute(f"SELECT COALESCE(MAX(id), 0) FROM {m.TABLE}")
                    state.last_notified_id = (await cur.fetchone())[0]
                    return  # this pass only anchors, it never replays history
                cur = await db.execute(
                    f"SELECT * FROM {m.TABLE} WHERE id > ? ORDER BY id ASC",
                    (state.last_notified_id,))
            else:
                cur = await db.execute(
                    f"SELECT * FROM {m.TABLE} WHERE exit_reason IS NOT NULL "
                    f"AND last_checked_at >= ? ORDER BY id ASC",
                    ((datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),))
            rows = [dict(r) for r in await cur.fetchall()]

        agg = await aggregate(cfg)

        for row in rows:
            pool = row.get("pool_address") or ""
            lien = f"https://dexscreener.com/{cfg.dexscreener_chain_slug}/{pool}" if pool else ""
            if kind == "open":
                state.last_notified_id = max(state.last_notified_id, row["id"])
                age = ""
                if row.get("pool_created_at") and row.get("detected_at"):
                    try:
                        mins = (datetime.fromisoformat(row["detected_at"])
                                - datetime.fromisoformat(row["pool_created_at"])).total_seconds() / 60.0
                        age = f"{mins:.1f} min"
                    except Exception:  # noqa: BLE001
                        age = "inconnu"
                else:
                    age = "inconnu"
                liq = row.get("reserve_usd")
                liq_txt = f"${liq:.0f}" if liq is not None else "inconnue"
                if liq is not None and liq < cfg.liquidity_floor_usd:
                    liq_txt += (
                        "  <-- POOL TROP MINCE, PnL non executable"
                        if cfg.liquidity_floor_calibrated
                        else "  <-- POOL TROP MINCE (seuil emprunte, non recalibre pour cette poche)"
                    )
                await send(
                    f"{cfg.label} ({row.get('symbol') or '?'})\n"
                    f"OUVERTURE\n"
                    f"Age du pool: {age} [MAX {m.MAX_POOL_AGE_MINUTES:.0f} min]\n"
                    f"Liquidite: {liq_txt}\n"
                    f"Variation 5min: {row.get('m5_pct') if row.get('m5_pct') is not None else '?'}% "
                    f"| 15min: {row.get('m15_pct') if row.get('m15_pct') is not None else '?'}%\n"
                    f"Acheteurs/vendeurs 15min: {row.get('buyers_m15')}/{row.get('sellers_m15')}\n"
                    f"Entree: ${(row.get('entry_price') or 0):.10g} "
                    f"a {_local_hms(row.get('detected_at'))}\n"
                    + exit_text(cfg) + "\n"
                    + lien + agg
                )
            else:
                if row["id"] in state.notified_closes:
                    continue
                state.notified_closes.add(row["id"])
                if len(state.notified_closes) > _NOTIFIED_CLOSES_MAX:
                    for old_id in sorted(state.notified_closes)[:100]:
                        state.notified_closes.discard(old_id)
                mult = row.get("realistic_final_multiplier") or row.get("final_multiplier")
                pnl = f"{(mult-1)*100:+.1f}%" if mult is not None else "n/a"
                vente = row.get("realistic_realized_proceeds") or row.get("realized_proceeds")
                vente_txt = f"${vente:.10g}" if vente else "n/a"
                pal_txt = ""
                try:
                    nxt = row.get("next_scale_level")
                    ent = row.get("entry_price")
                    if nxt and ent and nxt > 0 and ent > 0:
                        pas = 1.0 + m.SCALE_OUT_STEP_PCT / 100.0
                        rang = round(math.log(nxt / ent) / math.log(pas))
                        franchis = max(0, rang - 1)
                        if franchis:
                            pal_txt = (f"\nPaliers franchis: {franchis} "
                                       f"(vendu ~{(1-(1-m.SCALE_OUT_SELL_FRACTION)**franchis)*100:.0f}% "
                                       f"de la position avant la sortie)")
                except Exception:  # noqa: BLE001 -- a label must never crash the notif
                    pal_txt = ""
                await send(
                    f"{cfg.label} ({row.get('symbol') or '?'})\n"
                    f"CLOTURE -- {row['exit_reason']}\n"
                    f"Achat:  ${(row.get('entry_price') or 0):.10g} "
                    f"a {_local_hms(row.get('detected_at'))}\n"
                    f"Vente:  {vente_txt} "
                    f"a {_local_hms(row.get('last_checked_at') or row.get('closed_at'))}\n"
                    f"PnL: {pnl}"
                    + pal_txt + "\n"
                    f"Detention: {_format_hold_duration(row.get('detected_at'), row.get('last_checked_at') or row.get('closed_at'))}\n"
                    + lien + agg
                )
    except Exception as exc:  # noqa: BLE001 -- a notify failure never affects the pocket
        print(f"{cfg.label} notify ({kind}) failed: {exc!r}", flush=True)
