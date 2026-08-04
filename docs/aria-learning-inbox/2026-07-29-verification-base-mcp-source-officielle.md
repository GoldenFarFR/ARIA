# Base MCP -- verification against the official source (07/29)

**Trigger**: the operator shared an X link (`x.com/i/status/2059305572793508045`,
redirects to `x.com/base/status/...`) asking whether it's relevant for ARIA.
Verified live via browser (certified `@base` account, real content captured via
screenshot + text extraction -- not a search inference, since a Google search
returned nothing usable on this specific link).

**Closes an open branch from 07/16** (`2026-07-16-veille-base-198-jesse-pollak-
leadership-grants.md`): "Base MCP (May 2026) -- verify against the official source
(not just a third-party summary) what it actually covers before treating it as a
serious lead." Done -- primary source read in full.

## What it actually is

Official `@base` post, May 26 2026 (so in place for ~2 months, not a same-day
novelty -- only its actual scope had never been verified). **Base MCP** connects
a human's **Base Account** (their Base App account) to any MCP-compatible client
-- the list cited EXPLICITLY includes **Claude Web/Desktop/Code, ChatGPT, Codex,
Cursor**. Once authenticated (OAuth 2.1, same standard as "Sign in with Google"),
the agent can: track the wallet, view history, send funds, swap, and use "skill
plugins" for apps in the Base ecosystem.

**Skills at launch**: Moonwell (lending), Morpho (lending/vaults), Uniswap (swaps/
liquidity), Avantis (perps), Bankr (token launches), Aerodrome (LP/swaps),
Virtuals (agent/token launches). One of the explicitly listed capabilities:
**"Pay for x402 enabled services."**

## Security model -- the most important point for ARIA

"Nothing happens onchain without your explicit approval." The MCP server **never
holds or accesses private keys**. The agent builds the transaction and stores it
as a "pending request" (the "stored requests" primitive, already used for
Shopify Base Pay payments); a link is sent, the human's Base Account opens it in
a separate window, simulates the asset changes, and the user confirms or
cancels. **No autonomous execution without human confirmation, ever,
structurally** -- philosophy identical to ARIA's absolute rule on real capital
(human validation before any mainnet movement), but carried by a native
Base/OAuth rail rather than Telegram/`wallet_guard.escalate_spend`.

## Relevance to ARIA -- integration verdict

- **Does NOT overlap with ARIA's existing data clients** (DexScreener/GeckoTerminal/
  Blockscout/GoPlus) -- Base MCP is EXECUTION-oriented on behalf of a human's
  account, not market-data reading for an analysis engine. Directly answers the
  question left open on 07/16: different scope, no duplication, nothing to
  replace in the analysis pipeline.
- **Governance lead to compare someday (not urgent, nothing built)**: the
  "stored request + Base Account confirmation link" model could be a future
  alternative or complement to `wallet_guard.escalate_spend` (Telegram) for the
  real agent-wallet pilot -- but it ALWAYS requires human confirmation per
  transaction, so it neither replaces nor competes with the bounded autonomous
  execution already granted to ARIA on its pilot (Named Exceptions #3/#4, 10-15$
  cap): this would be a rail for direct human use (the operator themselves, from
  Claude Code), not for ARIA acting autonomously.
- **Potential request channel for ARIA's future x402-seller** (Item #39/#188,
  dormant, gates OFF): the "Pay for x402 enabled services" capability listed in
  Base MCP means a third-party user could someday pay for ARIA's composite
  wallet score via this rail, without ARIA needing to build its own payer
  client. Keep in mind if/when the x402-seller goes to mainnet -- nothing to do
  now.
- **Confirms the legitimacy of choices ARIA already made**, without adding
  anything new to build: the 7 launch skills (notably Morpho, Virtuals, Bankr,
  Uniswap) overlap with protocols already identified/integrated on ARIA's side
  (Morpho Yield module evaluated #32/Item #194 pending, Virtuals bonding already
  in prod, Bankr Doppler client already built #93/94) -- Base itself validates
  these protocols as ecosystem pillars.

## Detail found in passing, unrelated to Base MCP

The `@base` account bio states literally: **"Base is beginning to explore a
network token."** First-hand signal (not a third-party rumor, the official
account itself) -- connects directly to `2026-07-27-diligence-launchpads-
tokenisation-aria.md` (ARIA tokenization diligence). No concrete detail beyond
this sentence as of today.

## Open branches

- Periodically re-check whether "Base is beginning to explore a network token"
  materializes (timeline, mechanism, implications for the competitive landscape
  of a potential ARIA tokenization).
- If ARIA's x402-seller ever goes to mainnet, concretely check whether Base MCP
  becomes a real access path for third-party payers (not just theoretical).
- Formally compare (dedicated session, not now) the "stored request + Base
  Account link" model vs `wallet_guard.escalate_spend` if the operator ever
  wants an alternative approval channel to Telegram for real capital.
