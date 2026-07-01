# DocInfo

Document intelligence tool for consulting workflows. Point it at a folder of PDFs, DOCX, or TXT files — it classifies each document across four independent dimensions and surfaces aggregated insights.

## Classification dimensions

| Dimension | Technique | Ground truth |
|---|---|---|
| **Document type** | Embeddings + logistic regression | SEC EDGAR filing types |
| **Industry/sector** | Embeddings + logistic regression | SEC SIC codes → taxonomy |
| **Pain points** | LLM candidate generation + embedding similarity | Threshold calibration (no objective truth) |
| **Importance level** | LLM (Haiku) with locked rubric + few-shot examples | Sonnet-as-judge + hand-labeled spot-check |

---

## Quick start (local, Docker)

### 1. Prerequisites

- Docker Desktop installed and running
- An Anthropic API key

### 2. Clone and configure

```bash
git clone <repo-url>
cd docinfo
cp .env.example .env.local
# Edit .env.local and fill in your ANTHROPIC_API_KEY
```

`.env.local` is gitignored. Never commit it. Each developer maintains their own copy.

### 3. Build and run

```bash
docker compose up
```

The FastAPI server starts at `http://localhost:8000`. The CLI is the primary interface in phase 1.

### 4. Run the CLI inside the container

```bash
# Classify a folder of documents
docker compose run app python cli.py classify /app/documents/

# With JSON output file and CSV export
docker compose run app python cli.py classify /app/documents/ --output results.json --csv results.csv
```

Or run directly (outside Docker, with Python 3.11 and deps installed):

```bash
pip install -r requirements.txt
python cli.py classify ./my_documents/
```

---

## Training the classifiers (one-time setup)

The pre-trained model files in `model/` are committed to the repo — cloning gives you a working classifier with no training step needed.

**Only re-run training** when you expand the EDGAR dataset or add new label categories.

```bash
# Step 1: download EDGAR training data (~30-60 min, rate-limited)
python data/edgar_download.py

# Step 2: train and save model files
python train.py

# Step 3: commit the updated models
git add model/
git commit -m "Retrain classifiers on expanded EDGAR dataset"
git push
```

Teammates pull the new models with `git pull` — no training on their end.

---

## Eval harness

```bash
# Full eval across all dimensions
python eval/harness.py --data eval/labeled_set.jsonl

# Pain point threshold calibration sweep only
python eval/harness.py --data eval/labeled_set.jsonl --mode threshold-calibration

# Importance rubric ablation: compare variants
python eval/harness.py --data eval/labeled_set.jsonl --mode ablation \
    --rubric-variants eval/rubric_v1.txt eval/rubric_v2.txt
```

Report is printed to console and saved to `eval/report_<timestamp>.json`.

### Labeled set format (`eval/labeled_set.jsonl`)

One JSON object per line:

```json
{
  "filepath": "path/to/doc.pdf",
  "doc_type_label": "financial_report",
  "industry_label": "technology",
  "pain_points": ["supply chain delay", "margin compression"],
  "importance_label": "high"
}
```

- `doc_type_label` and `industry_label`: derived from EDGAR (objective ground truth)
- `pain_points`: hand-labeled spot-check set (30-50 docs); used for threshold calibration
- `importance_label`: Sonnet-as-judge labels, supplemented with hand-labeled examples

---

## Output format

Per-document JSON:

```json
{
  "filename": "acme_q3_report.pdf",
  "document_type": { "label": "financial_report", "probability": 0.94 },
  "industry": { "labels": ["finance", "retail"], "probabilities": [0.88, 0.61] },
  "pain_points": [
    { "label": "supply chain delay", "similarity_score": 0.82 },
    { "label": "margin compression", "similarity_score": 0.74 }
  ],
  "importance_level": {
    "label": "high",
    "rationale": "Material revenue decline with actionable liquidity risk and supply chain pain points requiring immediate response",
    "confidence": 0.81,
    "needs_review": false
  },
  "classified_at": "2025-01-15T14:32:00Z"
}
```

Field naming is intentional:
- `document_type` and `industry` use `probability` — real calibrated classifier output
- `importance_level` uses `confidence` — LLM self-report, not a calibrated probability
- `pain_points` use `similarity_score` — cosine similarity from embedding comparison

---

## Architecture

```
docinfo/
├── app/
│   ├── config.py          # Settings, loaded from .env.local
│   ├── models.py          # Pydantic output schemas
│   ├── ingestion.py       # PDF/DOCX/TXT parsing
│   ├── classify.py        # Pipeline orchestrator
│   ├── main.py            # FastAPI app (phase 2 routes TBD)
│   └── pipelines/
│       ├── embeddings.py  # Sentence-transformer wrapper
│       ├── classifier.py  # Logistic regression wrapper
│       ├── pain_points.py # Haiku + embedding similarity
│       └── importance.py  # Haiku rubric + consistency check
├── data/
│   ├── edgar_download.py  # EDGAR data pull script
│   └── sic_to_industry.json  # Editable SIC → taxonomy mapping
├── model/                 # Committed trained model files
│   ├── doc_type_model.joblib
│   ├── doc_type_encoder.joblib
│   ├── industry_model.joblib
│   ├── industry_encoder.joblib
│   └── training_report.json
├── eval/
│   └── harness.py         # Eval harness + ablation runner
├── cache/
│   └── pain_points_cache.json  # Per-industry Haiku candidate cache
├── cli.py                 # Typer CLI entry point
├── train.py               # Classifier training script
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## AWS production path

Local development uses SQLite (`local.db` mounted as a Docker volume). Moving to production on AWS requires two changes:

**1. Swap the database:** Set `DATABASE_URL=postgresql://...` pointing to an RDS instance. The app reads `DATABASE_URL` and works with either SQLite or PostgreSQL with no code changes.

**Why RDS in production:** ECS containers are stateless and disposable — data written inside a container is lost when the container restarts or is replaced. The database must live outside the container as a separate persistent service. This also enables horizontal scaling: multiple container instances behind a load balancer all share one RDS instance cleanly.

**2. Manage secrets via AWS, not .env files:** In production (ECS), inject `ANTHROPIC_API_KEY` and `DATABASE_URL` as ECS environment variables or via AWS Secrets Manager. No `.env` file exists on the server. Clients using a hosted version of this tool never touch environment variables at all — they just use the web UI.

```bash
# Production docker run example (ECS task definition sets these env vars)
docker run \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e DATABASE_URL=$DATABASE_URL \
  docinfo:latest
```

---

## Configuration

All tunable parameters are in `app/config.py` and can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `DATABASE_URL` | `sqlite:///local.db` | SQLite locally, PostgreSQL in prod |
| `PAIN_POINT_THRESHOLD` | `0.65` | Cosine similarity cutoff for pain point matching |
| `CONSISTENCY_CHECK_CONFIDENCE_THRESHOLD` | `0.7` | Trigger consistency check below this |
| `CONSISTENCY_CHECK_RUNS` | `3` | Re-run count for consistency check |
