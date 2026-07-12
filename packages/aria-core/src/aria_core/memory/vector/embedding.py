"""Embeddings texte -> vecteur, local (ONNX via fastembed) — aucun appel réseau à l'exécution.

Modèle téléchargé une seule fois (cache local, ~130 Mo) au premier usage réel —
jamais au chargement du module (lazy), pour ne pas pénaliser un boot où le flag
vecteur est désactivé (cas par défaut).
"""
from __future__ import annotations

from typing import Any

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

_model: Any = None


def embedding_installed() -> bool:
    try:
        import fastembed  # noqa: F401

        return True
    except ImportError:
        return False


def _get_model() -> Any:
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name=_MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    """Vecteur (``EMBEDDING_DIM`` dims) pour un texte.

    Lève si fastembed est absent — l'appelant filtre déjà via ``embedding_installed()``
    avant d'atteindre ce point (même contrat que ``lancedb_installed()``).
    """
    model = _get_model()
    vec = next(iter(model.embed([text])))
    return [float(x) for x in vec]


def reset_model_cache() -> None:
    """Tests uniquement — réinitialise le singleton."""
    global _model
    _model = None
