# Feature Specification: RPC Security Shadow

**Feature Branch**: `018-rpc-security-shadow`

**Created**: 2026-09-02

**Status**: Draft

**Input**: User description: "An RPC-based token-security engine running in SHADOW alongside the existing GoPlus/Honeypot.is path, producing a normalized, reproducible verdict for every candidate, plus a comparator measuring agreement, disagreement, unknown rate and real RU cost. Operator framing: this spec must NOT decide that Chainstack replaces GoPlus — it specifies the measurement experiment that will allow that decision to be made. We are not coding the conviction; we are coding the experiment that can falsify it."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Produce a reproducible security verdict from the chain itself (Priority: P1)

For a candidate token, the system simulates a purchase and then a sale directly against the blockchain, without holding any funds and without sending any transaction. It records whether each leg would succeed, how much would be received, the implied sale tax, and — when a sale fails — which contract and function rejected it. Every result carries the exact conditions under which it was produced, so the same check can be re-run later and be expected to give the same answer.

**Why this priority**: This is the capability everything else measures. It also carries a property the current security source structurally cannot offer: a verdict anchored to a specific block, therefore replayable. Without it there is nothing to compare, nothing to cost, and no way to reconstruct what was knowable at the moment of a past decision.

**Independent Test**: Run the engine on a token known to be tradeable and on a token known to trap sellers; confirm the first returns a sellable verdict with a plausible received amount, the second returns a blocked verdict naming the failing contract and function, and that both results carry their full reproduction context.

**Acceptance Scenarios**:

1. **Given** a normally tradeable token, **When** the engine evaluates it, **Then** it reports both legs succeeding, a received amount for each, an estimated sale tax, and a verdict of "safe".
2. **Given** a token whose sale always reverts, **When** the engine evaluates it, **Then** it reports the purchase succeeding, the sale failing, and identifies the contract and function where the failure occurred.
3. **Given** any completed evaluation, **When** its record is inspected, **Then** it contains the block it was evaluated at, the provider and endpoint role used, the chain, the router, the input amount, and a fingerprint of the simulated state — enough to re-run the identical experiment later.
4. **Given** the same token and the same historical block, **When** the evaluation is re-run, **Then** it produces the same verdict as the original run.

---

### User Story 2 - Measure agreement with the existing security source, without ever influencing a decision (Priority: P2)

Both security sources run on the same candidates. A comparator records, for each pair, whether they agree, disagree, or whether one of them could not conclude — together with what the chain-based check cost. No trading decision consults the chain-based verdict at any point during this phase.

**Why this priority**: This is the measurement the whole feature exists for. The operator's decision — whether the chain-based engine can ever carry security on its own — must rest on a real agreement rate and a real cost, not on the expectation that it should work. Until those numbers exist, the existing source stays the sole authority.

**Independent Test**: Run both sources over a set of candidates with a mix of outcomes; confirm every pair produces exactly one comparison outcome, that disagreements are individually inspectable, and confirm — by examining the trading path — that no decision changed as a result of the chain-based verdict.

**Acceptance Scenarios**:

1. **Given** both sources returning a verdict for the same token, **When** the comparator records the pair, **Then** it classifies it as agreement, disagreement, or unknown, and stores both verdicts so the disagreement can be examined case by case.
2. **Given** a disagreement, **When** an analyst inspects it, **Then** they can see both verdicts, the failure diagnosis from the chain-based check, and the reproduction context — enough to determine which source was right.
3. **Given** the feature running for a full day, **When** the trading decisions are compared against what they would have been without it, **Then** they are identical — the chain-based verdict gated, rejected and sized nothing.
4. **Given** any evaluation, **When** its cost is recorded, **Then** it is accounted against the existing chain-budget mechanism, never a second parallel budget.

---

### User Story 3 - Establish the real cost on a controlled benchmark before any volume (Priority: P3)

Before the engine is pointed at the live candidate stream, it is run over a small curated set covering the situations that matter: normally tradeable tokens, tokens that trap sellers, tokens with punitive sale taxes, tokens with almost no liquidity, and tokens with atypical behaviour. Each category is measured for cost, latency, failure rate, inconclusive rate, agreement, and whether the failure cause was actually identified.

**Why this priority**: The operator's explicit sequencing — a curated set first, not two thousand tokens straight away. The per-token cost figure is what determines whether the available chain budget can absorb the real candidate volume at all, and it must be measured rather than estimated. Running at volume first would spend the budget to discover it was unaffordable.

**Independent Test**: Execute the benchmark over the curated set and produce a per-category report; confirm every measured quantity is present for every category and that the per-token cost is derived from observed measurements, not from an assumed price per operation.

**Acceptance Scenarios**:

1. **Given** the curated benchmark set, **When** it is executed, **Then** the report gives, per category and overall: cost per operation, cost per complete token evaluation, latency, error rate, inconclusive rate, agreement rate, and the share of failures whose cause was identified.
2. **Given** the measured cost per token, **When** it is projected against the available daily chain budget, **Then** the report states plainly how many evaluations per day that budget actually allows.
3. **Given** a category where the engine performs poorly, **When** the report is read, **Then** that category is visible on its own rather than hidden inside an overall average.

---

### Edge Cases

- What happens when the simulated purchase itself fails (no route, no liquidity, unsupported pool)? → The verdict is "unknown" or "simulation error", never "risky" — an unusable simulation is an absence of information, not evidence against the token.
- What happens when a sale succeeds but returns almost nothing (punitive tax)? → Both legs are recorded as succeeding with the received amounts and the implied tax; classifying that as safe or risky is a threshold question and the threshold is an explicitly uncalibrated starting value, flagged as such.
- What happens when the chain budget is exhausted mid-run? → Evaluation stops cleanly and records that it stopped for budget reasons; a partially spent budget never produces a verdict presented as complete.
- What happens when the same token is evaluated at two different blocks with different results? → Both are kept; a verdict is always relative to its block, and a change between blocks is itself an observation (the contract's behaviour changed), not a contradiction to resolve.
- What happens when the two sources disagree? → Nothing, during this phase. The disagreement is recorded for analysis; neither source overrides the other and the existing one remains the sole authority for decisions.
- What happens on a chain whose budget is set to zero, or that the engine does not support? → The candidate is skipped and recorded as out of scope, never evaluated with a fabricated verdict.
- What happens if the failure diagnosis cannot identify the failing function? → The verdict still stands with the diagnosis marked unavailable; a missing explanation never silently becomes "no failure".

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST evaluate a token's sellability by simulating a purchase followed by a sale against the chain, without holding funds and without broadcasting any transaction.
- **FR-002**: Every evaluation MUST produce a normalized result containing: an overall status among safe / risky / unknown / simulation error; per-leg success and received amount for the purchase and the sale; an estimated sale tax; and, on failure, the contract, function and reason.
- **FR-003**: Every evaluation MUST record its full reproduction context: the block it was evaluated at, the provider, the endpoint role, the chain, the router, the input amount, and a fingerprint of the simulated state — so the evaluation can be re-run as an experiment rather than merely read as a stored verdict.
- **FR-004**: The provider MUST be recorded explicitly on every result and MUST NEVER be inferred from the chain. Two different providers currently serve the same chain in this system, and a later analysis mixing verdicts from different providers without knowing it would be indistinguishable from a real behavioural change.
- **FR-005**: No secret-bearing endpoint URL may ever be persisted. The endpoint is recorded by role, never by its credentialed address.
- **FR-006**: Re-running an evaluation for the same token at the same block MUST produce the same verdict.
- **FR-007**: The chain-based verdict MUST NOT gate, reject, size, delay, or otherwise influence any trading decision during this phase. This MUST be verifiable mechanically, not merely asserted.
- **FR-008**: The system MUST NOT modify the existing security-check path, the existing watchlist, or any existing threshold or gate.
- **FR-009**: For each candidate evaluated by both sources, the system MUST record a comparison classified as agreement, disagreement, or unknown, retaining both verdicts so any disagreement can be examined individually.
- **FR-010**: Every evaluation's resource cost MUST be accounted against the existing chain-budget mechanism, never a second parallel budget, and MUST be recorded on the result.
- **FR-011**: The system MUST stop evaluating when the chain budget for that chain is exhausted, and MUST record that this is why it stopped.
- **FR-012**: A benchmark capability MUST exist that runs a curated set covering tradeable, seller-trapping, punitive-tax, low-liquidity and atypical tokens, and reports cost per operation, cost per complete token, latency, error rate, inconclusive rate, agreement rate and cause-identification rate — per category and overall.
- **FR-013**: The system MUST be gated by its own dedicated switch, off by default, so it performs no network call until deliberately enabled.
- **FR-014**: An inconclusive or failed simulation MUST NEVER be recorded as evidence against a token; absence of information and negative information MUST remain distinguishable in the stored result.

### Key Entities *(include if data involved)*

- **Security Evaluation**: one chain-based verdict for one token at one block. Holds the overall status, the two simulated legs with their received amounts, the estimated sale tax, the failure diagnosis when applicable, the full reproduction context (block, provider, endpoint role, chain, router, input amount, state fingerprint), and the measured cost.
- **Verdict Comparison**: one pairing of a chain-based evaluation with the corresponding verdict from the existing source, classified as agreement, disagreement or unknown, retaining both verdicts and the measured cost.
- **Benchmark Report**: the aggregated measurements over the curated set, broken down by category and overall, including the projection of how many daily evaluations the available budget allows.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of stored evaluations carry a complete reproduction context — no evaluation exists whose conditions of production are unknown.
- **SC-002**: Re-running any sampled past evaluation at its recorded block reproduces its original verdict in at least 95% of cases; every exception is explainable by a real on-chain change rather than by missing context.
- **SC-003**: Over any 24-hour window with the feature enabled, the number of trading decisions changed by it is exactly zero.
- **SC-004**: The benchmark yields a per-token cost figure derived from observed measurements, and states how many daily evaluations the available budget allows — a number an operator can act on without further computation.
- **SC-005**: For seller-trapping tokens in the benchmark, the share whose failing contract and function were identified is reported explicitly, so the diagnostic capability is quantified rather than assumed.
- **SC-006**: Agreement, disagreement and inconclusive rates against the existing source are reported with their sample size, and disagreements are individually retrievable for inspection.
- **SC-007**: Zero stored result contains a credentialed endpoint address.
- **SC-008**: No evaluation is attributed to a provider that did not produce it — verifiable by checking that every stored provider value matches an endpoint role actually configured for that chain.

## Assumptions

- The chain-based simulation relies on the ability to evaluate a call against modified account state and to trace a failing call. Both were verified as available on the target chain's endpoint before this specification was written; if a future endpoint lacks them, the engine reports inconclusive rather than degrading to a guess.
- Coverage is limited to the chains that currently have a non-zero chain budget. A chain whose budget is zero is out of scope by construction, not by omission.
- The existing security source remains the sole authority for trading decisions throughout this phase. Any move of security off the critical path is a separate, later decision that this experiment exists to inform.
- Thresholds used to translate an observed sale tax into a safe/risky classification are explicitly uncalibrated starting values, labelled as such, and are not to be treated as validated because the engine ships.
- The curated benchmark set is assembled from tokens whose real behaviour is already known, including from this system's own historical records; it is a fixed reference set, not a live sample.
- Resource accounting reuses the existing chain-budget mechanism, so this feature's consumption is visible alongside every other consumer of the same budget rather than in isolation.
- A stale comment in the existing security client asserts that no monthly or daily quota was ever confirmed for that source. Public figures now contradict it, and the quotas are the real binding constraint. Correcting that comment is in scope for this work, as leaving it would keep understating the very risk this feature measures.
- Static contract inspection (ownership, minting, blacklisting, pausing, fee-setting, transfer limits) and any routing of the existing source into a fallback position are explicitly later phases, not this one.
