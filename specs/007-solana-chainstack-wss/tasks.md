# Tasks: Migrate Solana streaming off Helius onto Chainstack WSS

**Input**: spec.md in this directory. No fixed end date -- closes when no further bug/improvement is found.

- [x] T001 [P1] DONE 25/08 -- confirmed live: Chainstack Solana WSS reachable by deriving it from `ARIA_SOLANA_RPC_HTTP_POLLING` (same host/token, `https://`->`wss://`), no new env var/credential needed. `getVersion` round-trip succeeded.
- [x] T002 [P1] DONE 25/08 -- confirmed via Chainstack's own support docs: WebSocket billing is 1 RU per pushed event, identical shape to `eth_subscribe` on Base/Robinhood -- migrating provider doesn't change per-message cost, only which budget absorbs it.
- [ ] T003 [P1] Measure real push rate for `pumpswap_ws.py` (targeted, per-pool subscription) against a representative sample of currently-tracked pools -- project against typical concurrent open-position counts, compare to the 175k/day Solana cap (already shared with `pumpfun_curve_tracker.py`'s ~50-55k/day polling).
- [ ] T004 [P1] Same measurement for `pumpfun_bonding_ws.py`.
- [ ] T005 [P1] If real headroom confirmed: migrate `pumpswap_ws.py`/`pumpfun_bonding_ws.py` to the Chainstack-derived WSS URL. Reuse the SAME derivation helper for both (never duplicate the https->wss string logic).
- [ ] T006 [P2] Update `pumpfun_trade_stream.py`'s own docstring/comments to explicitly state it stays on Helius/the free-tier fallback (program-wide volume, ~5.7M/day projected, would blow the Solana cap alone) -- cross-reference this spec so it reads as a deliberate exception, not an oversight.
- [ ] T007 [P2] Full test suite + deploy (restart `shadow_persistent.py` if the standalone process is affected).

## Closure

Mark Status "Closed" in spec.md once every task above is done or explicitly deprioritized with a reason, HANDOFF entry in the same commit as the closing change.
