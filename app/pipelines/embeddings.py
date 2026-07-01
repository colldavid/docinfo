"""
Shared embedding utility. Loads the sentence-transformers model once and
caches it in memory for the lifetime of the process.
"""
import numpy as np
from functools import lru_cache
from app.config import settings


@lru_cache(maxsize=1)
def get_embedding_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.embedding_model)


def embed(texts: list[str]) -> np.ndarray:
    """Embed a list of strings. Returns (N, D) float32 array."""
    model = get_embedding_model()
    return model.encode(texts, convert_to_numpy=True, show_progress_bar=False)


def embed_one(text: str) -> np.ndarray:
    """Embed a single string. Returns (D,) float32 array."""
    return embed([text])[0]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors."""
    a_norm = a / (np.linalg.norm(a) + 1e-10)
    b_norm = b / (np.linalg.norm(b) + 1e-10)
    return float(np.dot(a_norm, b_norm))


def cosine_similarity_matrix(query: np.ndarray, corpus: np.ndarray) -> np.ndarray:
    """
    Cosine similarities between one query vector (D,) and a corpus (N, D).
    Returns (N,) array of similarity scores.
    """
    query_norm = query / (np.linalg.norm(query) + 1e-10)
    corpus_norms = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-10)
    return corpus_norms @ query_norm
