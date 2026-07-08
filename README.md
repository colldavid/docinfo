# DocInfo

Document intelligence tool for consulting workflows. Point it at a folder of PDFs, DOCX, or TXT files — it classifies each document across five independent dimensions and surfaces aggregated insights.

## Classification dimensions

| Dimension | Technique | Eval approach |
|---|---|---|
| **Document type** | Embeddings + logistic regression trained on SEC EDGAR filings | Accuracy against EDGAR ground truth labels |
| **Industry/sector** | Embeddings + logistic regression trained on SEC SIC codes | Accuracy against SIC-derived ground truth labels |
| **Pain points** | Haiku generates ~25 industry-specific candidates; document is matched against them via cosine similarity; matches above a tunable threshold are surfaced | No ground truth — threshold is a judgment call tuned by spot-checking whether surfaced matches feel relevant |
| **Confidentiality** | LLM (Haiku) with locked rubric + few-shot examples | Agreement rate vs. Sonnet-as-judge labels |
| **Importance level** | LLM (Haiku) with locked rubric + few-shot examples; takes pain points and confidentiality as explicit inputs | Agreement rate vs. Sonnet-as-judge labels |

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
PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl

# Importance rubric ablation: compare variants
PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl --mode ablation \
    --dimension importance --rubric-variants eval/rubric_v1.txt eval/rubric_v2.txt

# Confidentiality rubric ablation: compare variants
PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl --mode ablation \
    --dimension confidentiality --rubric-variants eval/conf_rubric_v1.txt eval/conf_rubric_v2.txt
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
  "confidentiality_label": "confidential",
  "importance_label": "high"
}
```

- `doc_type_label` and `industry_label`: derived from EDGAR (objective ground truth)
- `pain_points`: optional; if present, surfaced in output but not evaluated against ground truth — there is no ground truth for pain points
- `confidentiality_label` and `importance_label`: Sonnet-as-judge labels, supplemented with hand-labeled examples

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
  "confidentiality": {
    "label": "confidential",
    "rationale": "Non-public financial results including guidance withdrawal and covenant breach risk.",
    "confidence": 0.95,
    "needs_review": false
  },
  "importance_level": {
    "label": "high",
    "rationale": "Confidential document with material revenue decline and actionable liquidity risk requiring immediate response.",
    "confidence": 0.91,
    "needs_review": false
  },
  "classified_at": "2025-01-15T14:32:00Z"
}
```

Field naming is intentional:
- `document_type` and `industry` use `probability` — real calibrated output from the logistic regression classifier
- `confidentiality` and `importance_level` use `confidence` — LLM self-report, not a calibrated probability
- `pain_points` use `similarity_score` — cosine similarity between the document embedding and the candidate embedding

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
│       ├── pain_points.py      # Haiku candidate generation + embedding similarity
│       ├── confidentiality.py  # Haiku rubric + consistency check
│       └── importance.py       # Haiku rubric + consistency check (takes pain points + confidentiality)
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
