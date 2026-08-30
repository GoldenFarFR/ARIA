# Diligence -- price/market-data API alternatives for critical issues #271/#269

Date: 2026-08-30
Trigger: research-loop promotion pass. Two critical `system_issues` have been
open since 2026-08-26 (#271, CoinGecko monthly credit cap exhausted, blocking
WETH/USD conversion on Base+Robinhood) and since before that (#269,
DexPaprika returning 402 Payment Required on ~99.8% of calls, Base/
Robinhood/Solana). Across five separate research-log passes (2026-08-25
through 2026-08-30) six distinct replacement/complement candidates have
surfaced with zero consolidation and zero live test against ARIA's actual
pools -- this fiche gathers them in one place with what's actually verified,
so the next session that picks up #271/#269 doesn't re-discover the same
list from scratch.

Nothing built, nothing wired, no code touched by this fiche -- pure
diligence, per CLAUDE.md's rule that a code fix stays for a dev session,
never for this promotion pass.

## Candidates found so far, verified where flagged

| Candidate | Coverage | Pricing (verified) | Fit for ARIA's actual chains | Verified? |
|---|---|---|---|---|
| **Mobula** (logged 08-29) | Base+Solana+ETH/BNB/Polygon/Avalanche/Arbitrum, indexes pools directly (no listing wait) | Free key, usage-based beyond | Good on paper -- direct pool indexing suits freshly-created tokens | Not live-tested |
| **Bitquery** (logged 08-29) | 300+ DEX multi-chain, sub-300ms | Personal 49$/mo, Pro 99$/mo w/ streaming | Good on paper, but the ONLY Bitquery live test ARIA has run to date (2026-08-21, `docs/aria-learning-inbox/2026-08-21-quota-helius-et-sourcing-alternatif.md`) was for Solana bonding-curve progress, not WETH/USD pricing, and found the free tier caps at ~330 queries/month -- re-test would be needed for this specific use case | Partially (different use case) |
| **CoinStats** (logged 08-29) | Wallet/DeFi/price/tx history via API+MCP | Free 20k credits/mo, ~0.05$/1000 calls beyond (~7x cheaper than CoinGecko per source) | Unverified coverage for Base/Robinhood specifically | Not live-tested |
| **Birdeye Data Services (BDS)** (logged 08-30) | Solana + Sui + EVM; real-time price/liquidity/supply + wallet PnL API confirmed live (`docs.birdeye.so`); has a dedicated `/user/security` product line | Custom/tiered, not published; Compute-Unit-based (some endpoints cost 3 CU/request) | Solana-first by design -- most useful for `solana_late_bonding_shadow`/`solana_pump_shadow`'s own pricing needs, less clear fit for the Base/Robinhood WETH/USD gap #271 actually names | WebSearch-confirmed real product, pricing/reliability not tested |
| **CoinMarketCap "Trial Pro" keyless tier** (logged 08-30) | 36 endpoints (19 Standard + 17 DEX), REST only, GET only, rate-limited, **no published numeric quota** | Free, zero signup | Real (confirmed via `coinmarketcap.com/academy`, launched 30/04/2026) -- but CMC's own docs say "products moving into production should use authenticated access," i.e. this tier is a prototyping sandbox, not a #271 fix | Verified real, explicitly NOT production-grade |
| **CoinAPI "DEX Onchain Flat Files"** (logged 08-30) | Uniswap V2/V3, SushiSwap V2, Curve, Balancer V2, Dodo V2 only -- **five protocols, none of which are Aerodrome (Base) or any Solana/Robinhood DEX** | $250 per 1000 OHLCV download requests (S3 endpoint); dataset starts 2025-12-23, no multi-year depth | **Does not cover ARIA's actual chains/DEXs at all** -- verified via CoinAPI's own docs, this is a batch/historical product for a different protocol set entirely | Verified real, verified NOT applicable |

## What this table actually says

Two of six candidates (CoinMarketCap keyless, CoinAPI flat files) fail on
inspection once checked against ARIA's real needs -- the keyless tier is
explicitly a sandbox, and the flat files product doesn't cover a single DEX
ARIA actually sources from. That leaves four real candidates (Mobula,
Bitquery for a different use case already, CoinStats, Birdeye) none of
which has been tested against a real Base or Robinhood WETH/USD pool yet.

## Recommended next step (not taken here)

Before adding a seventh candidate to this list on the next research pass,
a dev session should pick the two most promising (Mobula for Base/Robinhood
coverage breadth, Birdeye if the fix is scoped to Solana pockets instead)
and run the same kind of live cross-check already done for Bitquery's
bonding-curve formula on 2026-08-21: fetch the same pool from ARIA's
existing pipeline and from the candidate, compare, then decide -- rather
than continuing to catalogue names.

## Sources

- [CoinAPI Flat Files docs](https://docs.coinapi.io/flat-files-api/)
- [CoinAPI Flat Files product page](https://www.coinapi.io/products/flat-files)
- [CoinMarketCap keyless Trial Pro API guide](https://coinmarketcap.com/academy/article/coinmarketcap-keyless-public-api-guide-no-key-crypto-data-for-developers)
- [Birdeye Data Services](https://bds.birdeye.so/)
- [Birdeye Data Services security product](https://bds.birdeye.so/user/security)
