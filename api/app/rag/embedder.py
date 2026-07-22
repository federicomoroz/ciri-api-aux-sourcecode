"""
Custom ONNX embedder loading from fixed /app/models/ path.

No HF Hub calls at runtime. The Dockerfile copies the model file to
/app/models/model.onnx (prefers INT8 quantized ~30 MB, falls back to FP32 ~120 MB)
and tokenizer.json to /app/models/tokenizer.json.

All heavy imports (onnxruntime, tokenizers) are deferred to first encode()
call so startup RAM stays low. Thread-safe via double-checked locking.
"""

import os
import threading
import numpy as np

_MODEL_ONNX = os.environ.get("EMBEDDER_ONNX_PATH", "/app/models/model.onnx")
_MODEL_TOKENIZER = os.environ.get("EMBEDDER_TOKENIZER_PATH", "/app/models/tokenizer.json")


class FastEmbedder:
    """Loads quantized ONNX model from fixed path. No network calls at runtime."""

    def __init__(self, model_name: str) -> None:
        # model_name kept for API compatibility; actual path comes from env/const
        self._model_name = model_name
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._lock = threading.Lock()

    def _ensure_loaded(self):
        if self._session is None:
            with self._lock:
                if self._session is None:
                    self._load()
        return self._session

    def _load(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1

        self._session = ort.InferenceSession(
            _MODEL_ONNX,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {inp.name for inp in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(_MODEL_TOKENIZER)
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()

    def encode(self, texts: list[str], show_progress_bar: bool = False) -> np.ndarray:
        self._ensure_loaded()
        encodings = self._tokenizer.encode_batch(texts)

        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(None, feed)

        # Mean pooling + L2 normalise (fastembed 0.8+ behaviour for this model)
        token_embeddings = outputs[0]
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        mean_emb = (token_embeddings * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)
        norms = np.linalg.norm(mean_emb, axis=1, keepdims=True)
        return mean_emb / np.maximum(norms, 1e-9)
