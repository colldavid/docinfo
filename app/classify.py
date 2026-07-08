"""
Core classification orchestrator. Runs all five pipelines in sequence for a
single document and returns a ClassificationResult.

Imported by both the CLI and the FastAPI routes.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    ClassificationResult,
    ConfidentialityResult,
    DocumentTypeResult,
    ImportanceResult,
    PainPoint,
)
from app.pipelines.classifier import classify_document_type
from app.pipelines.pain_points import detect_pain_points
from app.pipelines.confidentiality import classify_confidentiality
from app.pipelines.importance import classify_importance

logger = logging.getLogger(__name__)


def classify_document(path: Path, text: str, industry: str | None = None) -> ClassificationResult:
    """
    Run the full classification pipeline on a single parsed document.

    industry: user-provided industry label (e.g. "healthcare", "technology").
              Used for pain point candidate generation. If None, generic
              candidates are used. In the web UI this comes from a dropdown;
              in the CLI it's --industry.

    Pipelines run in dependency order:
      1. document_type    (no deps)
      2. pain_points      (needs industry)
      3. confidentiality  (no deps)
      4. importance       (needs pain_points + confidentiality)
    """
    filename = path.name

    # 1. Document type
    doc_type_label, doc_type_prob = classify_document_type(text)
    logger.debug(f"{filename}: doc_type={doc_type_label} ({doc_type_prob:.2f})")

    # 2. Pain points (uses user-supplied industry for candidate generation)
    pain_point_dicts = detect_pain_points(text, industry=industry or "general")
    logger.debug(f"{filename}: pain_points={[p['label'] for p in pain_point_dicts]}")

    # 3. Confidentiality
    confidentiality_dict = classify_confidentiality(text)
    logger.debug(
        f"{filename}: confidentiality={confidentiality_dict['label']} "
        f"(confidence={confidentiality_dict['confidence']:.2f})"
    )

    # 4. Importance (pain points + confidentiality are explicit inputs)
    importance_dict = classify_importance(
        text,
        pain_points=pain_point_dicts,
        confidentiality_label=confidentiality_dict["label"],
    )
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
        industry=industry,
        pain_points=[
            PainPoint(label=p["label"], similarity_score=p["similarity_score"])
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
        classified_at=datetime.now(timezone.utc),
    )
