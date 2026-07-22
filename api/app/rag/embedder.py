"""
Custom ONNX embedder — bypasses fastembed to use INT8 quantized model.

Key constraints on Render free tier (512MB RAM):
  - FP32  model.onnx     = ~120 MB disk → ~180 MB ONNX session → OOM
  - INT8  model_quantized.onnx = ~30 MB disk → ~60 MB ONNX session → safe

All heavy imports (onnxruntime, tokenizers, huggingface_hub) are deferred to
the first encode() call so startup RAM stays low.

Thread-safe: double-checked locking ensures only one thread loads the model
when 6 parallel n8n §2 requests arrive simultaneously.
"""

import threading
import numpy as np

_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Preference order: smallest (lowest RAM) first
_ONNX_CANDIDATES = [
    "onnx/model_quantized.onnx",  # INT8, ~30 MB — preferred
    "onnx/model.onnx",            # FP32, ~120 MB — fallback
]


class FastEmbedder:
    """Thin wrapper around the quantized ONNX model with a SentenceTransformer API.

    Uses onnxruntime + tokenizers directly (no fastembed TextEmbedding) so we can
    select the quantized ONNX file and control pooling explicitly.

    Vectors are produced with mean-pooling + L2-normalisation, matching
    fastembed 0.8+ behaviour for paraphrase-multilingual-MiniLM-L12-v2.
    """

    def __init__(self, model_name: str) -> None:
        self._model_id = (
            f"sentence-transformers/{model_name}"
            if "/" not in model_name
            else model_name
        )
        self._session = None
        self._tokenizer = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self):
        if self._session is None:
            with self._lock:
                if self._session is None:  # double-check after acquiring lock
                    self._load()
        return self._session

    def _load(self) -> None:
        """Download (or find in HF cache) and load quantized ONNX + tokenizer."""
        import onnxruntime as ort
        from tokenizers import Tokenizer
        from huggingface_hub import hf_hub_download

        # Prefer quantized file (HF_HUB_OFFLINE=1 at runtime → uses cache only)
        onnx_path: str | None = None
        for filename in _ONNX_CANDIDATES:
            try:
                onnx_path = hf_hub_download(
                    repo_id=self._model_id, filename=filename
                )
                break
            except Exception:
                continue

        if onnx_path is None:
            raise RuntimeError(
                f"No ONNX model found for {self._model_id}. "
                "Tried: " + ", ".join(_ONNX_CANDIDATES)
            )

        tokenizer_path = hf_hub_download(
            repo_id=self._model_id, filename="tokenizer.json"
        )

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            onnx_path,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        # Inspect actual input names (quantized export may omit token_type_ids)
        self._input_names = {inp.name for inp in self._session.get_inputs()}

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()  # pads to longest text in each batch

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode(self, texts: list[str], show_progress_bar: bool = False) -> np.ndarray:
        """Return L2-normalised mean-pooled embeddings of shape (len(texts), 384)."""
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

        # outputs[0] = last_hidden_state: (batch, seq_len, 384)
        token_embeddings = outputs[0]
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        mean_emb = (token_embeddings * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)

        # L2 normalise
        norms = np.linalg.norm(mean_emb, axis=1, keepdims=True)
        return mean_emb / np.maximum(norms, 1e-9)
