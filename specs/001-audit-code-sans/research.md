# Phase 0 Research: measurement method per component class

No `NEEDS CLARIFICATION` markers remain from plan.md's Technical Context --
this phase instead nails down HOW each component gets a real measurement,
since FR-001 bans inferring a verdict from documentation alone. Method is
picked per component **class**, reused across every component in that class
(never re-derived case by case -- that would itself violate the "réutiliser
les mécanismes existants" doctrine).

## Component classes and their measurement method

### 1. Heartbeat-gated cycle (e.g. `paper_weekly_review_cycle`, `vc_crawl`, `polymarket_paper_cycle`)
- **Source of truth**: `/opt/aria-data/heartbeat_state.json` -> `last_runs` --
  the SAME field `heartbeat.py` itself reads to decide if a task is due (the
  `vc-watch` pattern, not a guessed business table).
- **Expected output**: defined per-cycle in its own HANDOFF or CLAUDE.md
  entry (e.g. "a qualified smart wallet", "a closed paper position").
- **Real output**: `sqlite3 -readonly aria.db` aggregate query (COUNT/GROUP BY
  over the full table, never a `LIMIT` sample) against the table that cycle
  is supposed to populate.

### 2. External API client / sourcing pocket (e.g. CabalSpy, signal cascade sources)
- **Source of truth**: the client's own request log or the downstream table
  it feeds (candidate table, triage queue).
- **Expected output**: a qualified candidate reaching the next stage
  (convergence, a trade, a Telegram alert) -- not a raw request count, which
  only proves the client was called, not that it delivered.
- **Real output**: count of rows that reached the NEXT stage, divided by
  count of rows the source itself produced -- a conversion rate, not a
  binary. A client that fires often but converts 0% is the wallet-scoring
  pattern exactly.

### 3. Gate/flag (e.g. `ARIA_X_ENABLED`)
- **Source of truth**: `docker inspect aria-api` (live env), never CLAUDE.md's
  cited state -- it has already gone stale more than once (documented in
  CLAUDE.md itself, e.g. `ARIA_BRAIN_ENABLED`, `ARIA_BONDING_DISCOVERY_ENABLED`).
- **Expected output**: N/A for the gate itself -- a gate is a switch, not a
  producer. It routes to a component in class 1 or 2, whose own measurement
  applies. A gate being ON with its target component producing nothing is
  itself the finding (component looks alive, isn't).

### 4. Standalone/out-of-repo process (e.g. `shadow_persistent.py`)
- **Source of truth**: `ps`/`systemctl` (or absence of a systemd unit, a known
  standing gap for this exact process) for liveness, plus the same DB
  aggregate as class 1 for output.
- **Note**: not wired to `heartbeat_state.json`, so class 1's source of truth
  does not apply -- liveness must be checked separately (real incident: this
  process has died silently before, cf. `feedback_background_process_needs_systemd_run`
  in session memory).

### 5. Guardrail / kill-switch (e.g. `outgoing_pause`, circuit breakers)
- **Expected output is legitimately zero** when nothing bad happened (spec.md
  Edge Cases). Never flagged for low activity. Only flagged if it fires
  constantly (mis-tuned) or if its own state has silently diverged from what
  the operator believes (the exact class of incident that produced
  `system_issues` #241 this same session).

## Why aggregation-over-sampling is load-bearing here, not just a style rule

The founding incident (wallet-scoring) was invisible under the sampling that
`test_coherence.py` and every normal code review already does -- the code
worked, the tests passed, the rate limits fired exactly as configured. The
only view that would have caught it is "how many rows are in the qualified
output table, over the entire lifetime of the mechanism" -- a single
aggregate query. Every method above is built around that same one shape of
query, applied to a different table each time.

## Reused mechanisms (FR-003), not re-derived

- `aria_core.system_issues` -- if a component's audit surfaces a live
  discrepancy (gate state vs documented state, dead process), open an issue
  there rather than only noting it in the report; that is what makes it
  visible to the next session even if this audit's report is not re-read.
- Existing HANDOFF files -- each component's own HANDOFF is read FIRST for
  its originally stated purpose (the "expected result" per spec.md's Key
  Entities), before any measurement is taken. This is the inference source
  spec.md's Assumptions section allows for components predating a written
  criterion.
- `docs/registre-automatisations.md` -- the bounded list this audit's scope
  is drawn from (see `audit-scope.md`), rather than re-discovering every
  cron/hook/sidecar from scratch.
