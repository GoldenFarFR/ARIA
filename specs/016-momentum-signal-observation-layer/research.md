# Phase 0 Research: Momentum Signal Observation Layer

All findings below are re-verified against the live code this session (not recalled from prior audits or documentation), per this repo's "verify before asserting" doctrine.

## 1. Capture point: single wrapper, never duplicated at internal gates

**Decision**: Wrap `evaluate_momentum_entry()` at its one true call boundary, not inside it.

**Rationale**: `evaluate_momentum_entry()` (`momentum_entry.py:4374+`) has ~16 internal early-return points inherited from `evaluate_hard_gates()` (`momentum_entry.py:3512-3872`, verified: 16 distinct `return` statements) plus several more of its own (`ohlcv_unavailable`, `best is None`, the final BUY/HOLD paths). Injecting a capture call at each would duplicate the gate list outside the real one — exactly the kind of second, drift-prone copy CLAUDE.md's architectural-coherence doctrine forbids, and exactly what FR-009 (never modify existing decision logic) and FR-011 (observations must survive future pipeline changes) rule out.

Instead, rename the current function body to an internal `_evaluate_momentum_entry_core(...)` and expose `evaluate_momentum_entry(...)` as a thin wrapper: call the core, capture the observation from whatever it returned, then return that value completely unchanged. Every one of the ~16+ internal returns already funnels through this single external call boundary — Python guarantees a function returns to its one caller regardless of which internal `return` fired. This makes correctness by construction: the wrapper physically cannot alter the decision (FR-008), and there is nothing to keep in sync as new gates are added inside the core function later.

Precedent for this exact call site already exists in the same file: `narrative_signal_shadow.record_evaluation(...)` is called right after the hard-gate check (`momentum_entry.py:~4530`) in a `try/except Exception: pass` best-effort block, proving this file already tolerates a non-blocking observational side-call at this stage of the pipeline. The wrapper approach generalizes that same posture to the function's single exit instead of one specific internal point.

**Alternatives considered**:
- *Capture inside each internal gate* — rejected: duplicates ~16+ decision points, guaranteed to drift as `evaluate_hard_gates`/`evaluate_momentum_entry` evolve (this session's own on-chain audit already found two doc/code drifts in this exact file, evidence this file changes faster than its own comments).
- *Capture at each caller site (`paper_trader.py`, `robinhood_pump_shadow.py`, etc.)* — rejected: multiple call sites today and more may appear later (constitution explicitly flags 16 modules on a related dome-wide migration); a single wrapper around the shared function covers all callers automatically, per Sobriety doctrine (never duplicate).

## 2. Signal value availability naturally follows the existing return shape

**Decision**: Derive each signal family's "available / not available" state directly from what the core function's return value already contains, rather than re-deriving it from scratch.

**Rationale**: Verified live: `dex_composite_score` is computed *only* if `action == "BUY"` **after** the fundamental (`conviction_research`) check (`momentum_entry.py:~5062`, guarded by `if action == "BUY":`) — meaning for the majority of rejections (golden pocket/RSI failure, weak R:R, honeypot, holder concentration, insufficient liquidity, etc.) the on-chain composite score is **never computed at all**, not hidden. This is not a gap to fix; it is the real shape of the pipeline, and it is exactly the "not evaluated" state FR-005 requires the observation layer to represent honestly. The wrapper's job is to read whatever keys the core function's return dict actually populated for this specific decision, and mark every family/sub-signal whose value never got computed as `not_available` with reason `not_evaluated_this_gate` — never infer a neutral value for it.

Chart signals (`entry_signals.detect_entry`, `_technical_alignment`, `_check_volume_confirmation`, `market_sentiment.resolve_meta_regime()`) are computed earlier and more consistently in the pipeline (chart is the primary gate, per this session's audit), so they will be populated far more often than on-chain/social in the observation record — this asymmetry is expected and correct, not a defect to correct in this feature.

**Alternatives considered**:
- *Always recompute all three families independently after the decision, regardless of whether the core pipeline computed them* — rejected: violates FR-010 (no look-ahead: a signal recomputed after the decision using post-decision data is not the same measurement as what actually gated the decision) and would double the network/API cost this repo's Resource-Engineering doctrine explicitly forbids paying twice for the same read.

## 3. Forward price tracking for rejected candidates: new short-cadence cycle, not the existing lazy pattern

**Decision**: A new heartbeat cycle (~60s cadence) that actively resolves due-but-unmeasured horizons, reusing `aria_core.services.dexscreener.fetch_token_pairs(contract, chain)` as the sole price source — not the existing "lazy, resolved on next read" pattern already used elsewhere in this repo for a superficially similar problem.

**Rationale**: Two existing modules already solve "forward price for a candidate that never became a position" — `signal_cascade_convergence.refresh_forward_prices()` (+24h/+7d) and `narrative_signal_shadow.py` (same doctrine, per its own docstring). Both are deliberately **lazy**: "no dedicated poller, a return is captured the first time someone looks after the window elapses" — acceptable for day/week-scale horizons, where being read a few hours late barely distorts the measurement. This feature's horizons are **minutes to hours** (+1m/+5m/+15m/+1h/+4h); the same lazy pattern would be actively wrong here, because `refresh_forward_prices()` records `_current_price_usd()` (the price *at the moment the function happens to run*), not the price *at the true target timestamp* — for a +1m horizon, being read 40 minutes late (plausible if nothing else triggers a read) would silently record a +41m price mislabeled as +1m.

A dedicated short-cadence cycle avoids this by construction: it runs often enough that the gap between "horizon due" and "horizon measured" stays small relative to the horizon itself, the same reasoning already codified in this repo for the momentum WebSocket drain cadence (30s) versus the once-an-hour DexScreener scan.

Price source: `dexscreener.fetch_token_pairs(contract, chain)` (verified public, already used synchronously inside `momentum_entry.py` for the same tokens) — chosen over `EVMSwapWebSocketFeed` because the latter only covers Base/Robinhood and requires the pool to already be subscribed (`add_pool`), which a rejected candidate's pool never is; DexScreener covers every chain this pipeline already trades and is the same source used for the observation's own T0 reference price, so forward deltas are computed against a consistent source rather than mixing two providers with potentially different price conventions (same reasoning as this repo's "a system's own data can never validate that system's own prices" doctrine, applied here as "don't compare a T0 price from source A against a T+5m price from source B").

Funnel design (Resource-Engineering doctrine, staged filtering before any network call):
1. Cheap local step: `SELECT` observation/horizon pairs where `decision_ts + horizon <= now AND status = 'pending'` — pure SQL, zero network cost.
2. Deduplicate by `(contract, chain)` within the batch — if a token has 2+ due horizons across different observations in the same cycle, one `fetch_token_pairs` call serves all of them.
3. Only then the throttled network call, reusing `dexscreener.py`'s existing rate coordination (no new throttle to calibrate, per constitution's "Throughput calibrated" norm — this reuses the already-calibrated shared throttle instead of adding a second consumer with its own).

**Alternatives considered**:
- *Reuse `signal_cascade_convergence.refresh_forward_prices()`'s lazy pattern as-is* — rejected for the reason above (horizon-mismatch risk at minute-scale).
- *Reuse each pocket's own exit-tracking loop* — rejected: those loops are keyed to an open position/wallet object that a rejected candidate never has; retrofitting them to also track positionless candidates would entangle this purely-observational feature with each pocket's real trade-management code, which FR-009 explicitly rules out.
- *`EVMSwapWebSocketFeed` for all forward prices* — rejected as sole source (Base/Robinhood-only, requires active subscription); not excluded as a future enrichment once a token happens to already be subscribed for another reason, but out of scope for this feature's baseline.

## 4. Social signal read paths: two of three are readable passively today, one is not

**Decision**: `conviction_research` and `signal_cascade_convergence` can be read passively (no new scan triggered); `radar_x` currently cannot, and this feature will surface that as an honest `not_available` state rather than adding persistence to `radar_x.py` (out of scope, would touch a file FR-009 places off-limits).

**Rationale, verified live**:
- `conviction_research.py`'s score is already synchronous inside the core decision path (`momentum_entry.py:4964-5052`) — its value, when present in the core's return, is fresh by construction; `data_timestamp` = the decision timestamp itself.
- `signal_cascade_convergence.py` persists a real, directly queryable table: `signal_cascade_convergence(contract, chain, source, signal, accelerating, detail, recorded_at, contract_confirmed_on_site)`, primary-keyed on `(contract, chain, source)`. A plain read-only `SELECT * FROM signal_cascade_convergence WHERE contract = ? AND chain = ?` (0 to 4 rows, one per source: x/web/farcaster/github) gives exactly the "most recently known state for this token" this feature needs, with each row's own `recorded_at` becoming that sub-signal's `data_timestamp` — distinct from the observation's decision timestamp, exactly as FR-004/User Story 3 require. This is a pure read of already-committed state; it triggers no new scan and adds no latency-relevant work beyond one indexed SQLite query.
- `radar_x.py` was checked for any persisted table (`CREATE TABLE`/`INSERT INTO`) or any read-oriented function: **none exists**. Its only public function, `run_radar(...)`, executes a live scan and returns an in-memory result to its caller (`token_absorber.py`/VC pipeline) — there is no "last known radar_x state for contract X" to read passively. Adding one would mean modifying `radar_x.py` itself, which is explicitly out of this feature's scope (FR-009: read existing signals, never modify the modules that produce them). The observation schema still reserves the field (so the target architecture the operator specified stays visible in the data model), but its value will be `not_available` with reason `no_persisted_state` for every observation, until a separate future feature (not this one) adds persistence to `radar_x.py`.

**Alternatives considered**:
- *Call `radar_x.run_radar()` synchronously from the observation wrapper to get a live value* — rejected: this would be a brand-new live scan triggered by every momentum evaluation, violating FR-008 (never add latency/dependency to the decision path) and Resource-Engineering doctrine (uncontrolled linear cost growth tied to candidate volume, not to radar_x's own intended cadence).

## 5. Persistence: new tables, not an extension of `dex_score_log`

**Decision**: Two new tables (`momentum_signal_observation`, `momentum_signal_forward_performance`), following the exact same architectural pattern already established by `dex_score_log.py` (`aiosqlite`, `aria_core.paths.aria_db_path()`, append-only, one `_ensure_table()` guard) — not an extension of `dex_score_log` itself.

**Rationale**: `dex_score_log` (verified live, full file read) stores only `(id, contract, scored_at, score_json)` — one undifferentiated JSON blob per row, no `signal_version` column, no separation between signal families, and no decision/forward-performance concept at all; it was built for a narrower purpose (composite on-chain score calibration only, per its own docstring). Retrofitting FR-002 (three families strictly separate, never combined), FR-004 (`signal_version` + a *per-family* `data_timestamp`, not one blob timestamp), and forward-performance tracking onto it would either overload its JSON blob with meaning it was never designed to carry, or require a schema migration that changes what every existing `dex_score_log` reader expects — both worse than a small, purpose-built pair of tables that copies a pattern this repo already trusts.

**Alternatives considered**:
- *Extend `dex_score_log`* — rejected per above.
- *One single wide table instead of two* — rejected: forward-performance is a 1-to-5 relationship (five horizons per observation) that resolves at different future times per row; forcing it into the observation's own row would mean either five sets of nullable columns updated at different times (schema smell already avoided elsewhere in this repo, e.g. `signal_cascade_triage_queue`'s narrower two-horizon case already uses discrete columns because it only has two, not five) or repeated UPDATEs mutating an otherwise-append-only observation row, contradicting the append-only convention this repo's logs deliberately follow (auditability: a row's value never silently changes under a reader that already saw it).
