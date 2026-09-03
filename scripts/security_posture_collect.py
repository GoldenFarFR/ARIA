"""Collect real security evidence and emit the posture contract.

Deterministic layer only: shells out to gh / osv-scanner / the filesystem and
turns each measurement into an Evidence (see security_posture.py). No model, no
inference, no judgement -- a measurement that cannot be made becomes UNKNOWN
rather than an assumption.

Emits JSON on stdout (machine-readable contract) and, with --markdown, a human
report. Exit code mirrors the posture so cron and CI can branch on it:
    0 PASS, 1 FAIL, 2 UNKNOWN, 3 STALE.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import security_posture as sp  # noqa: E402

REPOS = [
    "ARIA", "obv-ao-screener", "aria-ops",
    "ai-companion-avatar", "aria-brain", "aria-core", "GoldenFarFR",
]
REPO_DIR = Path("/opt/aria")

# Max age per evidence kind. A dependency answer older than a day says nothing
# about today; the CLI version moves slower and tolerates a week.
MAX_AGE_DEPENDABOT = 24 * 3600
MAX_AGE_OSV = 24 * 3600
MAX_AGE_SESSIONS = 7 * 24 * 3600

EXPECTED = ["dependabot", "osv-local", "cli-version", "session-guardrails"]


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return 127, "", str(exc)


def collect_dependabot(now: float) -> sp.Evidence:
    """Open alerts across every repo, plus whether the watch is even switched on.

    state=open is not optional here: without it the API returns the whole
    history, fixed alerts included. That exact mistake produced a "30 open
    alerts" figure on 2026-09-03 when the real count was zero.
    """
    rc, _, _ = _run(["gh", "auth", "status"], timeout=60)
    if rc != 0:
        return sp.unknown("dependabot", "gh unavailable or not authenticated", source="gh")

    discovered = len(REPOS)
    verified = 0
    total_open = 0
    inactive: list[str] = []
    details: list[str] = []
    for repo in REPOS:
        rc_w, _, _ = _run(["gh", "api", f"repos/GoldenFarFR/{repo}/vulnerability-alerts", "--silent"], timeout=60)
        watching = rc_w == 0
        rc_a, out, _ = _run(
            ["gh", "api", f"repos/GoldenFarFR/{repo}/dependabot/alerts?state=open&per_page=100", "--jq", "length"],
            timeout=90,
        )
        if rc_a != 0 or not out.strip().isdigit():
            details.append(f"{repo}: unreadable")
            continue
        verified += 1
        n = int(out.strip())
        total_open += n
        if not watching:
            inactive.append(repo)
        if n:
            details.append(f"{repo}: {n} open")

    if inactive:
        details.append(f"watch disabled on: {', '.join(inactive)}")
    ok = total_open == 0 and not inactive
    return sp.measured(
        "dependabot",
        ok,
        "; ".join(details) or "no open alerts on any repo",
        checked_at=now,
        max_age_seconds=MAX_AGE_DEPENDABOT,
        discovered=discovered,
        verified=verified,
        source="gh api",
    )


def collect_osv(now: float) -> sp.Evidence:
    """Local dependency scan over the checked-out repo."""
    rc, _, _ = _run(["osv-scanner", "--version"], timeout=60)
    if rc != 0:
        return sp.unknown("osv-local", "osv-scanner not installed", source="osv-scanner")

    rc, out, err = _run(
        ["osv-scanner", "scan", "source", "-r", str(REPO_DIR), "--format", "json"], timeout=1800
    )
    if not out.strip():
        return sp.unknown("osv-local", f"scanner produced no output (rc={rc})", source="osv-scanner")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        return sp.unknown("osv-local", f"unparseable scanner output: {exc}", source="osv-scanner")

    vulns = sum(
        len(p.get("vulnerabilities") or [])
        for r in data.get("results", [])
        for p in r.get("packages", [])
    )
    scanned = err.count("Scanned ")
    filtered = 0
    for line in err.splitlines():
        if "Filtered" in line and "vulnerabilit" in line:
            token = next((t for t in line.split() if t.isdigit()), None)
            if token:
                filtered = int(token)
            break
    detail = f"{vulns} vulnerabilities over {scanned} manifest(s), {filtered} suppressed by config"
    return sp.measured(
        "osv-local", vulns == 0, detail,
        checked_at=now, max_age_seconds=MAX_AGE_OSV, source="osv-scanner",
    )


def collect_cli_version(now: float) -> sp.Evidence:
    """Which Claude Code the machine would actually run, and whether one lags.

    Reports UNKNOWN rather than guessing when npm is unreachable -- an unknown
    upstream version cannot certify that the local one is current.
    """
    versions: dict[str, str] = {}
    rc, out, _ = _run(["/usr/bin/claude", "--version"], timeout=60)
    if rc == 0 and out.split():
        versions["system"] = out.split()[0]
    nvm = sorted(Path("/root/.nvm/versions/node").glob("*/bin/claude"))
    if nvm:
        rc, out, _ = _run([str(nvm[-1]), "--version"], timeout=60)
        if rc == 0 and out.split():
            versions["nvm"] = out.split()[0]
    if not versions:
        return sp.unknown("cli-version", "no Claude Code binary could be interrogated")

    rc, out, _ = _run(["npm", "view", "@anthropic-ai/claude-code", "version"], timeout=90)
    latest = out.strip() if rc == 0 and out.strip() else None
    if latest is None:
        return sp.unknown(
            "cli-version",
            f"installed {versions}, but npm unreachable so currency is unproven",
            source="npm",
        )

    distinct = set(versions.values())
    problems = []
    if len(distinct) > 1:
        # Two generations side by side: a script resolving `claude` through PATH
        # can silently execute the older one.
        problems.append(f"two versions coexist {versions}")
    served = versions.get("nvm") or versions.get("system")
    if served != latest:
        problems.append(f"served {served} behind latest {latest}")
    return sp.measured(
        "cli-version", not problems,
        "; ".join(problems) or f"single version {served}, current",
        checked_at=now, max_age_seconds=MAX_AGE_SESSIONS, source="npm + local binaries",
    )


def collect_session_guardrails(now: float) -> sp.Evidence:
    """Hooks and git guardrails that protect the sessions themselves."""
    settings = REPO_DIR / ".claude" / "settings.json"
    if not settings.exists():
        return sp.unknown("session-guardrails", "no .claude/settings.json to read")
    try:
        cfg = json.loads(settings.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return sp.unknown("session-guardrails", f"settings unreadable: {exc}")

    hooks = sum(len(e.get("hooks", [])) for lst in cfg.get("hooks", {}).values() for e in lst)
    hooks_dir = REPO_DIR / ".git" / "hooks"
    required = ["commit-msg", "pre-commit", "pre-push"]
    present = [h for h in required if (hooks_dir / h).exists()]
    problems = []
    if hooks == 0:
        problems.append("no session hook wired")
    if len(present) < len(required):
        missing = set(required) - set(present)
        problems.append(f"git hooks missing: {', '.join(sorted(missing))}")
    return sp.measured(
        "session-guardrails", not problems,
        "; ".join(problems) or f"{hooks} session hooks, {len(present)}/{len(required)} git hooks",
        checked_at=now, max_age_seconds=MAX_AGE_SESSIONS,
        discovered=len(required), verified=len(present), source="filesystem",
    )


def to_markdown(rep: dict) -> str:
    lines = [
        f"# Security posture -- {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(rep['generated_at']))}",
        "",
        f"**GLOBAL: {rep['global_status']}**",
        "",
        "| Check | Status | Coverage | Detail |",
        "|---|---|---|---|",
    ]
    for e in rep["evidence"]:
        cov = f"{e['verified']}/{e['discovered']}" if e.get("discovered") is not None else "-"
        lines.append(f"| {e['id']} | {e['status']} | {cov} | {e['detail'] or '-'} |")
    lines += ["", "Counts: " + ", ".join(f"{k}={v}" for k, v in rep["counts"].items() if v)]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", metavar="PATH", help="also write a human report here")
    ap.add_argument("--json", metavar="PATH", help="write the contract here instead of stdout")
    args = ap.parse_args()

    now = time.time()
    evidences = [
        collect_dependabot(now),
        collect_osv(now),
        collect_cli_version(now),
        collect_session_guardrails(now),
    ]
    rep = sp.report(evidences, now, expected_ids=EXPECTED)

    payload = json.dumps(rep, indent=2)
    if args.json:
        Path(args.json).write_text(payload)
    else:
        print(payload)
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(rep))

    return {sp.PASS: 0, sp.FAIL: 1, sp.UNKNOWN: 2, sp.STALE: 3}[rep["global_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
