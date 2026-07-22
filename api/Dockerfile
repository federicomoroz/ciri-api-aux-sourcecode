FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Download ONNX model + tokenizer to fixed path /app/models/ (no HF Hub at runtime).
# model.onnx FP32 = 470 MB → OOM on Render 512 MB free tier.
# INT8 quantized variants = 118 MB → ~130 MB ONNX session → fits in 512 MB.
# Correct quantized filenames (model_quantized.onnx does NOT exist for this model):
#   model_quint8_avx2.onnx   (118 MB, x86_64 AVX2 — broadest server compatibility)
#   model_qint8_avx512.onnx  (118 MB, x86_64 AVX512 — fallback)
# v7
RUN python -c "
import os, shutil
from huggingface_hub import hf_hub_download

os.makedirs('/app/models', exist_ok=True)
model_id = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'

onnx_dst = '/app/models/model.onnx'
candidates = [
    'onnx/model_quint8_avx2.onnx',   # INT8 quant, AVX2, 118 MB
    'onnx/model_qint8_avx512.onnx',  # INT8 quant, AVX512, 118 MB
    'onnx/model_qint8_arm64.onnx',   # INT8 quant, ARM64, 118 MB
    'onnx/model_O4.onnx',            # FP32 optimised, 235 MB (last resort)
]
for filename in candidates:
    try:
        src = hf_hub_download(model_id, filename, local_files_only=False)
        shutil.copy(src, onnx_dst)
        size_mb = os.path.getsize(onnx_dst) // 1024 // 1024
        print(f'OK {filename} -> /app/models/model.onnx ({size_mb} MB)')
        break
    except Exception as e:
        print(f'SKIP {filename}: {e}')

tok_src = hf_hub_download(model_id, 'tokenizer.json')
shutil.copy(tok_src, '/app/models/tokenizer.json')
print('Tokenizer -> /app/models/tokenizer.json')
"

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
