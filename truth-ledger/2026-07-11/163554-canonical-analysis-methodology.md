---
id: 366d2cd1-4526-45ee-84fa-5f87e47581b6
created_at: 2026-07-11T16:35:54.848499+00:00
canonical_id: analysis-methodology
topic: capability
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [capability, grounding, vc]
supersedes: [none]
answer_hash: 58aa2a1cbda5
status: verified
---

## Question
How does ARIA analyze a token — is it generative AI end to end?

## Answer
No — signals are computed, not guessed. Security filter (contract verified, mint authority, holder concentration, SAFE/CAUTION/DANGER verdict via skills/safety_screen.py), honeypot/ taxes/hidden owner via GoPlus (services/goplus.py), real technical analysis (RSI, EMA/MACD, Bollinger, golden pocket, RSI divergence via skills/indicators.py), holders and contract code via Blockscout, price/liquidity/OHLCV via DexScreener and GeckoTerminal. The LLM only writes up the qualitative thesis (target/invalidation) from these already-computed signals at the end — it never invents a number.

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `none`
- Sandbox path: `truth-ledger/2026-07-11/163554-canonical-analysis-methodology.md`
