# DocInfo — Outstanding Tasks & Wants

## Must do before the tool is usable

- [ ] Run `python data/edgar_download.py` to pull real SEC EDGAR training data
- [ ] Run `python train.py` to train the document_type and industry classifiers on real data
- [ ] Commit the trained model files in `model/` to the repo

## Eval & calibration

- [ ] Generate real sample documents (use the Claude prompt from our conversation) and add them to `documents/samples/`
- [ ] Build a labeled eval set for confidentiality and importance — these can't come from EDGAR (which only provides filing type and SIC code). Needs real documents your team has reviewed and judged. Document type and industry eval sets are covered automatically by the EDGAR data.
- [ ] Create a hand-labeled pain point set for threshold calibration: classify real docs, have a team member note which pain points they would have flagged, add those to the `pain_points` field in `eval/labeled_set.jsonl`, then run `--mode threshold-calibration` to find the optimal similarity threshold
- [ ] Run the Sonnet-as-judge labeling script (to be built) on the sample documents to generate confidentiality and importance ground truth labels automatically — these will be Sonnet-generated, not human-validated. Note in eval set that labels are model-generated; replace with real human-reviewed labels when the team has bandwidth.
- [ ] Build `eval/generate_labels.py` — a script that sends each document in the eval set to Sonnet with a neutral prompt and writes back confidentiality and importance labels to `eval/labeled_set.jsonl`
- [ ] Add `eval/README.md` explaining the labeled set format, how to run each eval mode, and what the metrics mean

## Polish & reliability

- [ ] Fix relative paths in the code so the CLI works from any directory, not just the project root
- [ ] Suppress or fix the pydantic `model_dir` namespace warning on startup
- [ ] Add a `--version` flag to the CLI

## Phase 2 — Web API

- [ ] Implement FastAPI routes: `POST /classify`, `GET /results`, `GET /results/{id}`
- [ ] Add SQLAlchemy models for storing run metadata and results
- [ ] Wire document upload through the API (not just local folder path)

## Phase 3 — Web UI

- [ ] Build Streamlit UI over the Phase 2 API
- [ ] Decide: stay on Streamlit or migrate to React when the tool goes external-facing

## Phase 4 — AWS hosting

- [ ] Set up AWS ECR for Docker image storage
- [ ] Set up ECS Fargate for container hosting
- [ ] Set up RDS PostgreSQL (swap `DATABASE_URL` from SQLite)
- [ ] Set up S3 for document upload/storage (replace local folder input)
- [ ] Configure ALB for load balancing
- [ ] Set up AWS Secrets Manager for `ANTHROPIC_API_KEY` and `DATABASE_URL`
