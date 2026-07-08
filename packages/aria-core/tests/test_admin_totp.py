"""TOTP opérateur — prouvé correct contre les vecteurs officiels RFC 6238."""
import base64

import pytest

from aria_core.admin_totp import (
    generate_secret,
    provisioning_uri,
    totp_code,
    verify_totp,
)

# RFC 6238, seed SHA1 = ASCII "12345678901234567890" (20 octets), en base32.
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()

# Vecteurs RFC 6238 (TOTP 8 chiffres) tronqués aux 6 derniers chiffres (mode authenticator).
_RFC_VECTORS = {
    59: "287082",
    1111111109: "081804",
    1234567890: "005924",
    2000000000: "279037",
}


@pytest.mark.parametrize("at,expected", _RFC_VECTORS.items())
def test_rfc6238_known_vectors(at, expected):
    assert totp_code(_RFC_SECRET, at=at) == expected
    assert verify_totp(_RFC_SECRET, expected, at=at, window=0) is True


def test_verify_accepts_adjacent_window():
    # Un code du pas précédent/suivant reste accepté (tolérance d'horloge ±1).
    prev = totp_code(_RFC_SECRET, at=59 - 30)
    nxt = totp_code(_RFC_SECRET, at=59 + 30)
    assert verify_totp(_RFC_SECRET, prev, at=59, window=1) is True
    assert verify_totp(_RFC_SECRET, nxt, at=59, window=1) is True


def test_verify_rejects_wrong_and_out_of_window():
    assert verify_totp(_RFC_SECRET, "000000", at=59) is False
    # Deux pas d'écart, hors fenêtre ±1.
    far = totp_code(_RFC_SECRET, at=59 + 60)
    assert verify_totp(_RFC_SECRET, far, at=59, window=1) is False


def test_verify_rejects_malformed():
    assert verify_totp(_RFC_SECRET, "", at=59) is False
    assert verify_totp(_RFC_SECRET, "12345", at=59) is False   # trop court
    assert verify_totp(_RFC_SECRET, "abcdef", at=59) is False  # non numérique
    assert verify_totp("", "287082", at=59) is False           # secret vide


def test_generate_secret_is_valid_base32():
    s = generate_secret()
    assert len(s) >= 32
    # décodable (avec padding) => base32 valide, et utilisable immédiatement.
    assert totp_code(s).isdigit()


def test_provisioning_uri_shape():
    uri = provisioning_uri("ABC234", label="ARIA Admin", issuer="Aria Vanguard ZHC")
    assert uri.startswith("otpauth://totp/")
    assert "secret=ABC234" in uri
    assert "algorithm=SHA1" in uri and "digits=6" in uri and "period=30" in uri
