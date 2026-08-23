"""Provider credentials must never reach a log line.

23/08 -- written after finding the Chainstack RPC key, access path and all, in
two `429 Too Many Requests` lines. The pocket already routed httpx to WARNING to
hide the URL of successful calls, which closed exactly half the hole: errors
carry the same URL and errors are what WARNING lets through.

Every credential-shaped string below is DELIBERATELY fake and self-labelled:
the first draft used a realistic-looking UUID and the repo's own pre-commit
secret scan blocked the commit, which is the guardrail working exactly as it
should. The redaction rules key off the parameter NAME and the path SHAPE, not
off any property of the value, so a visibly fake value tests the same code path.
"""
from __future__ import annotations

import logging

import pytest

from aria_core.log_redaction import (
    SecretRedactingFilter,
    install_log_redaction,
    redact_secrets,
)


class TestRedactSecrets:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://solana-mainnet.core.chainstack.com/0123456789abcdef0123456789abcdef",
            "https://mainnet.helius-rpc.com/?api-key=FAKEKEY-not-a-real-credential-000",
            "GET https://x.example/v1?apikey=FAKEVALUE-not-real-000 failed",
            "Authorization: Bearer FAKE-bearer-not-a-real-token-000",
            "https://api.example.com/rpc?token=aaaaaaaaaaaaaaaaaaaa",
        ],
    )
    def test_a_credential_never_survives(self, raw):
        out = redact_secrets(raw)
        assert "[REDACTED]" in out
        for leak in ("0123456789abcdef0123456789abcdef", "abcd1234-ef56-7890",
                     "FAKEVALUE-not-real-000", "sk-ant-api03", "aaaaaaaaaaaaaaaaaaaa"):
            assert leak not in out

    @pytest.mark.parametrize(
        "raw",
        [
            # A Solana address is base58 and must stay readable -- redacting it
            # would make every pool log line useless.
            "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
            "pool 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU progress=0.80",
            "https://api.geckoterminal.com/api/v2/networks/solana/pools",
            "entry_price=0.00000123 reserve=6042.55",
        ],
    )
    def test_legitimate_text_is_left_alone(self, raw):
        assert redact_secrets(raw) == raw

    def test_it_never_raises_on_odd_input(self):
        assert redact_secrets(None) == "None"
        assert redact_secrets(42) == "42"
        assert redact_secrets(ValueError("boom")) == "boom"


class TestFilter:
    def test_the_secret_is_gone_from_the_formatted_record(self, caplog):
        """The leak arrives through the ARGUMENT, not the format string.

        This is the whole reason the filter formats first: `record.msg` alone is
        `"... (%s)"` and holds no secret at all.
        """
        logger = logging.getLogger("test_redaction_arg")
        logger.addFilter(SecretRedactingFilter())
        url = "https://solana-mainnet.core.chainstack.com/0123456789abcdef0123456789abcdef"
        with caplog.at_level(logging.INFO, logger="test_redaction_arg"):
            logger.info("add_pools resolution failed (%s)", RuntimeError(f"429 for {url}"))
        text = caplog.text
        assert "0123456789abcdef0123456789abcdef" not in text
        assert "[REDACTED]" in text
        # the diagnosable part must survive -- a redaction that eats the error
        # message trades one blindness for another
        assert "429" in text and "core.chainstack.com" in text

    def test_install_is_idempotent(self):
        root = logging.getLogger()
        install_log_redaction()
        first = sum(isinstance(f, SecretRedactingFilter) for f in root.filters)
        install_log_redaction()
        install_log_redaction()
        assert sum(isinstance(f, SecretRedactingFilter) for f in root.filters) == first
