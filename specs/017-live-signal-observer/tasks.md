---

description: "Task list for the Live Signal Observer"
---

# Tasks: Live Signal Observer

**Input**: Design documents from `/specs/017-live-signal-observer/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/live_signal_observer.md, quickstart.md

**Tests**: Included (Permanent Norms: every shipped capability ships with a test wired into CI).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Confirm the real column names of `paper_position` (`packages/aria-core/src/aria_core/paper_trader.py:1192`) and `pending_limit_order` (`packages/aria-core/src/aria_core/limit_orders.py:312`) so the FR-004 zero-execution test counts rows against the true schema, not a guessed one.
- [X] T002 [P] Read `packages/aria-core/tests/test_momentum_websocket.py` to reuse its fixture style for faking the WebSocket snapshot and the drain, so `test_live_signal_observer.py` mocks the same seams the same way.

---

## Phase 2: Foundational

- [X] T003 Create `packages/aria-core/src/aria_core/live_signal_observer.py` with: the gate `live_signal_observer_enabled()` (`ARIA_LIVE_SIGNAL_OBSERVER_ENABLED`, OFF by default), `_signal_chat_id()` (`ARIA_SIGNAL_TELEGRAM_CHAT_ID` → int or None), the `live_signal_notification` DDL + `_ensure_tables()` (data-model.md), and every constant IMPORTED from `momentum_websocket` / `momentum_entry` / `risk_guard` per research.md §4 — never a restated value.

---

## Phase 3: User Story 1 — Candidates keep being evaluated while paper-trading is paused (P1) 🎯 MVP

**Goal**: A running `LiveSignalObserver` discovers candidates, evaluates them via `evaluate_momentum_entry()` with the execution path's own `mode`/`current_regime`, producing specs/016 observation rows, while `paper_pause.is_paused()` is True — and never creates a position or pending order.

**Independent Test**: With `paper_pause` patched paused, feed one fake snapshot frame → drain once → one observation row exists; `paper_position` and `pending_limit_order` row counts unchanged.

### Tests

- [X] T004 [P] [US1] Test in `packages/aria-core/tests/test_live_signal_observer.py`: `_ingest_frame` applies chain filter (`DEFAULT_CHAINS`), `reference_tokens_excluded`, and `DEDUP_TTL_SECONDS`, exactly as `momentum_websocket` does (same inputs → same `_pending` contents).
- [X] T005 [P] [US1] Test: `_drain_once` with `paper_pause.is_paused` patched to True still calls `evaluate_momentum_entry` (FR-003), passing `mode` from `paper_trader.get_trading_mode()` and `current_regime` from `market_sentiment.resolve_meta_regime()` (FR-002).
- [X] T006 [P] [US1] Test (FR-004, mechanical): count rows in `paper_position` and `pending_limit_order` before and after a full `_drain_once` against an isolated DB — zero delta; plus a static assertion that the module's source never references `run_paper_cycle`, `open_position`, `_default_momentum_analyzer`, `process_active_orders`, `send_trading_notification`.
- [X] T007 [P] [US1] Test: `MAX_EVALUATIONS_PER_HOUR` sliding window truncates the batch (never skips all), and one candidate raising inside `evaluate_momentum_entry` does not stop the others (FR-013).

### Implementation

- [X] T008 [US1] Implement `LiveSignalObserver` in `live_signal_observer.py`: `start()`/`stop()`, `_endpoint_loop`, `_ingest_frame`, `_drain_loop`, `_drain_once` steps 1-5 of contracts/live_signal_observer.md (prefilter → cooldown → hourly cap → resolve mode/regime once → evaluate each candidate in try/except). No `paper_pause`/`outgoing_pause` check anywhere (FR-003, research.md §1).
- [X] T009 [US1] Wire `live_signal_observer.start()`/`.stop()` into `vanguard/backend/app/main.py` next to `momentum_websocket_listener` (L149 / L176), same two-line pattern.

**Checkpoint**: observations accumulate in prod with `/offpaper` active, zero execution.

---

## Phase 4: User Story 2 — Dedicated live-signal Telegram message (P2)

**Goal**: For sendable statuses, a plain-text message in the dedicated format reaches the signal chat (operator channel fallback) via `send_message`, never `send_trading_notification`, with no banned word.

**Independent Test**: `format_signal(...)` on a full observation → header `⚡ ARIA LIVE SIGNAL`, three family blocks, one status, no `(?i)\b(BUY|ENTRY|OPENED|FILLED)\b`; the send path calls `send_message` with `chat_id=_signal_chat_id()`.

### Tests

- [X] T010 [P] [US2] Test: `format_signal` output structure and the banned-word regex over several inputs (all-available, partial, LOW-quality family); `_signal_chat_id()` returns None when unset/invalid, the int when set.
- [X] T011 [P] [US2] Test: `_maybe_notify` calls `gateway.telegram_bot.send_message` (patched) and never `send_trading_notification` (patched, asserted not called); a failed send is logged and does not raise (FR-013).

### Implementation

- [X] T012 [US2] Implement `build_presentation`, `classify`, `format_signal` and `_maybe_notify` (steps 6-8 of the contract) in `live_signal_observer.py`, using research.md §8's initial constants, each labeled as an uncalibrated starting value in code.

**Checkpoint**: messages arrive on Telegram, visually distinct from trade alerts.

---

## Phase 5: User Story 3 — Data quality never reads as a weak signal (P3)

**Goal**: LOW-quality family → no figure, status DATA_INCOMPLETE; stale social sub-signal → excluded from the tally; per-token cooldown and sending threshold enforced.

### Tests

- [X] T013 [P] [US3] Test: on-chain family all `available:false` → quality LOW, `figure is None`, `classify` == `DATA_INCOMPLETE` even when chart/social are HIGH and favorable (FR-008/FR-009, SC-005).
- [X] T014 [P] [US3] Test: a `signal_cascade_convergence` sub-signal with `data_timestamp` 30h old counts as stale (not fresh, not favorable); 1h old counts as fresh (FR-007).
- [X] T015 [P] [US3] Test: two CONVERGENCE observations for the same token 10 min apart → exactly one `send_message`; a MIXED observation → no send but the observation row still exists (FR-011, SC-006).

### Implementation

- [X] T016 [US3] Implement STALE derivation (per-source thresholds), the quality/figure rules (FR-007/FR-008), `DATA_INCOMPLETE` precedence, `SENDABLE_STATUSES`, and the `live_signal_notification` upsert/lookup in `live_signal_observer.py`.

---

## Phase 6: Polish

- [X] T017 Run `packages/aria-core/tests/test_live_signal_observer.py` plus the regression set `test_momentum_websocket.py`, `test_paper_trader.py`, `test_momentum_entry.py`, `test_momentum_signal_observation.py` — all green, aggregate count only.
- [ ] T018 Commit; deploy per Zero-Permission Policy (`./vanguard/deploy.sh`); verify the served commit via `curl 127.0.0.1:<port>/api/health` against `git rev-parse main`; update `.claude/last-deployed-ref`.
- [ ] T019 Ask the operator to add `ARIA_LIVE_SIGNAL_OBSERVER_ENABLED=true` (and optionally `ARIA_SIGNAL_TELEGRAM_CHAT_ID`) to `vanguard/backend/.env` (never touched by this session), then redeploy; verify `docker inspect aria-api` shows the gate and logs show `live_signal_observer: started (4 endpoints)`; run quickstart.md Scenario 1 (SC-001/SC-002).
- [ ] T020 Add the 3-line entry to `docs/HANDOFF_PIPELINE_MOMENTUM.md` with the real commit hash, same commit as `.claude/last-deployed-ref`.

---

## Dependencies

- Phase 2 blocks everything. US1 (T004-T009) first; US2 (T010-T012) and US3 (T013-T016) both extend the same file after US1 — sequence US2 then US3 (US3's cooldown/threshold gates US2's send). Polish last.
- T019 depends on an operator action (the `.env` line) — the code deploy (T018) can happen before it; the gate stays OFF until the operator flips it.

## Implementation Strategy

MVP = Phase 1-3 (observations accumulate with `/offpaper` active — already the decoupling the operator asked for). Then US2 (Telegram), then US3 (quality/anti-spam), then deploy + activation.
