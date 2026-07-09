"""ARIA language — Telegram uses English (admin translates via Telegram)."""

LANG_EN = "en"
LANG_FR = "fr"

_FR_HINTS = (
    "bonjour", "salut", "merci", "comment", "pourquoi", "constru", "optim",
    "créat", "amélior", "déplo", "mémoire", "répertoire", "holding", "filiale",
    "tu ", "je ", "une ", "des ", "dans ", "avec ", "être", "faire",
)


def detect_lang(text: str) -> str:
    lower = text.lower()
    if any(c in text for c in "éèêàùçîôâëïü"):
        return LANG_FR
    if sum(1 for w in _FR_HINTS if w in lower) >= 2:
        return LANG_FR
    return LANG_EN


def portfolio_empty(lang: str) -> str:
    if lang == LANG_EN:
        return "Watchlist is empty — the discovery engine hasn't ranked a candidate yet. Try /watchlist shortly."
    return "Watchlist vide — le moteur de découverte n'a pas encore classé de candidat. Réessaie /watchlist dans un instant."


def portfolio_failed(lang: str) -> str:
    if lang == LANG_EN:
        return "Could not analyze watchlist (rate limit or insufficient data)."
    return "Impossible d'analyser la watchlist (rate limit ou données insuffisantes)."


def portfolio_header(lang: str, count: int, avg: float) -> list[str]:
    if lang == LANG_EN:
        return [
            f"Portfolio analysis — {count} pairs scanned",
            f"Average score: {avg:.1f}/100",
            "",
        ]
    return [
        f"Analyse portefeuille — {count} paires scannées",
        f"Score moyen : {avg:.1f}/100",
        "",
    ]


def portfolio_buy_signals(lang: str, signals: str) -> str:
    if lang == LANG_EN:
        return f"BUY signals: {signals}"
    return f"Signaux ACHAT : {signals}"


def portfolio_sell_warnings(lang: str, warnings: str) -> str:
    if lang == LANG_EN:
        return f"SELL alerts: {warnings}"
    return f"Alertes VENTE : {warnings}"


def portfolio_neutral(lang: str) -> str:
    if lang == LANG_EN:
        return "No strong signal — neutral market on watchlist."
    return "Pas de signal fort — marché neutre sur la watchlist."