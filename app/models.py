from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class DocumentTypeResult(BaseModel):
    label: str
    probability: float  # real classifier output, not LLM self-report
    needs_review: bool = False


class IndustryResult(BaseModel):
    label: str
    probability: float
    needs_review: bool = False
    user_provided: bool = False


class PainPoint(BaseModel):
    label: str                          # the specific pain point (concise phrase)
    context: str = ""                   # 1 sentence grounding it in the document
    question: str = ""                  # a brainstorming/diligence question to pursue
    category: str = ""                  # broad theme, for portfolio-level aggregation
    similarity_score: float = 1.0       # legacy field, kept for stored-record compat


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
    industry: Optional["IndustryResult"] = None
    pain_points: list[PainPoint] = []
    confidentiality: Optional[ConfidentialityResult] = None
    importance_level: Optional[ImportanceResult] = None
    summary: Optional[str] = None
    classified_at: datetime
    error: Optional[str] = None  # set if parsing or classification failed
