---
id: d6e4a215-b582-4b42-b2a3-95e5aa9bd155
created_at: 2026-07-03T09:16:44.676666+00:00
canonical_id: github-governance
topic: infra
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [github, operator, aria, governance]
supersedes: [none]
answer_hash: e1815d08d8dc
status: verified
---

## Question
Does ARIA have her own GitHub? Who owns the repos?

## Answer
Production code lives under the GoldenFarFR GitHub org — not a separate personal account for ARIA. ARIA reads and writes via an operator-configured token: aria-sandbox (experiments, truth-ledger) and aria-token-base (token R&D). The operator may prototype on a personal GitHub during R&D; promotion to GoldenFarFR repos is explicit. aria-sandbox hosts the aria-core runtime package and experiment workspace; aria-vanguard is the holding site and deploy host (API + market plugins).

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `none`
- Sandbox path: `truth-ledger/2026-07-03/091644-canonical-github-governance.md`
