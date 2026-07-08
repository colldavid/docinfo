# DocInfo

Document intelligence tool for consulting workflows. Upload a PDF, DOCX, or TXT — it classifies each document across five dimensions and surfaces actionable insights.

## Classification dimensions

| Dimension | Technique | Notes |
|---|---|---|
| **Document type** | Embeddings + logistic regression trained on 1,542 SEC EDGAR filings | 97% accuracy on holdout set |
| **Pain points** | Haiku generates ~25 industry-specific candidates; matched via cosine similarity above a tunable threshold | Industry provided by user via `--industry` flag or UI dropdown |
| **Confidentiality** | Haiku with locked rubric + few-shot examples; consistency check if confidence < 0.7 | Labels: public / internal / confidential / restricted |
| **Importance level** | Haiku with locked rubric; takes pain points + confidentiality as explicit inputs | Labels: low / medium / high |

---

## Quick start (local)

### 1. Prerequisites

- Python 3.12+
- An Anthropic API key

### 2. Clone and configure

```bash
git clone <repo-url>
cd docinfo
cp .env.example .env.local
# Edit .env.local and fill in your ANTHROPIC_API_KEY
```

### 3. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run the Streamlit UI

```bash
PYTHONPATH=. streamlit run ui/app.py
# Open http://localhost:8501
```

### 5. Or use the CLI

```bash
# Classify a folder of documents
PYTHONPATH=. python cli.py classify ./my_documents/

# With industry hint, JSON output, and CSV export
PYTHONPATH=. python cli.py classify ./my_documents/ --industry healthcare --output results.json --csv results.csv
```

### 6. Or use the REST API

```bash
# Start the server
PYTHONPATH=. uvicorn app.main:app --port 8000

# Upload a document
curl -X POST http://localhost:8000/classify \
  -F "file=@report.pdf" \
  -F "industry=finance"

# List past results
curl http://localhost:8000/results

# Interactive API docs
open http://localhost:8000/docs
```

---

## Output format

```json
{
  "filename": "acme_q3_report.pdf",
  "document_type": { "label": "financial_report", "probability": 0.94 },
  "industry": "finance",
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
    "rationale": "Confidential document with material revenue decline and actionable liquidity risk.",
    "confidence": 0.91,
    "needs_review": false
  },
  "classified_at": "2025-01-15T14:32:00Z"
}
```

---

## Architecture

```
docinfo/
├── app/
│   ├── config.py          # Settings (loaded from .env.local)
│   ├── models.py          # Pydantic output schemas
│   ├── ingestion.py       # PDF/DOCX/TXT parsing
│   ├── classify.py        # Pipeline orchestrator
│   ├── database.py        # SQLAlchemy ORM (ClassificationRecord)
│   ├── main.py            # FastAPI routes
│   └── pipelines/
│       ├── embeddings.py       # Sentence-transformer wrapper
│       ├── classifier.py       # Logistic regression wrapper
│       ├── pain_points.py      # Haiku candidate generation + cosine similarity
│       ├── confidentiality.py  # Haiku rubric + consistency check
│       └── importance.py       # Haiku rubric + consistency check
├── ui/
│   └── app.py             # Streamlit UI (classify, history, needs-review)
├── data/
│   ├── edgar_download.py  # EDGAR training data pull script
│   └── sic_to_industry.json
├── eval/
│   ├── harness.py                # Eval harness + ablation runner
│   ├── generate_synthetic.py     # Synthetic eval set generator (async, 12 workers)
│   └── synthetic_labeled.jsonl   # 400 synthetic docs, 100 per confidentiality level
├── model/                 # Trained model files (committed)
├── cache/                 # Per-industry pain point candidate cache
├── cli.py                 # Typer CLI
├── train.py               # Classifier training script
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Configuration

All parameters in `app/config.py`, overridable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `DATABASE_URL` | `sqlite:///local.db` | SQLite locally; swap for PostgreSQL in prod |
| `PAIN_POINT_THRESHOLD` | `0.65` | Cosine similarity cutoff for pain point matching |
| `CONSISTENCY_CHECK_CONFIDENCE_THRESHOLD` | `0.7` | Re-run pipeline below this confidence |
| `CONSISTENCY_CHECK_RUNS` | `3` | Number of re-runs for consistency check |

---

## Eval harness

```bash
# Full eval against the synthetic labeled set
PYTHONPATH=. python eval/harness.py --data eval/synthetic_labeled.jsonl

# Regenerate the synthetic eval set (400 docs, 12 async workers)
PYTHONPATH=. python eval/generate_synthetic.py
```

---

## AWS production path (Phase 4)

Local uses SQLite. Moving to production requires two changes:

1. **Swap the database:** Set `DATABASE_URL=postgresql://...` pointing to RDS. No code changes needed.
2. **Manage secrets via AWS:** Inject `ANTHROPIC_API_KEY` and `DATABASE_URL` as ECS task environment variables or via Secrets Manager. No `.env` files on the server.
