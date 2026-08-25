# Feature Specification: Migrate Solana streaming off Helius onto Chainstack WSS

**Feature Branch**: `007-solana-chainstack-wss`

**Created**: 2026-08-25

**Status**: In progress (closes when no further bug/improvement is found, same doctrine as `001-audit-code-sans`/`005-discovery-budget`/`006-onchain-dayzero-entry`)

**Input**: Operator-directed (25/08), after spotting Chainstack's own Solana node dashboard (HTTPS+WSS endpoints, same `*.core.chainstack.com` shape already used for Base/Robinhood): "arrete d'utiliser autre chose que le rpc de chainstack, sa fait 20 fois que tu le saoul a utiliser d'autres fournisseurs" -> "oui supprime helius des archives et regarde wss et fait le prochain spec".

## Background (verified facts, 25/08, never assumed)

- **The Chainstack Solana node already has a WSS endpoint, confirmed live**: deriving it from the already-configured `ARIA_SOLANA_RPC_HTTP_POLLING` value (same pattern already used elsewhere in this dome: same host/token, just `https://` -> `wss://`) connects successfully -- a real `getVersion` call over this WSS returned `{"solana-core": "4.2.0"}`. No new credential/env var needed, this dome already has everything required.
- **Billing model confirmed identical to EVM, verified via Chainstack's own support docs**: "each event sent [over WebSocket] will count as 1 request" -- same 1-RU-per-push rule already established for `eth_subscribe` on Base/Robinhood. Migrating provider does NOT change the per-message cost shape, only which budget absorbs it.
- **Prior operator decision found in the code, now being reversed**: `solana_late_bonding_shadow.py`/`pumpswap_ws.py` both carry a 21/08 comment, "on a dit tout par le RPC Helius" -- Helius was explicitly chosen for ALL Solana RPC at the time. This spec supersedes that decision on the operator's own direction today; noted for the historical record, not re-litigated.
- **A real Helius reliability incident already happened** (`pumpswap_ws.py`, 22/08 comment): "Helius' monthly quota ran out and answered 429" -- a concrete prior outage, not just tonight's `max usage reached` on a throwaway measurement script. Reinforces the case for centralizing on Chainstack, which this dome already has a calibrated, dedicated daily budget for (175k/day, `chainstack_ru_budget.py`).
- **Not every Helius-connected flow has the same volume, and volume is what decides migration safety** -- verified live via `grep`, three real consumers exist:
  - `pumpfun_trade_stream.py`: PROGRAM-WIDE (every pump.fun trade, not just tracked positions) -- its own docstring measured "~6650 trades/100s" (~239k/hour, ~5.7M/day if that rate held). This is explicitly why it was pinned to Helius in the first place (own comment: "the dome's highest-volume subscription... on the endpoint with the tightest per-IP limits" would have been the free public RPC otherwise). Migrating this ONE flow to Chainstack at 1 RU/push would blow the entire 175k/day cap on its own, many times over -- same shape as the Base/Robinhood v4-fanout incident this dome already fixed once (`evm_swap_ws.py`'s two-separate-subscriptions fix, 24/08).
  - `pumpswap_ws.py`/`pumpfun_bonding_ws.py`: targeted, per-pool/per-mint subscriptions (add/remove as positions open/close), same architecture as `EVMSwapWebSocketFeed` -- volume scales with open positions, not the whole chain. Real headroom unmeasured yet, but structurally comparable to Base/Robinhood's own WS feeds, which stayed well under their caps.
  - `jupiter_swap_signer.py`: not a subscription at all -- uses Helius for one-shot RPC calls (sign/send a real swap transaction). Different risk category entirely (touches real capital execution path), out of THIS spec's scope (discovery/pricing feeds only) unless the operator explicitly extends it.

## User Scenarios & Testing

### User Story 1 - Measure each targeted feed's real volume before migrating (Priority: P1)

**Why this priority**: same anti-brute-force doctrine as every prior spec here -- never guess a throughput number, especially when a wrong guess could blow a shared daily cap silently.

**Independent Test**: for `pumpswap_ws.py` and `pumpfun_bonding_ws.py`, measure the real push rate per currently-open position (or a representative sample), project against the number of positions typically open, and compare to Chainstack's 175k/day Solana cap (already shared with `pumpfun_curve_tracker.py`'s own polling, ~50-55k/day).

**Acceptance Scenarios**:
1. **Given** the real per-position push rate, **When** projected against typical concurrent open-position counts, **Then** report whether real headroom exists under the 175k/day cap alongside the existing polling load.

### User Story 2 - Migrate targeted (non-program-wide) feeds to Chainstack WSS (Priority: P1)

**Why this priority**: this is the actual ask -- centralize on Chainstack wherever the volume is safe to.

**Independent Test**: `pumpswap_ws.py`/`pumpfun_bonding_ws.py` connect to the Chainstack-derived WSS URL instead of `ARIA_SOLANA_RPC_WS` (Helius), verified live (real pool/mint tracked, real price ticks received), RU usage visible in `chainstack_ru_budget.py`'s existing `solana` counter.

**Acceptance Scenarios**:
1. **Given** the migration is live, **When** a tracked pool/mint receives a real trade, **Then** the tick is captured via the Chainstack WSS connection, not Helius.
2. **Given** the daily Solana RU counter, **When** read after migration, **Then** it reflects this new traffic without exceeding the calibrated cap.

### User Story 3 - Leave the program-wide feed on Helius, explicitly, not silently (Priority: P2)

**Why this priority**: `pumpfun_trade_stream.py` cannot move without either a much larger Solana Chainstack budget or a narrower subscription filter (neither exists today) -- this must be a DOCUMENTED exception, never an accidental leftover that looks like unfinished migration.

**Independent Test**: the module's own docstring/comments updated to state plainly why it stays on Helius (or `ARIA_SOLANA_RPC_WS_STREAM`'s free-tier fallback), cross-referencing this spec.

### Edge Cases

- Chainstack Solana WSS connection drops -- same reconnect-backoff doctrine already proven on the EVM feeds, never a silent permanent gap.
- The 175k/day Solana cap is ALREADY shared with `pumpfun_curve_tracker.py`'s polling -- migrating even a targeted feed onto the same budget needs real headroom math, not an assumption that "targeted = automatically cheap".

## Requirements

### Functional Requirements

- **FR-001**: `pumpswap_ws.py` and `pumpfun_bonding_ws.py` MUST connect via the Chainstack-derived WSS URL (same host/token as `ARIA_SOLANA_RPC_HTTP_POLLING`, protocol swapped) once real headroom is confirmed -- never migrated on a guess.
- **FR-002**: `pumpfun_trade_stream.py` MUST stay on its current provider (Helius or the free-tier stream fallback), with an explicit, updated comment stating why -- never silently left behind.
- **FR-003**: Any RU consumed by the migrated feeds MUST be visible in the existing `chainstack_ru_budget.py` per-chain (`solana`) counter -- no new parallel budget.
- **FR-004**: `jupiter_swap_signer.py`'s use of Helius stays untouched -- out of scope (real-capital execution path, not a discovery/pricing feed).

## Success Criteria

- **SC-001**: Every targeted (non-program-wide) Solana WS feed runs on Chainstack, verified live, with a measured RU cost that leaves real headroom under the 175k/day cap.
- **SC-002**: The one deliberately-kept-on-Helius feed (`pumpfun_trade_stream.py`) is documented as an explicit, reasoned exception, not an oversight.

## Assumptions

- Shadow/paper parameters and provider choices here are revisited freely as real usage data accumulates, same doctrine as every prior spec.
- Real-money guardrails and `jupiter_swap_signer.py`'s real-capital execution path stay outside this spec's scope.
