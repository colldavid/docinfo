# ── Stage 1: build React frontend ─────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ── Stage 2: Python backend + embedded frontend ────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY train.py ./
COPY data/sic_to_industry.json ./data/sic_to_industry.json
COPY model/ ./model/

# Embed the built React app — FastAPI serves it from web/dist
COPY --from=frontend-builder /app/web/dist ./web/dist

RUN mkdir -p /app/cache /app/logs

# Mount a volume at /app/data for SQLite persistence across restarts
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
