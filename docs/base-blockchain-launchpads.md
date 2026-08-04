# Base blockchain — token launchpads (reference sheet)

> LIVING reference sheet (not a dated snapshot like `docs/aria-learning-inbox/`) — to be
> updated over time as any of these launchpads evolve. Context: due diligence launched
> on 07/27/2026 toward a **future** ARIA tokenization (no action taken to date) —
> triggered by the Bankr.bot X account compromise incident. Full sourced initial due
> diligence: `docs/aria-learning-inbox/2026-07-27-diligence-launchpads-tokenisation-aria.md`.
> Each section below carries the main weak point identified, framed as a question to
> revisit before any real commitment or whenever this file is picked up again.

## Bankr

**Diligence**: built on the Doppler protocol (Uniswap V4 hooks), 0.7% swap fee. 100
billion tokens at mint, 85% seeds liquidity, 15% vests to the creator over 2 years
(30-day cliff) — a sound mechanism in itself. But two security incidents in 2 months in
2026: (1) May — wallet hack via prompt injection targeting Grok↔Bankrbot, >$440,000 in
damage, 14 wallets compromised; (2) July — takeover of the official X account. The
tokenomics mechanism isn't at fault, it's the platform's operational trustworthiness.
**Status: ruled out.**

**Question on its weak point**: has Bankr had a new security incident since July 2026,
or has it shown a real improvement in its security posture (published independent
audit, infrastructure change, transparency communication) that would justify
reconsidering the platform?

---

## Virtuals Protocol

**Diligence**: the most mature player in the "AI agent tokens" ecosystem — strict fair
launch (1B fixed tokens/agent), liquidity locked for **10 years**, 1% fee. ARIA is
already in this ecosystem (compute.virtuals.io). But confirmed structural decline (not
just a cyclical price dip): revenue -95% since January 2025 ($3.5M/month →
<$200K/month), daily active users 30,000 → <12,000, current 24h volume only $174,000
against $382M mcap. Technical development continues actively (Chainlink CCIP
migration, Robinhood Chain integration) without reversing the user exodus. A real
accelerator fund exists (Virtuals Ventures, virtuals.vc) focused on AI broadly (not
just robotics), extended to finance — but no public amounts/criteria found. **Status:
ruled out for the token launch itself, Virtuals Ventures angle worth exploring
separately.**

**Question on its weak point**: has Virtuals' revenue/user decline stabilized or
reversed since July 2026, or is it still deteriorating despite the new partnerships
(Robinhood Chain, Eastworld Labs, ACP v2.0)? And has Virtuals Ventures published
concrete amounts/criteria, or does it remain a black box?

---

## Clanker

**Diligence**: 1% Uniswap V3 fee — creator receives 100% of initial LP fees via the
clanker.world interface (80% via the Farcaster bot). Liquidity locked **until 2100**
(the strongest anti-dump mechanism of the whole diligence). Real current growth: daily
fees +37% in one month ($65K→$89K), 100,000+ cumulative traders within the first
month, 486,000+ total holders. Real security audit (0xMacro, June-July 2025). Acquired
by Farcaster (Oct. 2025) then Neynar (Jan. 2026) — added legitimacy. Incident
clarified: developer "_proxystudio" resigned immediately in May 2025 after being
exposed for a fund theft at an **other** project (Velodrome) before joining Clanker —
funds returned, no link to Clanker's funds. Remaining gray areas: Discord support not
empirically tested, exact scope of the $CLANKFUN requirement (negligible cost ~$3-5)
not confirmed, no native governance/staking for third-party tokens. **Status:
recommended candidate.**

**Question on its weak point**: does Clanker's Discord support actually respond within
a reasonable time to a real technical question (to be tested firsthand)? And does the
$CLANKFUN requirement apply to every deployment path (API/SDK included) or only to the
clanker.world web interface?

---

## Flaunch

**Diligence**: a 30-minute "Fixed Price Fair Launch" (anti-bot/anti-sniping, CAPTCHA +
per-wallet cap), 100% of fees redistributed (creator chooses their %), Uniswap v4
"Progressive Bid Wall" hook (mechanical price support). But TVL of only $2.0M, annual
revenue $2.8M (DeFiLlama) — ~200x smaller than Virtuals at its peak. Liquidity lock
duration not found despite two targeted searches. Founders not identified. **Status:
ruled out for now (too small).**

**Question on its weak point**: has Flaunch's TVL grown significantly since July 2026
(Clanker/Virtuals getting closer)? Has a verifiable team/founders been identified, and
has the real liquidity lock duration been clarified?

---

## Robinhood Chain (Noxa / PONS / RobinPad / hood.fun)

**Diligence**: mainnet launched July 1, 2026 — a 3-week-old ecosystem at the time of
this diligence. Noxa, the flagship launchpad (60,000 tokens launched, headline
memecoin CASHCAT), already shut down in under 2 weeks: ~$12M in fees generated
(July 11-14), then an announcement of 100% revenue redistribution to creators and
closure, citing "low-quality tokens flooding the platform" — not a theft, but a real
governance failure. CASHCAT -33% in 24h following the announcement. **Status: ruled
out (too young, already unstable).**

**Question on its weak point**: has a new, stable launchpad emerged on Robinhood Chain
since Noxa's closure (PONS? another one?), with at least several months of
track record without a major incident, before reconsidering this ecosystem?

---

## Coinbase / Base (general ecosystem)

**Diligence**: no "officially backed" launchpad from Coinbase — the Base Ecosystem
Fund (Coinbase Ventures) invests in the Base ecosystem broadly, not earmarked for any
specific token launchpad. No evidence of direct Coinbase Ventures investment in
Clanker or Virtuals. Jesse Pollak has not recommended any official launchpad. Most
relevant fact: strategic pivot announced 07/15/2026 toward
tokenization/trading/payments/AI agents, after the acknowledged failure of the
"creator coin"/onchain social bet — favorable macro timing for ARIA, independent of
launchpad choice. **Status: not a candidate in itself, macro signal to watch.**

**Question on its weak point**: has Coinbase since launched a native Base token or
designated an official/preferred launchpad since July 2026 — which would change the
strategic picture for the final choice?

---

## Synthesis / standing view

Clanker remains the recommended candidate (see the dated diligence for full source
detail). Before any real commitment: test Clanker's Discord support, clarify the scope
of the $CLANKFUN requirement, and revisit this file periodically to check whether the
answers to the questions above have changed the picture (notably any Virtuals rebound,
or a stabilization of the Robinhood Chain ecosystem).
