#!/usr/bin/env python3
"""Génère un secret TOTP opérateur (2FA admin) + l'URI otpauth à enrôler. À lancer UNE fois.

Prérequis : aria-core installé (venv du VPS ou de dev). Aucun secret n'est stocké par ce
script — il affiche le secret une fois ; à toi de le mettre dans le .env et dans ton app.
"""
from aria_core.admin_totp import generate_secret, provisioning_uri


def main() -> None:
    secret = generate_secret()
    uri = provisioning_uri(secret, label="ARIA Admin", issuer="Aria Vanguard ZHC")
    print("== 2FA opérateur (TOTP) — enrôlement ==\n")
    print("1) Ajoute cette ligne au .env du VPS (vanguard/backend/.env), puis chmod 600 :\n")
    print(f"   ADMIN_TOTP_SECRET={secret}\n")
    print("2) Dans ton app d'authentification (Google Authenticator, Aegis…), 'saisir une clé")
    print("   de configuration' avec ce même secret, OU scanne cette URI (via un générateur de QR) :\n")
    print(f"   {uri}\n")
    print("3) Redeploie (./vanguard/deploy.sh). Dès lors, chaque requête opérateur exige EN PLUS")
    print("   du secret admin le header 'X-Admin-Totp: <code à 6 chiffres>'.")
    print("   (Tant que ADMIN_TOTP_SECRET n'est pas dans le .env, le 2FA reste OFF — secret seul.)")


if __name__ == "__main__":
    main()
