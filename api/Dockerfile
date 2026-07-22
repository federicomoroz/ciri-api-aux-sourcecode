FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Download ONNX model + tokenizer directly via HTTP (no hf_hub_download — avoids
# HF Hub API rate limits and cache resolution failures in Render build).
# INT8 quantized (118 MB) fits in Render 512 MB; FP32 model.onnx = 470 MB → OOM.
# Build FAILS explicitly if download fails (no silent empty model).
# v8
RUN python -c "
import os, requests, sys

os.makedirs('/app/models', exist_ok=True)
BASE = 'https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/main'

def download(url, dst):
    print(f'Downloading {url}...')
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    with open(dst, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            f.write(chunk)
    mb = os.path.getsize(dst) // 1024 // 1024
    print(f'  -> {dst} ({mb} MB)')
    return mb

# Try INT8 quantized first (118 MB), fall back to O4 optimized (235 MB)
onnx_dst = '/app/models/model.onnx'
for onnx_file in ['onnx/model_quint8_avx2.onnx', 'onnx/model_qint8_avx512.onnx', 'onnx/model_O4.onnx']:
    try:
        mb = download(f'{BASE}/{onnx_file}', onnx_dst)
        if mb < 50:
            raise RuntimeError(f'File too small ({mb} MB)')
        print(f'ONNX ready: {onnx_file} ({mb} MB)')
        break
    except Exception as e:
        print(f'FAILED {onnx_file}: {e}')
        if os.path.exists(onnx_dst):
            os.remove(onnx_dst)
else:
    print('ERROR: all ONNX downloads failed', file=sys.stderr)
    sys.exit(1)

download(f'{BASE}/tokenizer.json', '/app/models/tokenizer.json')
print('Build complete.')
"

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
