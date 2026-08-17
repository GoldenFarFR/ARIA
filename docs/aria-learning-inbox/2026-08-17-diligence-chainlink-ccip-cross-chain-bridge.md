# Diligence -- Chainlink CCIP as the cross-chain bridge candidate (Base<->Solana)

Date: 2026-08-17
Trigger: operator request ("fait ta diligence sur chainlink"), in the context
of a future agent-wallet cross-chain need (consolidating/withdrawing between
Base and Solana, ARIA's own multi-chain footprint for the 1M$ test).
Nothing built, nothing wired -- this is a banked infrastructure decision for
whenever a real cross-chain transfer need appears. Depth calibrated to the
"touches a future architecture/security decision" tier (CLAUDE.md
"Anticipation" doctrine), not a quick lookup.

## Verdict

**Chainlink CCIP is the candidate**, for the bridging leg specifically (a
separate question from Chainlink Price Feeds, see "Out of scope" below).
Retained after comparing it against the two dominant alternatives
(LayerZero, Wormhole) and finding both carry a real, recent security
incident CCIP does not.

## What convinced

- **Layered, independently-operated security.** Two DONs (commit + execute)
  plus a fully separate Risk Management Network -- different codebase (Rust),
  different node operators, different team -- that re-validates every
  message before a destination contract will act on it. Minimum 16 node
  operators. ([Chainlink blog](https://blog.chain.link/ccip-risk-management-network/))
- **No CCIP exploit to date**, against two very recent competitor incidents:
  LayerZero ~$292-300M stolen April 2026 (a default 1-of-1 DVN quorum let one
  poisoned node forge a message), Wormhole $325M in 2022 plus a multi-week
  $1.4B freeze on its USDC bridge in 2025. ~$4B migrated into CCIP-connected
  infra in the weeks after the LayerZero incident; Kraken replaced LayerZero
  with CCIP; Coinbase picked CCIP as its exclusive bridge infra for Coinbase
  Wrapped Assets. ([The Block](https://www.theblock.co/post/272827/chainlink-circle-cctp-usdc),
  [Bitcoin.com](https://news.bitcoin.com/kelpdao-slams-layerzero-after-300m-exploit-shifts-rseth-to-chainlink-ccip/),
  [PR Newswire -- Coinbase](https://www.prnewswire.com/news-releases/coinbase-selects-chainlink-ccip-as-the-exclusive-bridge-infrastructure-to-supercharge-coinbase-wrapped-asset-growth-302638753.html))
- **Covers ARIA's actual multi-chain footprint.** CCIP v1.6 is native on both
  Base and Solana (Solana was CCIP's first non-EVM chain). A Coinbase+
  Chainlink co-secured Base<->Solana bridge has been live on mainnet since
  December 2025 -- the exact two chains the 1M$ momentum test already
  trades. ([PR Newswire](https://www.prnewswire.com/news-releases/chainlink-ccip-is-officially-live-on-solana-supercharging-the-growth-of-solana-defi-by-unlocking-access-to-19b-of-assets-302458899.html),
  [Bankless](https://www.bankless.com/base-launches-solana-bridge-via-chainlink-ccip))
- **Custody model fits what's already planned.** CCIP is messaging, not a
  third-party custodian -- funds stay in the receiving smart contract
  (compatible with the Safe/Squads agent-wallet design already in progress,
  cf. `docs/HANDOFF_AGENT_WALLET.md`). Cost is negligible (~$0.001-0.05/
  transfer + gas), self-serve token-pool deployment (burn/mint, no liquidity
  pool, no slippage).
- **USDC specifically routes through Circle's CCTP, already integrated into
  CCIP** -- no separate protocol decision needed for the most likely real
  transfer (USDC, not an exotic token). CCTP alone: 8-20s settlement, Base +
  Solana both supported. ([PR Newswire](https://www.prnewswire.com/news-releases/chainlink-ccip-integrates-circles-cctp-to-support-cross-chain-usdc-transfers-302034925.html))

## Reservation

CCIP is deliberately slower than a raw CCTP transfer (10-20min, the
risk-management checks are the point) -- fine for a withdrawal/consolidation,
not for anything urgent.

## Out of scope (deliberately not pursued here)

Chainlink Price Feeds (the price-oracle product) was looked at only in
passing. ARIA's security/data stack is already finalized at 5 tools
(DexScreener + GeckoTerminal + Blockscout + GoPlus + Alchemy, per CLAUDE.md).
Nothing here argues for reopening that -- flagged only so a future session
doesn't confuse "Chainlink as bridge candidate" with "Chainlink as a new
price source."

## Alternatives considered and rejected

- **LayerZero** -- dominates cross-chain volume (~75% market share) but the
  April 2026 exploit (~$292-300M) came from exactly the flexible-security
  model that makes it fast to integrate: a low default DVN quorum.
- **Wormhole** -- worst security track record of the three ($325M 2022 hack,
  $1.4B multi-week freeze 2025).

## Next step

None required now -- nothing to build. Revisit when a real Base<->Solana (or
any other cross-chain) transfer need appears for the agent wallet, at which
point this file is the starting point rather than a from-scratch search.
