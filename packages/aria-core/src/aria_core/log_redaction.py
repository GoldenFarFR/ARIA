"""Keep provider credentials out of the logs, at ONE point instead of 214.

23/08 -- found live while checking a restart: two 429 lines had logged the full
Chainstack RPC endpoint, access key and all, because the code logs the httpx
exception and httpx puts the URL in its message. Counted across the two files
that carry it: 5741 occurrences of the Chainstack key and 58234 of a Helius
`api-key=`. Nothing reached git (verified), so this is a local-disk exposure,
but it breaks the dome's own standing rule -- never a secret in a log -- and
those logs are read by crons and by Claude Code sessions.

WHY A FILTER AND NOT 214 EDITS. Every one of those sites logs an exception the
same legitimate way (`logger.info("... (%s)", exc)`); the URL arrives from
inside the exception, not from the call site. Patching call sites would fix
today's 214 and miss every one written tomorrow, and a single missed site
re-leaks the key. A filter on the root logger sees the FORMATTED record, so it
covers every current and future site, including third-party libraries.

DELIBERATELY NOT DONE HERE: rotating the exposed keys, and purging the existing
log files. Both are the operator's call -- rotation because it can break a
running pipeline, purging because deleting evidence of an exposure before it is
acknowledged is the wrong default.
"""
from __future__ import annotations

import logging
import re

_MASK = "[REDACTED]"

# A sensitive query parameter: the NAME is kept (it is useful to know which
# credential failed), the value never is.
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[-_]?key|apikey|key|token|secret|access[-_]?token|auth)=)[^&\s\"'<>]+"
)

# A long hexadecimal PATH segment, which is how Chainstack (and several other
# RPC providers) carry the credential. Bounded at 24+ hex chars on purpose: a
# Solana address is base58 and a transaction signature is base58 too, so
# neither matches a hex-only run of this length in practice, while a 32-hex
# provider key always does.
_PATH_SECRET = re.compile(r"(?i)(://[^/\s]+/)[0-9a-f]{24,}")

# Bearer tokens in headers echoed into an error message.
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}")


def redact_secrets(text: object) -> str:
    """Replace provider credentials with ``[REDACTED]``.

    Pure and total: it never raises, and a value it does not recognise is
    returned unchanged rather than mangled. Non-string input is coerced, since
    log records routinely carry exceptions and numbers.
    """
    s = text if isinstance(text, str) else str(text)
    s = _QUERY_SECRET.sub(rf"\1{_MASK}", s)
    s = _PATH_SECRET.sub(rf"\1{_MASK}", s)
    s = _BEARER.sub(rf"\1{_MASK}", s)
    return s


class SecretRedactingFilter(logging.Filter):
    """Redacts the record's already-formatted message.

    Formatting first is the point: the secret usually lives inside an argument
    (an exception), not in the format string, so filtering `record.msg` alone
    would miss it. The formatted text is written back as `msg` with `args`
    cleared, which is what `logging` itself does when a filter rewrites a
    record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 -- a broken record must never kill logging
            return True
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_log_redaction() -> None:
    """Attach the filter to the root logger, once.

    On the ROOT logger's handlers rather than on the logger itself: a filter on
    a logger only sees records that logger emits directly, never those
    propagated up from `aria_core.services.*`, which is exactly where the leak
    was.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
            handler.addFilter(SecretRedactingFilter())
    # A handler added after configure() would miss the filter, so also guard the
    # root logger itself -- harmless duplication, and it covers `logging.log()`
    # calls made straight on the root.
    if not any(isinstance(f, SecretRedactingFilter) for f in root.filters):
        root.addFilter(SecretRedactingFilter())
