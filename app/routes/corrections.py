"""
Human-in-the-loop label corrections.

When the classifier gets doc_type or industry wrong, a consultant corrects it
here. Corrections are stored *alongside* the model's prediction (in
ClassificationRecord.user_doc_type / user_industry) — the original prediction is
never overwritten, so we keep a clean audit trail of model-vs-human and can
measure real-world accuracy later.

scripts/export_corrections.py turns these corrections into training data, so the
local classifiers gradually learn the firm's own document universe.

Included by app.main via:
    from app.routes import corrections
    app.include_router(corrections.router)
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import ClassificationRecord, get_session
from app.pipelines.classifier import get_doc_type_classifier, get_industry_classifier

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Label vocabulary
# ---------------------------------------------------------------------------

def _doc_type_labels() -> list[str]:
    """All document-type labels the model knows, sorted."""
    return sorted(map(str, get_doc_type_classifier().encoder.classes_))


def _industry_labels() -> list[str]:
    """All industry labels the model knows, sorted."""
    return sorted(map(str, get_industry_classifier().encoder.classes_))


@router.get("/labels")
def get_labels():
    """
    Label vocabulary for the correction UI.

    Sourced from the trained LabelEncoders rather than a hardcoded list, so the
    dropdowns automatically stay in sync with whatever the model was trained on.
    """
    return {
        "doc_types": _doc_type_labels(),
        "industries": _industry_labels(),
    }


# ---------------------------------------------------------------------------
# Correction endpoint
# ---------------------------------------------------------------------------

class LabelCorrection(BaseModel):
    doc_type: Optional[str] = None
    industry: Optional[str] = None


@router.patch("/results/{record_id}/labels")
def correct_labels(record_id: int, body: LabelCorrection):
    """
    Record a human correction for a classification result.

    Only the fields present in the body are written; omitting a field leaves any
    existing correction for that dimension untouched. Submitting the *same*
    value the model predicted is allowed and meaningful — it's a human
    confirmation, which is just as useful for retraining as a disagreement.
    """
    if body.doc_type is None and body.industry is None:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of: doc_type, industry.",
        )

    # Validate against the model vocabulary before touching the DB, so a bad
    # request can never leave a partially-applied correction behind.
    if body.doc_type is not None:
        valid = _doc_type_labels()
        if body.doc_type not in valid:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown doc_type '{body.doc_type}'. Valid values: {', '.join(valid)}",
            )

    if body.industry is not None:
        valid = _industry_labels()
        if body.industry not in valid:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown industry '{body.industry}'. Valid values: {', '.join(valid)}",
            )

    with get_session() as session:
        record = session.get(ClassificationRecord, record_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Result {record_id} not found.")

        if body.doc_type is not None:
            record.user_doc_type = body.doc_type
        if body.industry is not None:
            record.user_industry = body.industry

        session.commit()
        session.refresh(record)

        logger.info(
            "Label correction on record %s: doc_type %s -> %s, industry %s -> %s",
            record_id, record.doc_type_label, record.user_doc_type,
            record.industry, record.user_industry,
        )

        return {
            "id": record.id,
            # The model's original predictions — deliberately preserved.
            "doc_type": record.doc_type_label,
            "industry": record.industry,
            "user_doc_type": record.user_doc_type,
            "user_industry": record.user_industry,
        }
