# Conflict check -- rules still active that contradict the new governance

    run by: Claude A, 2026-09-02, at B's request (condition 5 of 5).
    NOTHING WAS CORRECTED. Every item below is reported, not fixed: two touch
    files that govern how every session behaves, which is above A's solo remit.

## 1. CONFIRMED -- hooks address A's perimeter to EVERY session

`.claude/settings.json` registers `UserPromptSubmit` hooks that fire in any
Claude Code session on this project, B's included. `session-checkpoint.sh`
mentions push, commit, deploy, CLAUDE.md, HANDOFF and etat-systeme;
`context-ceiling.sh` mentions CLAUDE.md and HANDOFF.

The contradiction is mechanical, not theoretical: the hook instructs B to act on
the repository, which the new governance forbids B from doing. It fired five
times on B today. B correctly refused each time and relayed them to A.

Options, none applied:
- (a) leave as is; B keeps relaying. Costs a relay per firing, zero risk.
- (b) make the hooks role-aware, so they only address the session that owns the
  repository. Requires a way for a session to know its role, which does not
  exist today.
- (c) narrow the hooks' wording so they state a fact rather than issue an
  instruction. Cheapest, but only reduces the confusion, does not remove it.

A's recommendation: (a) for now, (c) when someone touches those hooks anyway.
Not A's call alone -- these hooks govern every session's behaviour.

## 2. CONFIRMED -- CLAUDE.md assumes a direct operator-to-A dialogue

`CLAUDE.md` is loaded into every session and still carries rules built on the
operator speaking directly to the executing agent: "Analyser -> Proposer un plan
-> attendre « go »/« ok »", "L'opérateur tranche chaque proposition par oui/non",
and the whole "Format de réponse" section, which specifies how to answer the
operator directly.

Under HUMAN -> B -> A these describe a channel that no longer carries daily
strategy. They are not wrong so much as addressed to a configuration that
changed today.

**Not corrected, deliberately.** CLAUDE.md's own absolute rules forbid modifying
guardrail and governance files without an explicit operator "ok", and CLAUDE.md
is itself the project's governance file. It is also within ~250 bytes of its
enforced size budget, so any addition requires removing something else. This
needs an explicit operator decision, not an agent's initiative.

## 3. NOT A CONFLICT, but worth stating

A's session output channel is displayed directly to the operator: it is the only
part of A's work the operator sees. A will keep reporting factual technical
state there, and will not carry strategic decisions in it. Raised with B under
the anti-divergence rule rather than settled alone.
