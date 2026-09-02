# Phase 0 Research: Live Signal Observer

Every finding below is from a live read of the code this session, never from documentation or memory.

## 1. Kill-switch scope: this module is outside `/stop`'s perimeter, by the kill-switch's own definition

**Decision**: the service neither gates evaluation/observation nor the Telegram send on `outgoing_pause.is_paused()`.

**Rationale**: `outgoing_pause.py`'s module docstring (read verbatim): "Covers tweets, X replies/likes, ACP spending, and scheduled jobs (heartbeat). [...] This module NEVER freezes operator Telegram messaging (`send_message` / `notify_admin`): the control channel must stay open to receive the `/stop` confirmation, approval prompts, and to allow `/start`." The two things this service does — compute signals (zero financial action) and message the operator — are both explicitly outside what `/stop` is designed to stop. Gating the signal on it would silence the operator's own control screen precisely when they armed the switch to look at things more carefully, the opposite of the module's stated intent.

One consequence shapes §3 below: "scheduled jobs (heartbeat)" IS covered — so if this service were a heartbeat cycle, `/stop` would freeze it as a side effect. A background service (like `momentum_websocket_listener`) is not a heartbeat job and is not frozen.

**Alternatives considered**: fail-closed on `/stop` for the send only — rejected: contradicts the kill-switch's documented contract for operator messaging, and would make the signal screen go dark exactly when the operator paused execution to watch.

## 2. Execution-purity of `evaluate_momentum_entry()` is a testable property, not an assumption

**Decision**: FR-004 is enforced by a test that counts rows in `paper_position` (`paper_trader.py:1192`) and `pending_limit_order` (`limit_orders.py:312`) before and after a direct `evaluate_momentum_entry()` call and asserts zero delta.

**Rationale**: verified this session that the function returns its golden-pocket/RSI "watch" candidates inside the decision dict for the caller to act on (`momentum_entry.py` ~L4652/4702, its own comment: "logged by the caller -- paper_trader.py/limit_orders.py"), and that order processing lives in `momentum_websocket._drain_once` → `limit_orders.process_active_orders(...)`, which this service never calls. A code-reading argument is not enough for a property this important; the row-count test makes it mechanical.

## 3. Process shape: background service, same pattern as `momentum_websocket_listener`

**Decision**: a `LiveSignalObserver` class with `start()`/`stop()` and asyncio tasks, instantiated as a module-level singleton and wired into `vanguard/backend/app/main.py`'s `_background_startup()` (after `momentum_websocket_listener.start()`, L149) and `lifespan` teardown (L176).

**Rationale**: discovery reconnects each endpoint every 30s (`DRAIN_INTERVAL_SECONDS`) and must keep four short-lived WS connections cycling — a long-running service, not a periodic job. And per §1, a heartbeat cycle would be frozen by `/stop` while a background service is not.

**Alternatives considered**: a heartbeat cycle — rejected for both reasons above.

## 4. Discovery: import the proven building blocks, own only the orchestration loop

**Decision**: import from `momentum_websocket`: `WS_BASE_URL`, `ENDPOINTS`, `DRAIN_INTERVAL_SECONDS`, `DEDUP_TTL_SECONDS`, `RESCAN_COOLDOWN_SECONDS`, `RESCAN_PRICE_MOVE_THRESHOLD_PCT`, `MAX_CANDIDATES_PER_DRAIN`, `MAX_EVALUATIONS_PER_HOUR`, `_CONNECT_TIMEOUT_SECONDS`, `_RECV_TIMEOUT_SECONDS`, `_BACKOFF_INITIAL_SECONDS`, `_BACKOFF_MAX_SECONDS`. Import from `momentum_entry`: `DEFAULT_CHAINS`, `normalize_contract_case`, `reference_tokens_excluded`, `_batch_liquidity_prefilter`. Import `services.dexscreener.parse_listing`. The service reimplements only the loop bodies (`_endpoint_loop`/`_ingest_frame`/`_drain`), which are plumbing, not calibrated parameters.

**Rationale**: constitution §1bis forbids restating a constant that exists elsewhere; the operator chose zero modification of `momentum_websocket.py` and named a shared `discovery_core` extraction as a later refactor. Importing gives identical dedup/cooldown/cap behavior (FR-001) with no value duplication; only ~120 lines of loop plumbing are duplicated, to be collapsed into `discovery_core` later.

## 5. Same evaluation parameters as the execution path, via read-only calls

**Decision**: per drain, resolve `mode = await paper_trader.get_trading_mode()` and `current_regime = await market_sentiment.resolve_meta_regime()`, then call `evaluate_momentum_entry(contract, chain, current_regime=current_regime, mode=mode)` directly. Never `_default_momentum_analyzer`, `run_paper_cycle`, `open_position`.

**Rationale**: `momentum_websocket._drain_new_candidates` (~L470-530) resolves exactly these two before evaluating; using anything else would make observed signals differ from what the execution path would compute — a second decision path, forbidden. `get_trading_mode()` is a pure DB read; `resolve_meta_regime()` is a pure DB read of the last sentiment reading.

## 6. CoinGecko cap (#271) does not touch this signal's price path; it can only degrade "fundamentals" to unavailable

**Decision**: no fix in this feature; the pipeline's existing fail-safe handles it.

**Rationale**: grep of `momentum_entry.py`/`dex_composite_score.py`/`liquidity_depth.py` finds no WETH/USD rate use; the only CoinGecko call is `coingecko_client.get_token_fundamentals` (`momentum_entry.py:1913`, mcap/FDV). If the monthly cap blocks it, that sub-signal surfaces as unavailable (specs/016 already records this honestly), so the message shows degraded data quality, never a wrong number.

## 7. Gate activation in production: an operator-owned one-line `.env` change

**Decision**: gate `ARIA_LIVE_SIGNAL_OBSERVER_ENABLED`, OFF by default in code. Activation = the operator appends `ARIA_LIVE_SIGNAL_OBSERVER_ENABLED=true` to `vanguard/backend/.env`, then `./vanguard/deploy.sh`.

**Rationale**: `deploy.sh` L32/L90 passes `--env-file vanguard/backend/.env` to the container. That file holds every secret; this session's standing rule is never to read or write it via Bash (two real leaks in July). Same protocol specs/012 used. Verification after activation: `docker inspect aria-api` shows the variable, and the service logs "live_signal_observer: started (4 endpoints)".

## 8. Presentation heuristics — initial values, explicitly uncalibrated

All constants below are starting points labeled as such in code; calibrating them against forward performance is the exact purpose of the observation layer.

- **STALE**: a social sub-signal whose `data_timestamp` is older than its source's freshness threshold: `signal_cascade_convergence` 24h (its cycles run every 15-60 min, so 24h is unambiguously stale); `conviction_research` is fresh by construction (synchronous); `radar_x` is always `not_available` (no persisted state, unchanged from specs/016).
- **Data quality per family** = (available AND fresh sub-signals) / (sub-signals in the family): ≥ 0.75 HIGH, ≥ 0.50 MEDIUM, else LOW.
- **Per-family figure**: on-chain = the pipeline's real `composite_score` value when available (else no figure); chart and social = round(100 × favorable / (available fresh)), where "favorable" per sub-signal is: `golden_pocket_present` true, `rsi_divergence_present` true, `risk_reward_ratio` ≥ 1, `technical_align_score` ≥ 2, `rvol_confirmed.confirmed` true, `market_regime` ≠ "peur"; `conviction_research_score` ≥ `risk_guard.FUNDAMENTAL_WEAK_THRESHOLD` (4.0, imported, the pipeline's own weak/strong boundary), `signal_cascade_convergence` has ≥ 1 accelerating source. A figure is shown only at MEDIUM quality or above (FR-008).
- **Status** (FR-009): DATA INCOMPLETE if any family is LOW. Otherwise each ≥MEDIUM family reads favorable (figure ≥ 65), unfavorable (≤ 35) or neutral: all favorable → CONVERGENCE; at least one favorable AND one unfavorable → DIVERGENCE; anything else → MIXED.
- **Sending threshold and cooldown** (FR-011): send only CONVERGENCE and DIVERGENCE; per-token cooldown = `RESCAN_COOLDOWN_SECONDS` (4h, imported — never notify more often than the pipeline itself would re-evaluate). Cooldown state persisted in a small table so a blue-green redeploy does not re-notify every token.
