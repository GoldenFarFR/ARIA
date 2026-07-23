"""Operator TOTP (RFC 6238) — second factor for admin access.

PURE stdlib implementation (hmac/hashlib/base64), no external dependency: a TOTP
is a time-indexed HOTP (RFC 4226). Compatible with standard authenticator apps
(Google Authenticator, Aegis…): base32 secret, HMAC-SHA1, 6 digits, 30s step.

Proven correct against the official RFC 6238 test vector (see tests/test_admin_totp.py).

Usage (guard rail): see aria_core.public_mode.is_operator_request — TOTP is only
REQUIRED if the ADMIN_TOTP_SECRET environment variable is set (opt-in, OFF by default).
NEVER a secret in plaintext here; the secret lives in the VPS .env (chmod 600).
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
    """Decode a tolerant base32 secret (spaces, lowercase, missing padding)."""
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
    """Current TOTP code for a base32 secret."""
    now = time.time() if at is None else at
    counter = int(now // step)
    return _hotp(_normalize_b32(secret), counter, digits)


def verify_totp(secret: str, code: str, *, at: float | None = None, window: int = 1) -> bool:
    """True if `code` is valid within a ±`window` step range (clock tolerance).

    Constant-time comparison. Rejects a code of invalid length/format without raising.
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
    """New random base32 secret (20 bytes = 160 bits, standard authenticator)."""
    raw = secrets.token_bytes(length_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, *, label: str = "ARIA Admin", issuer: str = "Aria Vanguard ZHC") -> str:
    """otpauth:// URI to scan/paste into an authenticator app."""
    lab = quote(label)
    return (
        f"otpauth://totp/{lab}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={_DIGITS}&period={_STEP}"
    )
