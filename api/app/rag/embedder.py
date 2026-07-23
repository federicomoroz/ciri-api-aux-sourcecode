"""
Voyage AI embedder.

voyage-multilingual-2: 1024 dims, native Spanish/multilingual, no local model.
Same encode() interface as the previous ONNX embedder — all callers unchanged.
Thread-safe via double-checked locking.
"""

import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)


class FastEmbedder:
    """Voyage AI embedder. Drop-in replacement for the previous ONNX-based embedder."""

    def __init__(self, model_name: str, api_key: str = "") -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._client = None
        self._lock = threading.Lock()

    def _ensure_loaded(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import voyageai
                    key = self._api_key or os.environ.get("CB_VOYAGE_API_KEY", "")
                    if not key:
                        raise RuntimeError(
                            "CB_VOYAGE_API_KEY is required. "
                            "Get a free key at https://dash.voyageai.com/"
                        )
                    self._client = voyageai.Client(api_key=key)
                    logger.info("Voyage AI client initialized with model=%s", self._model_name)
        return self._client

    def encode(self, texts: list[str], show_progress_bar: bool = False) -> np.ndarray:
        client = self._ensure_loaded()
        try:
            result = client.embed(texts, model=self._model_name)
            return np.array(result.embeddings, dtype=np.float32)
        except Exception as e:
            logger.error("Voyage AI embed() failed for %d texts: %s", len(texts), e)
            raise
