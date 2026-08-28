# Implementation Plan: Robinhood Chainstack-Only Sourcing

**Branch**: `015-robinhood-chainstack-only` | **Date**: 2026-08-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/015-robinhood-chainstack-only/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Remove GeckoTerminal and DexPaprika from the `robinhood_pump` shadow pocket's (v1+v2) discovery/qualification/pricing path, replacing every real call site with direct on-chain reads over Chainstack (RPC + websocket). Research found the real dependency is narrower and more precise than the initial spec assumed: `record_signals` (pass 1) makes zero network calls; `OnChainPoolDiscoveryFeed.check_candidates` (the "primary" on-chain feed) actually falls through to DexPaprika whenever a fresh pool has no observed Sync/Swap event yet -- the direct, confirmed cause of today's `qualified_this_cycle=0` outage; `_snapshot_with_fallback` (shared by pass 2 and pass 3) has a DexScreener-first/GeckoTerminal-reserve-backfill/DexPaprika-reserve-backfill cascade; pass 2 (`evaluate_open_signals`) never received the websocket feed pass 3 already uses. The fix is a targeted on-chain reserve/price/symbol resolver (direct `eth_call`, mirroring a peer session's already-validated pattern) at each of these points, plus wiring the websocket into pass 2, plus an explicit subscription cap (measured at ~66 concurrent, capped at 150) and price provenance fields (`tx_hash`/`block_number`) so SC-002 is mechanically verifiable.

## Technical Context

**Language/Version**: Python 3.11+ (async/await, `aiosqlite`), matching the rest of `packages/aria-core`.

**Primary Dependencies**: `aria_core.services.evm_swap_ws.EVMSwapWebSocketFeed` (Chainstack websocket, extend with a targeted `eth_call` reserve/price/symbol resolver), `aria_core.services.onchain_pool_discovery.OnChainPoolDiscoveryFeed` (the day-zero candidate feed to fix), `aria_core.services.chainstack_ru_budget` (existing shared RU-throughput coordination, reused not duplicated), `aria_core.services.doppler` (existing WETH/BTC USD rate resolution, unchanged), `aria_core.services.dexscreener` (kept -- not in the removal scope, remains the pocket's primary REST price source when the websocket hasn't ticked yet).

**Storage**: SQLite (`shadow.db`, via `shadow_db_path()`) -- `robinhood_pump_shadow_log`/`robinhood_pump_v2_shadow_log` tables gain two new columns (`entry_tx_hash`, `entry_block_number`) for price provenance (SC-002); no other schema change.

**Testing**: `pytest` (`packages/aria-core/tests/`, `pytest-xdist` for full-suite runs), matching every other shadow pocket in this dome.

**Target Platform**: Linux VPS -- the discovery/exit loops run inside `/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py`, a standalone always-on process outside the git repo and outside Docker (see `docs/registre-automatisations.md`); the pocket module itself (`robinhood_pump_shadow.py`/`_v2`) lives in the git repo and is imported by that process.

**Project Type**: Single project (existing monorepo, `packages/aria-core`) -- no new service, no new deployable unit.

**Performance Goals**: No new RPC call added per pool beyond what the DexPaprika path it replaces already cost (1-2 `eth_call`s per pool needing the fallback, never more) -- see research.md's FR-009 section for the measured baseline (~6.56 raw candidates/min, ~66 concurrent).

**Constraints**: Never fabricate a price (dome-wide doctrine, restated in spec.md User Story 2); never duplicate `chainstack_ru_budget`'s throughput coordination; never touch the 14 other GeckoTerminal/DexPaprika-dependent modules (FR-006); zero guardrail file, zero real capital, kill-switch untouched (FR-007).

**Scale/Scope**: Two source files (`robinhood_pump_shadow.py`, `robinhood_pump_v2_shadow.py`, the latter needing no independent change per research.md), one shared service module to extend (`evm_swap_ws.py`), one service module to fix (`onchain_pool_discovery.py`), zero new modules.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Zero-Permission Policy / guardrail boundary**: PASS -- no guardrail file, no real capital, no destructive git operation anywhere in scope (confirmed by the full call-chain trace in research.md).
- **Cohérence architecturale absolue (1bis)**: PASS by design -- the plan explicitly reuses `chainstack_ru_budget` (existing shared throughput point) and `EVMSwapWebSocketFeed`'s existing `add_pool`/snapshot pattern rather than introducing a second parallel mechanism; this is the gate the operator's own review most emphasized (extend, never rebuild in parallel).
- **Doctrine d'ingénierie systémique / funnel pattern**: PASS -- the existing cheap-filter-first funnel (websocket snapshot tried before any `eth_call`, `eth_call` only on candidates the cheap check couldn't resolve) is preserved, not flattened into a brute-force call-everything design.
- **Testability**: PASS, planned -- every new code path (reserve `eth_call`, symbol `eth_call`, subscription cap skip/defer, provenance fields) gets a dedicated test per FR/SC, per this dome's standing norm.
- **Real-limit-before-throttle doctrine ("throughput calibrated... never guessed")**: PASS -- FR-009's 150-subscription cap is derived from a real measurement (see research.md), not an assumed number, with an explicit note to re-measure once DexPaprika is fully removed.
- No violations requiring the Complexity Tracking table below.

**Post-Phase-1 re-check**: data-model.md (two new nullable columns, one new-field dataclass extension, one in-memory counter) and quickstart.md introduce no new external interface, no guardrail touch, and no departure from the funnel/reuse pattern above -- all gates above still PASS unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/015-robinhood-chainstack-only/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── checklists/
│   └── requirements.md  # Spec quality checklist (/speckit-specify command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
packages/aria-core/src/aria_core/
├── robinhood_pump_shadow.py       # v1 pocket -- record_signals (unchanged),
│                                   # _snapshot_with_fallback (DexPaprika/
│                                   # GeckoTerminal backfills removed),
│                                   # evaluate_open_signals (ws_feed wired in)
├── robinhood_pump_v2_shadow.py    # v2 pocket -- no independent fix needed,
│                                   # inherits via the shared functions above
└── services/
    ├── evm_swap_ws.py              # EVMSwapWebSocketFeed -- extended with a
    │                                # targeted eth_call reserve/price/symbol
    │                                # resolver + tx_hash/block_number fields
    │                                # on EVMSwapSnapshot
    ├── onchain_pool_discovery.py   # OnChainPoolDiscoveryFeed.check_candidates
    │                                # -- DexPaprika reserve/price/symbol
    │                                # fallback calls replaced by the new
    │                                # eth_call resolver above
    └── chainstack_ru_budget.py     # existing shared throughput point --
                                     # reused for the new eth_call traffic,
                                     # not duplicated

packages/aria-core/tests/
├── test_robinhood_pump_shadow.py       # existing suite, extended
├── test_robinhood_pump_v2_shadow.py    # existing suite, unaffected (verify)
├── test_evm_swap_ws.py                 # existing suite, extended (resolver
│                                        # + provenance fields)
└── test_onchain_pool_discovery.py      # existing suite, extended (fallback
                                         # removal + subscription cap)

/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py
    # outside the git repo -- the DexPaprika discovery-fallback branch in
    # robinhood_discovery_loop (used only when _ROBINHOOD_DISCOVERY_FEED is
    # None) is removed here, edited in place same as prior sessions' work
    # on this file
```

**Structure Decision**: Single existing project (`packages/aria-core`), no new
module beyond the one new function group (`eth_call` resolver) inside the
already-existing `evm_swap_ws.py` service. This matches research.md's finding
that the fix is narrow and reuses existing infrastructure rather than
introducing a new component.

## Complexity Tracking

No Constitution Check violations -- this table intentionally left empty.
