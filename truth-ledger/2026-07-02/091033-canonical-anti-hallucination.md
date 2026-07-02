---
id: b1781c3c-88f6-49d6-8a12-3294665cb269
created_at: 2026-07-02T09:10:33.662960+00:00
canonical_id: anti-hallucination
topic: policy
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [policy, grounding]
supersedes: [none]
answer_hash: 2e3e189a028b
status: verified
---

## Question
How does ARIA avoid making things up?

## Answer
Verified-facts-only mode by default (ARIA_LLM_ENABLED=false). Answers come from canonical facts, FAQ, and verified Truth Ledger — no generative LLM on public chat. Factual skills are never rewritten by LLM. Social messages get a short template ack. If no verified source exists, ARIA says she lacks verified information. Operator can re-enable LLM later when grounding is trusted.

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `none`
- Sandbox path: `truth-ledger/2026-07-02/091033-canonical-anti-hallucination.md`
