#!/usr/bin/env python3
"""Génère un secret TOTP opérateur (2FA admin), écrit directement la ligne dans le .env
du VPS (jamais de copier-coller manuel -- source d'erreurs vécue en pratique le 27/07,
plusieurs secrets générés successivement, confusion sur lequel était réellement dans le
fichier) + affiche l'URI otpauth à scanner. À lancer autant de fois que nécessaire pour
régénérer (écrase la ligne existante, ne duplique jamais).

Prérequis : aria-core installé (venv du VPS ou de dev). Aucun secret n'est stocké ailleurs
que dans ce .env (chmod 600 réappliqué à chaque écriture) -- le secret n'est affiché à
l'écran QUE pour le scan/saisie manuelle dans l'app d'authentification.
"""
import re
from pathlib import Path

from aria_core.admin_totp import generate_secret, provisioning_uri

_ENV_PATH = Path(__file__).resolve().parent.parent / "backend" / ".env"
_LINE_RE = re.compile(r"^#?ADMIN_TOTP_SECRET=.*$", re.MULTILINE)


def _write_secret_to_env(secret: str) -> None:
    line = f"ADMIN_TOTP_SECRET={secret}"
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
    # sensitive-data: accepted by design, same reasoning as gen-admin-
    # secret.py's own comment -- local one-shot admin CLI, .env (chmod 600)
    # is the established store, the print below is the one-time enrollment
    # display the docstring above describes.
    secret = generate_secret()
    uri = provisioning_uri(secret, label="ARIA Admin", issuer="Aria Vanguard ZHC")
    _write_secret_to_env(secret)
    print("== 2FA opérateur (TOTP) — enrôlement ==\n")
    print(f"1) Ligne ADMIN_TOTP_SECRET écrite automatiquement dans {_ENV_PATH} (chmod 600 appliqué).")
    print("   Rien à copier-coller pour cette étape.\n")
    print("2) Scanne le QR ci-dessous directement depuis ce terminal (Google Authenticator,")
    print("   Aegis…), ou 'saisir une clé de configuration' avec le secret ci-dessous :\n")
    print(f"   {secret}\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make()
        qr.print_ascii(invert=True)
        print()
    except ImportError:
        print(f"   (qrcode non installé -- URI brute a scanner via un generateur externe : {uri})\n")
    print("3) Redeploie (./vanguard/deploy.sh). Dès lors, chaque requête opérateur exige EN PLUS")
    print("   du secret admin le header 'X-Admin-Totp: <code à 6 chiffres>'.")
    print("   Relancer ce script régénère un nouveau secret et réécrit la même ligne --")
    print("   jamais de duplication, jamais un ancien secret qui traîne.")


if __name__ == "__main__":
    main()
