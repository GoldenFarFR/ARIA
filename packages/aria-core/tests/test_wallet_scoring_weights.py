"""#157, correction 14/07 (décision opérateur) : `WEIGHTS` doit pouvoir charger
ses vraies valeurs depuis un fichier privé externe (`ARIA_WALLET_SCORING_WEIGHTS_PATH`),
avec repli explicite sur les valeurs par défaut du dataclass si la variable
n'est pas définie ou si le fichier est absent/invalide."""
from __future__ import annotations

import logging

from aria_core.services.wallet_scoring_weights import WalletScoringWeights, _load_weights


class TestLoadWeightsFallback:
    def test_no_env_var_returns_defaults(self, monkeypatch):
        monkeypatch.delenv("ARIA_WALLET_SCORING_WEIGHTS_PATH", raising=False)

        weights = _load_weights()

        assert weights == WalletScoringWeights()

    def test_missing_file_falls_back_to_defaults_and_logs(self, monkeypatch, tmp_path, caplog):
        missing_path = tmp_path / "does_not_exist.yaml"
        monkeypatch.setenv("ARIA_WALLET_SCORING_WEIGHTS_PATH", str(missing_path))

        with caplog.at_level(logging.WARNING):
            weights = _load_weights()

        assert weights == WalletScoringWeights()
        assert "illisible" in caplog.text or "invalide" in caplog.text

    def test_invalid_yaml_falls_back_to_defaults_and_logs(self, monkeypatch, tmp_path, caplog):
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text("max_tokens_analyzed: [unclosed", encoding="utf-8")
        monkeypatch.setenv("ARIA_WALLET_SCORING_WEIGHTS_PATH", str(bad_path))

        with caplog.at_level(logging.WARNING):
            weights = _load_weights()

        assert weights == WalletScoringWeights()
        assert "illisible" in caplog.text or "invalide" in caplog.text


class TestLoadWeightsExternalFile:
    def test_yaml_file_overrides_defaults(self, monkeypatch, tmp_path):
        weights_path = tmp_path / "wallet_scoring_weights.yaml"
        weights_path.write_text(
            "max_tokens_analyzed: 50\n"
            "suspect_win_rate_min: 0.85\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ARIA_WALLET_SCORING_WEIGHTS_PATH", str(weights_path))

        weights = _load_weights()

        assert weights.max_tokens_analyzed == 50
        assert weights.suspect_win_rate_min == 0.85
        # champs non précisés dans le fichier -- toujours les défauts du dataclass
        assert weights.min_closed_trades_for_sortino == WalletScoringWeights().min_closed_trades_for_sortino

    def test_json_file_overrides_defaults(self, monkeypatch, tmp_path):
        # yaml.safe_load lit le JSON nativement (sous-ensemble de YAML) -- même
        # chemin de code, pas de branchement dédié nécessaire.
        weights_path = tmp_path / "wallet_scoring_weights.json"
        weights_path.write_text('{"suspect_positive_min_axes": 4}', encoding="utf-8")
        monkeypatch.setenv("ARIA_WALLET_SCORING_WEIGHTS_PATH", str(weights_path))

        weights = _load_weights()

        assert weights.suspect_positive_min_axes == 4

    def test_unknown_keys_ignored_not_a_crash(self, monkeypatch, tmp_path, caplog):
        weights_path = tmp_path / "weights.yaml"
        weights_path.write_text("not_a_real_field: 123\nmax_tokens_analyzed: 30\n", encoding="utf-8")
        monkeypatch.setenv("ARIA_WALLET_SCORING_WEIGHTS_PATH", str(weights_path))

        with caplog.at_level(logging.WARNING):
            weights = _load_weights()

        assert weights.max_tokens_analyzed == 30
        assert "inconnues" in caplog.text

    def test_non_mapping_file_falls_back_to_defaults(self, monkeypatch, tmp_path, caplog):
        weights_path = tmp_path / "weights.yaml"
        weights_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        monkeypatch.setenv("ARIA_WALLET_SCORING_WEIGHTS_PATH", str(weights_path))

        with caplog.at_level(logging.WARNING):
            weights = _load_weights()

        assert weights == WalletScoringWeights()
