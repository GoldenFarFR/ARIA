#!/usr/bin/env python3
"""Creates/resets the operator mobile account (Item #201, Android fallback channel
if Telegram goes down) -- password + mandatory TOTP, no other way in.

Password and TOTP are never both displayed together outside this script; the TOTP
secret is shown ONLY when --show-secret-once is passed, and only once (relaunching
the script generates a brand new secret, never re-displays an old one). Password/
TOTP changes here always revoke every existing session for this account (a stolen
old session must never survive a credential reset).

--unlock purges the account's failed-login counter (the progressive slowdown, never
a hard lockout -- see operator_account.py) without touching password/TOTP/sessions
at all -- a second, SSH-independent unlock path also exists via the owner-only
Telegram /unlockmobile command.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_ROOT))

from aria_core.admin_totp import generate_secret, provisioning_uri  # noqa: E402

from app.auth import operator_account as accounts  # noqa: E402
from app.auth import operator_session as sessions  # noqa: E402

_MIN_PASSWORD_LENGTH = 12


async def _unlock(username: str) -> None:
    ok = await accounts.reset_failed_attempts(username)
    if ok:
        print(f"Compte '{username}' débloqué -- failed_attempts purgé.")
    else:
        print(f"Aucun compte '{username}' trouvé.", file=sys.stderr)
        sys.exit(1)


async def _create(username: str, *, show_secret_once: bool) -> None:
    password = getpass.getpass("Nouveau mot de passe opérateur : ")
    confirm = getpass.getpass("Confirme le mot de passe : ")
    if password != confirm:
        print("Les mots de passe ne correspondent pas.", file=sys.stderr)
        sys.exit(1)
    if len(password) < _MIN_PASSWORD_LENGTH:
        print(f"Mot de passe trop court ({_MIN_PASSWORD_LENGTH} caractères minimum).", file=sys.stderr)
        sys.exit(1)

    secret = generate_secret()
    account_id = await accounts.create_or_replace_account(
        username=username, password=password, totp_secret=secret,
    )
    revoked = await sessions.revoke_all_operator_sessions(account_id)

    print(f"\n== Compte opérateur mobile '{username}' créé/réinitialisé (id={account_id}) ==")
    if revoked:
        print(f"{revoked} session(s) existante(s) révoquée(s) -- l'app devra se reconnecter.")

    if not show_secret_once:
        print("\nSecret TOTP NON affiché. Relance avec --show-secret-once pour l'enrôlement.")
        return

    # CodeQL py/clear-text-logging-sensitive-data (x2 below: password
    # confirmation flow above uses getpass, never echoed): accepted by
    # design, same reasoning as gen-admin-secret.py's own comment -- this
    # is the explicit ``--show-secret-once`` path, a local one-shot CLI
    # display gated behind its own flag, not a log/shared system.
    uri = provisioning_uri(secret, label=f"ARIA Mobile ({username})", issuer="Aria Vanguard ZHC")
    print("\n⚠️  Secret TOTP affiché UNE SEULE FOIS -- scanne-le maintenant, il ne sera")
    print("   plus jamais réaffiché (relancer ce script en génère un nouveau) :\n")
    print(f"   {secret}\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make()
        qr.print_ascii(invert=True)
        print()
    except ImportError:
        print(f"   (qrcode non installé -- URI brute à scanner via un générateur externe : {uri})\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gère le compte opérateur mobile (Item #201).")
    parser.add_argument("--username", default="operator", help="Nom d'utilisateur (défaut: operator).")
    parser.add_argument(
        "--show-secret-once", action="store_true",
        help="Affiche le secret TOTP une seule fois après création/réinitialisation.",
    )
    parser.add_argument(
        "--unlock", action="store_true",
        help="Purge uniquement l'historique d'échecs de connexion (aucun changement de mot de passe/TOTP).",
    )
    args = parser.parse_args()

    if args.unlock:
        asyncio.run(_unlock(args.username))
    else:
        asyncio.run(_create(args.username, show_secret_once=args.show_secret_once))


if __name__ == "__main__":
    main()
