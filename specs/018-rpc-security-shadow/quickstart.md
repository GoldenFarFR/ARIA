# Quickstart: RPC Security Shadow

Validation guide. Authoritative shapes live in data-model.md and contracts/.

## Prerequisites

- `ARIA_RPC_SECURITY_SHADOW_ENABLED=true` added to `vanguard/backend/.env` by the operator (never by a session), then `./vanguard/deploy.sh`.
- Chain budget available: `chainstack_ru_budget.daily_status("base")` shows remaining units.

## Scenario 1 — A verdict is reproducible (US1 / SC-001, SC-002)

```python
ev = await rpc_security_engine.evaluate_token(TOKEN, "base", pair_address=PAIR, dex_id=DEX)
assert ev.simulation_block and ev.rpc_provider and ev.state_override_hash   # full context
again = await rpc_security_engine.evaluate_token(TOKEN, "base", pair_address=PAIR,
                                                 dex_id=DEX, block=ev.simulation_block)
assert again.status == ev.status        # same token, same block -> same verdict
```

Then the **historical** case, which is the one that can silently fail: re-run at a block ~24 h old and confirm it still resolves (archive depth was measured at ~926 days on Base, so this must work; if it does not, the endpoint changed and that is a finding).

## Scenario 2 — A trapped token is diagnosed, not just flagged (US1)

On a token known to block sellers: `status == "risky"`, `failure_cause` in the token-evidence subset, and `failure_contract` / `failure_function` populated from the trace. On a token with merely thin liquidity: `status == "unknown"` with `INSUFFICIENT_LIQUIDITY` — **not** `risky`. This pair is the core check that a failed sell is never conflated with a honeypot.

## Scenario 3 — Shadow is provable, not asserted (US2 / SC-003)

Run the AST test and the zero-row-delta test from contracts/. Then, over a live day, confirm the trading decisions are identical to what they were before the feature was enabled.

## Scenario 4 — Cost is measured, with distributions (US3 / SC-004)

```python
await rpc_security_engine.run_benchmark("bench-001", curated_set)   # 50 tokens x 5 categories x 3 tiers
report = await rpc_security_engine.benchmark_report("bench-001")
```

Expected in the report, per category **and** overall: `ru_total` at p50/p95/p99/max (a mean alone is not acceptable), the same for latency, separate `unknown` and `simulation_error` rates, agreement rate, cause-identification rate, and the daily-capacity projection computed from **p95** rather than the mean.

Sanity anchor from the official RU table: ~4 RU per token with trace at the current block, ~12 RU across three tiers. A large divergence between observed and expected RU is itself a finding to report, not a number to quietly accept.

## Scenario 5 — Provider failure never becomes a rejection (operator #7)

Inject: timeout, rate limit, endpoint down, trace unavailable, and `eth_call` OK + trace KO. Required: `unknown` or `simulation_error` every time, never `risky`. The asymmetric case must yield a **valid verdict with `trace_available=0`**, since sellability was genuinely measured.

## Scenario 6 — Concurrency (operator #8)

```python
await rpc_security_engine.probe_concurrency("base", levels=(1, 5, 10, 25))
```

Confirms whether latency or rate limiting degrades before the daily quota binds.

## Scenario 7 — Disagreements stay open questions (US2 / SC-006)

Confirm `ground_truth` is NULL on every row until `adjudicate()` is called, and that setting it requires a source. A disagreement without adjudication must remain visibly unresolved — neither engine wins by default.

## Scenario 8 — Zero regression

`test_momentum_entry.py`, `test_paper_trader.py`, `test_goplus_watchlist.py`, `test_momentum_signal_observation.py` unchanged and green.
