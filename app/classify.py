"""
Core classification orchestrator. Runs all four pipelines in sequence for a
single document and returns a ClassificationResult.

Imported by both the CLI and the FastAPI routes.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    ClassificationResult,
    DocumentTypeResult,
    ImportanceResult,
    IndustryResult,
    PainPoint,
)
from app.pipelines.classifier import classify_document_type, classify_industry
from app.pipelines.pain_points import detect_pain_points
from app.pipelines.importance import classify_importance

logger = logging.getLogger(__name__)


def classify_document(path: Path, text: str) -> ClassificationResult:
    """
    Run the full classification pipeline on a single parsed document.
    Pipelines run in dependency order:
      1. document_type  (no deps)
      2. industry       (no deps)
      3. pain_points    (needs industry)
      4. importance     (needs pain_points)
    """
    filename = path.name

    # 1. Document type
    doc_type_label, doc_type_prob = classify_document_type(text)
    logger.debug(f"{filename}: doc_type={doc_type_label} ({doc_type_prob:.2f})")

    # 2. Industry
    industry_labels, industry_probs = classify_industry(text)
    primary_industry = industry_labels[0] if industry_labels else "other"
    logger.debug(f"{filename}: industry={industry_labels}")

    # 3. Pain points (uses primary industry for candidate generation)
    pain_point_dicts = detect_pain_points(text, industry=primary_industry)
    logger.debug(f"{filename}: pain_points={[p['label'] for p in pain_point_dicts]}")

    # 4. Importance (pain points are explicit input)
    importance_dict = classify_importance(text, pain_points=pain_point_dicts)
    logger.debug(
        f"{filename}: importance={importance_dict['label']} "
        f"(confidence={importance_dict['confidence']:.2f}, "
        f"needs_review={importance_dict['needs_review']})"
    )

    return ClassificationResult(
        filename=filename,
        document_type=DocumentTypeResult(
            label=doc_type_label,
            probability=round(doc_type_prob, 4),
        ),
        industry=IndustryResult(
            labels=industry_labels,
            probabilities=[round(p, 4) for p in industry_probs],
        ),
        pain_points=[
            PainPoint(label=p["label"], similarity_score=p["similarity_score"])
            for p in pain_point_dicts
        ],
        importance_level=ImportanceResult(
            label=importance_dict["label"],
            rationale=importance_dict["rationale"],
            confidence=round(importance_dict["confidence"], 4),
            needs_review=importance_dict["needs_review"],
        ),
        classified_at=datetime.now(timezone.utc),
    )
