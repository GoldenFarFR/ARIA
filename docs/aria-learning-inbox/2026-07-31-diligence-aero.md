# Maximalist Diligence — Aerodrome Finance (AERO), 07/31/2026

> Dated snapshot, not a living sheet — frozen as of 07/31/2026, to be rechecked before
> citing any precise figure if reused later. Context: long-term conviction thesis
> proposed by the operator for capital held in EVM on Base, distinct from the
> yield/return thesis already settled elsewhere. Method: 26-agent fan-out (1 scope + 5
> research + 20 source retrieval), **cross-verification done manually by this session
> after voluntarily halting the workflow before its own automatic verification phase**
> (explicit operator decision, 07/31 — keep Scope/Search/Fetch in wide fan-out, but
> take back manual control of Verify/Synthesize to avoid wasting agents, cf.
> `feedback_workflow_keep_verify_synthesize_manual` in persistent memory). Every
> claim below cites its source; divergences between sources are flagged explicitly
> rather than hidden.

## 1. Protocol mechanics

**ve(3,3)**: users lock AERO to receive `veAERO` (an NFT), with voting power
proportional to the amount and duration of the lock (up to 4 years for the
maximum). Each week (epoch), veAERO holders vote to direct new emissions
toward specific liquidity pools — the more votes a pool receives, the more
emissions it gets, which attracts liquidity and volume, which generates fees
paid back to voters. Structuring point confirmed by DefiLlama's primary data:
**100% of protocol revenue is paid back to veAERO voters**, a "zero leakage"
model — no share is retained by a treasury or a team
([DefiLlama — Aerodrome](https://defillama.com/protocol/aerodrome)).

**Slipstream** (the concentrated-liquidity module, Uniswap v3-style): two
sources converge on a launch **on April 22, 2024** with a direct, dated
citation
([CCN, 04/22/2024](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/));
a third source says "March 2024" with no precise date
([fullstack-development technical wiki](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md))
— minor divergence, the dated and cited 04/22 date takes precedence. The
Slipstream code is a **direct fork of Velodrome's Slipstream**, itself
adapted from the Uniswap V3 architecture (core/periphery), confirmed on the
official GitHub repository
([github.com/aerodrome-finance/slipstream](https://github.com/aerodrome-finance/slipstream)).
Slipstream targets stable/low-volatility pairs; Aerodrome's classic variable
pools remain used for more volatile/less liquid pairs
([CCN](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/)).

**Code license**: the Core/Periphery contracts remain under GPL-2.0-or-later
(inherited from the Uniswap V3 dependencies), but the Gauge folder (which
routes emissions/rewards) is under the **Business Source License 1.1** — so
not fully open-source, a restrictive license on the part that handles the
money
([PERMISSIONS.md, official GitHub](https://github.com/aerodrome-finance/contracts/blob/main/PERMISSIONS.md)).

## 2. Tokenomics

**Initial supply and distribution**: 500M AERO at launch (August 2023), of which 450M
(90%) were locked in veAERO from day one. 40% of the initial supply (200M tokens) was
airdropped to existing veVELO (Velodrome) holders, restricted to wallets holding at
least 1000 veVELO (~3,500 eligible wallets) — two independent sources converge exactly
on this figure
([official AerodromeFi Medium](https://medium.com/@aerodromefi/aerodrome-launch-tokenomics-30b546654a91);
[technical wiki](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md)).
The team allocation is locked in veAERO on a 2-to-4-year vesting schedule.

**Detailed allocation breakdown** — two snapshots at different dates give slightly
different figures (consistent with cumulative emissions shifting the relative
percentages over time, not a contradiction):
- Tokenomist snapshot: Gauge Emissions 67.81% / Airdrop 9.47% / Rebase 5.00% /
  Ecosystem-Public Goods 4.97% / Foundation-Team (initial mint) 4.50% + (new mint) 3.52%
  / Grants 2.37% / Voter Incentives 1.89% / Genesis LP 0.47%
  ([Tokenomist](https://tokenomist.ai/aerodrome-finance)).
- Tokenomics.com snapshot (02/2026): Gauge Emissions 65.44% / Airdrop 10.17% /
  Foundation-Team 8.61% / Rebase 5.37% / Ecosystem 5.34% / Grants 2.54% / Voter Incentives
  2.03% / Genesis LP 0.51% ([Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)).

**Emissions** — strong convergence across 3 independent sources: 10M AERO/epoch at
launch (2% of initial supply), a "Take-off" phase with +3%/epoch for the first 14
epochs (peaking slightly above 15M AERO), then a "Cruise" phase with -1%/epoch decay
([official Medium](https://medium.com/@aerodromefi/aerodrome-launch-tokenomics-30b546654a91);
[technical wiki](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md);
[Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)).

**"Aero Fed"**: starting around epoch 67 (when weekly emissions fall below 9M AERO),
veAERO voters take direct control of monetary policy — they can raise emissions by
+0.01% of total supply per epoch, lower them by -0.01%, or hold the status quo. The
cited bounds vary slightly by source: a 0.52% annualized minimum and a 52% annualized
maximum are cited consistently across 3 sources, one source specifies the mechanism as
a maximum of 1%/week (52%/year) and a minimum of 0.01%/week (0.52%/year)
([technical wiki](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md);
[official Medium](https://medium.com/@aerodromefi/aerodrome-launch-tokenomics-30b546654a91);
[Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)) — **key
implication**: long-term inflation is no longer set by immutable code but becomes a
subject of community vote, with a high ceiling (52%/year) far above the current rate.

**Current supply**: total supply **uncapped** ("infinite" per Tokenomist), circulating
supply at **978.88M AERO** (nearly double the initial supply of 500M, from cumulative
emissions since August 2023) against a market cap of $417.90M and a fully diluted
valuation of $668.36M — two independent sources cite exactly the same circulating
figure (978.88M/978,879,231), a sign of reliable data drawn from the same underlying
primary source ([DefiLlama](https://defillama.com/protocol/aerodrome); [Tokenomist](https://tokenomist.ai/aerodrome-finance)).

**Economic signal to watch** (DefiLlama, own methodology): annualized tokenized
incentives ($137.76M) **currently exceed** annualized protocol revenue ($120.54M),
producing negative "earnings" of -$17.22M/year by this calculation — the protocol is
currently distributing more in emissions than it generates in real revenue
([DefiLlama](https://defillama.com/protocol/aerodrome)).

## 3. Actual market position

**TVL** — figures vary widely depending on the snapshot moment (normal, TVL
fluctuates), listed chronologically to show the actual trajectory rather than a
misleading fixed number:
- January 2024: ~$120M ([The Block, via TradingView](https://tr.tradingview.com/news/the_block%3Ad3d3c4d57094b%3A0-aerodrome-tops-1-billion-in-deposits-dominating-defi-on-base))
- April 2024: ~$790M (~half of Base's TVL ATH at the time, $1.64B)
  ([CCN](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/))
- August 2025: ~$602M ([DWF Labs](https://www.dwf-labs.com/research/has-aerodrome-finance-become-the-leading-defi-protocol-on-base))
- The Block (date not specified in the article itself): >$1 billion in deposits, ~50%
  of Base's total TVL and >50% of Base's DeFi TVL
  ([The Block](https://tr.tradingview.com/news/the_block%3Ad3d3c4d57094b%3A0-aerodrome-tops-1-billion-in-deposits-dominating-defi-on-base))
- January 2026: ~$1.3B, ~70% of all of Base's DEX liquidity
  ([Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees))
- June 2026 (DefiLlama snapshot cited by a third party): ~$453.76M
  ([CryptoDaily](https://cryptodaily.co.uk/2026/06/aero-base-proxy-liquidity))
- Freshest DefiLlama snapshot (as of this diligence): **$268.6M, down 13.4% over 30
  days** ([DefiLlama](https://defillama.com/protocol/aerodrome))
- Slipstream alone (concentrated liquidity): $131.81M TVL, -22% over 30 days
  ([DefiLlama — Slipstream](https://defillama.com/protocol/aerodrome-slipstream))

**Honest read of this trajectory**: strong growth 2024-January 2026 ($120M →
$1.3B), then a **sharp pullback** since ($1.3B → $453M → $268M at the latest
snapshot) — a decline of roughly 80% since the January 2026 peak. This is not
necessarily disqualifying (the entire crypto market may have pulled back over the
period), but it is a real signal that should not be glossed over for a "long-term
conviction" thesis.

**Dominance on Base**: Aerodrome remains the largest DEX on Base by TVL, volume, and
fees, ahead of Uniswap and Aave on this network
([The Block](https://tr.tradingview.com/news/the_block%3Ad3d3c4d57094b%3A0-aerodrome-tops-1-billion-in-deposits-dominating-defi-on-base)).
Delivered near-2x the volume of the top Uniswap pool with roughly half its TVL; 7-day
fees exceeding Curve and PancakeSwap despite less than a third of their respective TVL
([DWF Labs](https://www.dwf-labs.com/research/has-aerodrome-finance-become-the-leading-defi-protocol-on-base)).
30-day volume of $9.02B (comparable to Solana's Orca/Raydium) per the DWF Labs
snapshot, versus $11.2B for Slipstream alone and $12.4B for Aerodrome overall per a
more recent DefiLlama snapshot (June 2026) — again, figures that move over time, not a
contradiction.

**Protocol revenue**: 30-day ~$6.12-6.22M in fees, ~$4.19-4.42M in net protocol
revenue, annualized ~$160M in fees / ~$120M in revenue (trailing year)
([DefiLlama](https://defillama.com/protocol/aerodrome)). Cumulative fees since launch
(August 2023): $295M ([Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)).
**Declining quarterly trend on Slipstream**: Q3 2025 $59.06M gross revenue → Q4
2025 $34.97M → Q1 2026 $19.53M → Q2 2026 $25.16M (slight rebound) → Q3 2026 partial $5.18M
([DefiLlama — Slipstream](https://defillama.com/protocol/aerodrome-slipstream)) — clear
downward trend since the Q3 2025 peak, consistent with the TVL decline documented
above.

**Coinbase integration**: Coinbase has integrated Aerodrome directly into its main app
and offers fee-free trading on Aerodrome via a Coinbase One subscription
([DWF Labs](https://www.dwf-labs.com/research/has-aerodrome-finance-become-the-leading-defi-protocol-on-base)).

**Structural dependence on Base**: Aerodrome operates 100% on Base (no multi-chain
diversification to date, aside from the 2026 merger project detailed in section 10) —
confirmed directly by DefiLlama's own data ([DefiLlama](https://defillama.com/protocol/aerodrome)).
One commentator explicitly labels this dependence a "structural risk" — Aerodrome's
growth is mechanically capped by Base's own growth
([CryptoDaily](https://cryptodaily.co.uk/2026/06/aero-base-proxy-liquidity)).

## 4. Security

**Two audit levels not to be conflated** — this is the most important nuance found
in this diligence, nearly absent from the marketing content:

1. **EtherAuthority audit (06/05/2024)** — covers **only the AERO ERC-20 contract**
   itself (`Aero.sol`, address `0x940181a94a35a4569e4529a3cdfb74e38fd98631` on Base), NOT
   the full protocol (Router/Voter/veAERO/gauges). Result: 0 critical, 0 high,
   0 medium, 1 low, 2 informational — verdict "Passed". Notable point raised by
   the auditor itself: the contract has a `minter` address that can mint tokens without
   limit (no supply cap at the contract level) — explicitly flagged as a
   centralization "business risk," the contract is "not fully decentralized" due
   to this owner control. The auditor also notes not having received automated test
   scripts for this contract — the analysis relied on static/manual review
   (Slither, Solhint, Remix), not a verified test suite
   ([EtherAuthority PDF report](https://etherauthority.io/wp-content/uploads/2024/06/Aerodrome-AERO.pdf)).

2. **The full protocol (Router/Voter/veAERO/gauges/Slipstream) has NO independent
   audit of its own specific to Aerodrome** — the official security page itself points to
   the security of **Velodrome V2**, from which Aerodrome inherits the contract
   architecture and full security maintenance. No Aerodrome-specific bug bounty
   either — security researchers are redirected to the Velodrome program
   ([aerodrome.finance/security, official page](https://aerodrome.finance/security)).
   Consistent with DefiLlama, which lists "Audits: No" for the Slipstream page
   specifically ([DefiLlama — Slipstream](https://defillama.com/protocol/aerodrome-slipstream)) —
   DefiLlama's categorization likely does not count an inherited audit as an audit
   "of its own" for the listed product, consistency rather than contradiction. The contracts'
   GitHub repository nonetheless mentions an active Immunefi bug bounty program and
   Echidna invariant tests, though this point was not verified directly on the Immunefi
   page itself within this diligence
   ([github.com/aerodrome-finance/slipstream](https://github.com/aerodrome-finance/slipstream)).

**Admin key status — NOT fully decentralized despite the ve(3,3) narrative**:
- A "Protocol Team" multisig (`0xE6A41fE61E7a1996B59d508661e3f524d6A32075`) holds the Team
  role in Minter and VotingEscrow, fee management on all pools, ownership of the
  Factory Registry, and initial control of ProtocolGovernor and EpochGovernor.
- A separate "EmergencyCouncil" multisig (`0x99249b10593fCa1Ae9DAE6D4819F1A6dae5C013D`) can
  **unilaterally kill or revive a gauge** (cut reward distribution to a
  specific pool at will) and manage emergency adjustments.
- The "Vetoer" role has **not yet been renounced** — future intent only, stated in
  the official docs themselves.
- ProtocolGovernor is currently controlled by the team, with a plan (not yet realized
  at the time of this diligence) to replace it with a modified OpenZeppelin Governor contract.
- AERO minting is restricted to the sole Minter contract, which distributes to the Voter
  contract for gauge emissions — the emission mechanism is therefore locked by contract, not
  manually mintable by an admin key.

([PERMISSIONS.md, official repository](https://github.com/aerodrome-finance/contracts/blob/main/PERMISSIONS.md))

**Confirmed real security incident (November 2025)**: over $1 million stolen in roughly
one hour, via a **DNS hijack** of the domain registrar (NameSilo/Box Domains) —
**not a smart contract exploit**. Users were tricked into signing malicious
authorizations disguised as a simple "1" confirmation, which the attacker then used
to drain ETH/WETH/USDC and other tokens from connected wallets. Aerodrome
redirected users to ENS-based mirrors (rather than DNS) as a safer access point during
the incident. Technical post-mortem published by Halborn, a recognized Web3
security firm ([Halborn](https://www.halborn.com/blog/post/explained-the-aerodrome-finance-hack-november-2025)).
**Honest read**: Web2/DNS infrastructure remains a demonstrated and real attack vector,
distinct from the protocol's own on-chain security.

## 5. Team and governance

**Dromos Labs** is the entity behind both Aerodrome (Base) and Velodrome (Optimism).
**Alexander Cutler** (co-founder/CEO) was the only publicly identified figure for a
long time — the rest of the team remained pseudonymous/anonymous, including internally: Cutler
himself only learned the real names of some colleagues **one month before November 2025**,
"we broadly maintained this anonymity within the team until almost that
point." The stated reason for the move to public identities: to reassure
lawmakers and institutional finance executives, who are wary of pseudonymous developers
([DL News](https://www.dlnews.com/articles/defi/aerodrome-founder-talks-aero-uniswap-feud-pseudonymity/)).

**"Feud" with Uniswap**: Cutler has publicly and repeatedly criticized
Uniswap's "fee switch" proposal (which would redirect protocol revenue from
liquidity providers to token holders) — framed by Cutler himself as
"legitimate competitive benchmarking" rather than a personal quarrel. A former Uniswap
delegate publicly reacted, calling Aerodrome's competing announcement
"impressive but unconvincing" given the earlier criticism
([DL News](https://www.dlnews.com/articles/defi/aerodrome-founder-talks-aero-uniswap-feud-pseudonymity/)).

**Concentration of voting power**: roughly **54% of AERO's circulating supply is
locked in veAERO**, concentrating governance and reducing the liquid float, per
third-party research (TokenIntel) cited by CryptoDaily
([CryptoDaily](https://cryptodaily.co.uk/2026/06/aero-base-proxy-liquidity)) — consistent with
the 90% locked at launch, this rate having naturally declined with dilution from
ongoing emissions.

## 6. Institutional investors

**Coinbase Ventures / Base Ecosystem Fund** invested in AERO in **February 2024**,
triggering a price jump from $0.09 to $0.62 within a week of the announcement
([The Currency Analytics](https://thecurrencyanalytics.com/altcoins/coinbases-20m-investment-in-aero-fuels-growth-potential-147255);
confirmed by [thedefiant.io](https://thedefiant.io/news/defi/aerodrome-founder-denies-that-coinbase-stabbed-them-in-the-back)).
The amount of **$20 million** is cited by several independent press sources
(CoinDesk, BitcoinWorld, CoinMarketCap) but could not be confirmed against a
strictly primary source in this diligence (to be treated as "very likely, widely
repeated" rather than "100% certain").

**"Coinbase stabbed us in the back" controversy** — full context: Coinbase
launched its "Verified Pools" feature **on Uniswap V4 rather than on Aerodrome**, despite
its investment, sparking community backlash. Cutler denies that this choice was due
to a technical limitation: he states that Aerodrome deliberately chose not to
prioritize building for Verified Pools because usage remained unproven ("our
priority is to be best in market, not first"), and that Aerodrome's
modular design would allow this type of pool to be added if needed. Cutler states that the
relationship with Coinbase remains close and cooperative despite this choice — frequent
daily communication, and **Coinbase remains one of the largest veAERO lockers**
([The Defiant](https://thedefiant.io/news/defi/aerodrome-founder-denies-that-coinbase-stabbed-them-in-the-back)).
Price context at the time of this article: Aerodrome $875M TVL / $30M 24h volume vs.
Uniswap $4.1B TVL / $266M 24h volume on Base — AERO was trading at $0.53 against an ATH of
$2.21 in December (a divergent mention of the real ATH, see section 8).

## 7. Competition and positioning

Aerodrome dominates Base against Uniswap v3/v4 and Aave on all three metrics (TVL, volume,
fees) per DefiLlama/The Block, but Coinbase's launch of "Verified Pools"
**on Uniswap V4** (and not Aerodrome) shows that the advantage is not total even with
its own strategic partner — Uniswap remains a real and active competitive
threat on Base ($4.1B TVL cited vs. Aerodrome ~$875M at the same comparison date). In
addition, a direct comparison with **Velodrome** (the twin protocol on Optimism, same
team) shows a considerable asymmetry: Aerodrome ~$475.9M TVL against Velodrome ~$39M
TVL at the time of a given report — Aerodrome has largely surpassed its origin protocol
in importance ([TheDefiant](https://thedefiant.io/news/defi/dromos-labs-merges-aerodrome-and-velodrome-into-new-dex-aero)).

## 8. Price history and volatility

ATH cited at **$2.38 on April 12, 2024**, after a rise of roughly +2300% since early March
2024, with a direct, dated citation
([CCN](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/)).
Another source (article on the Coinbase controversy, not precisely dated) mentions an
ATH of **$2.21 "in December"** — a different figure, not resolved with certainty in this
diligence: could reference a separate local ATH (a December rebound) rather than
the absolute April 2024 ATH, but this is not confirmed, to be verified if the exact figure
matters for a decision. Price cited at $0.53 at the time of that article (>75% pullback since
the April 2024 ATH).

## 9. Identified structural risks

1. **Ongoing inflationary dilution**: uncapped supply, circulating already close to
   double the initial supply in ~3 years, and annualized incentives currently exceed
   the real revenue generated (-$17.22M/year per DefiLlama's methodology) —
   section 2.
2. **Total dependence on Base**: 100% of TVL on a single chain, Aerodrome's
   growth is mechanically capped by that of Base — section 3, explicitly
   flagged by a third-party analyst as a "structural proxy problem."
3. **Governance not fully decentralized**: team multisig not renounced, Vetoer role
   not renounced, EmergencyCouncil that can unilaterally kill a gauge — section 4.
4. **Demonstrated Web2/DNS attack vector**: the November 2025 incident (>$1M stolen) shows
   that operational security (domain registrar, frontend) remains a real risk
   distinct from on-chain security — section 4.
5. **No independent audit of its own for the full protocol**: Aerodrome entirely
   inherits Velodrome V2's security, never audited as such in its current
   version — section 4.
6. **Net decline in TVL and revenue since the January 2026 peak**: ~-80% TVL and
   a clearly declining quarterly revenue trend on Slipstream — section 3.
7. **Governance concentration**: ~54% of circulating supply locked in veAERO,
   voting power concentrated among large holders (including Coinbase Ventures) — section 5.

## 10. Major 2026 development — the Aerodrome + Velodrome merger into "Aero"

**This is the most structuring fact found in this diligence, independently confirmed
by 4 distinct sources** (a specialized blog, CoinDesk, The Defiant, and a
fourth secondary source) — a development that fundamentally changes the nature of the
"AERO" conviction:

- **Announced on November 12, 2025**, at a launch event in New York. Aerodrome
  (Base) and Velodrome (Optimism) are merging into a single unified platform/brand called
  **"Aero,"** developed by Dromos Labs
  ([CoinDesk](https://www.coindesk.com/tech/2025/11/13/leading-base-dex-aerodrome-merges-into-aero-in-major-overhaul);
  [The Defiant](https://thedefiant.io/news/defi/dromos-labs-merges-aerodrome-and-velodrome-into-new-dex-aero);
  [HashBasis](https://www.hashbasis.xyz/blog/aerodrome-velodrome-protocols-set-to-merge-in-2026)).
- **Migration planned for Q2 2026.**
- **A new token will fully replace AERO and VELO.** Conversion ratio:
  AERO holders receive **94.5%** of the new supply, VELO holders **5.5%** — a ratio
  based on a 52-week revenue split (Aerodrome $260M vs. Velodrome $15M).
- **New "Metadex 03" architecture** (replaces Metadex 02): a "Revenue Engine" (REV)
  consolidating several fee streams (swap, frontend, bridge, aggregator, automation,
  marketplace, launch, MEV auctions), and an "Adaptive Emissions Rate" (AER) designed to
  **reduce token dilution** by paying only the liquidity incentives that are
  necessary.
- **Slipstream V3** integrates an MEV auction directly into the AMM itself.
- **Multi-chain expansion**: beyond Base and Optimism, expansion is planned toward
  Ethereum mainnet and Circle's Arc chain.
- The existing Aerodrome and Velodrome protocols will continue to function after
  Aero's launch, **but will no longer receive support/development from Dromos Labs**.
- Strategic positioning stated by Cutler: "Aero is at the forefront of a
  better, faster, and cheaper financial system than the one in place" — presented as
  a structuring overhaul, not a simple rebranding.

**Direct implication for a "long-term conviction on AERO" thesis**: holding AERO
today actually means betting on a token that will be **replaced** by a new unified
token by Q2 2026, with a conversion ratio already fixed (94.5%). The conviction
thesis must therefore focus on the NEW "Aero" token/protocol and its new
anti-dilution mechanism (AER), not solely on AERO as it exists today — a point that the
marketing/simplified content (CoinGecko Learn, generic guides) does not mention at all,
found only via the wide fan-out of specialized sources.

## Synthesis — positive signals and warning signals, without complacency

**Real positive signals**:
- Confirmed, multi-sourced dominant position on Base (top DEX by TVL/volume/
  fees, far ahead of Uniswap and Aave on this network)
- 100% of real revenue paid back to locking holders (no leakage to an
  opaque treasury)
- Direct integration into the Coinbase app + fee-free trading via Coinbase One — strong
  strategic alignment with the Base ecosystem
- Ambitious and concrete 2026 roadmap (Aero merger, AER anti-dilution mechanism,
  multi-chain expansion) rather than a stagnant project
- A team embracing growing transparency (public identities) rather than remaining
  opaque indefinitely

**Real warning signals, non-negotiable to ignore**:
- TVL/revenue pullback of roughly 80% since the January 2026 peak as of this
  diligence — not just a cyclical price decline, a real contraction in activity
- Tokenized incentives that already exceed real revenue (-$17.22M/year) — a model that
  distributes more than it earns, to be monitored to see whether "Aero"/AER (section 10)
  actually fixes this problem or merely defers it
- Governance NOT fully decentralized despite the ve(3,3) narrative (team multisig,
  EmergencyCouncil, Vetoer not renounced)
- No independent audit of the full protocol — security entirely inherited from Velodrome V2
- Real and recent security incident (Nov. 2025, >$1M stolen, DNS vector) — operational
  security remains a real, demonstrated weakness, not hypothetical
- **AERO as it exists today has an announced expiration date** (merger into
  "Aero" in Q2 2026) — any conviction must be re-evaluated in light of the new
  protocol, not frozen on the current token

**This session's verdict (not an investment recommendation, a technical read)**:
Aerodrome's dominance and strategic alignment with Base are real and well documented, but the
"long-term conviction on AERO" thesis must explicitly account for the facts that (a) the token will be
replaced by Q2 2026 and (b) the current economic model distributes more than it earns as
of this diligence. Point to revisit periodically: did the "Aero" merger
happen as planned in Q2 2026, did the AER mechanism actually reduce
dilution, and has the TVL/revenue trend stabilized or does it continue to
deteriorate?
