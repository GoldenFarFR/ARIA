"""TOTP opérateur (RFC 6238) — second facteur pour l'accès admin.

Implémentation stdlib PURE (hmac/hashlib/base64), sans dépendance externe : un TOTP
est un HOTP (RFC 4226) indexé sur le temps. Compatible avec les apps d'authentification
standard (Google Authenticator, Aegis…) : secret base32, HMAC-SHA1, 6 chiffres, pas de 30 s.

Prouvé correct contre le vecteur officiel RFC 6238 (voir tests/test_admin_totp.py).

Usage (garde-fou) : voir aria_core.public_mode.is_operator_request — le TOTP n'est EXIGÉ
que si la variable d'environnement ADMIN_TOTP_SECRET est définie (opt-in, OFF par défaut).
JAMAIS de secret en clair ici ; le secret vit dans le .env du VPS (chmod 600).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

_STEP = 30
_DIGITS = 6


def _normalize_b32(secret: str) -> bytes:
    """Décode un secret base32 tolérant (espaces, minuscules, padding manquant)."""
    s = (secret or "").strip().replace(" ", "").upper()
    if not s:
        raise ValueError("empty TOTP secret")
    pad = (-len(s)) % 8
    return base64.b32decode(s + ("=" * pad))


def _hotp(key: bytes, counter: int, digits: int = _DIGITS) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def totp_code(secret: str, *, at: float | None = None, step: int = _STEP, digits: int = _DIGITS) -> str:
    """Code TOTP courant pour un secret base32."""
    now = time.time() if at is None else at
    counter = int(now // step)
    return _hotp(_normalize_b32(secret), counter, digits)


def verify_totp(secret: str, code: str, *, at: float | None = None, window: int = 1) -> bool:
    """Vrai si `code` est valide dans une fenêtre de ±`window` pas (tolérance d'horloge).

    Comparaison à temps constant. Refuse un code de longueur/format invalide sans lever.
    """
    code = (code or "").strip()
    if not code.isdigit() or len(code) != _DIGITS:
        return False
    try:
        key = _normalize_b32(secret)
    except Exception:
        return False
    now = time.time() if at is None else at
    base = int(now // _STEP)
    for w in range(-abs(window), abs(window) + 1):
        candidate = _hotp(key, base + w, _DIGITS)
        if hmac.compare_digest(candidate, code):
            return True
    return False


def generate_secret(length_bytes: int = 20) -> str:
    """Nouveau secret base32 aléatoire (20 octets = 160 bits, standard authenticator)."""
    raw = secrets.token_bytes(length_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, *, label: str = "ARIA Admin", issuer: str = "Aria Vanguard ZHC") -> str:
    """URI otpauth:// à scanner/coller dans une app d'authentification."""
    lab = quote(label)
    return (
        f"otpauth://totp/{lab}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={_DIGITS}&period={_STEP}"
    )
