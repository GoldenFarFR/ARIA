# Diligence — ERC-4337 account abstraction ecosystem (bundlers/paymasters), Robinhood Chain angle

Date: 2026.08.24. Triggered by an operator diligence chain on the Robinhood Chain
AllowanceModule pilot (`docs/HANDOFF_AGENT_WALLET.md`, 23-24/08 entries): while
verifying whether Candide's `AbstractionKit` SDK could simplify deploying/using
Safe's AllowanceModule v1.0.0, the question widened to "is Candide even the right
provider, or are there better ones for Robinhood Chain specifically". Purely
informational — no code touched, no integration decision made. Banked for a
FUTURE need (gas sponsorship, passkeys, batched transactions, account recovery
for end users), explicitly NOT needed for the current pilot (direct Safe +
AllowanceModule execTransaction, no Bundler/UserOperation involved, already
proven live on testnet 23/08).

## Verified facts

1. **Robinhood Chain's own docs name Alchemy as its primary account-abstraction
   provider, ZeroDev as the alternative — Candide is not mentioned at all.**
   (docs.robinhood.com/chain/account-abstraction/). Robinhood Chain has
   first-class ERC-4337 support plus EIP-7702 (in-place EOA upgrade without a
   new address, for batching/sponsorship/session keys).

2. **Market position (2026, aggregated from multiple sources)**: bundler market
   share by UserOp count — Pimlico ~38%, Coinbase ~22%; Pimlico/Stackup/Coinbase
   together process ~78% of EVM UserOperations by count (Q1 2026). Pimlico is
   pure plumbing (bundler+paymaster only, no wallet/auth/SDK) — it's the layer
   MetaMask, Safe, Trust Wallet, Zora, and Thirdweb use under the hood.
   Biconomy's differentiator is a unified cross-chain gas tank (one balance
   sponsors multiple chains). Account models differ: Alchemy (ERC-6900 Modular
   Account), Pimlico (account-agnostic SDK), Biconomy (Smart Account v2 /
   Nexus, ERC-7579), ZeroDev (Kernel, the most widely deployed ERC-7579
   account by unique address count).

3. **Python support is the real differentiator for ARIA (Python-only stack)**:
   - Candide `AbstractionKit`: TypeScript/Node only. No Python path.
   - Alchemy Account Kit: TypeScript only (built on viem); the separate
     general-purpose Alchemy Python SDK exists but does NOT cover Account Kit
     functionality. Alchemy's own JS SDK (the older, general one) is being
     deprecated/archived January 2026 — a data point on how fast this
     ecosystem's JS tooling churns.
   - **ZeroDev is the only one of the four with a genuine Python path**: a
     Python-callable "Omni SDK" (ctypes bindings, also covers Swift/Go/Rust/
     Kotlin) plus a plain "UserOp API" — a REST endpoint any language
     (including Python) can call directly to submit UserOperations through
     ZeroDev's infrastructure, announced January 2026.

## Conclusion (for a future re-evaluation, not now)

If ARIA ever needs real account-abstraction features — gas sponsorship for an
end-user-facing product, passkey auth, batched transactions, social recovery —
**ZeroDev is the standing candidate to re-diligence first**: it's one of
Robinhood Chain's two named official providers AND the only one with a real
Python integration path that doesn't require standing up a separate Node
microservice just to talk to it. Alchemy remains Robinhood's primary-listed
provider but is JS-only, which would force exactly that extra Node service.

This does NOT apply to the current pilot: bounded delegate transfers via Safe's
AllowanceModule need no Bundler/Paymaster/UserOperation at all — direct
`execTransaction` (already coded and proven, `onchain/safe_robinhood_deploy.py`)
stays the right approach until/unless a real end-user-facing product need
appears.

## Open branches (banked, not dug into)

- ZeroDev's "UserOp API" being callable via plain REST from Python means it
  could in principle be evaluated with zero new infra risk (no key
  material beyond a normal API key) — worth a real hands-on test the day a
  concrete gas-sponsorship or passkey need actually appears, not before.
- EIP-7702 (native on Robinhood Chain per their AA docs) is a parallel,
  possibly simpler path to some of the same capabilities (batching, session
  keys) without needing a Bundler/Paymaster provider at all — distinct
  research thread, not explored this pass.
- Never independently verified: ZeroDev's real pricing/rate limits/actual
  chain coverage for Robinhood Chain specifically (only that Robinhood's own
  docs list them as "available") — check before any real integration attempt.

## Sources

- https://docs.robinhood.com/chain/account-abstraction/
- https://eco.com/support/en/articles/15254049-best-erc-4337-infrastructure-2026-alchemy-pimlico-biconomy-zerodev
- https://www.openfort.io/blog/best-account-abstraction-providers
- https://pypi.org/project/alchemy-sdk/
- https://www.alchemy.com/support/what-are-the-supported-chain-for-account-kit
- https://docs.zerodev.app/
- https://github.com/zerodevapp
