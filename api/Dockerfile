FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Download ONNX model files and copy to /app/models/ (fixed, known path).
# No HF Hub calls at runtime — embedder loads directly from /app/models/.
# Tries quantized (INT8, ~30 MB, ~60 MB session) first; falls back to FP32 (~120 MB).
# v6
RUN python -c "
import os, shutil
from huggingface_hub import hf_hub_download

os.makedirs('/app/models', exist_ok=True)
model_id = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'

onnx_dst = '/app/models/model.onnx'
for filename in ['onnx/model_quantized.onnx', 'onnx/model.onnx']:
    try:
        src = hf_hub_download(model_id, filename, local_files_only=False)
        shutil.copy(src, onnx_dst)
        size_mb = os.path.getsize(onnx_dst) // 1024 // 1024
        print(f'Cached {filename} -> /app/models/model.onnx ({size_mb} MB)')
        break
    except Exception as e:
        print(f'Skip {filename}: {e}')

tok_src = hf_hub_download(model_id, 'tokenizer.json')
shutil.copy(tok_src, '/app/models/tokenizer.json')
print('Tokenizer copied to /app/models/tokenizer.json')
"

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
