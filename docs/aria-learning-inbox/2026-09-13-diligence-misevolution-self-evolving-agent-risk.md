# "Your Agent May Misevolve" — self-evolving agent risk framework vs the v8 mandate

Not independently re-verified beyond the research-log entry's own sourcing (arXiv 2509.26354, "Your Agent May Misevolve: Emergent Risks in Self-Evolving LLM Agents", accepted ICLR 2026). Read the full paper before acting on this fiche — this is a research pointer, not a completed diligence.

## Why this concerns ARIA directly

The paper names "Misevolution" as a risk class specific to an agent that modifies its OWN functioning, and splits it into 4 axes: model, memory, tool, and WORKFLOW. It reportedly shows this risk appears even in agents built on frontier-grade LLMs — self-evolution capability alone, not model weakness, is the source of risk.

The v8 mandate (`CLAUDE.md`, "v8 = l'agent de Claude") authorizes Claude Code to modify `scalping_v8` on its own initiative — code, commit, and deploy — without human validation, bounded to paper trading only. That is a real, running instance of exactly the WORKFLOW/TOOL axis this paper studies. Distinct from every memory-poisoning entry already catalogued (Zombie Agents, MemAudit, OWASP Agent Memory Guard, #338/#408): those cover the MEMORY axis only. Nothing in ARIA's mandate inventory has been explicitly checked against a WORKFLOW/TOOL self-modification risk framework before.

## What a future dedicated session should do

- Read the full paper (not just the abstract/research-log summary) to extract its named mitigations — the log entry mentions "staged approvals on high-impact changes," "rollback to a known-safe baseline on drift," and "structured self-critique" as examples, but these need verification against the actual paper text before being treated as established findings.
- Map each mitigation against what the v8 mandate already has: does a v8 self-modification pass have any staged-approval gate before deploy (today: none, it deploys directly once tests are green), any automatic rollback-on-drift mechanism (today: manual, via the existing blue-green near-instant rollback, but never triggered by a *drift* signal specifically), and any structured self-critique step (today: informal, via commit messages and the Devil's Advocate post-push review — not built for this specific failure mode).
- Decide whether any gap found is worth closing, and if so scope it as a normal Fast-Track or spec-kit item per CLAUDE.md's routing table (this touches the v8 mandate, not a guardrail file itself, so it is very likely Fast-Track — but the classification should be made explicitly, not assumed here).

No code from this fiche alone. Read-only pointer for a future session with the bandwidth to read the full paper.
