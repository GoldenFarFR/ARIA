---
id: 1f0393c5-4a98-428d-9825-37bd1bf440cf
created_at: 2026-07-11T15:56:13.788212+00:00
canonical_id: repos
topic: infra
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [github, repos]
supersedes: [c45ffe9b-e599-42e8-80a4-a19f73938de3]
answer_hash: 509f256b5aad
status: verified
---

## Question
What are the main GitHub repos?

## Answer
GoldenFarFR/ARIA (public monorepo — aria-core, vanguard holding+API, truth-ledger; replaces the old separate aria-vanguard and aria-sandbox, merged in). GoldenFarFR/aria-ops (private — operator scripts, secrets vault, collegue-memoire). GoldenFarFR/aria-token-base (token R&D), GoldenFarFR/aria-skills, GoldenFarFR/kikou, GoldenFarFR/template-grok-cursor, GoldenFarFR/aria-acp-showcase. Operator scripts in aria-ops/vanguard/operator; secrets in local vault. Retired repos: dexpulse, dexpulse-secrets, aria-vanguard, aria-sandbox (merged into ARIA); collegue-memoire and aria-local-sync as standalone repos (folded into aria-ops). Live inventory — github status.

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `c45ffe9b-e599-42e8-80a4-a19f73938de3`
- Sandbox path: `truth-ledger/2026-07-11/155613-canonical-repos.md`
