FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by pdfplumber and python-docx
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpoppler-cpp-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies — baked into the image at build time.
# No installation step needed at runtime; every container from this image
# already has all packages available.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY cli.py train.py ./
COPY data/sic_to_industry.json ./data/sic_to_industry.json
COPY eval/ ./eval/

# Pre-trained model files (committed to repo, baked into image)
# If model/ does not exist at build time, the image won't have classifiers —
# run train.py first, commit model/, then rebuild.
COPY model/ ./model/

# Secrets are never baked in — inject ANTHROPIC_API_KEY and DATABASE_URL
# at runtime via environment variables or .env.local volume mount.

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
