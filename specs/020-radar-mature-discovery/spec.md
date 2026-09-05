# Feature Specification: RADAR Mature Discovery & Dynamics Architecture

**Feature Branch**: `020-radar-mature-discovery`

**Created**: 2026-09-04

**Status**: Draft

**Input**: User description: "Design the feeding architecture for the Robinhood Chain RADAR: today it only ever discovers a pool at the exact block it is created (event-driven Birth discovery), so every downstream mechanism — including the future Dynamics/Exhaustion detectors — inherits a structural blind spot for any token more than a few minutes old. Two established, real Robinhood tokens (NET, ~$4.47M market cap/49 days old; CASHCAT, ~$267M/78 days old) do not exist in any ARIA table despite real ongoing activity, while 20/20 recently qualified candidates were all under $12.6k market cap and under 24 seconds old at qualification. Specify a second, independent discovery source for already-mature pools (any age) that feeds the exact same pool registry and tracking/reality pipeline Birth discovery already uses — never a parallel, duplicated pipeline, never a source that itself judges quality, and never an unbounded scan that risks a resource-budget incident. This document defines the feeding architecture only (two discovery sources → shared pool registry → shared tracking/reality layer); the Birth/Dynamics/Exhaustion detectors that consume this feed are explicitly out of scope, to be specified separately once this foundation is validated."

## Architectural Context

Two independent discovery sources feed one shared pipeline:

```
                    DISCOVERY SOURCES
                           |
             +-------------+-------------+
             |                           |
       Birth discovery             Mature discovery
       (event-driven,               (scan / ranking,
        pool creation)                any pool age)
             |                           |
             +-------------+-------------+
                           v
                     POOL REGISTRY
                           |
                           v
                  TRACKING / REALITY
                           |
             +-------------+-------------+
             v             v             v
           BIRTH        DYNAMICS     EXHAUSTION
        (future, out of scope for this document)
```

Birth discovery is the existing event-driven pipeline (unchanged by this feature). Mature discovery is the new source this feature specifies: a way to find pools of any age that Birth discovery never saw or has long since stopped tracking. Both sources hand off only a bare `(chain, pool, token)` reference to the same pool registry, which is the single on-ramp into the tracking/reality layer already used today. The three detectors at the bottom are explicitly future work; this document stops at the tracking/reality layer.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A mature, already-established token can enter the RADAR pipeline (Priority: P1)

Today, a token that graduated or launched weeks or months ago, and that Birth discovery never saw at its creation block, can never be discovered by ARIA at all — regardless of how much real trading activity it now has. A new, independent discovery path finds such tokens and hands them to the same pipeline Birth-discovered tokens already go through, so the RADAR is no longer permanently blind to anything that existed before ARIA started watching Robinhood Chain, or that Birth discovery simply missed (a missed creation event, a restart gap, a chain reorg).

**Why this priority**: This is the exact capability gap that triggered this feature — verified live on 2026-09-04 that two real, active Robinhood tokens (NET, CASHCAT) are structurally invisible to every ARIA table. Every other story in this feature only matters once a mature token can actually enter the pipeline at all.

**Independent Test**: Pick a real, currently-trading Robinhood pool that is more than 24 hours old and that ARIA has never tracked. Confirm the new discovery source can surface it as a `(chain, pool, token)` candidate and that it subsequently appears in the pool registry, without any dependency on ARIA having observed its creation block.

**Acceptance Scenarios**:

1. **Given** a real pool that is several weeks old and has never appeared in any ARIA table, **When** mature discovery runs its next pass, **Then** the pool becomes reachable as a discovery candidate without requiring its original creation event.
2. **Given** Birth discovery never saw a pool's creation (missed event, feed restart gap, chain reorg), **When** mature discovery later encounters that same pool through its own signal, **Then** the pool still enters the registry exactly once, through the normal admission path.
3. **Given** a token is genuinely brand new (seconds old), **When** mature discovery's own ranking pass runs, **Then** it is not required to also find that token — Birth discovery remains the fast path for genuine day-zero coverage, mature discovery is not expected to duplicate it.

---

### User Story 2 - Discovery never decides that a token is interesting (Priority: P1)

Whichever source finds a pool — Birth or mature — it only ever hands over the bare fact that the pool exists (`chain`, `pool address`, `token address`). Neither source is allowed to attach a quality judgment, a "worth watching" label, or any interestingness score to what it reports. That judgment stays entirely downstream, in the tracking/reality layer and — later — the detectors, so the same evaluation logic applies uniformly no matter which source found the pool.

**Why this priority**: Without this separation, a "smarter" mature-discovery ranking signal could quietly become a second, un-audited scoring layer that decides what ARIA pays attention to — exactly the kind of untracked judgment this project's existing legitimacy engine and gate discipline (SECURITY, ON-CHAIN, X-transparency) is built to avoid. This must be true from the very first line of mature-discovery code, not retrofitted later.

**Independent Test**: Feed mature discovery's own internal ranking output directly to the pool registry's admission step and confirm the registry entry contains only the identity triplet plus a record of which source found it — no score, verdict, or quality field carried over from the discovery step itself.

**Acceptance Scenarios**:

1. **Given** a pool discovered by either source, **When** it is admitted to the pool registry, **Then** the registry entry records only its identity and which discovery source(s) found it — never a quality, safety, or interestingness verdict originating from the discovery step.
2. **Given** mature discovery's own cheap ranking signal is used to decide which candidates get admitted, **When** a candidate is admitted, **Then** the ranking value itself is not treated as a signal about the token by anything downstream — it only ever gated the admission decision, nothing else.
3. **Given** two candidates are admitted with very different ranking values, **When** they both proceed to tracking, **Then** they are tracked identically — the ranking value carries no special treatment forward.

---

### User Story 3 - Every discovered token, of any age, goes through the identical tracking/reality pipeline (Priority: P2)

A pool discovered by Birth and a pool discovered by mature discovery must be indistinguishable once they reach the tracking/reality layer — same snapshot mechanism, same price/liquidity resolution rules, same availability semantics. Nothing in the tracking layer is allowed to special-case "how this pool was found."

**Why this priority**: This is what makes the architecture actually load-bearing rather than cosmetic — without it, "mature discovery" would just be a second, parallel system that happens to also report to ARIA, defeating the entire point of unifying under one registry, and any future Dynamics detector would have to know which pipeline it's reading from.

**Independent Test**: Trace a Birth-discovered pool and a mature-discovered pool through the tracking/reality layer side by side. Confirm both produce the same snapshot shape, the same all-or-nothing price/availability behavior, and that no code path branches on discovery origin.

**Acceptance Scenarios**:

1. **Given** a pool discovered via Birth and a pool discovered via mature discovery, **When** both reach the tracking/reality layer, **Then** both are processed by the exact same mechanism, with no separate "mature" code path.
2. **Given** a mature-discovered pool has not yet completed the standard price/liquidity resolution, **When** its data is requested, **Then** it is reported unavailable — exactly like any Birth-discovered pool in the same state — never a shortcut or an inferred price.
3. **Given** a pool's discovery origin is Birth, Mature, or both, **When** its tracking history is later reviewed, **Then** the origin is visible as metadata but never changes what data or computation was applied to it.

---

### User Story 4 - Mature discovery never becomes a resource-budget risk (Priority: P2)

Unlike Birth discovery (which only reacts to rare, one-time creation events), a mature-token scanner could in principle try to examine every existing pool on the chain. This must never happen: mature discovery operates within an explicit, shared resource budget, admitting only a bounded number of candidates for short observation at a time, using the project's existing shared throughput-coordination mechanism rather than a second, independent throttle.

**Why this priority**: This project has already suffered one real cost incident (a duplicated, uncoordinated throttle) and carries a standing "single throughput-coordination point" rule for exactly this reason. A mature-token scanner is the single most likely place for this feature to accidentally reintroduce that failure mode, since it is the first Robinhood Chain mechanism that could plausibly want to look at "every pool" instead of a rare event stream.

**Independent Test**: Configure a deliberately large pool of eligible mature candidates (far exceeding the intended concurrent-observation budget) and confirm the number actually admitted for short observation stays within the documented budget, coordinated through the shared rate-limit mechanism, never a second independent limiter.

**Acceptance Scenarios**:

1. **Given** more eligible mature candidates exist than the observation budget allows, **When** admission runs, **Then** only the budgeted number are admitted, and the rest are deferred or discarded per an explicit, documented rule — never all admitted at once.
2. **Given** mature discovery needs to query the chain to build or rank its candidate universe, **When** it does so, **Then** it coordinates through the project's existing shared rate-limit/budget mechanism rather than maintaining its own independent throttle.
3. **Given** the shared resource budget is already near its limit from other mechanisms, **When** mature discovery would otherwise admit a new candidate, **Then** it respects the shared budget and does not admit past it.

---

### User Story 5 - Dynamics computation stays blind to how a token was discovered (Priority: P3)

Once a future Dynamics detector reads from the tracking/reality layer, it must never receive — or be able to infer through its inputs — whether the token it is looking at came from Birth or from mature discovery. The interface the tracking layer exposes upward carries only observed market reality (price, liquidity, activity history), never a discovery-origin flag as a usable input.

**Why this priority**: This is what actually delivers the reframe that triggered this feature — a Dynamics detector that silently behaves differently for "tokens I found at birth" versus "tokens I found later" would still be a disguised Birth detector wearing a new name. This is lower priority than Stories 1-4 only because there is no Dynamics detector to violate this yet — but the tracking/reality interface must be shaped correctly now, before that detector is built on top of it.

**Independent Test**: Inspect the data contract the tracking/reality layer exposes to any downstream consumer. Confirm discovery origin is not part of the payload used for dynamics calculations (it may still exist as separate registry metadata for audit purposes, just not on the calculation path).

**Acceptance Scenarios**:

1. **Given** the tracking/reality layer's output for any pool, **When** a downstream detector consumes it, **Then** the payload used for calculation contains no discovery-origin field.
2. **Given** two pools with identical observed market history but different discovery origins (one Birth, one mature), **When** the same detector logic is applied to both, **Then** it produces the same result for the same inputs — origin cannot be a hidden tie-breaker.

---

### User Story 6 - Mature discovery's own blind spots are visible, not hidden (Priority: P3)

Because the mature-discovery scanner necessarily uses some cheap, imperfect signal to decide which pools are even worth a short observation window, it will inevitably have its own structural blind spots — for example, if it can only see pools that already show some minimum trading activity, it will never find one that is dynamically interesting but has been quiet until this exact moment. This feature requires that whatever population of tokens the scanner structurally cannot reach is written down and reviewable, not left as an undocumented, invisible bias.

**Why this priority**: The operator explicitly flagged this as the central risk of the whole redesign — an architecture that is decoupled from Birth on paper but, in practice, still only ever sees "already popular" tokens through its ranking signal would not actually deliver independence from Birth, just a slower version of the same bias. This is priority 3 only because it is a documentation/reviewability requirement rather than a blocking mechanism — but it must ship with the first version of mature discovery, not be added later.

**Independent Test**: Read the documented description of what mature discovery's ranking/admission signal structurally excludes. Confirm it names concrete categories of tokens the mechanism cannot currently reach (not just "some are missed").

**Acceptance Scenarios**:

1. **Given** mature discovery's ranking/admission signal is defined, **When** it ships, **Then** its documented design explicitly states what kinds of pools it structurally cannot or is unlikely to surface.
2. **Given** an operator asks "what can mature discovery never find", **When** this is answered, **Then** the answer comes from an existing written record, not from a fresh ad hoc investigation.

### Edge Cases

- What happens when the same pool is found by both Birth discovery and mature discovery close together in time (a brand-new pool that also happens to satisfy the mature ranking signal)? Must resolve to exactly one pool registry entry, never two competing tracking states for the same pool.
- What happens when a mature candidate is admitted but its liquidity or tradability has collapsed by the time short observation actually starts? It must be filtered by the same downstream gates that already handle this for Birth-discovered candidates (e.g. the existing liquidity-eligibility gate) — never a special mature-only exception.
- What happens when more eligible mature candidates exist at once than the concurrent-observation budget allows? An explicit, documented tie-break or deferral rule must exist — never an unbounded queue and never a silent drop with no record.
- What happens if the data source mature discovery relies on for its cheap ranking signal becomes unavailable? Mature discovery must report itself degraded/unavailable for that period rather than silently stopping with no visible trace — consistent with this project's existing fail-visible-not-fail-silent sourcing doctrine.
- What happens to an admitted candidate that completes its short observation window without a clear continue/discard outcome (an ambiguous case)? A default behavior must exist (discard, or explicit "still unknown" carried forward) — no candidate may remain in limbo indefinitely.
- What happens if mature discovery and Birth discovery disagree about a pool's identity metadata (e.g. token address mismatch from a proxy or migrated contract)? The pool registry must have a single source of truth for identity, not silently trust whichever source wrote last.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a mature-pool discovery source, independent from Birth discovery, capable of surfacing pools of any age as `(chain, pool_address, token_address)` candidates.
- **FR-002**: System MUST continue to operate Birth discovery unchanged; mature discovery MUST NOT replace, gate, or depend on Birth discovery in any way, and Birth discovery MUST NOT depend on mature discovery.
- **FR-003**: System MUST guarantee that neither discovery source attaches any quality, safety, or interestingness judgment to a discovered pool — a discovery source's only output is the bare identity triplet plus which source(s) found it.
- **FR-004**: System MUST route every discovered pool, regardless of source, through the exact same pool registry and the exact same tracking/reality mechanisms already used today — no parallel or duplicated tracking path may exist for mature-discovered pools.
- **FR-005**: System MUST preserve the existing all-or-nothing price/availability resolution semantics for every pool: a mature-discovered pool is never treated as priceable or trackable until it has completed the same resolution process any other pool must complete.
- **FR-006**: System MUST bound mature discovery's resource usage: the number of candidates concurrently admitted for short observation MUST stay within an explicit, documented budget, and any chain queries mature discovery performs MUST be coordinated through the project's existing shared throughput-coordination mechanism rather than an independent throttle.
- **FR-007**: System MUST implement mature-candidate admission as a distinct staged process — building or consulting a universe of known mature pools, applying a cheap ranking/filter signal, admitting a bounded number of candidates, running a short, time-bounded observation, and reaching an explicit continue/discard outcome — rather than an unstaged or unbounded scan.
- **FR-008**: System MUST expose, on the interface any future Dynamics/Birth/Exhaustion detector consumes from the tracking/reality layer, no discovery-origin field as part of the calculation-relevant payload; discovery origin, if retained, MUST live only in separate registry/audit metadata.
- **FR-009**: System MUST record, for every pool registry entry, which discovery source(s) found it and when, for audit and debugging purposes, without that record influencing how the pool is subsequently tracked or evaluated.
- **FR-010**: System MUST document, alongside the mature-discovery ranking/admission signal, the concrete categories of pools it is structurally unlikely or unable to surface, and MUST make this documentation available for review independent of any single investigation.
- **FR-011**: System MUST define and apply a single, explicit rule for resolving duplicate or near-simultaneous discovery of the same pool by both sources, resulting in exactly one pool registry entry.
- **FR-012**: System MUST define and apply an explicit rule for what happens when an admitted mature candidate's observation window ends without a clear continue/discard outcome — no candidate may remain unresolved indefinitely.
- **FR-013**: System MUST report mature discovery as degraded/unavailable, visibly, whenever its underlying data source is unavailable — never silently stopping without a trace.

### Key Entities

- **Discovery Source**: Either of the two independent origins of pool identity — Birth (event-driven, existing) or Mature (scan/ranking, new). Emits only identity triplets, never judgments.
- **Pool Registry**: The single shared record of every pool known to ARIA's Robinhood RADAR, regardless of which source(s) found it. Holds identity, discovery-origin metadata, and is the sole on-ramp into tracking.
- **Mature Candidate**: A pool that has passed mature discovery's cheap ranking/filter signal and has been admitted for short observation, but has not yet reached a continue/discard outcome.
- **Tracking / Reality Snapshot**: The existing, source-agnostic mechanism that resolves a pool's real price/liquidity/activity state over time — reused unchanged for both discovery sources.
- **Observation Budget**: The explicit, shared limit on how many candidates (across all sourcing mechanisms coordinated through the same throughput point) can be concurrently observed at once.
- **Structural Coverage Gap**: A documented category of pool that the mature-discovery ranking signal cannot or is unlikely to surface — an explicit, reviewable statement of the mechanism's own blind spot.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A real, currently-active Robinhood pool more than 24 hours old that ARIA never tracked before can be discovered and enter the pool registry without any dependency on its original creation event having been observed.
- **SC-002**: 100% of pools admitted into tracking, from either discovery source, pass through the identical tracking/reality mechanism — measured by zero source-conditional branches in that layer's logic.
- **SC-003**: Disabling Birth discovery for a test period does not reduce the rate at which mature-origin candidates are admitted, and disabling mature discovery does not reduce the rate at which Birth-origin candidates are admitted — demonstrating the two sources are operationally independent.
- **SC-004**: The number of pools concurrently held in mature-discovery short observation never exceeds the documented budget figure, verified against the shared resource-coordination mechanism's own accounting.
- **SC-005**: Zero instances, across a representative observation period, of a price, liquidity, or availability value being reported for a pool that has not completed the standard resolution process.
- **SC-006**: The mature-discovery mechanism's documented structural coverage gaps are available in writing and can be produced on request without a fresh investigation.

## Assumptions

- This feature covers Robinhood Chain only, matching the current scope of the RADAR pipeline it extends; extending mature discovery to other chains (e.g. Base) is an explicit future decision, not part of this feature.
- The existing Birth discovery pipeline (event-driven, `onchain_pool_discovery.py`) and the existing tracking/reality layer (`early_life_observation.py`, `evm_swap_ws.py`) are extended, not rewritten — this feature adds a new discovery source and a shared pool registry concept, reusing the tracking mechanism as-is wherever possible.
- The Birth, Dynamics, and Exhaustion detectors that will eventually consume this feed are explicitly out of scope for this document; their concrete signals, thresholds, and formulas are deferred to a future specification, to be written only once this feeding architecture is validated and approved.
- No trading, sizing, or capital-allocation decision is affected by this feature — it is a discovery/feeding layer only, strictly upstream of any future signal or decision logic.
- The concrete method for building the mature-pool universe, the specific cheap ranking/admission signal, the exact concurrent-observation budget number, and the exact short-observation window duration are intentionally not decided in this document — see Open Questions below, which the planning phase's research is required to resolve with verified evidence before implementation begins.
- The existing shared resource-coordination mechanism referenced by FR-006 (the project's single throughput-coordination point pattern) is assumed to remain the correct place to register any new mature-discovery chain queries, rather than a new, separate mechanism.

## Open Questions for Planning Phase

These five questions are deliberately left unresolved in this specification. Per this project's governance for durable architecture decisions, the planning phase's research step is required to answer each with verified evidence (existing project mechanisms checked first, real documented limits, no invented figures) before any implementation design is finalized:

1. **Mature universe construction**: What real, already-available mechanism (existing indexed data, direct factory/registry queries, an existing swap-observation stream, or a new local index) should build the set of "known mature pools" mature discovery ranks against? Verify what already exists for Robinhood/Base before proposing anything new.
2. **Cheap admission signal**: What specific, inexpensive-to-compute signal (recent volume, volume change, trader count, liquidity change, recent activity, or a minimal combination) justifies admitting a candidate to short observation? Must be explicit and justified, never an opaque score.
3. **Concurrent admission budget**: How many pools can mature discovery admit for observation at once? Must be a concrete figure, checked against this project's own documented real rate/resource limits — never an assumed number.
4. **Observation window duration**: How long should a candidate be observed before a continue/discard decision is made? Must be justified on its own terms, not copied from the unrelated Birth-side tracking window without reconsideration.
5. **Avoiding a new bias source**: What specific design choice prevents the scanner itself from only ever finding already-popular tokens — which would leave Dynamics practically, if not architecturally, dependent on the same kind of bias Birth discovery has today? This question requires an explicit, concrete answer, not a design that leaves it open.
