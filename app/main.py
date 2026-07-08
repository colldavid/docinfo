"""
FastAPI backend for DocInfo.

Endpoints:
  POST /classify          Upload a file, run full pipeline, persist + return result
  GET  /results           List all past classification results (newest first)
  GET  /results/{id}      Fetch one result by ID
  GET  /health            Liveness check
"""
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.classify import classify_document
from app.database import ClassificationRecord, get_session, init_db
from app.ingestion import parse_document

logger = logging.getLogger(__name__)

app = FastAPI(
    title="DocInfo",
    description="Document intelligence and classification API",
    version="0.2.0",
)


@app.on_event("startup")
def startup():
    init_db()


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
        } if r.doc_type_label else None,
        "industry": r.industry,
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

    result = classify_document(tmp_path.with_name(file.filename), text, industry=industry)

    # Persist
    with get_session() as session:
        record = ClassificationRecord(
            filename=result.filename,
            classified_at=result.classified_at,
            doc_type_label=result.document_type.label if result.document_type else None,
            doc_type_probability=result.document_type.probability if result.document_type else None,
            industry=result.industry,
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
            error=result.error,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return JSONResponse(status_code=201, content=_record_to_dict(record))


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


@app.get("/results/{result_id}")
def get_result(result_id: int):
    """Fetch a single classification result by ID."""
    with get_session() as session:
        record = session.get(ClassificationRecord, result_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Result {result_id} not found.")
        return _record_to_dict(record)
