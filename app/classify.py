"""
Core classification orchestrator. Runs all five pipelines in sequence for a
single document and returns a ClassificationResult.

Imported by both the CLI and the FastAPI routes.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from app import runtime_settings
from app.models import (
    ClassificationResult,
    ConfidentialityResult,
    DocumentTypeResult,
    ImportanceResult,
    IndustryResult,
    PainPoint,
)
from app.pipelines.classifier import classify_document_type, classify_industry
from app.pipelines.pain_points import detect_pain_points
from app.pipelines.confidentiality import classify_confidentiality
from app.pipelines.importance import classify_importance
from app.pipelines.summarize import summarize_document

logger = logging.getLogger(__name__)


def classify_document(path: Path, text: str, industry: str | None = None) -> ClassificationResult:
    """
    Run the full classification pipeline on a single parsed document.

    Dependency order:
      Parallel: doc_type, industry, pain_points, confidentiality, summary
      Sequential after above: importance (needs pain_points + confidentiality)
    """
    filename = path.name

    # Run independent pipelines in parallel
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_doc_type     = pool.submit(classify_document_type, text)
        f_industry     = None if industry else pool.submit(classify_industry, text)
        f_pain_points  = pool.submit(detect_pain_points, text, industry or "general")
        f_confidential = pool.submit(classify_confidentiality, text)
        f_summary      = pool.submit(summarize_document, text)

        doc_type_label, doc_type_prob = f_doc_type.result()
        pain_point_dicts = f_pain_points.result()
        confidentiality_dict = f_confidential.result()
        summary = f_summary.result()

        if f_industry is not None:
            industry_labels, industry_probs = f_industry.result()
            industry_result = IndustryResult(
                label=industry_labels[0],
                probability=round(industry_probs[0], 4),
                needs_review=industry_probs[0] < runtime_settings.get_float("industry_review_threshold"),
                user_provided=False,
            )
        else:
            industry_result = IndustryResult(
                label=industry,
                probability=1.0,
                needs_review=False,
                user_provided=True,
            )

    doc_type_needs_review = doc_type_prob < runtime_settings.get_float("doc_type_review_threshold")

    logger.debug(f"{filename}: doc_type={doc_type_label} ({doc_type_prob:.2f})")
    logger.debug(f"{filename}: pain_points={[p['label'] for p in pain_point_dicts]}")
    logger.debug(f"{filename}: confidentiality={confidentiality_dict['label']} (confidence={confidentiality_dict['confidence']:.2f})")

    # Importance depends on pain_points + confidentiality
    importance_dict = classify_importance(
        text,
        pain_points=pain_point_dicts,
        confidentiality_label=confidentiality_dict["label"],
    )
    logger.debug(f"{filename}: importance={importance_dict['label']} (confidence={importance_dict['confidence']:.2f})")

    return ClassificationResult(
        filename=filename,
        document_type=DocumentTypeResult(
            label=doc_type_label,
            probability=round(doc_type_prob, 4),
            needs_review=doc_type_needs_review,
        ),
        industry=industry_result,
        pain_points=[
            PainPoint(
                label=p["label"],
                context=p.get("context", ""),
                question=p.get("question", ""),
                category=p.get("category", ""),
                similarity_score=p.get("similarity_score", 1.0),
            )
            for p in pain_point_dicts
        ],
        confidentiality=ConfidentialityResult(
            label=confidentiality_dict["label"],
            rationale=confidentiality_dict["rationale"],
            confidence=round(confidentiality_dict["confidence"], 4),
            needs_review=confidentiality_dict["needs_review"],
        ),
        importance_level=ImportanceResult(
            label=importance_dict["label"],
            rationale=importance_dict["rationale"],
            confidence=round(importance_dict["confidence"], 4),
            needs_review=importance_dict["needs_review"],
        ),
        summary=summary or None,
        classified_at=datetime.now(timezone.utc),
    )
