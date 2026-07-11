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

    def _rescale(self, proba: np.ndarray) -> np.ndarray:
        """
        Rescale raw LR probabilities to [0, 1] relative confidence.

        Raw LR probabilities are diluted by the number of classes — with 16
        industry classes, even a correct high-confidence prediction may have
        raw prob ~0.25. We rescale so that uniform random (1/n) maps to 0
        and perfect certainty maps to 1, giving an intuitive confidence signal.

        Formula: (p - 1/n) / (1 - 1/n), clipped to [0, 1].
        """
        n = len(proba)
        floor = 1.0 / n
        rescaled = (proba - floor) / (1.0 - floor)
        return np.clip(rescaled, 0.0, 1.0)

    def predict(self, text: str) -> tuple[str, float]:
        """
        Returns (top_label, rescaled_confidence) for the highest-confidence class.
        Confidence is rescaled so 0 = random guess, 1 = certain.
        """
        vec = embed_one(text).reshape(1, -1)
        proba = self.model.predict_proba(vec)[0]
        rescaled = self._rescale(proba)
        top_idx = int(np.argmax(proba))
        label = self.encoder.inverse_transform([top_idx])[0]
        return label, float(rescaled[top_idx])

    def predict_multi(
        self, text: str, threshold: float = 0.1
    ) -> tuple[list[str], list[float]]:
        """
        Multi-label variant: return all labels with rescaled confidence >= threshold.
        Always returns at least the top label.
        """
        vec = embed_one(text).reshape(1, -1)
        proba = self.model.predict_proba(vec)[0]
        rescaled = self._rescale(proba)
        pairs = sorted(enumerate(rescaled), key=lambda x: x[1], reverse=True)

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
