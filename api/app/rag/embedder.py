"""
Voyage AI embedder — replaces local ONNX model.

voyage-multilingual-2: 1024 dims, native Spanish/multilingual, no local model.
No Docker build step, no OOM risk, no HuggingFace download issues.

Same encode() interface as the previous ONNX embedder — all callers unchanged.
Thread-safe via double-checked locking.
"""

import os
import threading

import numpy as np

_VOYAGE_API_KEY = os.environ.get("CB_VOYAGE_API_KEY", "")
_VOYAGE_MODEL = os.environ.get("CB_VOYAGE_MODEL", "voyage-multilingual-2")


class FastEmbedder:
    """Voyage AI embedder. Drop-in replacement for the previous ONNX-based embedder."""

    def __init__(self, model_name: str) -> None:
        # model_name kept for API compatibility; actual model comes from env/const
        self._model_name = _VOYAGE_MODEL
        self._client = None
        self._lock = threading.Lock()

    def _ensure_loaded(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import voyageai
                    api_key = _VOYAGE_API_KEY or os.environ.get("CB_VOYAGE_API_KEY", "")
                    if not api_key:
                        raise RuntimeError(
                            "CB_VOYAGE_API_KEY is required. "
                            "Get a free key at https://dash.voyageai.com/"
                        )
                    self._client = voyageai.Client(api_key=api_key)
        return self._client

    def encode(self, texts: list[str], show_progress_bar: bool = False) -> np.ndarray:
        client = self._ensure_loaded()
        result = client.embed(texts, model=self._model_name)
        return np.array(result.embeddings, dtype=np.float32)
