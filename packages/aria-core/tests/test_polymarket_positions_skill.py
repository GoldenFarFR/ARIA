from __future__ import annotations

from aria_core.skills.polymarket_positions_skill import wants_polymarket_positions


def test_bare_word_polymarket():
    assert wants_polymarket_positions("polymarket") is True


def test_paris_en_cours_phrasing():
    assert wants_polymarket_positions("je veut voir les paris en cours") is True


def test_pari_possible_phrasing():
    assert wants_polymarket_positions("quel est le pari possible ?") is True


def test_case_insensitive():
    assert wants_polymarket_positions("POLYMARKET") is True


def test_unrelated_message_does_not_match():
    assert wants_polymarket_positions("salut, comment ça va ?") is False


def test_bare_paris_without_context_does_not_match():
    """The city of Paris (or "je pars") must never trigger this path -- only
    a phrasing explicitly tied to open/possible bets should."""
    assert wants_polymarket_positions("je vais à Paris demain") is False


def test_empty_message():
    assert wants_polymarket_positions("") is False
