"""Chart screenshot sent to Telegram when a limit order is placed (04/08,
operator request: "c'est possible davoir un screenshot du graphique pour
chaque poche quand elle etablie un ordre limite avec les signaux detecter").

Pilot scope (operator's own choice via AskUserQuestion, not "all pockets at
once"): ``scalping_v6``/``scalping_v7`` only -- the two pockets sharing the
same RSI-divergence/golden-pocket signal (``momentum_entry.evaluate_
momentum_entry``, only the trigger span differs), the freshest and most
actively observed pockets at the time of this request. Every other pocket
(v1-v5, swing, vc, megacap) is untouched -- extend ``PILOT_WALLETS`` once
this pilot is validated, never silently.

Reuses the pool resolved by THIS SAME scan (``sig["pool_address"]``, added
04/08 for exactly this) to refetch candles via ``momentum_entry.
fetch_candles`` -- almost always a cache hit (60s TTL, same pool just
scanned), never a fresh network call in the common case. Best-effort: a
chart failure (missing pool_address, no candles, Telegram down) never
raises into the real trading cycle that placed the order -- the existing
text alert (``limit_orders.format_limit_order_placed_alert``) is the
authoritative notification either way, this is a visual complement."""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# 04/08 -- pilot pockets only, operator's explicit choice. Extend here once
# validated -- never silently broaden the scope of a pilot.
PILOT_WALLETS = ("scalping_v6", "scalping_v7")

# A scalping setup plays out in hours, not weeks -- the label shown on the
# chart's simulation panel is overridden accordingly (chart_render.py's
# horizon_label), never the default "N sem." wording built for VC/marketing.
_HORIZON_LABEL = "quelques heures"


async def maybe_send_order_chart(order: dict, sig: dict) -> None:
    """Best-effort: sends a candles + signal-levels screenshot for a
    newly-placed limit order, only for ``PILOT_WALLETS``. Silently does
    nothing outside the pilot scope, on missing data, or on any failure --
    this is a visual complement to the text alert, never a blocking step."""
    wallet = order.get("wallet")
    if wallet not in PILOT_WALLETS:
        return
    pool_address = sig.get("pool_address")
    if not pool_address:
        return

    path = None
    try:
        from aria_core import momentum_entry
        from aria_core.gateway import telegram_bot
        from aria_core.skills import chart_render

        candles = await momentum_entry.fetch_candles(
            pool_address, order.get("chain") or "base",
            contract=order.get("contract", ""), mode="scalping",
        )
        if not candles:
            return

        data_uri = chart_render.render_scenario_png(
            candles,
            entry=sig.get("price_at_order_placed") or sig.get("price"),
            invalidation=sig.get("invalidation"),
            target=sig.get("target"),
            horizon_label=_HORIZON_LABEL,
        )
        path = f"/tmp/aria-limit-order-chart-{order.get('id', 'na')}-{int(time.monotonic() * 1000)}.png"
        chart_render.save_png_data_uri(data_uri, path)

        symbol = order.get("symbol") or sig.get("symbol") or "?"
        caption = f"{symbol} -- {wallet} -- signaux au moment de l'ordre limite"
        await telegram_bot.send_photo(path, caption=caption)
    except Exception as exc:  # noqa: BLE001 -- best-effort, never breaks the cycle
        logger.info("limit_order_chart: could not send chart for %s (%s)", order.get("contract"), exc)
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
