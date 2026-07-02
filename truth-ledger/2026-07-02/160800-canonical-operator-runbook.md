---
id: 66f728aa-d93b-4edd-beb2-79b4a29895ac
created_at: 2026-07-02T16:08:00.956045+00:00
canonical_id: operator-runbook
topic: infra
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [operator, setup, deploy, secrets, runbook]
supersedes: [none]
answer_hash: bca71bd4e23d
status: verified
---

## Question
How does the operator set up a new PC, GitHub account, or coding agent without forgetting steps?

## Answer
SSOT machine: aria_core/knowledge/operator_pitfalls.yaml (pitfalls + new_pc_checklist). SSOT humain: aria-vanguard/operator/OPERATOR-RUNBOOK.md. Scripts: operator/new-pc.ps1, check-aria-status.ps1 (audit), sync-render.ps1 (secrets → Render + mandatory redeploy). IDE agents: skill operator-runbook + Cursor rule always-on. ARIA recalls via MEMORY_RECALL when the operator says runbook, nouveau pc, or ne pas oublier. After each fixed incident: append operator_pitfalls.yaml, run check-aria-status.ps1, propose /learn for strategic memory. Golden rule: updating Render env vars does not reload the running Python process — redeploy first.

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `none`
- Sandbox path: `truth-ledger/2026-07-02/160800-canonical-operator-runbook.md`
