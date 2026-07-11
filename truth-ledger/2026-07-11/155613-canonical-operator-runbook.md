---
id: bc416d9a-a752-4643-8a43-348279e9cd6b
created_at: 2026-07-11T15:56:13.819313+00:00
canonical_id: operator-runbook
topic: infra
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [operator, setup, deploy, secrets, runbook]
supersedes: [cc56edca-9475-479b-b61f-b17231343d06]
answer_hash: 232aea8e7c79
status: verified
---

## Question
How does the operator set up a new PC, GitHub account, or coding agent without forgetting steps?

## Answer
SSOT machine: aria_core/knowledge/operator_pitfalls.yaml (pitfalls + new_pc_checklist). SSOT humain: aria-ops/vanguard/operator/OPERATOR-RUNBOOK.md. Scripts: operator/new-pc.ps1, check-aria-status.ps1 (audit), sync-render.ps1 (secrets → Render + mandatory redeploy). IDE agents: skill operator-runbook + Cursor rule always-on. ARIA recalls via MEMORY_RECALL when the operator says runbook, nouveau pc, or ne pas oublier. After each fixed incident: append operator_pitfalls.yaml, run check-aria-status.ps1, propose /learn for strategic memory. Golden rule: updating Render env vars does not reload the running Python process — redeploy first.

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `cc56edca-9475-479b-b61f-b17231343d06`
- Sandbox path: `truth-ledger/2026-07-11/155613-canonical-operator-runbook.md`
