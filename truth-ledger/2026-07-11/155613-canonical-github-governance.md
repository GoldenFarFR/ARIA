---
id: d295307f-7197-4e0a-a75d-775f7d95d635
created_at: 2026-07-11T15:56:13.803038+00:00
canonical_id: github-governance
topic: infra
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [github, operator, aria, governance]
supersedes: [6c6e81a8-0dfa-4171-90df-9ce92fde0c22]
answer_hash: 6ef7aee82ad5
status: verified
---

## Question
Does ARIA have her own GitHub? Who owns the repos?

## Answer
Production code lives under the GoldenFarFR GitHub org — not a separate personal account for ARIA. Read access is broad; write access is off by default (GITHUB_WRITE_REPOS empty/off in production — ARIA does not write to GitHub autonomously). GITHUB_SANDBOX_REPO points at ARIA (the monorepo, replacing the old separate aria-sandbox) and aria-token-base for token R&D. The operator may prototype on a personal GitHub during R&D; promotion to GoldenFarFR repos is explicit.

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `6c6e81a8-0dfa-4171-90df-9ce92fde0c22`
- Sandbox path: `truth-ledger/2026-07-11/155613-canonical-github-governance.md`
