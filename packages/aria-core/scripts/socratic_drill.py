"""
ARIA Socratic drill — batch Q/R vs ground truth, log JSONL exhaustif.

Usage:
  cd %ARIA_REPO_ROOT%\\packages\\aria-core
  py -3.12 scripts/socratic_drill.py
  py -3.12 scripts/socratic_drill.py --suite identity
  py -3.12 scripts/socratic_drill.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CATALOG = _ROOT / "knowledge" / "socratic_catalog.yaml"
_LOG_DIR = Path(os.environ.get("DATA_DIR", _ROOT.parent.parent / "vanguard" / "backend" / "data"))
_LOG_PATH = _LOG_DIR / "socratic_drill.jsonl"


def _bootstrap() -> None:
    repo = os.environ.get("ARIA_REPO_ROOT", "").strip()
    if not repo:
        candidate = _ROOT.parent.parent
        if (candidate / "collegue-memoire").is_dir():
            os.environ["ARIA_REPO_ROOT"] = str(candidate)
    os.environ.setdefault("ARIA_VECTOR_MEMORY", "true")
    os.environ.setdefault("ARIA_MEMORY_ARBITRATOR", "true")
    os.environ.setdefault("ARIA_LLM_ENABLED", "true")
    os.environ.setdefault("ARIA_PUBLIC_MODE", "false")
    os.environ.setdefault("ACCESS_CODE_ENABLED", "false")
    data = os.environ.get("DATA_DIR", "").strip()
    if not data:
        os.environ["DATA_DIR"] = str(_LOG_DIR)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    src = _ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _load_catalog() -> dict:
    text = _CATALOG.read_text(encoding="utf-8")
    try:
        import yaml

        raw = yaml.safe_load(text) or {}
    except ImportError:
        # Secours sans PyYAML : uniquement --dry-run via regex minimal
        raise SystemExit("PyYAML requis : pip install pyyaml (venv aria-core)")
    return raw.get("suites") or {}


def _score_reply(
    reply: str,
    *,
    expect_keywords: list[str],
    forbid_patterns: list[str],
) -> dict:
    lower = (reply or "").lower()
    hits = [kw for kw in expect_keywords if kw.lower() in lower]
    forbidden = [pat for pat in forbid_patterns if re.search(pat, reply or "", re.I)]
    ok = bool(hits) and not forbidden and "ACTU — sources web" not in (reply or "")
    return {
        "ok": ok,
        "keyword_hits": hits,
        "keyword_miss": [kw for kw in expect_keywords if kw.lower() not in lower],
        "forbidden_hits": forbidden,
        "reply_chars": len(reply or ""),
    }


async def _run_case(case: dict, suite: str) -> dict:
    from aria_core.brain import aria_brain

    question = case["question"]
    t0 = time.monotonic()
    try:
        resp = await aria_brain.process_message(
            question,
            lang="fr",
            visitor_id="shell-socratic",
            public_mode=False,
        )
        reply = resp.reply or ""
        route = resp.actions_taken or []
        skill = resp.skill_used.value if resp.skill_used else None
        data = resp.data if isinstance(resp.data, dict) else {}
        error = None
    except Exception as exc:
        reply = ""
        route = []
        skill = None
        data = {}
        error = str(exc)

    score = _score_reply(
        reply,
        expect_keywords=case.get("expect_keywords") or [],
        forbid_patterns=case.get("forbid_patterns") or [],
    )
    elapsed = round(time.monotonic() - t0, 2)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "suite": suite,
        "id": case.get("id"),
        "question": question,
        "expected_sources": case.get("expected_sources") or [],
        "reply_preview": (reply or "")[:400],
        "score": score,
        "route": route,
        "skill": skill,
        "data_flags": {k: data.get(k) for k in ("self_context", "collegue_recall", "web_verified")},
        "elapsed_s": elapsed,
        "error": error,
    }
    return entry


async def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="ARIA Socratic drill")
    parser.add_argument("--suite", default="", help="identity | collegue | routing (toutes si vide)")
    parser.add_argument("--dry-run", action="store_true", help="Liste les cas sans appeler le brain")
    parser.add_argument("--log", default=str(_LOG_PATH), help="Chemin JSONL de sortie")
    args = parser.parse_args()

    suites = _load_catalog()
    if args.suite:
        if args.suite not in suites:
            print(f"Suite inconnue : {args.suite}")
            return 1
        suites = {args.suite: suites[args.suite]}

    cases: list[tuple[str, dict]] = []
    for name, block in suites.items():
        for case in block.get("cases") or []:
            cases.append((name, case))

    if args.dry_run:
        for suite, case in cases:
            print(f"[{suite}] {case.get('id')}: {case.get('question')}")
        print(f"{len(cases)} cas — dry-run OK")
        return 0

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    ok_count = 0
    with log_path.open("a", encoding="utf-8") as logf:
        for suite, case in cases:
            entry = await _run_case(case, suite)
            logf.write(json.dumps(entry, ensure_ascii=False) + "\n")
            status = "OK" if entry["score"]["ok"] else "KO"
            if entry["score"]["ok"]:
                ok_count += 1
            print(f"[{status}] {suite}/{case.get('id')} ({entry['elapsed_s']}s)")
            if not entry["score"]["ok"]:
                if entry["score"]["forbidden_hits"]:
                    print(f"      interdit: {entry['score']['forbidden_hits']}")
                if entry["score"]["keyword_miss"]:
                    print(f"      manque: {entry['score']['keyword_miss']}")

    total = len(cases)
    print(f"\nRésultat : {ok_count}/{total} — log : {log_path}")
    return 0 if ok_count == total else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))