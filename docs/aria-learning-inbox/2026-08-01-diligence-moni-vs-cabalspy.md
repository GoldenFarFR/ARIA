# Diligence — Moni API vs CabalSpy (wallet-scoring sourcing), 08/01/2026

> Dated snapshot, not a living document. Direct follow-up to the CabalSpy
> diligence from 07/23 (`docs/HANDOFF_WALLET_SCORING.md`), which had ruled out
> Moni based solely on its public doc ("neither has smart money labels"). The
> operator provided a real Moni API key (`profile.moni.ai`, free tier) on 08/01
> -- this fiche is the first real test with authentication, never done before.
> **No key/secret value appears here**, per public-repo doctrine.

## What the API actually offers (verified live, not just the doc)

Confirmed base URL: `https://api.discover.getmoni.io/api/v3/` (distinct from the
main `moni.ai` domain -- found via `apiguide`/`llms.txt`, never assumed).
Authentication: `Api-Key` header. Excellent, stable latency: 80-130ms over 5
consecutive calls (average ~92ms). Free tier with a real rate-limit (429 "Rate
limit exceeded" observed after 2-3 rapid calls -- not precisely quantified, a
~15s delay was enough to clear it).

Real endpoints tested (`accounts/{username}/info/full/`, `projects/raw/`) --
data returned per X/Twitter account:
- `moniScore`: continuous composite score (not a category)
- `smartsCount` / `smartMentionsCount`: engagement volume from "smart" accounts
- `smartTier` / `smartTags`: **categorization** (e.g. "Exchange") -- confirmed
  PRESENT and populated on a project/exchange account (`RobinhoodCrypto`), but
  **always empty/null** on the 3 individual wallets tested below
- `mlProjectPrediction`: ML score for the probability that an account is a real
  crypto "project" (seen at 95% on a real example from the `projects/raw/` feed)

## Direct comparison on the same entities (CabalSpy "kol" wallets -> Moni via their X handle)

3 wallets randomly drawn from the real `cabalspy.list_wallets("base", wallet_type="kol")`
feed (238 wallets received), each already carrying an X handle attached by
CabalSpy -- then queried on Moni with that same handle:

| Wallet (CabalSpy type="kol") | X handle | Moni `moniScore` | Moni `smartsCount` | Moni `smartTier`/`smartTags` |
|---|---|---|---|---|
| Kaz1m | KZMKBL | 81 | 8 | `null` / `[]` |
| blanker | 0xblanker | 1850 | 165 | `null` / `[]` |
| milady | milady | 57 | 3 | `null` / `[]` |

**Clear finding, on direct evidence (not just the doc)**: for the 3 individual
wallets labeled "kol" by CabalSpy, Moni provides a continuous score and an
engagement count, but **never usable categorization** (`smartTier`/`smartTags`
empty) -- this field appears reserved for PROJECT/exchange accounts (confirmed
positive on `RobinhoodCrypto`), not for the individual wallets/KOLs that
`/walletqueue`/`/walletscore` need to categorize.

## Verdict

**The 07/23 diligence is confirmed, this time on direct evidence rather than the
doc alone**: CabalSpy remains the right choice for wallet-scoring sourcing
(direct category "kol"/"smart"/"whale" per wallet, usable without
transformation). Moni does not replace this role -- its potential value would
lie elsewhere (continuous social momentum score per X account, recent-project
discovery via `projects/raw/` + `mlProjectPrediction`), not explored further
here, out of scope for this diligence.

## Open branches (never dug into, just banked)

- `projects/raw/` (feed of recent projects + "real project" ML score) could be a
  complementary discovery source to `launchpad_discovery.py` -- angle not
  evaluated.
- Moni's free-tier rate-limit was never rigorously measured (just observed that
  it exists and a short delay is enough) -- to calibrate if real usage were ever
  considered.
