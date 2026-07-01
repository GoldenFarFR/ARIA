"""Verify Privy JWTs (access + identity tokens)."""

from __future__ import annotations

import json
import logging
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger(__name__)

PRIVY_ISSUER = "privy.io"


class PrivyAuthError(Exception):
    pass


def _app_id() -> str:
    app_id = (settings.privy_app_id or "").strip()
    if not app_id:
        raise PrivyAuthError("PRIVY_APP_ID not configured")
    return app_id


def _verification_key_pem() -> str | None:
    key = (settings.privy_jwt_verification_key or "").strip()
    if not key:
        return None
    return key.replace("\\n", "\n")


def _jwks_url(app_id: str) -> str:
    return f"https://auth.privy.io/api/v1/apps/{app_id}/jwks.json"


_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(app_id: str) -> PyJWKClient:
    client = _jwks_clients.get(app_id)
    if client is None:
        client = PyJWKClient(_jwks_url(app_id))
        _jwks_clients[app_id] = client
    return client


def _decode_privy_jwt(token: str) -> dict[str, Any]:
    app_id = _app_id()
    pem = _verification_key_pem()
    try:
        if pem:
            signing_key = pem
        else:
            signing_key = _jwks_client(app_id).get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            signing_key,
            algorithms=["ES256"],
            audience=app_id,
            issuer=PRIVY_ISSUER,
        )
    except jwt.PyJWTError as exc:
        logger.debug("Privy JWT verification failed: %s", exc)
        raise PrivyAuthError("Invalid Privy token") from exc


def verify_access_token(token: str) -> dict[str, Any]:
    claims = _decode_privy_jwt(token)
    if not claims.get("sub"):
        raise PrivyAuthError("Missing Privy user id")
    return claims


def privy_did_from_access(token: str) -> str:
    return str(verify_access_token(token).get("sub") or "")


def extract_member_from_identity(token: str) -> tuple[str, str]:
    """Return (privy_did, member_handle). Prefers X username, else email local-part."""
    claims = _decode_privy_jwt(token)
    privy_did = str(claims.get("sub") or "")
    if not privy_did:
        raise PrivyAuthError("Missing Privy user id")

    raw_accounts = claims.get("linked_accounts") or "[]"
    if isinstance(raw_accounts, list):
        accounts = raw_accounts
    else:
        try:
            accounts = json.loads(raw_accounts)
        except json.JSONDecodeError as exc:
            raise PrivyAuthError("Invalid linked_accounts claim") from exc

    twitter_username: str | None = None
    email_address: str | None = None
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_type = str(account.get("type") or "").lower()
        if account_type in ("twitter_oauth", "twitter"):
            twitter_username = (
                account.get("username")
                or account.get("name")
                or account.get("subject")
            )
            if twitter_username:
                twitter_username = str(twitter_username).lstrip("@")
                break
        if account_type == "email" and not email_address:
            email_address = account.get("address") or account.get("email")

    if twitter_username:
        return privy_did, twitter_username

    if email_address:
        local = str(email_address).split("@", 1)[0].strip()
        if local:
            return privy_did, local

    raise PrivyAuthError(
        "No linked account found. Sign in with email or connect X in Privy, then try again."
    )


def extract_twitter_from_identity(token: str) -> tuple[str, str | None]:
    """Return (privy_did, twitter_username) — legacy alias."""
    privy_did, handle = extract_member_from_identity(token)
    return privy_did, handle


def privy_configured() -> bool:
    return bool((settings.privy_app_id or "").strip())