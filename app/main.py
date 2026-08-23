"""
FastAPI backend for DocInfo.

Endpoints:
  POST /classify          Upload a file, run full pipeline, persist + return result
  GET  /results           List all past classification results (newest first)
  GET  /results/{id}      Fetch one result by ID
  GET  /health            Liveness check
"""
import asyncio
import csv
from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import or_

from app.classify import classify_document
from app.database import ClassificationRecord, DocumentChunk, Portfolio, get_session, init_db
from app.ingestion import parse_document
from app.persistence import persist_record as _persist_record, record_to_dict as _record_to_dict
from app.pipelines.action_items import suggest_action_items
from app.pipelines.theme import synthesize_theme
from app.routes.app_settings import router as app_settings_router
from app.routes.ask import router as ask_router
from app.routes.auth import router as auth_router, is_authenticated
from app.routes.contradictions import router as contradictions_router
from app.routes.corrections import router as corrections_router
from app.routes.deliverable import router as deliverable_router
from app.routes.entities import router as entities_router
from app.routes.watch import router as watch_router

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEB_DIST = _PROJECT_ROOT / "web" / "dist"

app = FastAPI(
    title="DocInfo",
    description="Document intelligence and classification API",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Feature routers (app/routes/) — must be registered before the SPA catch-all mount below.
app.include_router(auth_router)
app.include_router(contradictions_router)
app.include_router(ask_router)
app.include_router(corrections_router)
app.include_router(deliverable_router)
app.include_router(entities_router)
app.include_router(app_settings_router)
app.include_router(watch_router)

# API prefixes that require a valid session when auth is enabled. The SPA shell,
# assets, /health, and /auth/login/logout stay open (the login page needs them).
_PROTECTED_PREFIXES = (
    "/classify", "/results", "/portfolios", "/search",
    "/labels", "/pain-points", "/settings", "/watch",
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path.startswith(_PROTECTED_PREFIXES) and not is_authenticated(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    return await call_next(request)


@app.on_event("startup")
def startup():
    init_db()
    # Pre-load the embedding model so concurrent requests don't race to initialize it
    from app.pipelines.embeddings import get_embedding_model
    get_embedding_model()
    # Watched-folder auto-classify (no-op until a folder is configured in Settings)
    from app.watcher import start_watcher
    start_watcher()


# ---------------------------------------------------------------------------
# Routes  (serialization helpers live in app/persistence.py)
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


class ActionItemsBody(BaseModel):
    label: str
    context: str = ""


@app.post("/pain-points/action-items")
def pain_point_action_items(body: ActionItemsBody):
    """Generate concrete consulting action items for a single pain point, on demand."""
    items = suggest_action_items(body.label, body.context)
    return {"action_items": items}


@app.post("/classify", status_code=201)
async def classify(
    file: UploadFile = File(...),
    industry: Optional[str] = Form(None),
):
    """
    Upload a document (PDF, DOCX, or TXT) and run the full classification pipeline.
    Optionally pass `industry` as a form field to improve pain point matching.
    Returns the classification result and persists it to the database.
    """
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {suffix}")

    # Write upload to a temp file so parse_document can read it
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        text = parse_document(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not text or not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from document.")

    result = classify_document(tmp_path.with_name(Path(file.filename).name), text, industry=industry)

    with get_session() as session:
        record = _persist_record(result, session, text=text)
        session.commit()
        session.refresh(record)
        return JSONResponse(status_code=201, content=_record_to_dict(record))


@app.post("/classify/batch", status_code=201)
async def classify_batch(
    files: List[UploadFile] = File(...),
    industry: Optional[str] = Form(None),
):
    """Classify multiple documents concurrently. Returns list of results."""

    async def _classify_one(file: UploadFile) -> dict:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt"}:
            return {"filename": file.filename, "error": f"Unsupported file type: {suffix}"}

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)

        try:
            text = parse_document(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if not text or not text.strip():
            return {"filename": file.filename, "error": "Could not extract text"}

        # file.filename may include folder path (e.g. "samples/doc.txt") when uploading a folder
        bare_name = Path(file.filename).name
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: classify_document(tmp_path.with_name(bare_name), text, industry=industry),
        )
        return result, text

    results = await asyncio.gather(*[_classify_one(f) for f in files], return_exceptions=False)

    output = []
    with get_session() as session:
        for item in results:
            if isinstance(item, dict) and "error" in item:
                output.append(item)
            else:
                result, text = item
                record = _persist_record(result, session, text=text)
                session.flush()
                session.refresh(record)
                output.append(_record_to_dict(record))
        session.commit()

    return JSONResponse(status_code=201, content=output)


@app.get("/results/needs-review")
def list_needs_review(limit: int = 50, offset: int = 0):
    """List results flagged for human review."""
    with get_session() as session:
        records = (
            session.query(ClassificationRecord)
            .filter(
                (ClassificationRecord.confidentiality_needs_review == True)
                | (ClassificationRecord.importance_needs_review == True)
                | (ClassificationRecord.doc_type_needs_review == True)
                | (ClassificationRecord.industry_needs_review == True)
            )
            .order_by(ClassificationRecord.classified_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_record_to_dict(r) for r in records]


@app.get("/results")
def list_results(limit: int = 50, offset: int = 0):
    """List past classification results, newest first."""
    with get_session() as session:
        records = (
            session.query(ClassificationRecord)
            .order_by(ClassificationRecord.classified_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_record_to_dict(r) for r in records]


@app.delete("/results")
def clear_results():
    """
    Delete all classification results AND their stored document content.

    Chunks must be deleted explicitly: SQLite does not enforce the FK cascade
    here, and leaving them behind would keep sensitive document text on disk
    after the user believes it was cleared.
    """
    with get_session() as session:
        chunks_removed = session.query(DocumentChunk).delete()
        session.query(ClassificationRecord).delete()
        session.commit()
    return {"deleted": True, "chunks_removed": chunks_removed}


@app.get("/search")
def search(q: str, limit: int = 50):
    """Search filenames and summaries."""
    with get_session() as session:
        records = (
            session.query(ClassificationRecord)
            .filter(or_(
                ClassificationRecord.filename.ilike(f"%{q}%"),
                ClassificationRecord.summary.ilike(f"%{q}%"),
            ))
            .order_by(ClassificationRecord.classified_at.desc())
            .limit(limit)
            .all()
        )
        return [_record_to_dict(r) for r in records]


@app.get("/results/export")
def export_csv(portfolio_id: Optional[int] = None):
    """Export classification results as CSV. Optionally filter by portfolio."""
    with get_session() as session:
        q = session.query(ClassificationRecord).order_by(ClassificationRecord.classified_at.desc())
        if portfolio_id is not None:
            q = q.filter(ClassificationRecord.portfolio_id == portfolio_id)
        records = q.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "filename", "classified_at", "doc_type", "industry", "industry_source",
        "confidentiality", "confidentiality_confidence",
        "importance", "importance_confidence",
        "needs_review", "pain_points", "summary",
    ])
    for r in records:
        pain_labels = ", ".join(p["label"] for p in json.loads(r.pain_points_json or "[]"))
        needs_review = any([
            r.doc_type_needs_review, r.industry_needs_review,
            r.confidentiality_needs_review, r.importance_needs_review,
        ])
        writer.writerow([
            r.filename,
            r.classified_at.isoformat() if r.classified_at else "",
            r.doc_type_label or "",
            r.industry or "",
            "user" if r.industry_user_provided else "auto",
            r.confidentiality_label or "",
            f"{r.confidentiality_confidence:.2f}" if r.confidentiality_confidence else "",
            r.importance_label or "",
            f"{r.importance_confidence:.2f}" if r.importance_confidence else "",
            "yes" if needs_review else "no",
            pain_labels,
            (r.summary or "").replace("\n", " "),
        ])

    output.seek(0)
    filename = f"docinfo_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/results/{result_id}")
def get_result(result_id: int):
    """Fetch a single classification result by ID."""
    with get_session() as session:
        record = session.get(ClassificationRecord, result_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Result {result_id} not found.")
        return _record_to_dict(record)


# ---------------------------------------------------------------------------
# Portfolio endpoints
# ---------------------------------------------------------------------------

def _portfolio_to_dict(p: Portfolio, include_records: bool = False) -> dict:
    d = {
        "id": p.id,
        "name": p.name,
        "created_at": p.created_at.isoformat(),
        "theme": p.theme,
        "record_count": len(p.records),
    }
    if include_records:
        d["records"] = [_record_to_dict(r) for r in p.records]
    return d


@app.post("/portfolios", status_code=201)
def create_portfolio(body: dict):
    """
    Create a portfolio from a list of classification record IDs.
    Body: { "name": str, "record_ids": [int] }
    """
    name = body.get("name", "").strip()
    record_ids = body.get("record_ids", [])
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if not record_ids:
        raise HTTPException(status_code=422, detail="record_ids must be non-empty")

    with get_session() as session:
        records = session.query(ClassificationRecord).filter(
            ClassificationRecord.id.in_(record_ids)
        ).all()
        if not records:
            raise HTTPException(status_code=404, detail="No matching records found")

        summaries = [r.summary for r in records if r.summary]
        theme = synthesize_theme(summaries)

        portfolio = Portfolio(
            name=name,
            created_at=datetime.now(timezone.utc),
            theme=theme,
        )
        session.add(portfolio)
        session.flush()

        for r in records:
            r.portfolio_id = portfolio.id

        session.commit()
        session.refresh(portfolio)
        portfolio_id = portfolio.id

    _recompute_portfolio_analyses(portfolio_id)

    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        return JSONResponse(status_code=201, content=_portfolio_to_dict(portfolio, include_records=True))


def _recompute_portfolio_analyses(portfolio_id: int) -> None:
    """
    (Re)compute contradictions + entities for a portfolio so the next view is
    instant. Concurrent (each analysis fans out its own LLM calls), each in its
    OWN session — SQLAlchemy sessions are not thread-safe. Non-fatal: the
    endpoints can always compute on demand.

    Shared by portfolio creation and add-documents, which keeps the cached
    analyses in lockstep with membership — the only mutation path recomputes.
    """
    from app.routes.contradictions import compute_and_store as _compute_contradictions
    from app.routes.entities import compute_and_store as _compute_entities

    def _precompute(compute, name: str):
        try:
            with get_session() as s:
                p = s.get(Portfolio, portfolio_id)
                if p:
                    compute(p, s)
        except Exception as e:
            logger.warning(f"{name} precompute failed for portfolio {portfolio_id}: {e}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(_precompute, _compute_contradictions, "Contradiction")
        pool.submit(_precompute, _compute_entities, "Entity")


@app.post("/portfolios/{portfolio_id}/records", status_code=200)
def add_records_to_portfolio(portfolio_id: int, body: dict):
    """
    Add classified documents to an existing portfolio.
    Body: { "record_ids": [int] }

    Per-document analysis is untouched (it belongs to the documents). The three
    portfolio-owned artifacts are refreshed: theme is re-synthesized from the
    new full member list, and contradictions + entities are recomputed.
    """
    record_ids = body.get("record_ids", [])
    if not record_ids:
        raise HTTPException(status_code=422, detail="record_ids must be non-empty")

    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        records = session.query(ClassificationRecord).filter(
            ClassificationRecord.id.in_(record_ids)
        ).all()
        if len(records) != len(set(record_ids)):
            raise HTTPException(status_code=404, detail="One or more records not found")

        claimed = [r.filename for r in records
                   if r.portfolio_id is not None and r.portfolio_id != portfolio_id]
        if claimed:
            raise HTTPException(
                status_code=422,
                detail=f"Already in another portfolio: {', '.join(claimed)}",
            )

        for r in records:
            r.portfolio_id = portfolio_id

        # Theme is derived from member summaries, so membership change = new theme.
        # (Its LLM cache keys on the summary list, so an identical membership
        # would replay rather than re-spend.)
        all_summaries = [r.summary for r in portfolio.records if r.summary]
        portfolio.theme = synthesize_theme(all_summaries)

        session.commit()

    _recompute_portfolio_analyses(portfolio_id)

    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        return _portfolio_to_dict(portfolio, include_records=True)


@app.get("/portfolios")
def list_portfolios():
    with get_session() as session:
        portfolios = (
            session.query(Portfolio)
            .order_by(Portfolio.created_at.desc())
            .all()
        )
        return [_portfolio_to_dict(p) for p in portfolios]


@app.get("/portfolios/{portfolio_id}")
def get_portfolio(portfolio_id: int):
    with get_session() as session:
        p = session.get(Portfolio, portfolio_id)
        if not p:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        return _portfolio_to_dict(p, include_records=True)


@app.delete("/portfolios/{portfolio_id}")
def delete_portfolio(portfolio_id: int):
    with get_session() as session:
        p = session.get(Portfolio, portfolio_id)
        if not p:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        for r in p.records:
            r.portfolio_id = None
        session.delete(p)
        session.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Serve React frontend (must be last — catches all non-API routes)
# ---------------------------------------------------------------------------

if _WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        return FileResponse(_WEB_DIST / "index.html")
