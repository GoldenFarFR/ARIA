#!/usr/bin/env python3
"""Scanne le repo pour tout email hors liste blanche (incident #139, 12/07).

truth_ledger/sync.py a poussé des conversations Telegram en clair sur main, avec des
commits attribués à l'email réel de l'opérateur. detect-secrets (deja en place, cf.
.github/workflows/secrets-scan.yml) n'a pas de détecteur d'email -- ce n'est pas une
donnée à haute entropie, ce n'est pas son terrain. Ce script comble le trou : tout email
trouvé dans le repo doit être sur ALLOWLISTED_EMAILS (adresse exacte) ou sous un domaine
de ALLOWLISTED_DOMAINS (placeholder de doc/service connu) -- sinon le scan échoue.

Ne cible PAS une adresse précise (l'email de l'opérateur n'apparaît nulle part dans ce
fichier, jamais commité en clair) : liste blanche des adresses connues et LÉGITIMES
(ARIA elle-même, services tiers), tout le reste est refusé par défaut. Catch aussi bien
l'email de l'opérateur qu'un futur email personnel différent qui fuiterait par erreur.

Les fichiers de test (répertoire tests/, ou test_*.py) sont exemptés : le vecteur réel de
l'incident était du contenu PRODUCTION (conversations poussées automatiquement), pas des
fixtures de test qui utilisent déjà des domaines synthétiques variés (x.com, g.com...).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_EXCLUDE_DIR_NAMES = {
    ".venv", "node_modules", "__pycache__", ".git", "dist", "build", ".next",
}
_EXCLUDE_FILE_NAMES = {"package-lock.json"}

# Adresses de service/identité ARIA connues et légitimes -- exactes, jamais un domaine
# entier (un domaine large comme gmail.com couvrirait aussi de vraies adresses
# personnelles, ce qui viderait le garde-fou de son sens).
ALLOWLISTED_EMAILS = {
    "agentaria.zhc@gmail.com",       # mailbox produit ARIA (tests mailer/vc_delivery)
    "aria_vanguard_zhc@agents.world",  # identité agent ACP (showcase_pr_watch, email watcher)
    "noreply@anthropic.com",         # co-auteur de commit standard (CLAUDE.md)
    "cursoragent@cursor.com",        # identité bot Cursor (docs historiques)
}

# Domaines placeholder de documentation/exemple connus -- jamais une vraie adresse
# personnelle, safe à allowlister au niveau du domaine entier.
ALLOWLISTED_DOMAINS = {
    "example.com",
    "yourdomain.com",
    "users.noreply.github.com",
}


def _is_allowlisted(email: str) -> bool:
    email_l = email.lower()
    if email_l in ALLOWLISTED_EMAILS:
        return True
    domain = email_l.rsplit("@", 1)[-1]
    return domain in ALLOWLISTED_DOMAINS


def _is_exempt_path(rel: Path) -> bool:
    if any(part in _EXCLUDE_DIR_NAMES for part in rel.parts):
        return True
    if rel.name in _EXCLUDE_FILE_NAMES:
        return True
    if "tests" in rel.parts:
        return True
    return rel.name.startswith("test_") or rel.name.endswith("_test.py")


def find_unallowlisted_emails(root: Path) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _is_exempt_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        matches = {m.group(0) for m in EMAIL_RE.finditer(text)}
        bad = sorted(m for m in matches if not _is_allowlisted(m))
        if bad:
            findings[str(rel)] = bad
    return findings


def main() -> int:
    findings = find_unallowlisted_emails(REPO_ROOT)
    if findings:
        print("❌ Email(s) hors liste blanche détecté(s) :")
        for rel, emails in sorted(findings.items()):
            for email in emails:
                print(f"  - {rel}: {email}")
        print()
        print(
            "Si légitime (nouvelle adresse de service/identité ARIA) : ajoute-la à "
            "ALLOWLISTED_EMAILS dans scripts/check_no_personal_email.py, avec une "
            "raison en commentaire."
        )
        print("Si email personnel : retire-le du commit avant de pousser.")
        return 1
    print("✅ Aucun email hors liste blanche.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
