FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# HF_HOME controls where huggingface-hub caches model files at build AND runtime.
# HF_HUB_OFFLINE=1 at runtime prevents any re-download — model must be in the image.
# fastembed is still installed (as dep) but we use onnxruntime + tokenizers directly
# to load the INT8 quantized model (~30 MB) instead of the FP32 model (~120 MB).
ENV HF_HOME=/app/hf_cache
ENV HF_HUB_OFFLINE=1
ENV FASTEMBED_CACHE_PATH=/app/models

# v5: download quantized ONNX + tokenizer into /app/hf_cache (inside the Docker layer).
# Override HF_HUB_OFFLINE for this build step only so the download can happen.
RUN HF_HUB_OFFLINE=0 python -c "
from huggingface_hub import hf_hub_download
import os
model_id = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
# Prefer INT8 quantized (30 MB RAM) over FP32 (120 MB RAM)
for filename in ['onnx/model_quantized.onnx', 'onnx/model.onnx']:
    try:
        p = hf_hub_download(model_id, filename)
        print(f'Cached {filename}: {os.path.getsize(p) // 1024 // 1024} MB at {p}')
        break
    except Exception as e:
        print(f'Skip {filename}: {e}')
tok = hf_hub_download(model_id, 'tokenizer.json')
print(f'Tokenizer: {tok}')
"

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
