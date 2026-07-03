from aria_core.response_cost import (
    append_cost_footer,
    build_cost_meta,
    cost_meta_reply,
    format_cost_footer,
    is_cost_meta_question,
)


def test_build_cost_meta_gratuit():
    meta = build_cost_meta(total_tokens=0, calls=0)
    assert meta["billed"] is False
    assert meta["total_tokens"] == 0


def test_format_footer_plain_gratuit():
    footer = format_cost_footer(build_cost_meta(total_tokens=0), lang="fr")
    assert "🟢" in footer
    assert "gratuit" in footer
    assert "0 tok" in footer


def test_format_footer_plain_payant():
    meta = {"billed": True, "cloud": True, "total_tokens": 342}
    footer = format_cost_footer(meta, lang="fr")
    assert "🟠" in footer
    assert "payant" in footer
    assert "342 tok" in footer


def test_format_footer_html_colors():
    meta = {"billed": True, "cloud": True, "total_tokens": 100}
    footer = format_cost_footer(meta, lang="fr", channel="html")
    assert "#e67e22" in footer
    meta_free = build_cost_meta(total_tokens=0)
    footer_free = format_cost_footer(meta_free, lang="fr", channel="html")
    assert "#27ae60" in footer_free


def test_append_cost_footer():
    out = append_cost_footer("Salut ARIA", build_cost_meta(total_tokens=0), lang="fr")
    assert out.startswith("Salut ARIA")
    assert "gratuit" in out


def test_cost_meta_question_detected():
    assert is_cost_meta_question("pourquoi orange tu as utilisé grok api ?")
    assert is_cost_meta_question("why paid orange on last reply")


def test_cost_meta_reply_fr():
    reply = cost_meta_reply("fr")
    assert "gratuite" in reply
    assert "🟠" in reply