"""
FastEmbedder — drop-in replacement for SentenceTransformer using fastembed.

Uses ONNX Runtime instead of PyTorch: ~80MB RAM vs ~450MB.
Same model (paraphrase-multilingual-MiniLM-L12-v2), same 384-dim vectors,
same .encode(texts) interface.
"""

import numpy as np
from fastembed import TextEmbedding


class FastEmbedder:
    """Wraps fastembed.TextEmbedding with a SentenceTransformer-compatible API.

    Lazy initialization: ONNX model is NOT loaded until first .encode() call.
    This keeps startup RAM ~0MB for the embedder, deferring the ~150MB load
    to the first request (critical for Render free tier 512MB limit).
    """

    def __init__(self, model_name: str) -> None:
        # fastembed expects "sentence-transformers/..." prefix for ST models
        self._model_name = (
            f"sentence-transformers/{model_name}"
            if "/" not in model_name
            else model_name
        )
        self._model: TextEmbedding | None = None  # loaded on first encode()

    def _ensure_loaded(self) -> TextEmbedding:
        if self._model is None:
            self._model = TextEmbedding(self._model_name)
        return self._model

    def encode(self, texts: list[str], show_progress_bar: bool = False) -> np.ndarray:
        return np.array(list(self._ensure_loaded().embed(texts)))
