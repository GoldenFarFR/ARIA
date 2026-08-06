#!/usr/bin/env python3
"""Génère un nouveau secret opérateur (ADMIN_API_SECRET, 1er facteur du cockpit) et écrit
directement la ligne dans le .env du VPS -- même doctrine que gen-admin-totp.py (27/07) :
jamais de copier-coller manuel entre la sortie de ce script et le fichier, source d'erreurs
vécue en pratique. À la différence du TOTP (qui vit dans une app d'authentification), ce
secret n'a pas d'autre "coffre" -- il faut le noter toi-même (gestionnaire de mots de passe)
juste après l'avoir généré, c'est la seule fois où il est affiché.
"""
import re
import secrets
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / "backend" / ".env"
_LINE_RE = re.compile(r"^#?ADMIN_API_SECRET=.*$", re.MULTILINE)


def _write_secret_to_env(secret: str) -> None:
    line = f"ADMIN_API_SECRET={secret}"
    text = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.exists() else ""
    if _LINE_RE.search(text):
        text = _LINE_RE.sub(line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    _ENV_PATH.write_text(text, encoding="utf-8")
    _ENV_PATH.chmod(0o600)


def main() -> None:
    # CodeQL py/clear-text-storage-sensitive-data + py/clear-text-logging-
    # sensitive-data: both accepted by design for this LOCAL, one-shot admin
    # bootstrap script -- .env (chmod 600) IS the established secret store
    # for this project (see CLAUDE.md), and the print below is the single,
    # intentional one-time display so the operator can copy it into their
    # own password manager (docstring above). Not a log file, not a shared
    # system, not repeatable -- neither finding applies to this threat model.
    secret = secrets.token_urlsafe(32)
    _write_secret_to_env(secret)
    print("== Secret opérateur (1er facteur cockpit) — régénéré ==\n")
    print(f"Ligne ADMIN_API_SECRET écrite automatiquement dans {_ENV_PATH} (chmod 600 appliqué).\n")
    print(f"   {secret}\n")
    print("Note cette valeur MAINTENANT quelque part de sûr (gestionnaire de mots de passe) --")
    print("c'est la seule fois où elle s'affiche, et tu en auras besoin à chaque connexion")
    print("au cockpit (champ 'Secret opérateur').\n")
    print("Ensuite : ./vanguard/deploy.sh pour l'activer.")


if __name__ == "__main__":
    main()
