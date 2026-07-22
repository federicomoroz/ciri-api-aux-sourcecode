FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Pre-download embedding model into /app/hf_cache (inside the Docker image layer).
# HF_HOME controls where huggingface-hub caches model files — fastembed 0.8+ uses
# huggingface-hub's snapshot_download internally, so this is the correct env var.
# FASTEMBED_CACHE_PATH is kept for legacy fastembed path resolution.
# Both env vars persist into the runtime container so the model is FOUND, not re-downloaded.
ENV HF_HOME=/app/hf_cache
ENV FASTEMBED_CACHE_PATH=/app/models
# v4 — HF_HOME replaces ~/.cache/huggingface at build AND runtime
RUN python -c "from fastembed import TextEmbedding; list(TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2').embed(['warmup'])); print('HF cache:', '/app/hf_cache')"

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
