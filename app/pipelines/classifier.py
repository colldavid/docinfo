"""
Logistic regression classifier wrapper for document_type and industry.
Models are loaded from disk (trained once via train.py, committed to repo).
"""
import joblib
import numpy as np
import logging
from pathlib import Path
from app.config import settings
from app.pipelines.embeddings import embed_one

logger = logging.getLogger(__name__)


class LogisticClassifier:
    """
    Wraps a trained sklearn LogisticRegression + LabelEncoder pair.
    Provides predict_proba output mapped back to human-readable labels.
    """

    def __init__(self, model_path: Path, encoder_path: Path):
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model file not found: {model_path}\n"
                "Run `python train.py` to train and save the model first."
            )
        if not encoder_path.exists():
            raise FileNotFoundError(
                f"Label encoder not found: {encoder_path}\n"
                "Run `python train.py` to train and save the model first."
            )
        self.model = joblib.load(model_path)
        self.encoder = joblib.load(encoder_path)

    def predict(self, text: str) -> tuple[str, float]:
        """
        Returns (top_label, probability) for the highest-confidence class.
        probability is a real calibrated probability from the LR classifier.
        """
        vec = embed_one(text).reshape(1, -1)
        proba = self.model.predict_proba(vec)[0]
        top_idx = int(np.argmax(proba))
        label = self.encoder.inverse_transform([top_idx])[0]
        return label, float(proba[top_idx])

    def predict_multi(
        self, text: str, threshold: float = 0.3
    ) -> tuple[list[str], list[float]]:
        """
        Multi-label variant: return all labels with probability >= threshold.
        Used for industry classification (a doc can span multiple sectors).
        Always returns at least the top label even if below threshold.
        """
        vec = embed_one(text).reshape(1, -1)
        proba = self.model.predict_proba(vec)[0]
        pairs = sorted(enumerate(proba), key=lambda x: x[1], reverse=True)

        labels, scores = [], []
        for idx, score in pairs:
            if score >= threshold or not labels:
                labels.append(self.encoder.inverse_transform([idx])[0])
                scores.append(float(score))

        return labels, scores


# Module-level singletons — loaded once per process
_doc_type_classifier: LogisticClassifier | None = None
_industry_classifier: LogisticClassifier | None = None


def get_doc_type_classifier() -> LogisticClassifier:
    global _doc_type_classifier
    if _doc_type_classifier is None:
        _doc_type_classifier = LogisticClassifier(
            model_path=settings.model_dir / "doc_type_model.joblib",
            encoder_path=settings.model_dir / "doc_type_encoder.joblib",
        )
    return _doc_type_classifier


def get_industry_classifier() -> LogisticClassifier:
    global _industry_classifier
    if _industry_classifier is None:
        _industry_classifier = LogisticClassifier(
            model_path=settings.model_dir / "industry_model.joblib",
            encoder_path=settings.model_dir / "industry_encoder.joblib",
        )
    return _industry_classifier


def classify_document_type(text: str) -> tuple[str, float]:
    return get_doc_type_classifier().predict(text)


def classify_industry(text: str) -> tuple[list[str], list[float]]:
    return get_industry_classifier().predict_multi(text)
