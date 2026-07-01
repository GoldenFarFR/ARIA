from aria_core.gateway.telegram_format import plain_telegram


def test_plain_telegram_strips_markdown():
    raw = "**OPÉRATEUR** — ID `5864967247`\n→ GitHub\n- item"
    out = plain_telegram(raw)
    assert "**" not in out
    assert "`" not in out
    assert "OPÉRATEUR" in out
    assert "5864967247" in out
    assert " : " in out or "GitHub" in out


def test_plain_telegram_code_blocks():
    raw = "```python\nprint('hi')\n```\nDone"
    assert "```" not in plain_telegram(raw)
    assert "Done" in plain_telegram(raw)


def test_plain_telegram_preserves_x_handle_with_underscore():
    """Regression: two @Aria_ZHC in one /x status broke underscore via _italic_ strip."""
    from aria_core.identity import fix_handle_in_text, official_x_at

    raw = (
        f"X — {official_x_at()}\n\n"
        f"Politique X {official_x_at()} (pay-per-use)\n"
        f"Vérification: X connecté : {official_x_at()} (ARIA)"
    )
    out = plain_telegram(fix_handle_in_text(raw))
    assert "@AriaZHC" not in out
    assert official_x_at() in out