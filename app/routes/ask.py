"""
Portfolio Q&A — retrieval-augmented question answering over a batch of
classified documents.

Flow: embed the question with the same local sentence-transformer used at
classify time, score it against the stored DocumentChunk embeddings for the
portfolio, feed the top-k excerpts to the LLM, and return an answer grounded
in those excerpts with per-document citations.

Retrieval is local (no API call); only the final synthesis step hits the LLM.
"""

import logging

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import ClassificationRecord, DocumentChunk, Portfolio, get_session
from app.pipelines.chunks import vector_from_bytes
from app.pipelines.embeddings import cosine_similarity_matrix, embed_one
from app.pipelines.llm_client import call_llm

logger = logging.getLogger(__name__)

router = APIRouter()

# Retrieval / response shaping
TOP_K = 8            # chunks fed to the LLM (default mode)
TOP_K_DEEP = 20      # "deep" mode — wider context for scattered-answer questions
MAX_SOURCES = 5      # citation chips returned to the UI (deduped by filename)
EXCERPT_CHARS = 220  # excerpt length in the response payload

NO_CHUNKS_DETAIL = "No indexed content — re-classify these documents to enable Q&A."

SYSTEM_PROMPT = (
    "You are a consulting analyst answering questions about a client document "
    "portfolio. Answer ONLY from the excerpts provided — never use outside "
    "knowledge and never speculate. If the excerpts do not contain the answer, "
    "say so plainly and state what is missing. Be concise and specific; prefer "
    "concrete figures and quoted language from the excerpts over generalities.\n\n"
    "Formatting rules — the answer is displayed as PLAIN TEXT, so:\n"
    "- No markdown of any kind: no **bold**, no headers, no numbered markdown lists.\n"
    "- Structure with plain line breaks and simple hyphen bullets ('- ') only.\n"
    "- When drawing on a document, name it once in square brackets, e.g. "
    "[acme_q3.txt], at the start of that section or bullet — do not repeat the "
    "same citation on every line.\n"
    "- Finish every sentence; never end mid-thought."
)


class AskRequest(BaseModel):
    question: str
    deep: bool = False  # widen retrieval from 8 to 20 chunks


@router.post("/portfolios/{portfolio_id}/ask")
def ask_portfolio(portfolio_id: int, body: AskRequest):
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question must not be blank")

    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        # (chunk_text, embedding_bytes, record_id, filename) for every chunk
        # belonging to a document in this portfolio.
        rows = (
            session.query(
                DocumentChunk.text,
                DocumentChunk.embedding,
                ClassificationRecord.id,
                ClassificationRecord.filename,
            )
            .join(ClassificationRecord, DocumentChunk.record_id == ClassificationRecord.id)
            .filter(ClassificationRecord.portfolio_id == portfolio_id)
            .all()
        )

    if not rows:
        # Documents classified before chunk storage existed have no index.
        raise HTTPException(status_code=409, detail=NO_CHUNKS_DETAIL)

    # --- Retrieval (local, no API call) ---------------------------------
    top_k = TOP_K_DEEP if body.deep else TOP_K
    query_vec = embed_one(question)
    corpus = np.stack([vector_from_bytes(r[1]) for r in rows])
    scores = cosine_similarity_matrix(query_vec, corpus)
    top_indices = np.argsort(scores)[::-1][:top_k]
    top_rows = [rows[int(i)] for i in top_indices]

    # --- Synthesis ------------------------------------------------------
    excerpts = "\n\n".join(f"[{r[3]}]\n{r[0]}" for r in top_rows)
    user_message = (
        f"Excerpts from the portfolio:\n\n{excerpts}\n\n"
        f"---\n\nQuestion: {question}"
    )

    try:
        answer = call_llm(
            user_message,
            system=SYSTEM_PROMPT,
            max_tokens=1000,
            temperature=0.0,
        )
    except Exception:
        logger.exception("Portfolio Q&A failed (portfolio_id=%s)", portfolio_id)
        raise HTTPException(status_code=502, detail="Q&A failed")

    # Sources in rank order, one per file (highest-ranked chunk wins).
    sources = []
    seen_filenames = set()
    for text, _embedding, record_id, filename in top_rows:
        if filename in seen_filenames:
            continue
        seen_filenames.add(filename)
        excerpt = text[:EXCERPT_CHARS]
        if len(text) > EXCERPT_CHARS:
            excerpt = excerpt.rstrip() + "…"
        sources.append({
            "record_id": record_id,
            "filename": filename,
            "excerpt": excerpt,
        })
        if len(sources) >= MAX_SOURCES:
            break

    return {"answer": answer, "sources": sources}
