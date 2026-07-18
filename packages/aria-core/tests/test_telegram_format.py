from aria_core.gateway.telegram_format import plain_telegram


def test_plain_telegram_strips_markdown():
    raw = "**OPÉRATEUR** — ID `123456789`\n→ GitHub\n- item"
    out = plain_telegram(raw)
    assert "**" not in out
    assert "`" not in out
    assert "OPÉRATEUR" in out
    assert "123456789" in out
    assert " : " in out or "GitHub" in out


def test_plain_telegram_code_blocks():
    raw = "```python\nprint('hi')\n```\nDone"
    assert "```" not in plain_telegram(raw)
    assert "Done" in plain_telegram(raw)


def test_plain_telegram_preserves_snake_case_identifiers():
    """Incident réel (18/07) : "safety_screen.py ... momentum_entry.py" dans le même
    message -- le premier "_" de safety_screen s'appariait avec le second "_" de
    momentum_entry, effaçant les deux underscores et tout le texte entre les deux
    devenait un unique "span italique" fantôme. Les deux identifiants doivent survivre
    intacts, y compris quand DEUX apparaissent dans le même texte."""
    raw = (
        "Pipeline VC-thesis (safety_screen.py) et pipeline momentum (momentum_entry.py) "
        "restent deux mécanismes distincts."
    )
    out = plain_telegram(raw)
    assert "safety_screen.py" in out
    assert "momentum_entry.py" in out


def test_plain_telegram_still_strips_genuine_italic_underscore():
    """Non-régression : _mot_ entouré d'espaces/ponctuation doit toujours être traité
    comme de l'italique markdown, pas comme un identifiant."""
    raw = "C'est _vraiment_ important."
    out = plain_telegram(raw)
    assert "_" not in out
    assert "vraiment" in out


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