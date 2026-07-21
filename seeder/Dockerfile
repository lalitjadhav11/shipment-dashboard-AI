FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY 03_generate_phase1_data.py seed.py llm_augment_shipments.py ./

# One-shot container: runs, seeds, exits 0.
CMD ["python", "seed.py"]
