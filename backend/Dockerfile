FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
# Install CPU-only torch first, from PyTorch's own CPU wheel index. Without
# this, pip resolves sentence-transformers' torch dependency to the default
# GPU/CUDA build (several GB of nvidia-* packages this backend has no use
# for — it only runs small-model CPU inference for the chat agent).
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install --no-cache-dir -r requirements.txt

# Separate layer/file from requirements.txt on purpose — see requirements-dev.txt's
# own comment: keeps the pytest suite's deps from invalidating the torch layer above.
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY main.py .
COPY conftest.py .
COPY chat/ ./chat/
COPY tests/ ./tests/
COPY evals/ ./evals/
COPY pytest.ini .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
