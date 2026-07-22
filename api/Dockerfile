FROM python:3.11-slim

WORKDIR /app

# System deps — curl added for model download (busts v8 cached layer)
RUN apt-get update && apt-get install -y --no-install-recommends gcc curl && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Download ONNX model + tokenizer via curl (-fL follows LFS redirects; fails on HTTP 4xx/5xx).
# INT8 quantized (118 MB) fits in Render 512 MB; FP32 model.onnx = 470 MB → OOM.
# Tries 3 quantized variants; build FAILS if all fail (no silent empty model).
# v9 — curl replaces Python requests; apt-get change busts all downstream cache layers.
RUN mkdir -p /app/models && \
    BASE="https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/main" && \
    DEST=/app/models/model.onnx && \
    ( curl -fL --retry 3 --connect-timeout 30 --max-time 300 -o "$DEST" "$BASE/onnx/model_quint8_avx2.onnx" || \
      (rm -f "$DEST" && curl -fL --retry 3 --connect-timeout 30 --max-time 300 -o "$DEST" "$BASE/onnx/model_qint8_avx512.onnx") || \
      (rm -f "$DEST" && curl -fL --retry 3 --connect-timeout 30 --max-time 300 -o "$DEST" "$BASE/onnx/model_O4.onnx") ) && \
    SIZE=$(wc -c < "$DEST") && [ "$SIZE" -gt 50000000 ] && \
    echo "ONNX ready: $(( SIZE / 1024 / 1024 )) MB" && \
    curl -fL --retry 3 --connect-timeout 30 --max-time 120 -o /app/models/tokenizer.json "$BASE/tokenizer.json" && \
    echo "Build complete:" && ls -lh /app/models/

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
