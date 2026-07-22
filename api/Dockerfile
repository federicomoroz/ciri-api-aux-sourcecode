FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Pre-download embedding model into /app/models (inside the Docker layer, not ~/.cache)
# FASTEMBED_CACHE_PATH tells fastembed where to store/find model files at runtime too
ENV FASTEMBED_CACHE_PATH=/app/models
# v3 — cache bust: forces re-download to /app/models (previous cache was in ~/.cache)
RUN python -c "from fastembed import TextEmbedding; list(TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2').embed(['warmup'])); print('Model cached at:', __import__(\"os\").environ[\"FASTEMBED_CACHE_PATH\"])"

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
