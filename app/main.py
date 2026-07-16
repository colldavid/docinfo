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
import io
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import or_

from app.classify import classify_document
from app.database import ClassificationRecord, Portfolio, get_session, init_db
from app.ingestion import parse_document
from app.pipelines.theme import synthesize_theme

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEB_DIST = _PROJECT_ROOT / "web" / "dist"

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

_COOKIE = "docinfo_session"
_UNPROTECTED = {"/health", "/login", "/logout"}


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret)


def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get(_COOKIE)
    if not token:
        return False
    try:
        _signer().loads(token, max_age=86400 * 7)  # 7-day session
        return True
    except BadSignature:
        return False


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


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Allow unprotected paths and static assets through
    if path in _UNPROTECTED or path.startswith("/assets"):
        return await call_next(request)
    # API routes return 401 when unauthenticated (frontend handles redirect)
    if path.startswith("/") and not _is_authenticated(request):
        if path.startswith("/classify") or path.startswith("/results") or \
           path.startswith("/portfolios") or path.startswith("/search"):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    return await call_next(request)


@app.post("/login")
async def login(request: Request):
    data = await request.json()
    if (data.get("username") == settings.auth_username and
            data.get("password") == settings.auth_password):
        token = _signer().dumps("authenticated")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            _COOKIE, token,
            httponly=True, samesite="lax",
            max_age=86400 * 7,
            secure=False,  # set True behind HTTPS in prod
        )
        return response
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(_COOKIE)
    return response


@app.on_event("startup")
def startup():
    init_db()
    # Pre-load the embedding model so concurrent requests don't race to initialize it
    from app.pipelines.embeddings import get_embedding_model
    get_embedding_model()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _record_to_dict(r: ClassificationRecord) -> dict:
    return {
        "id": r.id,
        "filename": r.filename,
        "classified_at": r.classified_at.isoformat(),
        "document_type": {
            "label": r.doc_type_label,
            "probability": r.doc_type_probability,
            "needs_review": r.doc_type_needs_review,
        } if r.doc_type_label else None,
        "industry": {
            "label": r.industry,
            "probability": r.industry_probability,
            "needs_review": r.industry_needs_review,
            "user_provided": r.industry_user_provided,
        } if r.industry else None,
        "pain_points": r.pain_points,
        "confidentiality": {
            "label": r.confidentiality_label,
            "rationale": r.confidentiality_rationale,
            "confidence": r.confidentiality_confidence,
            "needs_review": r.confidentiality_needs_review,
        } if r.confidentiality_label else None,
        "importance_level": {
            "label": r.importance_label,
            "rationale": r.importance_rationale,
            "confidence": r.importance_confidence,
            "needs_review": r.importance_needs_review,
        } if r.importance_label else None,
        "summary": r.summary,
        "error": r.error,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


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
        record = _persist_record(result, session)
        session.commit()
        session.refresh(record)
        return JSONResponse(status_code=201, content=_record_to_dict(record))


def _persist_record(result, session) -> ClassificationRecord:
    record = ClassificationRecord(
        filename=result.filename,
        classified_at=result.classified_at,
        doc_type_label=result.document_type.label if result.document_type else None,
        doc_type_probability=result.document_type.probability if result.document_type else None,
        doc_type_needs_review=result.document_type.needs_review if result.document_type else False,
        industry=result.industry.label if result.industry else None,
        industry_probability=result.industry.probability if result.industry else None,
        industry_needs_review=result.industry.needs_review if result.industry else False,
        industry_user_provided=result.industry.user_provided if result.industry else False,
        pain_points=[
            {"label": p.label, "similarity_score": p.similarity_score}
            for p in result.pain_points
        ],
        confidentiality_label=result.confidentiality.label if result.confidentiality else None,
        confidentiality_rationale=result.confidentiality.rationale if result.confidentiality else None,
        confidentiality_confidence=result.confidentiality.confidence if result.confidentiality else None,
        confidentiality_needs_review=result.confidentiality.needs_review if result.confidentiality else None,
        importance_label=result.importance_level.label if result.importance_level else None,
        importance_rationale=result.importance_level.rationale if result.importance_level else None,
        importance_confidence=result.importance_level.confidence if result.importance_level else None,
        importance_needs_review=result.importance_level.needs_review if result.importance_level else None,
        summary=result.summary,
        error=result.error,
    )
    session.add(record)
    return record


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
        return result

    results = await asyncio.gather(*[_classify_one(f) for f in files], return_exceptions=False)

    output = []
    with get_session() as session:
        for result in results:
            if isinstance(result, dict) and "error" in result:
                output.append(result)
            else:
                record = _persist_record(result, session)
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
    """Delete all classification results."""
    with get_session() as session:
        session.query(ClassificationRecord).delete()
        session.commit()
    return {"deleted": True}


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
        return JSONResponse(status_code=201, content=_portfolio_to_dict(portfolio, include_records=True))


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
