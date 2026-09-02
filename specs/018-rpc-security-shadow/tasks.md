---

description: "Task list for the RPC Security Shadow experiment"
---

# Tasks: RPC Security Shadow

**Input**: Design documents from `/specs/018-rpc-security-shadow/`

**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/rpc_security_engine.md, quickstart.md

**Tests**: Included — this repo requires every shipped capability to ship with a test wired into CI, and three of this feature's guarantees are only meaningful if proven mechanically.

## Format: `[ID] [P?] [Story] Description`

---

## The three exit criteria (operator, verbatim — checked explicitly, not buried)

These are the conditions under which this experiment is considered valid. Each is verified by a named task, and each appears again as a phase checkpoint.

| # | Exit criterion | Verified by |
|---|---|---|
| **1** | Le moteur shadow ne peut physiquement modifier aucune décision. | T008 + T009 (AST proof + zero-row-delta) |
| **2** | Le benchmark compare coût prévu ↔ coût réellement observé. | T024 (observed vs 4/12 RU projection, divergence reported as a finding) |
| **3** | Toute divergence RPC / GoPlus reste analysable sans supposer lequel a raison. | T018 + T019 (`ground_truth` NULL by default, provenance mandatory) |

**Sequencing principle this list must respect** (operator): *mesurer → falsifier → comparer → seulement ensuite remplacer*. Nothing in this file authorises replacing the existing security source.

---

## Phase 1: Setup

- [ ] T001 Read `packages/aria-core/src/aria_core/services/evm_swap_ws.py`'s `_DEX_FAMILY` block and its Aerodrome-stable refusal in `_add_pool_v2v3`, so the engine imports the mapping and reproduces the refusal rather than restating either (constitution §1bis; the module's own docstring forbids a second mapping).
- [ ] T002 [P] Read `packages/aria-core/src/aria_core/services/chainstack_ru_budget.py` (`can_spend`, `record_usage`, `daily_status`, `cap_for`) to confirm the exact call signatures used for RU accounting — this is the sole budget mechanism, never a second one.
- [ ] T003 [P] Confirm, field by field, that data-model.md's three tables already cover the operator's required frozen-dataset record (`token`, `chain`, `pool`, `router`, `block_number`, `simulation_size`, `buy_result`, `sell_result`, `trace_result`, `classification`, `failure_reason`, `provider`, `latency`, `RU_observed`, `goplus_result`, `ground_truth`). Close any gap by extending an existing table — do NOT invent a fourth.

---

## Phase 2: Foundational (blocking)

- [ ] T004 Create `packages/aria-core/src/aria_core/rpc_security_engine.py` with: the gate `rpc_security_shadow_enabled()` (`ARIA_RPC_SECURITY_SHADOW_ENABLED`, OFF by default), `resolve_endpoint(chain)` returning `(https_url, provider_name, endpoint_role)` derived from the chain's WSS variable, and the three-table DDL from data-model.md. `resolve_endpoint` must resolve the provider from **which env var was actually used**, never from the chain name — on Base, `ARIA_BASE_RPC_URL` is Alchemy while `ARIA_BASE_RPC_WS` is Chainstack (FR-004). The credentialed URL never leaves this function (FR-005).
- [ ] T005 [P] Implement the closed failure vocabulary and the status-derivation rules from contracts/ in `rpc_security_engine.py` as pure, network-free functions: `classify_status(...)` and `derive_failure_cause(trace, ...)`. Encode the rule that only reverts originating **inside the token contract** are evidence about the token; `HIGH_SELL_TAX` is a successful sell; `INSUFFICIENT_LIQUIDITY` / `ROUTER_FAILURE` / `RPC_FAILURE` say nothing about honesty; an unmatched trace stays `UNKNOWN`.

**⚠️ SPIKE — blocks every downstream simulation task**

- [ ] T006 **[SPIKE, bounded]** Settle the sell-leg state-override shape empirically on 3-5 representative Robinhood tokens, in `specs/018-rpc-security-shadow/research.md` (append a §5-resolution section). Try both candidates from research.md §5: (a) chained simulation — simulate buy, read the received amount, then simulate sell with a token-balance slot override; (b) single-call bundle if the endpoint supports a sequence. **Deliverable is the chosen shape documented WITH what was tried and rejected**, not code. No benchmark task may start on an assumed shape — this is the one place where an implementation assumption would contaminate every downstream measurement.

**Checkpoint**: gate, provider resolution, tables, classification rules exist; the override shape is decided from evidence.

---

## Phase 3: User Story 1 — Reproducible verdict from the chain (P1) 🎯 MVP

**Goal**: `evaluate_token()` returns a normalized verdict carrying its full reproduction context, and the same token at the same block yields the same verdict.

**Independent Test**: run on a tradeable token and on a seller-trapping token; confirm the verdicts, the diagnosis on the second, and that both carry block + provider + endpoint role + router + amount + override fingerprint.

**Operator ordering note**: this phase runs on **Robinhood first**, reversing plan.md's "Base first" structure. Deliberate: MEOW is on Robinhood and gives the earliest possible test of historical reproducibility, the property that matters most.

### Tests for User Story 1

- [ ] T007 [P] [US1] Test in `packages/aria-core/tests/test_rpc_security_engine.py`: `classify_status` and `derive_failure_cause` over synthetic traces — a revert inside the token contract yields a token-evidence cause; a revert inside pool/router yields `INSUFFICIENT_LIQUIDITY`/`ROUTER_FAILURE` with status `unknown`; a successful sell with a large shortfall yields `HIGH_SELL_TAX`; an unmatched trace stays `UNKNOWN`; **no input path can produce `risky` from an infrastructure failure** (FR-014).
- [ ] T008 [P] [US1] **Exit criterion 1a** — AST-level test that `rpc_security_engine.py`'s identifiers never include `run_paper_cycle`, `open_position`, `_default_momentum_analyzer`, `process_active_orders`, `evaluate_hard_gates`, `_check_honeypot`, `send_trading_notification`. Parse with `ast`, not substring matching, so the docstring may name them to explain the ban.
- [ ] T009 [P] [US1] **Exit criterion 1b** — zero-row-delta test: row counts in `paper_position`, `pending_limit_order` and `momentum_signal_observation` are unchanged across a full evaluate + compare + benchmark run against an isolated DB.
- [ ] T010 [P] [US1] Test: an exhausted chain budget short-circuits before any network call and yields `simulation_error` with an explicit budget reason (FR-011); an unmapped `dex_id` and an Aerodrome **stable** pool both yield `unknown`/`ROUTER_FAILURE` without calling out.

### Implementation for User Story 1

- [ ] T011 [US1] Implement `evaluate_token(contract, chain, *, pair_address, dex_id, amount_tier, block=None)` in `rpc_security_engine.py` per contracts/: resolve endpoint → resolve `dex_family` via the imported `_DEX_FAMILY` → check `chainstack_ru_budget.can_spend` → simulate buy → simulate sell (shape from T006) → on failure `debug_traceCall` → record RU via `chainstack_ru_budget.record_usage` → return the normalized result. Trace unavailable must yield a **valid verdict with `trace_available=0`**, never an error.
- [ ] T012 [US1] Persist evaluations to `rpc_security_evaluation` (append-only), with amounts stored as strings (never floats — 18-decimal precision loss) and non-null `simulation_block`, `rpc_provider`, `rpc_endpoint_role`, `state_override_hash` on every row (SC-001).
- [ ] T013 [US1] Same-block reproducibility check in `rpc_security_engine.py` + its test: re-running a token at its recorded block returns the same status (SC-002). **A same-block divergence is a DEFECT** — distinguish this clearly in code comments and test names from cross-block drift (T021), which is a finding.

**Checkpoint (exit criterion 1)**: T008 and T009 both green — the engine is provably incapable of touching a decision.

---

## Phase 4: User Story 2 — Compare without assuming who is right (P2)

**Goal**: every evaluation pairs with the existing source's verdict, classified agreement/disagreement/unknown, with adjudication possible and never automatic.

**Independent Test**: run over candidates with mixed outcomes; confirm one comparison row each, disagreements individually retrievable, `ground_truth` NULL until adjudicated.

### Tests for User Story 2

- [ ] T014 [P] [US2] Test: the comparator reads the existing verdict via `goplus_watchlist.get_fresh` only — a test asserting no fresh paid GoPlus call is issued (spending the quota under measurement would distort the measurement). No cached verdict → `outcome="unknown"` on that side, never a triggered call.
- [ ] T015 [P] [US2] **Exit criterion 3** — test that `ground_truth` is NULL on every freshly written comparison, that `adjudicate()` requires a `ground_truth_source` in `{manual, onchain_check}`, and that **no code path derives `ground_truth` from either engine's verdict**.

### Implementation for User Story 2

- [ ] T016 [US2] Implement `compare_with_existing_source(evaluation)` in `rpc_security_engine.py`, writing to `rpc_security_comparison` with both verdicts, the cached verdict's freshness, and the outcome classification.
- [ ] T017 [US2] Implement `adjudicate(comparison_id, verdict, source, note)` — the only writer of `ground_truth`, requiring provenance.
- [ ] T018 [US2] Add a retrieval helper listing unadjudicated disagreements with everything needed to judge them (both verdicts, failure diagnosis, full reproduction context), so a disagreement stays a visible open question rather than a silent one.

**Checkpoint (exit criterion 3)**: a high agreement rate can never be mistaken for correctness — neither source is the reference.

---

## Phase 5: User Story 3 — Controlled benchmark, hard cases mandatory (P3)

**Goal**: measured cost distributions, separate unknown/error rates, agreement, cause-identification, and the daily-capacity projection — over a corpus that deliberately includes the difficult cases.

**Independent Test**: run the benchmark over the curated corpus; confirm every metric is present per category and overall, and that the per-token cost is observed, not assumed.

### Corpus and the Robinhood-first sequence

- [ ] T019 [US3] Assemble the curated corpus in `specs/018-rpc-security-shadow/corpus.md` covering **all eleven** operator-mandated categories: healthy · known honeypot · high sell tax · **dynamic tax** · **maxTx** · **maxWallet** · **trading disabled** · insufficient liquidity · **problematic router/pool** · **RPC failure** · **genuinely ambiguous**. This widens spec.md's original five. Rationale to record in the file: *"sinon V1 peut paraître excellente simplement parce que les cas difficiles n'ont jamais été testés."* Source candidates from ARIA's own history (`momentum_scan_log` has 119,958 rows with `hold_reason`; `counterfactual_rejection` has 36,637 with outcomes) plus known public cases.
- [ ] T020 [US3] Run the **Robinhood/MEOW validation first** (before any Base work): evaluate MEOW at the historical blocks matching the 11 buys of 2026-09-01 between 15:29 and 17:48 UTC (wallet `0xe38b36eBF2d1494099c1Ba8Eb4Fc0339913166C6`), confirming the T006 override shape works on a real historical case and that archive depth suffices. Confront the results with MEOW's real period traces already in ARIA (`momentum_scan_log`, `counterfactual_rejection`, `base_momentum_shadow_log_archive`). **This is the earliest test of the property that matters most.**
- [ ] T021 [US3] Implement and report **drift** as its own measurement: same token, current block vs historical block, recording whether the verdict changed and the cause on each side. Operator's framing to encode in the report: a cross-block change *"n'est pas une erreur : c'est potentiellement l'information historique recherchée"* — e.g. `SELL_OK/5%` at one block and `SELL_REVERT/TRADING_DISABLED` at another is a finding about the contract. Keep this strictly separate from T013's same-block check, which remains a correctness test.

### Benchmark, cost and negative data

- [ ] T022 [US3] Implement `run_benchmark(run_id, tokens_by_category)` — each token × 3 amount tiers (`small`, `mid`, `liquidity_relative`) × (buy, sell, trace), persisting every attempt to `rpc_security_benchmark_run`. Note in the code why three tiers exist: they are what **separates `MAX_TX` from `TRADING_DISABLED`** (the former yields to a smaller amount, the latter does not) and what reveals a dynamic tax — load-bearing for the diagnosis, not just a cost sweep.
- [ ] T023 [US3] **Negative data is a result, not garbage** — implement and test that `UNKNOWN`, `simulation_error`, disagreements and impossible simulations are persisted and surfaced as first-class counts in the report. Add a test asserting **no code path drops a row because it failed**. Operator: these *"sont probablement les données les plus utiles pour savoir où le moteur doit s'arrêter et où GoPlus reste nécessaire."*
- [ ] T024 [US3] **Exit criterion 2** — implement `benchmark_report(run_id)` producing, per category **and** overall: `ru_buy`/`ru_sell`/`ru_trace`/`ru_total` and `latency_ms` each as **p50/p95/p99/max** (a mean alone is explicitly not acceptable); separate `unknown` and `simulation_error` rates; agreement and cause-identification rates; and the daily-capacity projection computed from **p95**, not the mean. The report must state **observed RU against the 4 RU/token (with trace) and 12 RU/token (three tiers) projection** from the official rate table — a divergence is itself a finding to report, never a number to quietly accept.
- [ ] T025 [P] [US3] Implement `probe_concurrency(chain, levels=(1,5,10,25))` and report RU, latency, error and rate-limit rates per level — the instantaneous ceiling may bind long before the daily quota (the existing source's 10-per-minute limit shows this is real in this domain).
- [ ] T026 [P] [US3] Failure-injection tests: timeout, rate limit, endpoint unavailable, trace unavailable, and the asymmetric case (`eth_call` OK / trace KO). Required outcome every time: `unknown` or `simulation_error`, **never `risky`**, never a rejection. The asymmetric case must give a valid verdict with `trace_available=0`.
- [ ] T027 [US3] Only after Robinhood validates: run the wider benchmark on **Base** over the full corpus, and report both chains side by side.

**Checkpoint (exit criterion 2)**: predicted cost vs observed cost is stated explicitly, with distributions.

---

## Phase 6: Polish

- [ ] T028 [P] Correct the stale comment in `packages/aria-core/src/aria_core/services/goplus.py` asserting *"No monthly/daily GoPlus cap has ever been confirmed"* — the official Free tier is 150,000 CU/month, 30,000 CU/day, 150 CU/min, and those caps are the real binding constraint. Scope strictly the comment; no behaviour change. (`goplus_watchlist.py`'s docstring already had it right — note the contradiction is now resolved in favour of the correct one.)
- [ ] T029 Run the full test file plus the regression set (`test_momentum_entry.py`, `test_paper_trader.py`, `test_goplus_watchlist.py`, `test_momentum_signal_observation.py`) — aggregate counts only, per this repo's context-economy rule.
- [ ] T030 Run quickstart.md's eight scenarios end-to-end against a test `DATA_DIR`.
- [ ] T031 Write the 3-line entry in `docs/HANDOFF_SECURITE.md` (component HANDOFF, per CLAUDE.md's imposed format) with the real commit hash, in the same commit as the code.

**No deployment task here by design** — the operator wants to review this decomposition before any implementation begins, and the gate stays OFF until he adds the `.env` line himself.

---

## Dependencies

- **T006 (spike) blocks T011, T020, T022, T027** — no simulation task may start on an assumed override shape.
- T004 blocks everything in Phases 3-5.
- US1 (T007-T013) before US2 (T014-T018): the comparator needs an evaluation to compare.
- US3's benchmark (T022+) needs both US1 and US2 complete, since the report includes agreement rates.
- **T020 (Robinhood/MEOW) before T027 (Base)** — deliberate reversal of plan.md's ordering, on the operator's instruction.
- T021 (drift) depends on T013 existing, so the correctness check and the drift finding never get conflated.

## Parallel opportunities

- T002 and T003 (different files, pure reading).
- T007, T008, T009, T010 — four independent tests in the same new file; coordinate on the file, not on logic.
- T014 and T015 likewise.
- T025 and T026 are independent of each other and of T024.

## Implementation Strategy

**MVP = Phases 1-3.** A reproducible, provably shadow verdict engine — already useful on its own, and already answering operator questions 1 (detection) and 3 (replay). Then US2 adds the comparison without assuming who is right, and US3 produces the cost figures that make the eventual replacement decision possible.

**What this list deliberately does NOT contain**: any task that moves the existing security source off the critical path. That decision is what this experiment exists to inform, and it belongs to the operator once the numbers exist.
