---
id: 27dddf67-127c-404e-a7f4-d09f31e93da7
created_at: 2026-07-02T11:04:24.858939+00:00
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
- Sandbox path: `truth-ledger/2026-07-02/110424-canonical-github-governance.md`
