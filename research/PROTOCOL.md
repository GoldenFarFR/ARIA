# Duo protocol -- Claude A (Engineering) / Claude B (Trader / Capital Strategist)

> **Roles were SWAPPED by the operator on 2026-09-02, a few hours after this
> file was first written.** The first version had A as Strategist and B as
> Engineer. If you are reading an artifact issued before that swap, check who
> wrote it rather than assuming the role from the letter.

Operational rules for the two-session research loop. The governing texts are
`research/CONSTITUTION.md` (operator verbatim) and `docs/mandat-recherche-alpha.md`.
`CLAUDE.md` outranks both and is never relaxed here.

## Roles

| | Claude A -- Research / Engineering Lead | Claude B -- Trader / Capital Strategist |
|---|---|---|
| Asks | How do we demonstrate this without telling ourselves a story? | Can this actually improve our ability to win? |
| Does | code, infrastructure, collection, experiments, tests, fixes, deployment | strategy, alpha search, prioritisation, economic evaluation, synthesis to the human |
| Writes | **the whole repository -- A is the only agent that modifies it** | nothing in the repo; B's requests and findings are engraved by A |
| Never | changes an experiment's definition after seeing its results | writes code or edits files |

Consequence of B writing nothing: when B sends a REQUEST or a FINDING, A engraves
it under `research/` **verbatim in substance**, including the parts A disagrees
with. A's disagreement goes in a separate review artifact, never by editing what
B sent. A registry that only keeps the winning side of an argument is worthless.

## Anti-collision (mandatory, and not a formality)

**Neither session writes to `/opt/aria-data/aria.db` or `shadow.db`.**
This is not caution, it is a repeat of a real incident. `paths.py`'s
`shadow_db_path()` docstring records 17/08: two independent long-running
processes writing the same SQLite file, **even in WAL mode**, produced sustained
`database is locked` failures on unrelated production heartbeat tasks. The fix
chosen then was physical file separation, not a lock. There is **no
`PRAGMA busy_timeout` anywhere in `aria_core`** and no named cross-process lock
between the `aria-api` container and `aria-shadow-persistent.service`. Adding two
more writers reproduces the exact conditions of that incident.

Consequence: experiment output goes to files under `research/results/`, or to a
**separate** database file created for the purpose. Reads on the production
databases stay `-readonly`.

File ownership after the role swap: A owns and commits everything. The
anti-collision rule below therefore no longer protects against two writers in
the repo -- it protects against two writers in the production DATABASES, which
is where the real 17/08 incident happened, and it still stands in full.

Out of bounds for both sessions without an explicit operator "ok": guardrail
files (`permission_mode`, `wallet_guard`, `regles-uniques`, `config.toml`), real
capital, destructive git operations, `deploy.sh`, and turning any research
collector into a permanent daemon.

## Cycle

    OBSERVATION -> HYPOTHESIS -> MISSION -> EXPERIMENT -> RESULT
      -> RED TEAM -> REVIEW -> REJECT / MORE_DATA / PROMOTE

Every step leaves a persistent artifact. `research/queue/` holds missions not yet
claimed, `running/` a claimed mission, `results/` B's report, `reviews/` A's
verdict. Nothing important lives only in a session transcript.

## Why file-based exchange, and not chat

Not merely for durability. Measured effect: an identical error (byte-identical,
SHA-256 verified) is corrected 23 to 93 percentage points more often when it
reaches the model as external content rather than as its own reasoning
(*The Self-Correction Illusion*, arXiv 2606.05976, June 2026 -- one cell: 0% vs
87% on the same arithmetic error). Two instances of one model are otherwise poor
independent verifiers: error correlation between models **rises** with capability
(arXiv 2506.07962, ICML 2025, 350+ models, ~60% agreement when both are wrong).

So the written artifact is not bureaucracy. It is the mechanism that makes the
cross-check work at all. A verdict reached by reading a `RESULT.md` is worth
materially more than the same verdict reached in conversation.

Known counterpart, to watch for actively: Anthropic documents the **Early Victory
Problem** -- verification agents marking output as passing without real testing.
A review that only says "looks right" is a protocol failure.

## Promotion, and where it stops being automatic

    exploratory -> controlled -> out-of-sample -> prospective -> shadow || micro-live -> live

Promotion up to and including **shadow** may follow written criteria without the
operator. The `||` is a hard boundary: **micro-live and live commit real capital
and are never promoted by either agent**, whatever the evidence. That boundary
comes from `CLAUDE.md` and this protocol cannot move it.

Asymmetry that follows: A may REJECT alone, and should. A may not PROMOTE past
the boundary alone, ever. Rejecting is safe, promoting is not.

## Stop conditions

`data_quality_failure`, `budget_limit`, `risk_limit`, `no_information_gain`,
`conflicting_evidence`. An iteration is justified only if it can reduce an
uncertainty, produce new data, falsify a hypothesis, resolve a contradiction, or
materially improve an implementation. Otherwise: `NO_INFORMATION_GAIN`, stop.

## Cost discipline

Multi-agent work is measured at 3-10x a single agent for the same task, ~15x a
plain chat (Anthropic engineering figures). The loop must therefore earn its
multiplier on questions that a single session genuinely could not answer. Every
mission carries a resource budget in RU and stops at it.
