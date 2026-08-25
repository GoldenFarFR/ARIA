# Diligence -- Robinhood Chain now has live public AMMs (Uniswap + Pleiades)

Date: 2026-08-25
Trigger: research-loop promotion pass (backlog #388), a claim in the
2026-08-25 research log directly contradicting CLAUDE.md's own 23/08 note on
the Robinhood pilot ("aucun mécanisme de swap/achat de token sur cette
chaîne, seulement un transfert borné entre deux adresses fixes -- un
routeur de swap est en cours de construction EN ISOLATION"). Verified via
WebSearch before writing this fiche (per doctrine: never take a research-log
claim at face value on a real-capital-adjacent topic).

Nothing built, nothing wired by this fiche -- pure diligence, real-capital
router work stays gated behind the existing bounds in CLAUDE.md (2$ hard
cap, slippage <=10%, kill-switch, isolated wallet).

## What changed, verified

- Robinhood Chain's public mainnet went live 01/07/2026 ("The World is Flat"
  event). Day-one ecosystem partners included **Uniswap**, deploying a
  dedicated AMM, and **Pleiades**, running its own separate AMM. Alchemy,
  BitGo and Chainlink also integrated at launch.
  ([The Block](https://www.theblock.co/news/business/2026-07-01-robinhood-chain-goes-live-mainnet-alongside-24-7-tokenized-stocks-lighter-perps-planned-crypto-agentic-trading-406918),
  [Robinhood newsroom](https://robinhood.com/us/en/newsroom/robinhood-accelerates-global-expansion-robinhood-chain-mainnet-stock-tokens-agentic-trading/))
- Uniswap confirms v2, v3, v4 and UniswapX are all live on Robinhood Chain,
  with Uniswap positioning itself as "the primary public AMM ... from day
  one," supported directly in the Uniswap Web App, Wallet and API.
  ([Uniswap blog](https://blog.uniswap.org/robinhood-chain-is-live),
  [Uniswap on X](https://x.com/Uniswap/status/2072404765376430279))
- Uniswap Labs went further and launched **Pools.trade**, its own
  token-launch product, directly on Robinhood Chain on 05/08/2026 --
  described as a signal of how seriously the largest DEX team takes this
  network.
  ([Bankless](https://www.bankless.com/read/news/uniswaps-pools-trade-launchpad-goes-live-on-robinhood-chain),
  [CryptoTimes](https://www.cryptotimes.io/2026/08/06/uniswap-launches-pools-trade-token-launchpad-on-robinhood-chain/))
- Real usage, not just an announcement: daily DEX volume on Robinhood Chain
  was already ~$520M and stablecoin market cap ~$598M as of 06/08/2026.

## Why this matters for ARIA specifically

CLAUDE.md's Robinhood pilot section (point 4 of "Règles absolues," last
updated 23/08) states the contract Safe+AllowanceModule only exists on
testnet, that there is "aucun mécanisme de swap/achat de token sur cette
chaîne (seulement un transfert borné entre deux adresses fixes)," and that
"un routeur de swap est en cours de construction EN ISOLATION" the same day.
That description may now be stale on the *market* side (a swap venue exists)
even if it remains accurate on ARIA's *own build* side (nothing wired yet
either way). The two are separate facts and this fiche does not assume the
custom router work is wasted -- it opens the question of whether it still
needs to be built from scratch.

## Open questions, not yet answered

1. **Is Uniswap's Robinhood Chain deployment the SAME audited contracts ARIA
   already knows from Base**, or a fresh deployment with its own audit
   status? Uniswap v4's hook architecture in particular has had real
   incidents elsewhere (cf. backlog #290, Trail of Bits/Cork/Bunni
   $20M+ hook exploit) -- "it's Uniswap" is not automatically "it's safe on
   this specific chain," it needs its own check.
2. **Custody/execution model**: does routing a 2$ pilot swap through
   Uniswap's Robinhood Chain deployment fit the existing Safe+AllowanceModule
   design (`docs/HANDOFF_AGENT_WALLET.md`), or would it require a different
   integration shape than the transfer-only mechanism already built?
3. **Fee/slippage reality at this size**: a 2$ trade is far below typical
   liquidity-pool economics -- verify real effective slippage on Robinhood
   Chain's Uniswap pools at that size before assuming the carved-in-stone
   <=10% rule is trivially satisfiable there.
4. **Pleiades** is mentioned as a second AMM but was not independently
   diligenced here (no search budget spent on its own security/liquidity
   profile) -- treat as a name only, not a vetted option, until checked.
5. Does using Uniswap's own SDK/API (JS/TS-first, per the account-abstraction
   diligence already banked under #365) reopen the same "would force a
   separate Node microservice" friction noted there, or does Uniswap expose
   a simpler on-chain-only call path (`swapExactTokensForTokens`-style) that
   a Python client can call directly without an SDK dependency?

## Recommendation

Before the next Robinhood-pilot dev session continues building the
in-isolation swap router: read this fiche, then spend a short investigation
pass on question 1 (contract audit status on Robinhood Chain specifically)
and question 2 (fits Safe+AllowanceModule or not) before deciding whether to
keep building custom or wire against Uniswap's existing deployment. This
fiche frames the question; it does not commit to an answer -- the router
work already in isolation may still be the right call once these are
checked (e.g. if Uniswap's integration path turns out to be JS/TS-only and
custody-incompatible).

## Next step

None required immediately -- the 2$ pilot has no mainnet contract yet
either way (still testnet-only per CLAUDE.md's 23/08 note). Revisit at the
next real dev session on the Robinhood pilot, using this fiche as the
starting point rather than re-researching Robinhood Chain's AMM landscape
from scratch.
