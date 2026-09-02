# Phase 0 Research: RPC Security Shadow

Every figure below was measured live against the real endpoints during this session, or read from the provider's official documentation. Nothing here is estimated from memory.

## 1. Method availability — the blocker is cleared on BOTH chains

**Decision**: build on `eth_call` with `stateOverride` for the two simulation legs, and `debug_traceCall` for the failure diagnosis. Base and Robinhood are both in scope from day one.

**Measured** (HTTPS derived from the WSS endpoint, per this repo's standing rule):

| Method | Base | Robinhood |
|---|---|---|
| `eth_call` + `stateOverride` | available | available |
| `debug_traceCall` | available | available |
| `eth_createAccessList` | available | (not needed) |
| `eth_estimateGas` | available | (not needed) |

**Rationale**: without `stateOverride` there is no way to simulate a buy without holding funds, and the whole V1 collapses. Without `debug_traceCall` the engine could only report *that* a sell fails, never *why* — which is exactly the diagnosis capability that distinguishes this engine from the existing source (operator question 2).

**Alternatives considered**: `eth_estimateGas` alone as a cheap sellability probe — rejected as the primary signal: a gas estimate failure conflates "reverts" with "insufficient funds/allowance" and yields no structured cause. Kept as a possible cross-check, never as the verdict.

## 2. Archive depth — the historical replay is possible, far beyond what MEOW needs

**Decision**: historical-block simulation is a first-class capability, not a best-effort extra.

**Measured on Base**, `eth_call` + `stateOverride` at increasing depth from head (block 50,778,213):

| Depth | Approx. age | Result |
|---|---|---|
| 128 / 1 000 / 43 200 blocks | up to 24 h | OK |
| 86 400 / 200 000 blocks | 48 h / 111 h | OK |
| 500 000 / 2 000 000 | 11.6 d / 46 d | OK |
| 10 000 000 / 40 000 000 | 231 d / **926 d** | OK |

This is a full archive node, not a pruned one. The MEOW replay target (2026-09-01, ~24 h back, on Robinhood) sits trivially inside this range.

**Consequence for the design**: `simulation_block` is not decoration — a stored verdict can genuinely be re-executed. This is the property the existing source structurally cannot offer, and it is what makes the operator's P2 forensic replay possible at all.

## 3. Real cost — measured against the official RU table, to be confirmed empirically

**Decision**: cost model = **1 RU per standard call, 2 RU per archive/debug/trace call**, with no per-method multiplier (Chainstack bills per call, not per payload weight).

Derived per-token cost, and the resulting capacity against the headroom measured today (base 76,453 RU free; robinhood 580,898 RU free):

| Configuration | RU/token | base capacity/day | robinhood capacity/day |
|---|---|---|---|
| buy + sell, current block, no trace | 2 | 38,226 | 290,449 |
| buy + sell + trace, current block | 4 | 19,113 | 145,224 |
| the above × 3 simulation sizes (FR from operator #3) | 12 | **6,371** | 48,408 |
| buy + sell + trace at a historical block | 6 | 12,742 | 96,816 |

Against the ~2,000 verifications/day the pipeline actually needs, even the most expensive configuration leaves a **3× margin on Base** and far more on Robinhood — using only the budget that is currently idle.

For contrast, the constraint this feature exists to measure: the existing source's free tier allows 2,000 verifications/day **and only 10 per minute**.

**Rationale**: this is the single number that decides feasibility, so it is stated with its provenance. **It remains a projection from the official table** — the benchmark (User Story 3) must confirm it empirically, per the operator's explicit requirement that the RU/token figure be measured and never estimated. The benchmark therefore records observed RU alongside expected RU, and any divergence is itself a finding.

**Alternatives considered**: assuming a per-method weighting similar to other providers' compute-unit models — rejected after reading Chainstack's own documentation, which states there are no per-method multipliers.

## 4. Router resolution — reuse the existing DEX-family mapping, never a new one

**Decision**: resolve the swap path from `PairSnapshot.dex_id` through the mapping `evm_swap_ws._DEX_FAMILY` already maintains, and build calldata per family (v2 / v3 / v4), never per individual DEX.

The existing mapping (verified in code) already covers Base's real landscape: `uniswap_v2` → v2, `uniswap_v3` → v3, `uniswap_v4` → v4, `aerodrome_slipstream_3` and `aerodrome_v3` → v3 (Slipstream emits the identical Swap event, verified live 24/08), `aerodrome` → v2 (volatile pools only).

**Two constraints inherited from that module, to honour rather than rediscover**:
- Aerodrome **stable** pools are deliberately excluded — they use a stableswap curve the existing decoder does not implement, and `add_pool` refuses them rather than compute a wrong price. This engine must refuse them the same way, resolving to `UNKNOWN`, never a fabricated verdict.
- An unmapped `dex_id` is fail-open in the existing decoder. Here it must resolve to `UNKNOWN` with reason `ROUTER_FAILURE`, never to `RISKY`.

**Rationale**: the module docstring is explicit — *"Extend `_DEX_FAMILY` once verified, never guess a new mapping in."* Duplicating a second, divergent DEX table is exactly the defect constitution §1bis forbids.

**Alternatives considered**: simulating against a universal aggregator router — rejected: it would add an external dependency inside the very check meant to remove one, and would attribute an aggregator's own failure to the token.

## 5. State override shape — the open implementation question

**Decision**: the buy leg needs only a native-balance override on a synthetic caller (verified working). The **sell leg is the real unknown**: selling requires the caller to already hold the token, which means overriding an ERC-20 balance slot — and the slot index differs per contract layout.

Two candidate approaches, to settle during implementation with a real token, not by assumption:
1. **Chained simulation** — simulate the buy, read the received amount, then simulate the sell in a second call whose state override sets the token balance from the buy result. Requires locating the balance slot.
2. **Single-call bundle** — where the endpoint supports simulating a sequence, buy and sell resolve in one pass with no slot manipulation.

Whichever is chosen, the `state_override_hash` recorded on the result must be a fingerprint of the actual override applied (FR-003), because two different override shapes can produce two legitimately different verdicts for the same token and block — and without the fingerprint a later replay would look like a contradiction.

**Rationale**: this is the one genuinely unresolved technical point. It is scoped to implementation rather than guessed here, because getting it wrong silently produces plausible-looking wrong verdicts — the failure class this whole feature is meant to eliminate.

## 6. Comparison verdict — obtain it for free, never spend the budget being measured

**Decision**: read the existing source's verdict from its cached watchlist entry (`goplus_watchlist.get_fresh`, 48 h freshness) rather than issuing a fresh call.

**Rationale**: spending the very quota this experiment is measuring, in order to measure it, would both distort the measurement and consume 62 % of a monthly budget that is already the constraint under study. When no cached verdict exists, the comparison is recorded as `unknown` on the existing-source side — never by triggering a new paid call.

## 7. Failure-cause derivation — how each structured reason is obtained

**Decision**: the closed vocabulary maps to observable evidence, and anything unmatched stays `UNKNOWN` rather than being forced into the nearest label.

| Cause | Derived from |
|---|---|
| `SELL_REVERT` | sell leg reverts, trace shows the revert originating in the token contract |
| `TRANSFER_RESTRICTED` | revert inside the token's transfer path (blacklist/whitelist guard) |
| `MAX_TX` / `MAX_WALLET` | revert on an amount-bound check; distinguished by re-simulating a smaller amount succeeding |
| `TRADING_DISABLED` | revert on a global trading flag, reproduced across all simulation sizes |
| `HIGH_SELL_TAX` | sell **succeeds** but the received amount is far below the buy leg's implied value |
| `INSUFFICIENT_LIQUIDITY` | revert originating in the pool/router, not the token contract |
| `ROUTER_FAILURE` | unmapped DEX family, unsupported pool type (e.g. Aerodrome stable) |
| `RPC_FAILURE` | timeout, rate limit, endpoint error — never a statement about the token |
| `UNKNOWN` | trace available but matching none of the above |

**The critical rule this encodes** (operator's "main analytical trap"): `HIGH_SELL_TAX` is a *successful* sell, and `INSUFFICIENT_LIQUIDITY` / `ROUTER_FAILURE` / `RPC_FAILURE` say nothing about the token's honesty. Only the first four are evidence of a token that traps sellers. A failed sell simulation is never, by itself, "honeypot".

The size-dependency test (operator requirement #3) is not merely a cost measurement — it is what **separates** `MAX_TX` from `TRADING_DISABLED`, and what reveals a dynamic tax. It is therefore load-bearing for the diagnosis, not optional.

## 8. Provider attribution — mandatory, because two providers serve the same chain

**Decision**: every result records the provider identity and the endpoint's role, resolved from which environment variable was actually used, never inferred from the chain name.

**Measured trap**: on Base, `ARIA_BASE_RPC_URL` points to **Alchemy** while `ARIA_BASE_RPC_WS` points to **Chainstack**. A system assuming "Base RPC = Chainstack" would attribute Alchemy-produced verdicts to Chainstack. Six months later, a replay mixing both would be indistinguishable from a genuine change in contract behaviour.

The credentialed URL is never persisted — role only (this repo has had two real secret leaks through Bash in July; the rule is absolute).

## 9. Failure posture — shadow means shadow, including when the provider fails

**Decision**: every infrastructure failure resolves to `SIMULATION_ERROR` (distinct from `UNKNOWN`), and no failure path may ever produce `RISKY`.

The asymmetric case the operator specifically named — `eth_call` works but tracing does not — resolves to a **valid verdict with an unavailable diagnosis**, not to an error: sellability was genuinely measured, only the explanation is missing.

**Rationale**: this keeps the two rates the operator wants separated end-to-end — `UNKNOWN` measures the engine's ability to conclude, `SIMULATION_ERROR` measures the infrastructure's reliability. Merged, a bad week from the provider would look like a weak engine.

**On fail-closed**: the existing source's policy on unavailability is fail-closed for holder concentration (documented 03/08) and queue-and-retry for honeypot. This engine measures those policies; it does not reproduce them, because reproducing a rejection policy while in shadow would be a decision, and the engine takes none.
