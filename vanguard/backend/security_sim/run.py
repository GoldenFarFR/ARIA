"""Entrée CLI de la simulation d'attaque.

    python -m security_sim.run [--budget N] [--json rapport.json]

Code de sortie : 0 si aucune faille CRITICAL/HIGH, 1 sinon (utilisable en CI / cron).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .harness import run_attack_simulation


def _print_summary(report) -> None:
    sev = report.by_severity
    print("=" * 68)
    print("SIMULATION D'ATTAQUE ARIA — résumé")
    print("=" * 68)
    print(f"Routes testées   : {report.routes_tested}")
    print(f"Requêtes tirées  : {report.total_requests}")
    print(f"Findings         : {len(report.findings)} "
          f"(CRITICAL={sev.get('CRITICAL', 0)} HIGH={sev.get('HIGH', 0)} MEDIUM={sev.get('MEDIUM', 0)})")
    if report.baseline_broken:
        print(f"Routes KO en bénin (env/DB sim, exclues du verdict) : {len(report.baseline_broken)}")
    print("-" * 68)
    deduped = report.deduped()
    crit = [f for f in deduped if f.severity in ("CRITICAL", "HIGH")]
    if not crit:
        print("✅ Aucune faille CRITICAL/HIGH — surface tenue.")
    else:
        print(f"⚠️  {len(crit)} faille(s) CRITICAL/HIGH (dédupliquées) :")
        for f in crit[:60]:
            print(f"  [{f.severity}] {f.category} — {f.method} {f.path} "
                  f"(injection {f.injection}, status {f.status}) payload={f.payload}")
            if f.detail:
                print(f"        → {f.detail}")
    print("=" * 68)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Simulation d'attaque ARIA (red-team en-process)")
    ap.add_argument("--budget", type=int, default=5000, help="nb max de requêtes (def. 5000)")
    ap.add_argument("--json", type=str, default="", help="chemin d'export du rapport JSON")
    args = ap.parse_args(argv)

    report = asyncio.run(run_attack_simulation(budget=args.budget))
    _print_summary(report)

    if args.json:
        payload = {
            "routes_tested": report.routes_tested,
            "total_requests": report.total_requests,
            "by_severity": report.by_severity,
            "findings": [
                {
                    "severity": f.severity, "category": f.category, "method": f.method,
                    "path": f.path, "injection": f.injection, "status": f.status,
                    "payload": f.payload, "detail": f.detail,
                }
                for f in report.deduped()
            ],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"Rapport JSON écrit : {args.json}")

    return 1 if report.critical_or_high else 0


if __name__ == "__main__":
    sys.exit(main())
