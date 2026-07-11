---
id: 9a5aefd0-13df-4909-bbf9-f594d58551be
created_at: 2026-07-11T15:56:13.733133+00:00
canonical_id: anti-hallucination
topic: policy
skill: canonical_facts
sources: [canonical_facts.yaml]
tags: [policy, grounding]
supersedes: [a5445618-188e-41c7-8a5e-cd6e1a3b956d]
answer_hash: f3b92b23c005
status: verified
---

## Question
How does ARIA avoid making things up?

## Answer
Canonical facts, FAQ, and verified Truth Ledger are checked first and win on a match — never rewritten by the LLM. The LLM (ARIA_LLM_ENABLED=true in production) handles everything else, constrained by a grounding layer: verified sources first, calibrated fact/small-talk routing, and an explicit "I don't have verified information on that" instead of inventing an answer when no source backs one.

## Meta
- Canonical fact — edit `canonical_facts.yaml` when this truth changes
- Supersedes prior entry ids: `a5445618-188e-41c7-8a5e-cd6e1a3b956d`
- Sandbox path: `truth-ledger/2026-07-11/155613-canonical-anti-hallucination.md`
