from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class DocumentTypeResult(BaseModel):
    label: str
    probability: float  # real classifier output, not LLM self-report


# Industry is user-provided (CLI --industry flag or web UI dropdown), not classified.
# Kept as a plain optional string on ClassificationResult.


class PainPoint(BaseModel):
    label: str
    similarity_score: float


class ConfidentialityResult(BaseModel):
    label: str  # "public" | "sensitive" | "restricted"
    rationale: str
    confidence: float  # LLM self-report (0-1)
    needs_review: bool


class ImportanceResult(BaseModel):
    label: str  # "low" | "medium" | "high"
    rationale: str
    confidence: float  # LLM self-report (0-1), not a calibrated probability
    needs_review: bool


class ClassificationResult(BaseModel):
    filename: str
    document_type: Optional[DocumentTypeResult] = None
    industry: Optional[str] = None  # user-provided, not classified
    pain_points: list[PainPoint] = []
    confidentiality: Optional[ConfidentialityResult] = None
    importance_level: Optional[ImportanceResult] = None
    classified_at: datetime
    error: Optional[str] = None  # set if parsing or classification failed
