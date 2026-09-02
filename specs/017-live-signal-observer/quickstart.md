# Quickstart: Live Signal Observer

## Prerequisites

- specs/016 deployed (it is: `momentum_signal_observation` table exists in prod).
- Paper-trading paused (`paper_pause.pause_status()['paused'] is True`) — the whole point is that this does not matter.
- Gate `ARIA_LIVE_SIGNAL_OBSERVER_ENABLED=true` added to `vanguard/backend/.env` by the operator, then `./vanguard/deploy.sh`.

## Scenario 1 — Decoupling works (US1 / SC-001, SC-002)

After deploy, within ~2 minutes:

```bash
docker logs aria-api --since 5m 2>&1 | grep live_signal_observer | tail -5
```
Expect `live_signal_observer: started (4 endpoints)` and drain lines. Then:

```bash
docker exec aria-api python /app/backend/docker-entrypoint.py python3 -c "
import asyncio; from aria_core import bootstrap; bootstrap.configure_data_dir('/app/backend/data')
from aria_core import momentum_signal_observation as m
async def go():
    rows = await m.list_recent(limit=5); print('observations:', len(rows))
    for r in rows: print(r['decision_ts'], r['chain'], r['contract'][:10], r['decision_action'])
asyncio.run(go())"
```
Expect observations to appear and keep growing while `/offpaper` stays active. Then confirm zero execution:

```bash
sqlite3 -readonly /opt/aria-data/aria.db "select count(*) from paper_position where opened_at > datetime('now','-1 day'); select count(*) from pending_limit_order where created_at > datetime('now','-1 day');"
```
Expect `0` and `0` (column names to be confirmed against the real schema at implementation).

## Scenario 2 — Message format and routing (US2 / SC-003)

Unit test: `format_signal(...)` output starts with `⚡ ARIA LIVE SIGNAL`, contains the three family blocks and exactly one status, and matches none of `(?i)\b(BUY|ENTRY|OPENED|FILLED)\b`. Integration: the send goes through `gateway.telegram_bot.send_message`, never `send_trading_notification` (a test patches both and asserts which one was called).

## Scenario 3 — Data quality never reads as a weak signal (US3 / SC-005)

Unit test: an observation with every on-chain sub-signal `available:false` → `presentation['onchain'].quality == 'LOW'`, `.figure is None`, `classify(...) == 'DATA_INCOMPLETE'`. An observation with a `signal_cascade_convergence` row 30h old → counted as stale, not fresh.

## Scenario 4 — Anti-spam (SC-006)

Unit test: two CONVERGENCE observations for the same token 10 minutes apart → one send. Status MIXED → observed, no send.

## Scenario 5 — Zero regression on the execution path

`tests/test_momentum_websocket.py`, `tests/test_paper_trader.py`, `tests/test_momentum_entry.py` unchanged and green (this feature touches none of those modules).
