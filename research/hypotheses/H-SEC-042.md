# H-SEC-042 — no production execution path signs with wallet capability outside expected controls

status: **UNKNOWN (verdict reached, not PENDING)** — hypothesis, method, and
path space frozen 2026-09-03; 11 paths + 2 structural sub-findings verified
via the V1 static-source method below (no secret value, no environment dump,
ever). Verdict is `UNKNOWN`, not `PASS`, because `path_set_completeness_
proven = FALSE` — see the formal balance sheet near the end of this document.
Falsifiers (what would flip each path's finding) are the "expected controls"
named per path below, defined before this document's verdict section was
written.

This investigation follows a real method violation earlier the same day
(`docker exec aria-api env`/`printenv` run to check gate values before this
was caught and stopped) — see `docs/HANDOFF_SECURITE.md`'s 2026.09.03 entry
(commit `b9ea39a6`) and the frozen method below, written by the operator in
direct response to that incident.

## Hypothesis (frozen, operator-authored verbatim, 2026-09-03)

> Aucun chemin d'exécution de production ne permet de signer une transaction
> avec une capacité de wallet sans passer par les contrôles de sécurité
> attendus.

## Method (frozen alongside the hypothesis — operator-authored verbatim,
kept word for word like `research/CONSTITUTION.md`'s own operator text,
never reworded)

> 1. Sources autorisées : code source versionné ; imports et dépendances ;
>    tests ; scripts opérationnels ; Dockerfile/compose (éléments non
>    secrets uniquement) ; unités systemd (hors variables/environnements
>    sensibles) ; configurations versionnées sans valeurs secrètes ;
>    noms/références de symboles et gates, sans leurs valeurs.
> 2. Sources interdites : `.env`, `docker inspect`, `docker exec ... env`,
>    `docker exec ... printenv`, `/proc/*/environ`, secrets manager,
>    variables d'environnement avec leurs valeurs, credentials/clés
>    privées/tokens. Même avec un grep en aval, la commande source reste
>    interdite.
> 3. On cherche "quels sont tous les chemins logiciels permettant d'atteindre
>    une capacité de signature ?", jamais "quelle est la clé utilisée ?".
> 4. Produire PATH_SET avant toute conclusion, puis expliquer pourquoi cet
>    ensemble est complet, uniquement à partir de sources autorisées — une
>    simple liste issue d'un grep ne suffit pas.
> 5. Chaque chemin identifié doit être suivi jusqu'à sa capacité de
>    signature : PATH_IDENTIFIED → PATH_VERIFIED → FINDING/NO_FINDING. Un
>    chemin impossible à vérifier reste UNKNOWN, jamais implicitement sûr.
> 6. Verdict : `completeness_proven AND paths_exhausted AND
>    every_path_verified AND no_finding → PASS`. Sinon, insuffisance de
>    preuve → UNKNOWN. Chemin vérifié démontrant la violation → FAIL.
> 7. Règle d'arrêt : si une information semble nécessiter une valeur
>    d'environnement/secret/clé/dump de processus/`docker inspect`/
>    `printenv`/`env`, on ne contourne pas — on consigne
>    `observation_boundary_reached → preuve incomplète → UNKNOWN`.
>    L'exhaustivité de l'investigation ne justifie jamais de violer la
>    frontière de sécurité de l'observation.

## Why this hypothesis, and not another, first

First real business-mission P2 candidate (financial capability, max severity
by construction) once the generic derivation mechanism (FR-015/016,
`scripts/security_scientist_investigation.py`, `run_investigation()`) was
proven test-first on a synthetic pipeline. Chosen by the operator explicitly,
2026-09-03.

## Path space identified (via static sources only — source code, imports,
versioned comments/constants; verified 2026-09-03; grep methodology below)

**PATH-001 — Coinbase Agentic Wallet pilot (EOA), Base, ~10-25$**
- Files: `agent_wallet_pilot.py` (application-layer controls) →
  `agent_wallet_cdp_adapter.py` (low-level CDP signing call).
- Entrypoint: `heartbeat.py` task `agent_wallet_pilot_cycle` →
  `agent_wallet_pilot_cycle.run_agent_wallet_pilot_cycle()`.
- Expected controls, as documented in `agent_wallet_pilot.py`'s own doctrine
  comment: dedicated gate → kill-switch (`outgoing_pause.is_paused(strict=
  True)`) → real-balance cap (`MAX_TRANSACTION_USD`) → forced slippage
  (`MAX_SLIPPAGE_BPS`; any different `slippage_bps` argument is logged and
  IGNORED, never honored) → execution → systematic logging. Transfers add a
  strict address allowlist (`ALLOWED_TRANSFER_ADDRESS`, exact-match compare).
- Verification: **PATH_VERIFIED**. Exhaustive grep for real callers (import +
  call, not a comment substring) of `agent_wallet_cdp_adapter.execute_swap`/
  `transfer_usdc` across `packages/aria-core/src`, `vanguard/backend`,
  `scripts` (excluding tests) found exactly one: `agent_wallet_pilot_cycle.
  py:195`, which goes through `agent_wallet_pilot.attempt_swap` and therefore
  the controls above. **NO_FINDING** for this path as it exists today.

**PATH-001b — structural gap, documented by the code itself, not merely
inferred**
- The low-level signer (`agent_wallet_cdp_adapter.execute_swap`/
  `transfer_usdc`) carries no guard of its own — every bound (cap, address
  allowlist) lives ONLY in the `agent_wallet_pilot.py` application layer.
  `agent_wallet_cdp_policy.py`'s own module docstring states this directly:
  "a code path that skipped `attempt_swap`/`attempt_transfer` and called
  `agent_wallet_cdp_adapter.execute_swap`/`transfer_usdc` directly would be
  signed by CDP without objection." A corrective mechanism (a CDP-side
  Policy Engine, `build_pilot_bounded_policy()`) exists in code for exactly
  this gap but is explicitly **not applied**: "Nothing here is applied...
  No production module imports this file" (a dedicated test asserts it).
- Verification: **PATH_VERIFIED** (same exhaustive-caller grep as PATH-001).
  **FINDING (structural, not active)**: no direct call bypassing
  `attempt_swap`/`attempt_transfer` exists today, so there is no live
  violation — but the bypass is structurally possible and the code says so
  itself. Recorded as a distinct finding class from "active violation,"
  never silently folded into PATH-001's NO_FINDING.

**PATH-002 — Solana real-money pilot (locally-loaded delegate key)**
- Files: `solana_trade_pilot.py` (`MAX_TRADE_USD = 0.10`, `MAX_SLIPPAGE_BPS
  = 1000`, kill-switch) → `solana_agent_wallet.py` → `onchain/
  jupiter_swap_signer.py` (`sign_transaction(swap_transaction_b64, keypair)`
  with a keypair loaded from a local file).
- Entrypoint: **none found**. `heartbeat.py` has zero reference to
  `solana_trade_pilot`. An exhaustive grep for real imports (`from aria_core
  ... import solana_agent_wallet`, not a comment mentioning the filename —
  four false positives caught and discarded during this exact search: `services
  /solana_gateway.py`, `sol_usd_rate.py`, `solana_rpc_budget.py`,
  `pumpfun_bonding_ws.py` all only *mention* the module name in a comment)
  across the whole repo returns zero real callers.
- Verification: **PATH_VERIFIED** (exhaustive caller search performed).
  **NO_FINDING** — well-controlled code, currently dormant (unreachable from
  any known production entrypoint in the static call graph).

**PATH-003 — Robinhood Chain testnet rehearsal**
- Files: `onchain/robinhood_pilot_cycle.py` → `onchain/
  safe_robinhood_signer.py` (`account.sign_transaction(tx)`).
- Entrypoint: `heartbeat.py` task → `run_robinhood_testnet_rehearsal_cycle()`.
- Expected controls, per the file's own header comment: "gate → kill-switch
  → on-chain cap → signing → execution → log." Network: `CHAIN =
  "robinhood_testnet"` hard-coded, comment states funds "hold zero value
  (testnet)."
- Verification: **PATH_VERIFIED** on the application-layer controls.
  **NO_FINDING**. The deployed contract's actual testnet-vs-mainnet status
  is **UNKNOWN** in this pass — confirming that would need an on-chain read,
  outside this static-source method; left as a gap, not assumed safe.

**PATH-004 — Sepolia autonomous rehearsal**
- Files: `onchain/sepolia_autonomous.py` → `onchain/sepolia_wallet.py`.
- Entrypoint: `heartbeat.py` task `sepolia_autonomous_cycle`.
- Expected controls: kill-switch re-read every cycle (`outgoing_pause.
  is_paused()`), `MAX_AUTONOMOUS_TX_PER_DAY = 12` (sanity cap), fixed
  mechanical `TEST_SWAP_AMOUNT_WEI` (never a variable/Kelly-sized amount),
  network documented in the module's own opening comment as "testnet ONLY."
- Verification: **PATH_VERIFIED**. **NO_FINDING**.

**PATH-005 — Squads Solana multisig (devnet)**
- Files: `onchain/squads_solana_signer.py` (locally-loaded delegate key via
  file), reached from `jupiter_swap_signer.py` (PATH-002, already dormant)
  and `homemade_agent_wallet.py` (PATH-006).
- Network: devnet RPC hard-coded (`_devnet_rpc_url()`), never mainnet in
  this module's own code.
- Verification: **PATH_VERIFIED**. **NO_FINDING** — network separation is a
  structural fact of the hard-coded RPC endpoint, not a convention.

**PATH-006 — `homemade_agent_wallet.py` (generic wrapper over PATH-003/005)**
- Not an independent signer — delegates via an injected `transfer_fn` to
  `safe_robinhood_signer.send_allowance_transfer` (PATH-003) or
  `squads_solana_signer.send_spending_limit_transfer` (PATH-005).
- Structural guard documented in the module's own opening comment: an EVM
  chain-id preflight check that **raises an exception** if ever pointed at
  mainnet — a real code-level block, not just a documentation convention.
- Verification: **PATH_VERIFIED** (no independent signer of its own, inherits
  PATH-003/005's controls). **NO_FINDING**.

**PATH-007 — x402 outgoing payments (ARIA paying for a third-party resource)**
- Files: `x402_executor.fetch_paid_resource` → `x402_cdp_signer.py`
  (signs via a verified `EthAccountSigner(EvmLocalAccount(cdp_account))`
  wrapper — key held by Coinbase, never local).
- Entrypoint: multiple consumers converge on the same executor
  (`services/blockscout_x402.py`, `blockrun_kalshi.py`, `otto_ai.py`/
  `ottoai.py`, `quickintel.py`, `twitsh.py`).
- Expected controls: kill-switch checked FIRST ("the widest gate, checked
  before even knowing how" — the module's own comment), weekly cap
  `WEEKLY_CAP_USD = 5.0` (a versioned code constant, not a secret) enforced
  via an ATOMIC budget reservation (`x402_executor.py`'s own comment: "atomic
  reserve, replacing `can_spend()` (check-then-act, unsafe...)") that
  completes BEFORE `pay_fn` is ever invoked — no TOCTOU window between the
  budget check and the signing call.
- Verification: **PATH_VERIFIED**. Exhaustive grep for real callers of
  `x402_cdp_signer.build_x402_payment_header` (the only public function this
  module exports) found 6: `services/blockscout_x402.py`,
  `blockrun_kalshi.py`, `quickintel.py`, `otto_ai.py`, `ottoai.py`,
  `twitsh.py`. Read each call site: all 6, without exception, pass the
  function as `pay_fn=build_x402_payment_header` INTO
  `x402_executor.fetch_paid_resource(...)` — never invoked directly. **NO
  bypass exists today.** **PATH VERIFIED SAFE.**

**PATH-007b — structural gap, symmetric to PATH-001b, documented by reading
the signer's own body**
- `build_x402_payment_header(payment_required)` signs WHATEVER
  `payment_required` dict it is given — reading its full body confirms no
  amount/budget check exists inside the function itself. The weekly cap and
  kill-switch live ENTIRELY in `x402_executor.fetch_paid_resource`, exactly
  the same shape as PATH-001b: the signer trusts its caller completely.
  Nothing marks the function private (no `_` prefix), so it is importable
  and callable from anywhere in the codebase.
- Verification: **PATH_VERIFIED** (same exhaustive-caller grep as above).
  **FINDING (structural, not active)** — no caller bypassing
  `fetch_paid_resource` exists today, but nothing in the signer itself would
  refuse one. Recorded as its own path, not folded into PATH-007's
  NO_FINDING, same discipline as PATH-001b. **Not "corrected" here** — this
  investigation observes, it does not remediate (FR-018); `agent_wallet_
  cdp_policy.py` (the dormant CDP-side guard built for PATH-001b) is
  deliberately left untouched and not wired to PATH-007b either.

**PATH-010 — Solana rent recovery (`solana_rent_recovery.py`)**
- Files: `solana_rent_recovery.py`'s `reclaim()` → `onchain/
  jupiter_swap_signer`'s signing/RPC helpers (locally-loaded keypair via
  `signer.load_keypair(key_path)`).
- Expected controls, per the function's own comment ("Refuses rather than
  partially succeeding: the gate, the kill-switch, and the account cases are
  all checked BEFORE anything is signed"): dedicated gate,
  `outgoing_pause.is_paused(strict=True) OR custody_pause.is_paused()`
  (BOTH kill-switches checked, not just one), `MAX_CLOSES_PER_TRANSACTION =
  27` (batch-size sanity bound).
- Verification: **PATH_VERIFIED**. Exhaustive grep for real callers
  (`solana_rent_recovery.<fn>(` or a real import) across the whole repo
  found zero — the only 2 textual mentions elsewhere
  (`services/solana_gateway.py:323`, `services/solana_rpc_budget.py:67`) are
  both comments naming the module, not calls (same false-positive shape
  already caught once on `solana_agent_wallet.py` in the first pass of this
  investigation). No entrypoint in `heartbeat.py` or anywhere else.
- **PATH VERIFIED SAFE** in the sense of "well-controlled if ever reached,"
  and **PATH ELIMINATED / NOT APPLICABLE today** in the sense of current
  reachability — dormant, same shape as PATH-002.

**PATH-008 — Smart Account CDP swing (`agent_wallet_smart_swing*`)**
- Files: `agent_wallet_smart_swing.py`, `_grant.py`, `_cdp_adapter.py`.
- The module's own comment states: "Everything here is DORMANT: gate
  `ARIA_SMART_SWING_ENABLED` is OFF" and documents a fail-closed guarantee
  when the gate is off. This is a claim made BY the code about its own
  configuration state, not an independent measurement — per the frozen
  method, the actual gate value is out of scope for V1, so this claim is
  recorded as-is and NOT treated as verified.
- Verification: **UNKNOWN** on live gate state (out of V1 method by design).
  Internal structural controls (gate-off → fail-closed path) are readable in
  code but not yet detailed — left for a future pass if this path is
  prioritized.

**PATH-009 — Tangem hardware wallet bridge (human path, outside automation)**
- Files: `tangem_bridge.py` — requests a signature from the operator's
  physical Tangem hardware wallet (NFC tap approval); ARIA never holds or
  sees the private key, by the module's own design statement.
- Verification: **PATH_VERIFIED**. **NO_FINDING** by construction — this is
  the legitimate recovery/human channel the frozen method explicitly asks to
  cover, not a path to forbid.

## Paths not yet covered by this pass (explicit, so completeness is never
silently assumed)

Updated 2026-09-03 after the x402 and Solana-rent-recovery passes — 2 of the
original 5 gaps are now resolved (struck through, not deleted, so the
investigation's own history stays visible):

- ~~`solana_rent_recovery.py` — not yet traced~~ → **RESOLVED as PATH-010**:
  dormant, zero real entrypoint found.
- ~~Whether `x402_cdp_signer`'s low-level call is reachable bypassing
  `fetch_paid_resource`~~ → **RESOLVED as PATH-007b**: structurally possible,
  not actively exploited, same shape as PATH-001b.
- `agent_wallet_smart_swing*` (PATH-008) entrypoints — the module's own
  dormant-gate claim was recorded but never independently traced the way
  PATH-002/PATH-010 were. Still **UNRESOLVED**.
- A signing implementation that avoids every standard-named pattern this
  investigation's grep searched for (e.g. a hand-rolled EIP-712
  implementation never calling `.sign()`) — no dedicated search run to
  exclude this. Still **UNRESOLVED**.
- A non-Python process or external binary invoking a signer outside
  `aria-core`'s own source — outside this source-grep's reach entirely.
  Still **UNRESOLVED**.
- systemd unit **file contents** (deliberately not read — real risk of
  `Environment=` lines carrying sensitive values; only unit names already
  known from `heartbeat.py`/docs were used, never unit file content). Still
  **UNRESOLVED** by design (stop-rule boundary, not an oversight).
- Dockerfile / docker-compose — not specifically re-read line-by-line for
  this investigation. Still **UNRESOLVED**.
- Live ON/OFF state of every gate named above — out of the V1 method by
  design, permanently UNRESOLVED under this method (not a gap to close, a
  structural boundary of V1 itself).
- A future refactor removing today's "no caller bypasses the wrapper"
  guarantee on PATH-001b/PATH-007b — inherently unresolvable by a
  point-in-time static read; would need this investigation re-run
  periodically or a CI-level structural test, neither built yet.

## Formal balance sheet (operator-requested, 2026-09-03, after the x402 and
Solana-rent-recovery passes)

| Path | Classification |
|---|---|
| PATH-001 (CDP EOA pilot, via `attempt_swap`/`attempt_transfer`) | **PATH VERIFIED SAFE** |
| PATH-001b (CDP EOA low-level signer, no intrinsic guard) | **PATH UNRESOLVED** — no active bypass, but nothing structurally prevents one |
| PATH-002 (Solana real-money pilot) | **PATH ELIMINATED / NOT APPLICABLE** — dormant, zero entrypoint |
| PATH-003 (Robinhood Chain testnet rehearsal) | **PATH VERIFIED SAFE** on application-layer controls; on-chain contract mainnet-vs-testnet status itself is a separate **PATH UNRESOLVED** (needs an on-chain read, outside this static method) |
| PATH-004 (Sepolia autonomous rehearsal) | **PATH VERIFIED SAFE** |
| PATH-005 (Squads Solana, devnet) | **PATH VERIFIED SAFE** |
| PATH-006 (`homemade_agent_wallet.py` wrapper) | **PATH VERIFIED SAFE** |
| PATH-007 (x402 outgoing payments, via `fetch_paid_resource`) | **PATH VERIFIED SAFE** |
| PATH-007b (x402 low-level signer, no intrinsic guard) | **PATH UNRESOLVED** — no active bypass, but nothing structurally prevents one |
| PATH-008 (Smart Account CDP swing) | **PATH UNRESOLVED** — dormant-gate claim read from the code's own comment, never independently traced |
| PATH-009 (Tangem hardware bridge, human path) | **PATH VERIFIED SAFE** — legitimate recovery channel by construction |
| PATH-010 (Solana rent recovery) | **PATH ELIMINATED / NOT APPLICABLE** — dormant, zero entrypoint |
| **PATH-SET COMPLETENESS PROVEN** | **FALSE** — 6 named gaps remain UNRESOLVED (see above); no positive argument yet that the search taxonomy itself cannot be missing a path |

**Consequence, per the frozen verdict rule**: `PASS` is not eligible —
`path_set_completeness_proven = FALSE` alone already forecloses it,
independent of how many individual paths read SAFE. No verified path shows
an active violation, so `FAIL` is not warranted either. Two real structural
findings (PATH-001b, PATH-007b) are recorded and left unfixed by design —
this investigation observes, it never remediates (FR-018).

## Why this list is not yet proven complete (the mandatory question)

**Construction method**: exhaustive grep for signature-related code patterns
(`sign_transaction`, `.sign(`, `private_key`, `PRIVATE_KEY`, `Account.
from_key`, `from_key(`, CDP SDK imports, web3 imports) across
`packages/aria-core/src`, `vanguard/backend`, `scripts`, then tracing REAL
callers (import + function call, never a comment substring — a real error
caught and corrected within this same pass, on `solana_agent_wallet.py`'s
four false-positive "callers").

**What this covers**: any path reachable through a Python function named
after a standard signing pattern (`eth_account`/CDP SDK/Solana keypair
conventions).

**What this does NOT prove**:
1. A path that signs without calling any of these standard-named functions
   (e.g. a hand-rolled EIP-712 implementation never calling `.sign()`) —
   possible in principle, no dedicated search was run to exclude it.
2. A non-Python process or external binary invoking a signer outside
   `aria-core`'s own source — outside this source-grep's reach entirely.
3. The live activation state of any path — explicitly out of the V1 method.
4. systemd unit content and the real deployment configuration (deliberately
   unread this pass, per the stop rule).
5. A future refactor breaking today's PATH-001b/PATH-007 guarantee (no
   direct-caller bypass exists NOW; nothing structural prevents one being
   added later without this investigation being re-run).

`path_set_completeness_proven = FALSE` — stated honestly, not assumed. This
is `PATHS_IDENTIFIED` (9 paths + 1 documented structural sub-finding), and
for the paths covered, genuinely `PATH_VERIFIED` against real code. But no
positive argument yet exists that the search taxonomy itself (function-name
pattern matching) cannot be missing a path — exactly the FR-015 distinction
`security_scientist_investigation.py`'s `derive_verdict()` already encodes:
`paths_exhausted` for the paths found is not sufficient for `PASS` without
`path_set_completeness_proven`.

## Self-critique (G3, before any verdict)

- `coverage_complete?` **NO**, explicitly — see the 5 gaps above.
- `runtime_identity_verified?` N/A — static source analysis, no runtime
  observation claimed.
- `measurement_independence_checked?` **YES** — every source used is
  versioned, immutable code, not a mutable system state the investigation
  itself could have influenced.
- `instrument_integrity_checked?` **PARTIAL** — the grep instrument itself
  could miss a non-standard-named signing implementation (gap #1 above);
  named, not hidden.
- `hypothesis_scope_checked?` **YES** — scope is "production" as the
  hypothesis states; testnet/devnet paths are included in the map (the
  hypothesis space) but their network separation is itself part of what was
  verified, not assumed.
- `reproducibility_checked?` **YES** — every grep/trace here is exactly
  reproducible from the commands run in this session.

## Verdict (derived via the same rule as `security_scientist_investigation.
py`'s `derive_verdict()`, applied manually — not yet wired into that module's
automated pipeline for this business investigation; superseded by the formal
balance sheet above, kept here as the derivation trace)

```
paths_exhausted            = PARTIAL (11 paths + 2 structural sub-findings
                              checked; 6 named sub-questions remain
                              UNRESOLVED per the balance sheet above)
path_set_completeness_proven = FALSE
paths_with_findings        = [PATH-001b, PATH-007b] (both structural, not active)
```

→ **UNKNOWN** ("path-exhausted incomplete + completeness unproven"), never
PASS. Not FAIL either — no verified path demonstrates an ACTIVE violation
today. PATH-001b and PATH-007b are each recorded as a distinct **structural
finding** warranting separate attention, independent of the overall UNKNOWN
verdict — neither has been, or will be, "corrected" by this investigation
(FR-018: observation only, never remediation).

## Recommendation (a proposal only — FR-017, explicit operator validation
required before any binding effect; nothing here is applied autonomously)

PATH-001b and PATH-007b are now confirmed to be the same finding class,
occurring twice independently (CDP EOA pilot, x402 payments): a low-level
signer with no guard of its own, protected only by callers' current
discipline. `agent_wallet_cdp_policy.py` already builds the CDP-side fix for
PATH-001b but is explicitly dormant and untouched by this investigation, per
operator instruction. No equivalent has been built for PATH-007b. Any
decision on whether/how to close either gap is the operator's, not this
investigation's — recorded here as an observation, not a proposal to act on.

## Next steps (explicitly not done yet)

- PATH-008 (`agent_wallet_smart_swing*`) entrypoints — never independently
  traced the way PATH-002/PATH-010 were; still resting on the code's own
  self-description.
- List systemd unit NAMES only (never content) if a fuller picture of
  process-level entrypoints is needed.
- A repeatable/CI-level structural test that PATH-001b's and PATH-007b's
  "no bypass exists" finding cannot silently regress under a future
  refactor — this investigation is a point-in-time read, not a standing
  guarantee.
- Operator decision on `agent_wallet_cdp_policy.py` activation, or on
  building an equivalent for PATH-007b — out of scope for this
  observation-only investigation (FR-018).
