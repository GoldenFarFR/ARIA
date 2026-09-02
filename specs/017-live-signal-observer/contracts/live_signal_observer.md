# Contract: `aria_core.live_signal_observer`

Internal module contract (no HTTP surface).

## Gate

`live_signal_observer_enabled() -> bool` — reads `ARIA_LIVE_SIGNAL_OBSERVER_ENABLED`, OFF by default, read once at `start()` (dome doctrine, same as `momentum_websocket_enabled()`).

## Service

```python
class LiveSignalObserver:
    async def start(self) -> None   # no-op if gate off; spawns 4 endpoint loops + 1 drain loop
    async def stop(self) -> None    # cancels and awaits all tasks

live_signal_observer = LiveSignalObserver()   # module-level singleton, wired in main.py
```

Loops (orchestration only; every constant imported from `momentum_websocket`/`momentum_entry`, see research.md §4):
- `_endpoint_loop(endpoint)`: connect → read one snapshot → close → sleep `DRAIN_INTERVAL_SECONDS`; exponential backoff on error; never exits while running.
- `_ingest_frame(raw)`: `parse_listing` → `normalize_contract_case` → chain in `DEFAULT_CHAINS` → not in `reference_tokens_excluded(chain)` → `DEDUP_TTL_SECONDS` → `_pending`.
- `_drain_loop` → `_drain_once()` every `DRAIN_INTERVAL_SECONDS`.

## `_drain_once()` behavior (FR-001..FR-004, FR-011)

1. Take up to `MAX_CANDIDATES_PER_DRAIN` from `_pending`.
2. `_batch_liquidity_prefilter` (fail-open), then the adaptive cooldown (`RESCAN_COOLDOWN_SECONDS`/`RESCAN_PRICE_MOVE_THRESHOLD_PCT`) exactly as the execution path applies it.
3. Enforce `MAX_EVALUATIONS_PER_HOUR` with a sliding window; truncate, never skip-all.
4. Resolve `mode = await paper_trader.get_trading_mode()` and `current_regime = await market_sentiment.resolve_meta_regime()` once per drain.
5. For each candidate: `result = await momentum_entry.evaluate_momentum_entry(contract, chain, current_regime=..., mode=...)` inside try/except (one failure never stops the drain). The specs/016 wrapper persists the observation as a side effect.
6. Fetch the observation row just written (`momentum_signal_observation.list_recent` filtered by contract/chain, most recent); if none, skip (no message without a persisted row).
7. `presentation = build_presentation(observation)`; `status = classify(presentation)`.
8. If `status in SENDABLE_STATUSES` and the token is outside the cooldown (`live_signal_notification`), `await send_message(format_signal(...), chat_id=_signal_chat_id(), disable_preview=True)`, then upsert `live_signal_notification`.

**MUST NOT** call: `paper_trader.run_paper_cycle`, `paper_trader.open_position`, `paper_trader._default_momentum_analyzer`, `limit_orders.process_active_orders`, `send_trading_notification`. **MUST NOT** check `paper_pause.is_paused()` (FR-003) nor `outgoing_pause.is_paused()` (research.md §1).

## Pure helpers (unit-testable without network)

- `build_presentation(observation: dict) -> dict[str, FamilyPresentation]`
- `classify(presentation) -> str`
- `format_signal(symbol, contract, presentation, status, decision_ts, forward_rows) -> str` — plain text, header `⚡ ARIA LIVE SIGNAL`; a test asserts none of `BUY`/`ENTRY`/`OPENED`/`FILLED` appears (case-insensitive) for any input.
- `_signal_chat_id() -> int | None` — `ARIA_SIGNAL_TELEGRAM_CHAT_ID` parsed as int, `None` (operator channel fallback) when unset/invalid.

## `main.py` wiring

`await live_signal_observer.start()` right after `momentum_websocket_listener.start()` in `_background_startup()`; `await live_signal_observer.stop()` in `lifespan` teardown next to `momentum_websocket_listener.stop()`.
