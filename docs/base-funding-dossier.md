# Base funding dossier — Aria Vanguard ZHC

> **Note opérateur (FR).** Brouillon **prêt à adapter** au formulaire Base (Batches Startup Track
> / Builder Grants / Ecosystem Fund — vérifier le nom et les critères ACTUELS du programme avant
> de soumettre, ils changent). Rédigé en **anglais** (langue du programme), voix humaine, zéro
> em-dash/emoji, **aucune métrique inventée** — notre force = l'honnêteté « proof before promise ».
> **Avant de soumettre : remplace les chiffres marqués `[LIVE:` par les vrais chiffres du VPS
> en prod** (`/track`, `/watchlist`, `/cycles`, cockpit) — jamais les chiffres d'un environnement
> de dev. Ne mets aucune donnée privée (identité légale, coordonnées) ici : elles vont dans le
> formulaire, pas dans ce dépôt public.
>
> Décisions opérateur qui structurent ce brouillon (08/07) : ton 50/50 sobre-honnête et
> spectaculaire ; le narratif central = **le premier holding piloté par IA**, pas « la meilleure
> analyse crypto » ; le rehearsal Sepolia autonome = **le clou du dossier** ; 100% analyse, aucun
> token ; on soumet **maintenant**, sans attendre la fin du run de preuve de 20 jours (verrouillé
> en direct, c'est un argument, pas un défaut) ; budget demandé au maximum défendable par la
> qualité/vitesse d'exécution démontrée, pas un chiffre arbitraire ; le budget reste dans
> l'infrastructure et la preuve, **pas** un raccourci vers du capital réel (on reste en
> apprentissage, pacte `docs/protocole-argent-reel.md` inchangé.)

---

## One-liner
Aria Vanguard ZHC is the first AI-piloted holding on Base: an autonomous agent that researches
onchain markets, builds and defends its own track record, and answers to a single human for every
decision that touches real money. Nothing it claims has to be taken on faith. It can be checked.

## The problem
Crypto research runs on unverifiable claims: screenshots, deleted calls, wins remembered and
losses quietly forgotten. Capital flows to whoever markets loudest, not to whoever is right. And
every "autonomous trading AI" pitch today asks you to trust a black box. We built the opposite:
an agent whose reasoning, its wins, its losses and its mistakes, is logged, hashed, and anchored
onchain before it ever touches capital.

## The thesis: proof before promise
We invert the usual order. Before asking anyone to trust a number, we make the number checkable.
Every call ARIA makes is recorded with its reasoning, hashed into a tamper evident record, and its
Merkle root is anchored onchain on Base. The history cannot be backdated or edited after the fact.
If we are wrong, the chain remembers that too. The moat is the proven decision, not the execution.
And the operating model itself is the pitch: one human, final say on everything that matters, and
an AI that does the building, the analysis, and the operating in between.

## The centerpiece: we built the hardest test we could give it, on purpose
Before any real capital is ever on the table, ARIA has to survive Base Sepolia acting completely
alone. No human clicks approve. No safety net waits behind the scenes. She reads the real market,
sizes a position with a real risk formula (half-Kelly, capped, computed from her own calibrated
hit-rate, never guessed), signs the transaction herself, and broadcasts it. Every cycle is logged,
including the ones where she hesitates, fails, or gets it wrong: decision latency, raw reasoning,
errors, a circuit breaker that trips after repeated failures and re-evaluates itself the next
cycle. The goal, stated plainly by the operator: make Sepolia the hardest thing she ever has to
pass, so that once real capital is on the line, the only decision left for a human is a simple yes
or no.

This is deliberately bounded and never touches mainnet. Real capital keeps a human in the loop on
every single decision, permanently, no exception. The autonomous path runs on a testnet where the
asset has no value, wired through a completely separate code path from the guardrail that protects
real funds, so that the rehearsal can be brutal without the guardrail ever being at risk. That
separation is enforced in code and locked by a CI test, not just a promise.

`[LIVE: cycles run, tx sent, error rate, hesitation rate from GET /api/aria/sepolia-status]`

## What we have built (verifiable today, in this repo)
- **Autonomous analysis engine**: live onchain data (DexScreener, GeckoTerminal, Blockscout,
  CoinGecko), dynamic scam and honeypot screening (GoPlus), a technical analysis engine, and an
  LLM judge that audits the analysis. Runs continuously, not on request.
- **The Sepolia rehearsal** (above): full autonomous decide-and-execute loop on testnet, with
  complete behavioral telemetry, published live on the public cockpit.
- **Human in the loop for real capital, by design, no exception**: any action that could touch a
  real dollar waits for a human approval on Telegram or the web command center. The signing key
  for anything mainnet-bound never sits on the server.
- **Onchain proof mechanism**: Merkle attestation of the track record plus `AriaLedger.sol`
  (`anchor(bytes32)`, no value transfer). Server-side preparation is keyless by design for the
  mainnet path; signing and broadcasting happen locally. Ready to go live on Base.
- **x402 seam**: an anchor point for the Base agentic economy (HTTP 402 payments, USDC), gated
  off, fail closed, no autonomous spending.
- **A twenty day paper trading proof run, in progress right now**: real reports applied to a
  simulated one million dollar book, nothing hidden. We are not waiting for it to finish before
  talking to you. You can watch it complete in real time on the public cockpit.
- **A twenty day trading exam, running in parallel**: daily questions across a real curriculum
  (smart money concepts, order flow, quant risk, execution lifecycle, macro), scored by an
  independent LLM judge, published live. Contested frameworks are labeled as frameworks, not
  proven truths, in every answer.
- **A macro cycle engine**: real historical Bitcoin price data segmented into the last three
  halving-bounded cycles, with accumulation, markup, distribution, and markdown phases computed
  from actual numbers, not a memorized narrative.

## Why this is Base-native
- Built on and for Base: onchain data, onchain proof, USDC and x402 payments, low-cost anchoring,
  and now a live autonomous-agent rehearsal on Base Sepolia itself.
- Fits the agentic economy: an autonomous agent that transacts and proves its work onchain, with
  human governance and verifiable output. Not another unaccountable trading bot.
- Extensible by design (documented seams): new data sources, x402 live wiring, AgentKit or MiniKit
  surfaces plug in without rewriting the core.

## Status, honestly
No real capital has been deployed, anywhere, ever. There is no track record to inflate yet, and
that is the point. We are running the analysis, building the record, standing up the onchain
proof, and stress testing full autonomy where it is safe to do so. This is a pre-revenue project
with the proof infrastructure already built and running, run by a solo operator with the
construction handled end to end by AI. We are not a token. We are not asking anyone to speculate
on us. We are asking to be checked.

## The ask and use of funds
We are not naming a fixed number here on purpose. The honest answer is: as much as our
demonstrated execution earns. Every capability in this document, including the Sepolia rehearsal
and the macro cycle engine, shipped, tested, and documented inside a single working session. We
would rather let the pace and the quality of what we ship set the size of the bet than pick an
arbitrary figure. In priority order, funding goes to:
1. **Go live onchain**: deploy `AriaLedger` on Base and anchor the public track record, so the
   proof stops being a mechanism and becomes a live, verifiable feed.
2. **Ship the human/AI command center**: a web cockpit where a human validates ARIA's proposed
   decisions, alongside Telegram. The governance surface becomes a product.
3. **Complete the proof run and open the record**: finish the twenty day simulation, publish the
   verifiable results, then begin small, disciplined real capital by tiers of confidence, on our
   own timeline, never accelerated by a funding deadline.
4. **Harden and scale the analysis**: caching and throughput for continuous coverage, deeper
   position management (trailing stops, take-profit ladders) as human-validated proposals.
5. **x402 live**: turn on the payment seam so the agent can pay for data and be paid for analysis
   within the Base agentic economy, always human gated for spending.

We are explicit about one boundary: funding accelerates infrastructure and proof, not the decision
to deploy real capital. That decision follows our own proof, not a grant calendar.

## Roadmap
- **Now**: analysis and guardrails live, onchain proof mechanism built and tested, x402 seam, the
  Sepolia autonomous rehearsal running, the twenty day proof run and trading exam in progress.
- **Next**: anchor the record on Base, ship the command center, finish the proof run.
- **Then**: open the verifiable track record publicly, small real capital by tiers, always human
  gated.

## Links
- Site: https://ariavanguardzhc.com
- X: https://x.com/Aria_ZHC (agent), https://x.com/GoldenFarFR (builder)
- Repository: https://github.com/GoldenFarFR/ARIA
- Demonstrator: (link the published Base demonstrator page)

## Governance and safety (the guardrails are the pitch)
- Real capital, always human gated, no exception. One human validates every action that touches
  real money, on Telegram or the command center.
- The one deliberate, narrow exception: a Sepolia-only autonomous rehearsal, on an asset with no
  real value, structurally separated in code from the guardrail that protects real funds. It is
  there to make the human-gated mainnet path simple by comparison, not to weaken it.
- The signing key for anything mainnet-bound never sits on the server, by design.
- Fail closed: missing data is reported, never invented.
- No real money before the proof stands on its own.
- Every call carries its reasoning. A call without an explanation is not a call.
