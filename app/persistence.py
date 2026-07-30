"""
Persistence helpers shared by the API routes and the folder watcher.

Moved out of app.main so background workers (app/watcher.py) can persist
classification results without importing the FastAPI app (circular import).
"""

from app.database import ClassificationRecord
from app.pipelines.chunks import store_chunks


def record_to_dict(r: ClassificationRecord) -> dict:
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
        "user_doc_type": r.user_doc_type,
        "user_industry": r.user_industry,
        "error": r.error,
    }


def persist_record(result, session, text: str | None = None) -> ClassificationRecord:
    record = ClassificationRecord(
        filename=result.filename,
        classified_at=result.classified_at,
        doc_text=text,
        doc_type_label=result.document_type.label if result.document_type else None,
        doc_type_probability=result.document_type.probability if result.document_type else None,
        doc_type_needs_review=result.document_type.needs_review if result.document_type else False,
        industry=result.industry.label if result.industry else None,
        industry_probability=result.industry.probability if result.industry else None,
        industry_needs_review=result.industry.needs_review if result.industry else False,
        industry_user_provided=result.industry.user_provided if result.industry else False,
        pain_points=[
            {
                "label": p.label,
                "context": p.context,
                "question": p.question,
                "category": p.category,
                "similarity_score": p.similarity_score,
            }
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
    if text:
        session.flush()  # assigns record.id, needed for chunk FK
        store_chunks(session, record.id, text)
    return record
