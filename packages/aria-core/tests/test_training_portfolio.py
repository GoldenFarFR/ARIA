from aria_core.skills.training_skill import wants_training
from aria_core.training_portfolio import get_balance, read_portfolio_text


def test_wants_training():
    assert wants_training("analyse le portefeuille fictif") is True
    assert wants_training("bonjour") is False


def test_portfolio_file_exists():
    text = read_portfolio_text()
    assert "portefeuille" in text.lower()
    assert get_balance() > 0