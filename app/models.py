from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class DocumentTypeResult(BaseModel):
    label: str
    probability: float  # real classifier output, not LLM self-report


class IndustryResult(BaseModel):
    labels: list[str]
    probabilities: list[float]  # per-label, parallel to labels


class PainPoint(BaseModel):
    label: str
    similarity_score: float


class ImportanceResult(BaseModel):
    label: str  # "low" | "medium" | "high"
    rationale: str
    confidence: float  # LLM self-report (0-1), not a calibrated probability
    needs_review: bool


class ClassificationResult(BaseModel):
    filename: str
    document_type: DocumentTypeResult
    industry: IndustryResult
    pain_points: list[PainPoint]
    importance_level: ImportanceResult
    classified_at: datetime
    error: Optional[str] = None  # set if parsing or classification failed
