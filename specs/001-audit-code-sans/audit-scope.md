# Phase 1: bounded audit scope

Drawn from `docs/registre-automatisations.md` and the live gate dump (this
session, `docker inspect aria-api`), not a repo-wide re-discovery. Each row
gets one task in `tasks.md`. This list is a starting bound, not a ceiling --
per FR-002/FR-003, a component surfaces here because a prior document already
named its purpose; the audit does not go hunting beyond this list without a
reason found while auditing it (a caller, a co-located mechanism).

Priority follows spec.md: P1 = never-delivered candidates (the point of this
audit), P2 = orphan code / stale gates, P3 = success-criterion backfill for
survivors.

## P1 candidates -- plausible "ran, never delivered its purpose"

| Component | Expected output (per doc) | Class | Why it's a candidate |
|---|---|---|---|
| x402 seller (`ARIA_X402_SELLER_ENABLED`/`_MAINNET`) | real third-party micropayment sales | 2 | `docs/HANDOFF_X402.md`: only 2 sales ever, same payer both times (05/08), zero traffic since, no Bazaar listing -- structurally undiscoverable by a real payer |
| CabalSpy sourcing (`ARIA_CABALSPY_SOURCING_ENABLED`) | qualified wallet candidates feeding `/walletscore` | 2 | its consumer (`ARIA_WALLET_SCORING_ENABLED`) is now OFF post-removal (verified live this session) -- may be sourcing into a dead pipe |
| Polymarket paper trading (`ARIA_POLYMARKET_PAPER_ENABLED`) | paper bets placed at >=0.85 estimated probability | 1 | CLAUDE.md flags real cadence/volume as "to verify in the code before citing a figure" -- never actually verified in this session |
| `agent_wallet_copy_shadow` (`ARIA_WALLET_COPY_SHADOW_ENABLED`) | a confirmed-outperforming wallet worth real copying | 1 | forward-test on 8 real wallets, fictitious ledgers -- has it ever produced a verdict, or only logs? |
| Farcaster / GitHub / web signal cascade (3 of the 4 `_SIGNAL_CASCADE_ENABLED` sources) | a candidate reaching real convergence in the triage queue | 2 | `signal-cascade-watch` monitors cycle passes and convergence changes but a pass happening is not the same as a candidate ever clearing the bar |
| `candle_staleness_shadow.py` (#261) | enough calibration data to justify becoming a hard gate | 1 | explicitly shadow-only "jamais un hard-gate tant que non calibré" -- has it been running long enough to calibrate, or shadow-forever? |
| Sepolia autonomous pilot (`ARIA_SEPOLIA_AUTONOMOUS_ENABLED`/`_SWAP_ENABLED`) | a real proven swap pipeline before any mainnet move | 1 | testnet-only per CLAUDE.md's Agentic Wallet section -- has a real successful swap ever executed, or only wiring? |

## P2 candidates -- orphan code / stale gate

| Component | Suspicion | Class |
|---|---|---|
| `ARIA_WALLET_SCAN_QUEUE_ENABLED`, `ARIA_WALLET_CANDIDATE_SOURCING_ENABLED`, `ARIA_SMART_MONEY_LEADERBOARD_ENABLED` (all OFF, live-verified) | siblings of the just-removed wallet-scoring mechanism -- may already be dead code not yet cleaned up in the same pass | 3 |
| `ARIA_DAILY_TRADE_FLOOR_ENABLED` (OFF, live-verified) | no HANDOFF reference found yet in this session -- purpose unconfirmed | 3 |
| `ARIA_SCALPING_ONLY_SOURCING_ENABLED` (OFF, live-verified) | scalping v1-v9 all retired 18/08 -- check whether this flag's code path still has a live caller | 3 |
| `ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED` (ON, live-verified) | Robinhood pilot infra confirmed testnet-only as of 23/08 CLAUDE.md entry -- verify this rehearsal has produced anything since | 1 or 3 |

## P3 -- deferred until P1/P2 verdicts land

Success-criterion backfill applies to whatever survives with a "keep"
verdict from P1/P2 -- not scoped item by item here, since the criterion
should be written knowing the real measurement just taken, not guessed in
advance.

## Generalization check (T016, outside the original scope)

| Component | Expected output (per doc) | Class | Verdict |
|---|---|---|---|
| `dip_recovery_shadow` (13/08) | shadow-log an operator-proposed -30%/24h dip-buy signal, enough data to eventually judge it | 1 | Never delivered -- built and wired into the heartbeat scheduler, but `ARIA_DIP_RECOVERY_SHADOW_ENABLED` has never been turned on: 0 rows in both its tables, no entry ever in `heartbeat_state.json`. Neither a bug nor a post-retirement orphan -- simply never activated. |

## Explicitly out of scope for this pass

- Guardrail files and real-capital paths themselves (FR-004) -- reported if
  touched incidentally, never audited as a target.
- Components with a legitimately-zero expected output (kill-switches, circuit
  breakers, `runner-frequency-watch`'s own 0/20 reading) -- spec.md Edge Cases.
- Retired pockets (scalping v1-v9, megacap, Solana FAST discovery) -- already
  closed with an explicit operator verdict, not "never delivered", per
  [[project_shadow_paper_are_ephemeral_scaffolding]].
