FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Copy code before installing (hatchling needs app/ to exist)
COPY api/pyproject.toml .
COPY api/app/ app/

# Install Python dependencies
RUN pip install --no-cache-dir .

# Pre-download embedding model (cached in layer, ~120MB)
RUN python -c "from fastembed import TextEmbedding; list(TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2').embed(['warmup']))"

# Copy data
COPY data/ data/

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
