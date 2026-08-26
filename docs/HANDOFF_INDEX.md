# Index of HANDOFF files by component

Per-component description index for `docs/HANDOFF_<component>.md` files, moved out of
`CLAUDE.md` on 2026-08-26 (`specs/009-restructure-claude-md`) to recover size budget —
CLAUDE.md itself still names every file (grep-able there) and points here for the
description. Format of each HANDOFF file: `[STATUS] Subject` then `Date: YYYY.MM.DD /
Problem: ...` then `Solution: ... — file.py (short hash)`. `[STATUS]` ∈ `DEPLOYED` /
`CODE` (tested, not yet deployed) / `CONFIG` (manual action, no commit) / `CURRENT STATE`
(up-to-date snapshot, not a fix).

Before diagnosing a problem that *might* be a recurrence, check FIRST whether the
relevant component already has its file below — often faster than investigating from
scratch.

**Any new `docs/HANDOFF_<component>.md` file gets its entry added here in the SAME
commit** — a HANDOFF not indexed here is as invisible as a HANDOFF that doesn't exist.
CLAUDE.md's own "Index of HANDOFF files by component" section keeps only the file names
+ a pointer to this file — never the description itself.

- `docs/HANDOFF_GOPLUS.md` — Token Security API (honeypot check), auth, throughput calibration, cache.
- `docs/HANDOFF_BLOCKSCOUT.md` — holders, wallet scoring, contract data, Pro credits.
- `docs/HANDOFF_COINBASE_CDP.md` — REAL CAPITAL agent wallet (balance, swap, CDP auth).
- `docs/HANDOFF_AGENT_WALLET.md` — homemade agent wallet (Safe+AllowanceModule / Squads v4), testnet-only so far.
- `docs/HANDOFF_SOLANA_TRADE_PILOT.md` — REAL CAPITAL Solana leg (delegate key, Jupiter swaps, rent-deposit recovery).
- `docs/HANDOFF_X402.md` — micropayments, weekly budget, Bazaar providers.
- `docs/HANDOFF_LLM.md` — LLM provider (Spark/Grok/Virtuals), routing, identity.
- `docs/HANDOFF_PIPELINE_MOMENTUM.md` — sourcing, hard guardrails, sizing, exit (1M$ test).
- `docs/HANDOFF_PAPER_TRADING.md` — 1M$ portfolio, weekly protocol, resets.
- `docs/HANDOFF_GROUNDING.md` — anti-hallucination, web/factual routing, confabulations.
- `docs/HANDOFF_VPS_OPS.md` — git, deployment, worktrees, VPS dispatch.
- `docs/HANDOFF_DUNE.md` — Dune Analytics SQL sourcing, query pitfalls.
- `docs/HANDOFF_TELEGRAM.md` — natural-language routing, conversational workflows, aria-brain.
- `docs/HANDOFF_OPERATOR_MOBILE.md` — mobile fallback channel (account/sessions/chat/kill-switch REST).
- `docs/HANDOFF_SECURITE.md` — secrets, CI, key rotations, access.
- `docs/HANDOFF_MOTEUR_LEGITIMITE.md` — security score, mint_authority, safety_screen (VC pocket).
- `docs/HANDOFF_DOPPLER.md` — on-chain Uniswap v4 price reading for Bankr/Doppler tokens.
- `docs/HANDOFF_POLYMARKET.md` — paper bets on prediction markets, edge/quality judgment engine.
- `docs/HANDOFF_AUTOMATISATION.md` — VPS crons (Research watch, Devil's Advocate, backlog promotion, watchdogs).
- `docs/HANDOFF_WALLET_COPY_SHADOW.md` — forward-test de copie sur 8 wallets réels, ledgers fictifs indépendants, jamais un trigger réel.
- `docs/HANDOFF_SIGNAL_CASCADE.md` — cascade de signaux multi-source (GitHub/Farcaster/web/X), collecte + filtre + convergence + file d'attente.
- `docs/HANDOFF_CANDLE_HISTORY.md` — historique persistant de bougies (FIFO par token/timeframe), collecteur watchlist (#98/#97).
- `docs/HANDOFF_RESOURCE_BUDGET.md` — garde-fous budget/quota des providers API tiers (CoinMarketCap, CoinGecko, Mobula, Dune, Firecrawl, Tavily, Blockscout, GoPlus, TwitterAPI.io), consolidation `resource_budget.py` (#302).
- `docs/HANDOFF_LANCEDB.md` — mémoire vectorielle sémantique (recherche par sens, pas mot-clé), extra `[vector]` du Dockerfile, bonnes pratiques LanceDB sourcées, risque memory-poisoning ASI06.
- `docs/HANDOFF_CHAINSTACK.md` — Chainstack RPC provider (Solana polling, planned Robinhood Chain), pricing/RU billing model, RPS limits, node add-ons (Yellowstone gRPC, Warp, Unlimited Node), MCP-server tooling.
- `docs/HANDOFF_DEFILLAMA.md` — DefiLlama market-data (free API vs Pro vs LlamaAI), verified TVL/DEX-volume-by-chain endpoints, candidate cross-check for the regime indicator.
- `docs/HANDOFF_AUDIT_LIVRAISON.md` — audit of components that never delivered their expected result (seeded by the wallet-scoring lesson, `specs/001-audit-code-sans/`), 11 components checked, 3 real bugs found and fixed.
