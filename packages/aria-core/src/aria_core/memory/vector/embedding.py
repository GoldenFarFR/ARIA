"""Text -> vector embeddings, local (ONNX via fastembed) — no network call at runtime.

Model downloaded only once (local cache, ~130 MB) on first real use — never at
module load time (lazy), so as not to penalize a boot where the vector flag is
disabled (the default case).
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
    """Vector (``EMBEDDING_DIM`` dims) for a piece of text.

    Raises if fastembed is missing — the caller already filters via
    ``embedding_installed()`` before reaching this point (same contract as
    ``lancedb_installed()``).
    """
    model = _get_model()
    vec = next(iter(model.embed([text])))
    return [float(x) for x in vec]


def reset_model_cache() -> None:
    """Tests only — resets the singleton."""
    global _model
    _model = None
