import pytest

from aria_core.gateway.telegram_format import plain_telegram
from aria_core.knowledge.base_launchpads import (
    compare_launchpads_markdown,
    methodology_markdown,
    recommendation_verdict,
)
from aria_core.knowledge.base_launchpads import LAUNCHPADS


def test_verdict_investor_format_fr():
    body = recommendation_verdict(lang="fr", holding_context=True)
    assert "🏛 LAUNCHPADS BASE" in body
    assert "🥇 VERDICT" in body
    assert "📊 Profil par axe" in body
    assert "📋 CLASSEMENT" in body
    assert "Vol" in body
    assert "█" in body
    assert "**" not in body
    assert "Axe" in body or "Scr" in body


def test_table_columns_aligned():
    body = recommendation_verdict(lang="en", holding_context=True)
    assert "Launchpad       Score" in body
    assert " 89." in body or " 90." in body or " 85." in body
    assert "Vol" in body and " 95" in body


def test_verdict_survives_telegram_plain():
    body = plain_telegram(recommendation_verdict(lang="fr"))
    assert "VERDICT" in body
    assert "CLASSEMENT" in body


def test_methodology_investor_format():
    body = methodology_markdown(lang="fr", holding_context=True)
    assert "🔬 MÉTHODOLOGIE" in body
    assert "📈" in body
    assert "**" not in body


def test_compare_table():
    ids = [lp.id for lp in LAUNCHPADS[:3]]
    lps = [lp for lp in LAUNCHPADS if lp.id in ids]
    body = compare_launchpads_markdown(lps, lang="fr", holding_context=True)
    assert "⚖️ COMPARAISON" in body
    assert "V=Volume" in body