"""
FastEmbedder — drop-in replacement for SentenceTransformer using fastembed.

Uses ONNX Runtime instead of PyTorch: ~80MB RAM vs ~450MB.
Same model (paraphrase-multilingual-MiniLM-L12-v2), same 384-dim vectors,
same .encode(texts) interface.
"""

import numpy as np
from fastembed import TextEmbedding


class FastEmbedder:
    """Wraps fastembed.TextEmbedding with a SentenceTransformer-compatible API."""

    def __init__(self, model_name: str) -> None:
        # fastembed expects "sentence-transformers/..." prefix for ST models
        fastembed_name = (
            f"sentence-transformers/{model_name}"
            if "/" not in model_name
            else model_name
        )
        self._model = TextEmbedding(fastembed_name)

    def encode(self, texts: list[str], show_progress_bar: bool = False) -> np.ndarray:
        return np.array(list(self._model.embed(texts)))
