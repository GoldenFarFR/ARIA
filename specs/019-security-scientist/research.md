# Phase 0 Research: ARIA Security Scientist V1

Each topic below was a technical unknown in `plan.md`'s Technical Context. Resolved against the existing codebase (never a fresh assumption) and, where no in-repo precedent existed, against the measured host constraints in the approved plan.

## 1. Process discovery mechanism

**Decision**: Read `/proc/<pid>/{exe,cmdline,cwd,stat,status,cgroup}` directly via stdlib (`os.readlink`, `pathlib.Path.read_bytes`/`read_text`). No `psutil`.

**Rationale**: `psutil` is not a dependency anywhere in the project today (`grep -rl "import psutil"` returns nothing; `pyproject.toml` has no such entry). `security_posture_collect.py`'s existing convention is "shell out to an external binary, or read the filesystem directly — no framework in between." `/proc` on Linux already exposes everything the spec's FR-001-003 need (executable path via `exe` symlink, argv via `cmdline`, working directory via `cwd` symlink, process start time and state via `stat`/`status`) without adding a dependency whose own supply chain would need to be one more thing this feature has to trust.

**Alternatives considered**: `psutil` — rejected, new dependency for a capability `/proc` already provides natively on the only target platform (Linux VPS), and CLAUDE.md's architectural-coherence rule (1bis) forbids introducing new machinery where the existing pattern already fits. `ps`/`ps aux` subprocess parsing — rejected, `/proc` is the authoritative source `ps` itself reads, and per-field text parsing of `ps` output is strictly less reliable than reading the structured `/proc` files directly.

## 2. systemd unit correlation

**Decision**: Parse `/proc/<pid>/cgroup` (cgroup v2: single line, e.g. `0::/system.slice/aria-api.service`) to recover the owning systemd unit for a PID, with zero subprocess spawn per process. Once per discovery pass (not per-process), run one batched `systemctl list-units --type=service --no-legend --plain` to get the authoritative list of units systemd currently manages, and cross-reference unit names found via cgroup against that list to flag a process running under a name systemd itself doesn't recognize as managed (a strong `RESIDUAL`/`UNKNOWN` signal).

**Rationale**: Reading `/proc/<pid>/cgroup` for every discovered process is a single filesystem read (already open cost from `os.listdir("/proc")` iteration), versus a `systemctl status <pid>` subprocess spawn per process, which does not scale and reintroduces exactly the kind of per-item subprocess cost the project's resource-engineering doctrine (funnel pattern: cheap filter first, expensive step only on the qualified subset) explicitly warns against. Confirmed the host runs cgroup v2 (Ubuntu 7.0.0 kernel, systemd-managed) so the single-line format applies uniformly — no v1 hybrid-hierarchy parsing needed.

**Alternatives considered**: `python-dbus`/`systemd-python` bindings — rejected, new dependency for a capability two stdlib-reachable primitives (file read + one batched CLI call) already cover; the project's `security_posture_collect.py` precedent shells out to CLI tools (`gh`, `osv-scanner`) rather than binding to their APIs directly.

## 3. Lockfile / dependency-scan-state detection

**Decision** (revised -- see empirical correction below): V1 scope is Python virtual environments specifically (the founding incident's exact case) — resolved via **`argv[0]`/`cmdline[0]` (the path actually invoked), NOT the `exe` symlink target**. If `cmdline[0]` points inside a `.../bin/python*` under some directory `D`, check for `D/../requirements.txt`, `D/../pyproject.toml` + `D/../uv.lock` or `D/../poetry.lock`, or `D/pyvenv.cfg` alone (no lockfile) as the "no lockfile" case. A production runtime with `pyvenv.cfg` present but no lockfile file next to it is exactly the `aria-core/.venv` incident shape and must classify `UNKNOWN` per FR-002. Node/other runtimes are out of scope for the first increment — the collector still records `runtime_kind: "unrecognized"` for them (never silently skips them; FR-003's "stays in the inventory" applies), and a later task can add their lockfile heuristics without changing the `RuntimeObservation` shape.

**Empirical correction (found by testing the instrument before writing the collector, not after)**: research point 1's original assumption — resolve the venv via `/proc/<pid>/exe` — is WRONG and was verified wrong live before being coded: `python3 -m venv /tmp/x && ls -la /tmp/x/bin/python3` shows the venv's `bin/python3` is a SYMLINK to the base interpreter (`-> /usr/bin/python3`), and launching a process through it, `/proc/<pid>/exe` resolves to `/usr/bin/python3.14` (the base interpreter's real path) -- NEVER to the venv path, regardless of which venv launched it. `exe` therefore cannot distinguish "which venv" at all, which would have made the exact founding-incident classification (`aria-core/.venv`) silently unreachable by the code as originally planned. `/proc/<pid>/cmdline`'s first NUL-terminated field, by contrast, is exactly the string passed to `execve()` (or `Popen`) -- verified live to be `/tmp/x/bin/python3`, the real venv path -- because it records what was invoked, not what the kernel resolved a symlink to. `exe` remains useful for a DIFFERENT purpose (confirming the real underlying interpreter binary/identity for `environment_identity`, research point 5), just not for venv classification.

**Rationale**: The founding incident (`packages/aria-core/.venv` running two systemd services, six weeks unscanned) is specifically a Python-venv-outside-Docker case. Scoping the first increment to the exact incident class satisfies the plan's "no scope creep beyond seven steps" discipline while still recording every other process type as a first-class, never-dropped `UNKNOWN` entry (FR-003) rather than a silent gap.

**Alternatives considered**: Building a generic multi-language dependency-manifest detector (Node/Rust/Go/etc.) in the first increment — rejected as scope creep beyond what step 1 needs to prove; the four-coverage-property model (FR-004) already makes "runtime kind not yet classifiable" collapse correctly to `UNOBSERVED`/`UNKNOWN` rather than requiring every language to be handled before shipping.

## 4. argv / environment fingerprinting and secrets safety

**Decision**: `argv_fingerprint` = `sha256(b"\x00".join(argv_bytes)).hexdigest()[:16]` (truncated hash, never raw argv persisted). Environment metadata = the **set of variable names present** in `/proc/<pid>/environ` (split on `\0`, keep only the part before `=`), never values. A negative test asserts no persisted `RuntimeObservation` payload contains any substring that matches a value from the process's own loaded `.env` (mirrors the project's existing secret-exposure-audit doctrine, `scripts/secret-exposure-audit.py`, applied to this new surface).

**Rationale**: CLAUDE.md's absolute rule: never read/display secret values, metadata only (variable name, `configured=true`, truncated fingerprint). `argv` can legitimately contain a token in edge cases (a CLI tool invoked with `--api-key=...`); hashing rather than storing it raw closes that path by construction rather than by convention.

**Alternatives considered**: Storing raw argv/environ for maximum debuggability — rejected outright, direct violation of the carved-in-stone secrets rule ("déjà cédé 3 fois").

## 5. `host_identity` / `environment_identity` computation

**Decision**: `environment_identity` = a struct of `{hostname, kernel_release (uname -r), collector_version (module `__version__` / short git hash of `security_scientist_observe.py` at run time), python_version}`. This is attached to every `RuntimeObservation` and every `Evidence`/evaluation row, per G2 (provenance).

**Rationale**: The approved plan's founding "pip upgrade" lesson is specifically that the version on disk and the version actually executing are two different identities; recording `collector_version` as the actual running module's identity (not a static constant) is what makes a later "was the collector itself out of date when it produced this" question answerable.

**Alternatives considered**: A single opaque "environment hash" — rejected, not human-inspectable when diagnosing a TOCTOU gap (`observed_at` vs `recorded_at` divergence), which the plan explicitly wants visible.

## 6. Persistence schema

**Decision** (revised after review point #4 -- independence from ARIA): Three tables in a DEDICATED file, `DATA_DIR/security_scientist.db`, NOT the shared `aria.db`. Module structure (`aiosqlite`, explicit `CREATE TABLE IF NOT EXISTS`) still mirrors `gate_audit_log.py`'s discipline -- only the file target differs:

```python
# security_evidence.py
DB_PATH = str(Path(data_dir()) / "security_scientist.db")  # NOT aria_db_path()
```

```sql
CREATE TABLE IF NOT EXISTS security_observations (
    observation_id TEXT PRIMARY KEY,      -- uuid4
    surface_id TEXT NOT NULL,
    mission_id TEXT,
    experiment_id TEXT,
    observer_version TEXT NOT NULL,
    environment_identity TEXT NOT NULL,   -- JSON
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL            -- raw facts only; schema-checked to
);                                        -- reject any key named status/verdict/
                                           -- safe/unsafe/pass/fail (G1, mechanical)
CREATE INDEX IF NOT EXISTS idx_sec_obs_surface ON security_observations (surface_id, observed_at);

CREATE TABLE IF NOT EXISTS security_rejected_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at TEXT NOT NULL,
    surface_id TEXT,
    reason TEXT NOT NULL,                 -- e.g. "missing observer_version"
    payload_json TEXT                     -- best-effort capture of what was rejected
);

CREATE TABLE IF NOT EXISTS security_evaluations (
    evaluation_id TEXT PRIMARY KEY,       -- uuid4
    surface_id TEXT NOT NULL,
    status TEXT NOT NULL,                 -- PASS/FAIL/UNKNOWN/STALE/UNOBSERVED (sp.VALID_STATUSES)
    evaluated_at TEXT NOT NULL,
    observations_used TEXT NOT NULL,      -- JSON list of observation_id
    self_critique_id TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_sec_eval_surface ON security_evaluations (surface_id, evaluated_at);
```

`security_evaluations` is append-only and never updated in place — "current status" is always `SELECT ... ORDER BY evaluated_at DESC LIMIT 1`, and `state_at(surface_id, at)` is `SELECT ... WHERE evaluated_at <= at ORDER BY evaluated_at DESC LIMIT 1`, mirroring `gate_audit_log.state_at(gate, at)` line for line. This is what makes review point #2 ("verdict is a computed projection, never a stored ground-truth field") mechanical rather than a convention: there is no column anywhere named `status` that an application can `UPDATE`.

**Rationale**: Reuses the exact file, library (`aiosqlite`), and query-reconstruction pattern already proven in `gate_audit_log.py` rather than inventing a second persistence discipline. `payload_json`'s G1 schema check (reject `status`/`verdict`/etc. keys) is a single `assert not (set(payload) & _FORBIDDEN_KEYS)` in the write path plus a mirrored negative test — cheap, mechanical, exactly the "guarantee that only lives in a document doesn't exist" discipline the approved plan demands.

**Alternatives considered**: A single wide table mixing observations and evaluations — rejected, collapses G1's separation (raw facts vs. derived conclusion) into one row shape, making it trivial to accidentally let a "verdict" leak into what should be a pure observation. Sharing `aria.db` with the rest of ARIA (the first draft of this decision, matching `gate_audit_log.py` literally) — rejected on review: `research/PROTOCOL.md` already documents a real incident (17/08, `shadow_db_path()`'s founding motivation) where two independent long-running processes writing the same SQLite file, even in WAL mode, produced sustained "database is locked" failures on unrelated production heartbeat tasks. Beyond the performance risk, sharing the file undermines the actual property being built: "the Scientist observes independently of ARIA" is not demonstrable if its own evidence ledger lives inside the file `aria-api` owns and could lock or corrupt.

## 7. `observation_gap`

**Decision**: `last_discovery_pass_age(now) -> float | None` = `now - MAX(recorded_at)` over `security_observations WHERE surface_id = 'host-runtime-inventory'` (a reserved, always-present surface id representing "the inventory pass itself," distinct from any individual process surface). Returns `None` (not `0`) when no pass has ever recorded — `None` must render `UNOBSERVED`/`UNKNOWN` for the inventory as a whole, never be silently treated as `age=0`/fresh.

**Rationale**: Directly implements review point #1 — a single, queryable number the report can surface ("last successful discovery pass: 4m12s ago") rather than an implicit, undemonstrable "always up to date" claim. Reuses the same `security_observations` table rather than a bespoke "last run" file.

**Alternatives considered**: A separate heartbeat/lockfile timestamp file — rejected, duplicates state already derivable from the observations table (single source of truth, per the project's general anti-duplication doctrine).

## 8. Collector/Critic/Judge structural independence

**Decision**: Enforce via (a) file-level separation — `security_scientist_observe.py` (Collector), `security_scientist_critic.py` (Critic), `security_scientist_judge.py` (Judge) are three files with a one-directional import graph: Critic imports only the `RuntimeObservation` dataclass definition (moved to a small shared `security_scientist_types.py` with zero logic) never `security_scientist_observe`'s functions; Judge imports the Critic's `SelfCritique` type and `security_posture`'s `Evidence`/`measured`/`unknown`, never the Collector; (b) a static `test_coherence.py`-style test parses each module's AST import list and asserts the forbidden edges are absent — this is the same "turn `this property must hold` into a red CI check" pattern the approved plan cites `test_coherence.py` for; (c) a signature-level guard — `judge(observation: RuntimeObservation, critique: SelfCritique, expected: ExpectedCoverage) -> sp.Evidence` has no parameter type through which a pre-formed conclusion could be smuggled (no `**kwargs`, no `Any`-typed "result" field), so a producer literally cannot pass one in without a type-checker/test failure.

**Rationale**: This is the mechanical version of review point #4 — file separation alone is convention (could be bypassed by importing across files anyway); the AST-import test is what makes it an invariant, matching CLAUDE.md's explicit standard: "the guarantee must end as a mechanical test... a guarantee that only lives in a document does not exist."

**Alternatives considered**: A single module with three internally-separated classes/functions — rejected, no mechanical way to prevent one from reaching into another's closure/state in Python without the file-boundary + import-graph test, which is cheap and already has a project precedent (`test_coherence.py`'s `_EXTERNAL_WRITE_ALLOWLIST` pattern statically inspects source).

## 9. Decisive adversarial test harness (disposable process / disposable venv)

**Decision**: `test_security_scientist_observe.py` spawns a real short-lived subprocess (`python3 -c "import time; time.sleep(5)"`) inside a `tempfile.TemporaryDirectory()`-created fake venv directory (a `pyvenv.cfg` file plus no lockfile — enough to look like a Python venv to the collector's classification logic, without needing an actual `venv` module invocation, which would be slower and unnecessary for what's being tested), runs the collector against the real `/proc` of the live test host, asserts the spawned PID is present and classified `UNKNOWN`, kills it, re-runs the collector, and asserts a `security_observations` row records the disappearance (a `still_present: false` fact in the next pass, not a silently vanished row) — using a temporary, test-only `DATA_DIR` (`configure_data_dir` pointed at a pytest tmp path) so the test never touches the real production `aria.db`.

**Rationale**: This is the first of the two decisive experiments the review specified as a go/no-go gate. Using a real subprocess and real `/proc` (rather than mocking `/proc` reads) is deliberate — mocking the very instrument under test would let the test pass without proving the instrument works against reality, exactly the class of failure (an instrument that looks correct but doesn't measure what it claims to) this whole feature exists to catch.

**Alternatives considered**: Mocking `/proc` with a fake filesystem (`pyfakefs`) — rejected for this specific gate test (acceptable later for faster unit tests of classification logic in isolation, but the decisive gate test must run against a real process to be a real proof, not a simulation of one).

## 10. CI mutation testing harness (Step 5, sequenced after the foundation)

**Decision**: `test_security_scientist_mutations.py` calls `security_posture.py`'s existing `measured`/`unknown`/`surface_coverage`/`aggregate`/`apply_freshness` functions directly with fabricated inputs matching each row of the approved plan's mutation table (empty evidence list, `discovered != verified`, aged-out `checked_at`, missing expected id, etc.) and asserts the exact resulting status — this requires zero new scanner-mocking infrastructure because `security_posture.py`'s contract already takes plain-data `Evidence` objects, not raw scanner output; the "parser" being mutated is the caller's construction of `Evidence`, which the test constructs directly. `.github/workflows/security-mutation.yml` runs this file's tests on every PR touching `scripts/security_*.py` or `packages/aria-core/src/aria_core/security_evidence.py`.

**Rationale**: `security_posture.py`'s existing 18 negative tests already prove most of the plan's step-5 mutation table (see `test_security_posture.py`'s `test_unavailable_scanner_is_unknown_never_pass`, `test_expected_check_that_produced_nothing_is_unknown`, `test_pass_decays_to_stale_once_its_proof_ages_out`) — Step 5's actual net-new work is (a) wiring these into a dedicated CI workflow gated on the new files specifically, and (b) adding the handful of table rows not yet covered (digest-mismatch -> FAIL has no existing test since no artifact-digest concept exists yet in `security_posture.py`; this is added as part of Step 5, not invented here).

**Alternatives considered**: A separate mutation-testing framework/library — rejected, `security_posture.py`'s existing pure-function contract already makes direct fabricated-input testing trivial; a framework would be solving a problem that doesn't exist here.

## 11. `T_detect` -- a provable maximum detection window, not just a reported gap

**Decision**: The discovery pass runs at a fixed cadence `C` (candidate value: every 5 minutes via `run.sh`'s cron entry -- exact value confirmed against host resource cost, not assumed, before being frozen in `tasks.md`). `T_detect = 2 x C` is the provable worst-case bound: a process that starts one instant after a pass completes is captured by the NEXT pass at the latest, i.e. within `2 x C` of its own start. A test spawns a process at a randomized offset within one cadence window and asserts it is present in the inventory within `T_detect`, never later.

**Rationale**: `observation_gap` (research point 7 below... see also plan.md refinement #1) answers "how stale is the last pass" after the fact; it does not by itself prove an upper bound existed BEFORE the fact. Review point #1 asks for exactly that stronger guarantee -- "no process can remain invisible beyond this bound" -- which requires fixing and testing the cadence itself, not just reporting staleness reactively.

**Alternatives considered**: An unbounded/best-effort cadence with only reactive `observation_gap` reporting -- rejected, satisfies FR-001's letter but not review point #1's demand for a provable bound; the difference matters because "we'll notice eventually" is a strictly weaker claim than "we provably notice within N minutes," and SC-001 already promises the former is not enough.

**Known residual limit, documented rather than hidden (review, third pass)**: `T_detect` bounds detection latency ONLY for a process whose lifetime spans at least one full cadence window `C`. A process that starts and exits entirely between two passes (lifetime < `C`) is invisible to periodic polling by construction -- no value of `T_detect` closes this, because the process never exists at the instant either surrounding pass runs. This is tracked as a SEPARATE metric, `missed_surface_rate` (fraction of process lifetimes shorter than `C` in a monitoring window, estimated from `/proc` boot-id/PID-reuse counters where available), reported alongside `T_detect` rather than folded into it -- conflating the two would let "100% of passes individually correct" quietly imply "0% missed," which is false. Closing this gap for real (a kernel-level process-creation event stream, e.g. `netlink proc_events` or `fanotify`) is banked as a future increment: it adds a permanent listener process, which the approved plan's "no permanent process" constraint and RAM budget make a deliberate, explicit trade-off to defer, not an oversight.

## 12. Observer availability as its own Surface

**Decision**: A reserved `surface_id = "security-scientist-observer"` whose OWN observation is written by `run.sh` itself (the outside-the-repo cron wrapper, NOT the Python collector) on every single invocation, before it even calls `security_scientist_observe.py` -- a trivial one-row heartbeat write (`observed_at=now`, `payload={"invocation": "ok"}`) into `security_observations`. The current status of this surface is queried through the exact same `state_at`/freshness machinery as any other surface. If `run.sh` stops running (cron dead, host down, disk full), no fresh observation exists for this surface, and it falls to `STALE`/`UNOBSERVED` automatically -- not via a hardcoded `if not aria_running: return UNAVAILABLE` branch anywhere in the code.

**Rationale**: Review point #7 is precise: FR-019 must be a property the surface model derives, not an exception a developer remembered to code. Writing the observer's own heartbat through the identical generic path as every other surface means there is exactly one piece of machinery (`security_evidence.py`'s freshness/staleness logic) responsible for both "is this Python venv's security proof still fresh" and "is the Security Scientist itself still alive" -- one mechanism, two applications, rather than a special case.

**Alternatives considered**: A dedicated liveness/heartbeat file checked by a separate code path -- rejected, exactly the kind of parallel special-cased mechanism review point #7 warns against; it would need to be tested and maintained independently of every other surface's freshness logic, doubling the surface area for the same property.

## 13. Anti-loop invariant: the observer never mutates what it observes

**Decision**: `security_scientist_observe.py`, `_critic.py`, and `_judge.py` have exactly one allowed write target in this increment: their own evidence ledger (`security_scientist.db`) and their own stdout/report file. A static test (`test_security_scientist_no_side_effects.py`) parses each module's AST for `open(..., "w"/"a")`, `Path.write_text`/`write_bytes`, `os.remove`/`os.kill`/`shutil.*`, and `subprocess.run`/`Popen` calls, and asserts every file-path argument resolves under an explicit allowlist (`security_scientist.db`, a `--markdown`/`--json` CLI output path) and every subprocess call is one of the two read-only commands already named in research points 1-2 (`systemctl list-units`, never `systemctl stop`/`kill`/anything mutating).

**Rationale**: Review point #6: an observer that can alter the reality it measures (kill a process it doesn't like, touch a lockfile to make a check pass, write to a config it's auditing) can make its own diagnosis true by construction rather than by discovery -- exactly the "producer certifies its own measurement" failure class this whole feature exists to close, applied to the environment instead of to the verdict. Static AST analysis over dynamic runtime sandboxing (e.g. a seccomp/container jail) was chosen because it is cheap, matches the project's existing `test_coherence.py` static-analysis pattern, and the collector is a short-lived CLI script, not a long-running process worth containerizing on its own for this increment.

**Alternatives considered**: OS-level sandboxing (seccomp profile, dedicated unprivileged user, read-only bind mounts) -- banked as a stronger future hardening step (natural fit once this feature has a real track record, matching the plan's LEVEL 3 -> LEVEL 4 gating philosophy) but not required for V1: the static AST test already proves the CURRENT code has no such write path, which is the property FR-018/LEVEL 3 need today. Runtime sandboxing defends against a future malicious/compromised version of the code; the static test defends against the actual, present risk (an accidental side effect creeping into a well-intentioned collector).
