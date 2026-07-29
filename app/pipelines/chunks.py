"""
Document chunking + chunk-embedding persistence.

Written at classify time (see main.py _persist_record); read by retrieval
features such as portfolio Q&A. Embeddings use the same local
sentence-transformer as classification — no API involved.

Storage format: float32 vector → raw bytes (np.tobytes). Decode with
vector_from_bytes(). Keeping encode/decode in one module so the format
never drifts.
"""

import numpy as np

from app.database import DocumentChunk
from app.pipelines.embeddings import embed

# ~180 words per chunk with 30 words of overlap keeps chunks inside the
# embedding model's effective window while preserving cross-boundary context.
CHUNK_WORDS = 180
OVERLAP_WORDS = 30
MAX_CHUNKS_PER_DOC = 60  # safety cap for pathological inputs


def chunk_text(text: str) -> list[str]:
    """Split text into overlapping word-window chunks."""
    words = text.split()
    if not words:
        return []
    chunks = []
    step = CHUNK_WORDS - OVERLAP_WORDS
    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + CHUNK_WORDS])
        if chunk.strip():
            chunks.append(chunk)
        if len(chunks) >= MAX_CHUNKS_PER_DOC or start + CHUNK_WORDS >= len(words):
            break
    return chunks


def vector_to_bytes(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def vector_from_bytes(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype=np.float32)


def store_chunks(session, record_id: int, text: str) -> int:
    """
    Chunk + embed a document and persist rows for record_id.
    Caller owns the transaction (no commit here). Returns chunk count.
    """
    pieces = chunk_text(text)
    if not pieces:
        return 0
    vectors = embed(pieces)
    for idx, (piece, vec) in enumerate(zip(pieces, vectors)):
        session.add(DocumentChunk(
            record_id=record_id,
            chunk_index=idx,
            text=piece,
            embedding=vector_to_bytes(vec),
        ))
    return len(pieces)
