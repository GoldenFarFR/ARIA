"""aria_core.directives — doctrine statique (directives.md) + overlay operator.md
existant, injectées dans le contexte LLM d'ARIA via get_directives_text().

Aucun test ne couvrait ce module avant le 10/07 (trou de couverture qui a laissé
passer un bug réel : voir test_live_directives_survive_when_builtin_exceeds_limit).
La commande d'écriture live (/directive, append_directive) a depuis été retirée
(jamais utilisée en pratique) ; ces tests écrivent directement dans operator.md
pour simuler tout contenu déjà existant en prod avant ce retrait.
"""
from __future__ import annotations

from aria_core import directives


def _write_live_entry(tmp_path, monkeypatch, text: str, *, builtin: str | None = None):
    monkeypatch.setattr(directives, "data_dir", lambda: tmp_path)
    if builtin is not None:
        monkeypatch.setattr(directives, "_BUILTIN_DIRECTIVES", tmp_path / "builtin.md")
        directives._BUILTIN_DIRECTIVES.write_text(builtin, encoding="utf-8")
    path = directives.operator_directives_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n\n## [2026-07-10 12:00 UTC]\n{text}\n")


def test_existing_live_entry_is_read(tmp_path, monkeypatch):
    _write_live_entry(tmp_path, monkeypatch, "teste une regle")
    text = directives.get_directives_text()
    assert "teste une regle" in text


def test_live_directives_survive_when_builtin_exceeds_limit(tmp_path, monkeypatch):
    # Bug reel (10/07) : directives.md (le contenu "builtin") a grossi au-dela de la
    # limite par defaut -> [:limit] sur la chaine concatenee coupait AVANT (ou EN
    # PLEIN MILIEU DE) la section "Live operator directives", perdant silencieusement
    # tout contenu live existant.
    _write_live_entry(tmp_path, monkeypatch, "regle vivante critique", builtin="x" * 5000)

    text = directives.get_directives_text(limit=4000)
    assert "regle vivante critique" in text
    assert len(text) <= 4000


def test_get_directives_text_respects_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(directives, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(directives, "_BUILTIN_DIRECTIVES", tmp_path / "builtin.md")
    directives._BUILTIN_DIRECTIVES.write_text("y" * 10_000, encoding="utf-8")
    text = directives.get_directives_text(limit=500)
    assert len(text) <= 500


def test_no_live_directives_returns_only_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(directives, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(directives, "_BUILTIN_DIRECTIVES", tmp_path / "builtin.md")
    directives._BUILTIN_DIRECTIVES.write_text("doctrine de base", encoding="utf-8")
    text = directives.get_directives_text()
    assert "doctrine de base" in text
    assert "Live operator directives" not in text


def test_append_directive_removed():
    # La commande d'ecriture live (/directive) est retiree (10/07) : jamais utilisee
    # en pratique, doublon du vrai flux (Claude Code edite directives.md, revu/teste).
    assert not hasattr(directives, "append_directive")
