# Base funding dossier — Aria Vanguard ZHC

> **Note opérateur (FR).** Brouillon **prêt à adapter** au formulaire Base (Batches Startup Track
> / Builder Grants / Ecosystem Fund). Rédigé en **anglais** (langue du programme), voix humaine,
> zéro em-dash/emoji, **aucune métrique inventée** — notre force = l'honnêteté « proof before
> promise ». Vérifier les **montants et conditions actuels** du programme avant de soumettre.
> Ne mettre aucune donnée privée (identité légale, coordonnées) dans ce dépôt public : elles vont
> dans le formulaire, pas ici.

---

## One-liner
Aria Vanguard ZHC is an autonomous AI agent that researches onchain markets on Base, keeps a
human on every final decision, and writes its track record onchain so it can be verified instead
of claimed.

## The problem
Crypto research runs on unverifiable claims: screenshots, deleted calls, wins remembered and
losses quietly forgotten. Capital flows to whoever markets loudest, not to whoever is right. There
is no cheap, trustless way to prove that an analyst actually made a call, at a time, unchanged.

## The thesis: proof before promise
We invert the usual order. Before asking anyone to trust a number, we make the number checkable.
Every call ARIA makes is recorded with its reasoning, hashed into a tamper evident record, and its
Merkle root is anchored onchain on Base. The history cannot be backdated or edited after the fact.
If we are wrong, the chain remembers that too. The moat is the proven decision, not the execution.

## What we have built (verifiable today, in this repo)
- **Autonomous analysis engine**: live onchain data (DexScreener, GeckoTerminal, Blockscout,
  CoinGecko), dynamic scam and honeypot screening (GoPlus), a technical analysis engine, and an
  LLM judge that audits the analysis. Runs continuously, not on request.
- **Human in the loop, by design**: no automatic execution. Anything touching money waits for a
  human approval on Telegram or in the coming web command center. Autonomy is for analysis only.
- **Onchain proof mechanism**: Merkle attestation of the track record plus `AriaLedger.sol`
  (`anchor(bytes32)`, no value transfer). Server-side preparation is keyless by design; signing
  and broadcasting happen locally. Ready to go live on Base.
- **x402 seam**: an anchor point for the Base agentic economy (HTTP 402 payments, USDC), gated
  off, fail closed, no autonomous spending.
- **A twenty day paper trading proof run**: real reports applied to a simulated one million dollar
  book, nothing hidden, to test the method before a single real dollar.

## Why this is Base-native
- Built on and for Base: onchain data, onchain proof, USDC and x402 payments, low-cost anchoring.
- Fits the agentic economy: an autonomous agent that transacts and proves its work onchain, with
  human governance and verifiable output. Not another unaccountable trading bot.
- Extensible by design (documented seams): new data sources, x402 live wiring, AgentKit or MiniKit
  surfaces plug in without rewriting the core.

## Status, honestly
No real capital has been deployed. There is no track record to inflate yet, and that is the point.
We are running the analysis, building the record, and standing up the onchain proof. This is a
pre-revenue project with the proof infrastructure already built, run by a solo operator with the
construction handled end to end by AI.

## The ask and use of funds
Target: Base Batches Startup Track (grant plus investment) or the Ecosystem Fund. With funding we
would, in priority order:
1. **Go live onchain**: deploy `AriaLedger` on Base and anchor the public track record, so the
   proof stops being a mechanism and becomes a live, verifiable feed.
2. **Ship the human/AI command center**: a web cockpit where a human validates ARIA's proposed
   decisions, alongside Telegram. The governance surface becomes a product.
3. **Complete the proof run and open the record**: finish the twenty day simulation, publish the
   verifiable results, then begin small, disciplined real capital by tiers of confidence.
4. **Harden and scale the analysis**: caching and throughput for continuous coverage, deeper
   position management (trailing stops, take-profit ladders) as human-validated proposals.
5. **x402 live**: turn on the payment seam so the agent can pay for data and be paid for analysis
   within the Base agentic economy, always human gated for spending.

## Roadmap
- **Now**: analysis and guardrails live, onchain proof mechanism built and tested, x402 seam.
- **Next**: anchor the record on Base, ship the command center, finish the proof run.
- **Then**: open the verifiable track record publicly, small real capital by tiers.

## Links
- Site: https://ariavanguardzhc.com
- X: https://x.com/Aria_ZHC (agent), https://x.com/GoldenFarFR (builder)
- Repository: https://github.com/GoldenFarFR/ARIA
- Demonstrator: (link the published Base demonstrator page)

## Governance and safety (the guardrails are the pitch)
- No automatic financial execution, ever. Human validates every action.
- The signing key never sits on the server, by design.
- Fail closed: missing data is reported, never invented.
- No real money before the proof stands on its own.
- Every call carries its reasoning. A call without an explanation is not a call.
