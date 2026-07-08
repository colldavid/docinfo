# DocInfo — Outstanding Tasks

## Completed
- [x] Phase 1: CLI, all 5 classification pipelines, doc_type classifier trained on 1,542 EDGAR filings (97% acc)
- [x] Phase 2: FastAPI routes (POST /classify, GET /results, GET /results/{id}), SQLAlchemy + SQLite persistence
- [x] Phase 3: Streamlit UI (classify, history, needs-review queue)
- [x] Synthetic eval set: 400 docs, 100 per confidentiality level, generated async with Haiku
- [x] Fixed project-root path dependency (config.py), pydantic model_dir warning, industry classifier removed

## Near-term

- [ ] Run eval harness on synthetic set and review confidentiality/importance accuracy — tune rubric if needed
- [ ] Add a hand-labeled pain point set for threshold calibration (have a teammate review a few real docs)
- [ ] Add `--version` flag to CLI

## Phase 4 — AWS hosting (pending API key)

- [ ] ECR: push Docker image
- [ ] ECS Fargate: container hosting
- [ ] RDS PostgreSQL: swap DATABASE_URL from SQLite
- [ ] S3: document upload/storage
- [ ] ALB: load balancing
- [ ] Secrets Manager: ANTHROPIC_API_KEY + DATABASE_URL
