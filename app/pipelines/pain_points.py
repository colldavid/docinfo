"""
Pain point detection pipeline.

Flow:
  1. Given detected industry, load or generate ~20-30 pain point candidates via Haiku.
     Candidates are cached in cache/pain_points_cache.json keyed by industry.
  2. Embed both candidates and the document text.
  3. Match by cosine similarity >= threshold (default 0.65, configurable).
  4. Return matched pain points with similarity scores.

Design note: The LLM generates contextually appropriate vocabulary per industry;
embeddings handle the matching. Clean split of responsibilities.
Pain points have no objective ground truth — eval is threshold calibration,
not accuracy. See eval/README for calibration methodology.
"""

import json
import logging
from pathlib import Path

import anthropic

from app.config import settings
from app.pipelines.embeddings import embed, cosine_similarity_matrix

logger = logging.getLogger(__name__)

CACHE_FILE = settings.cache_dir / "pain_points_cache.json"

CANDIDATE_GENERATION_PROMPT = """\
You are a consulting analyst. List {n} specific, concrete pain points that \
commonly appear in documents related to the {industry} industry.

Pain points should be:
- Short phrases (2-5 words), not full sentences
- Specific enough to be distinctive (e.g. "reimbursement rate compression" \
not just "financial pressure")
- Operationally relevant — things a consulting team would flag as requiring action

Return ONLY a JSON array of strings. No explanation, no numbering, no extra text.
Example format: ["regulatory approval delays", "margin compression", "supply chain disruption"]
"""


def _load_cache() -> dict[str, list[str]]:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load pain point cache: {e}")
    return {}


def _save_cache(cache: dict[str, list[str]]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def _generate_candidates(industry: str, n: int = 25) -> list[str]:
    """Call Haiku to generate pain point candidates for an industry."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = CANDIDATE_GENERATION_PROMPT.format(industry=industry, n=n)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # Parse JSON array — strip any markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    candidates = json.loads(raw)

    if not isinstance(candidates, list):
        raise ValueError(f"Expected list from Haiku, got: {type(candidates)}")

    return [str(c).strip() for c in candidates if str(c).strip()]


def get_pain_point_candidates(industry: str) -> list[str]:
    """
    Return pain point candidates for an industry, using cache if available.
    Cache is a flat JSON file at cache/pain_points_cache.json.
    """
    cache = _load_cache()
    if industry in cache:
        logger.debug(f"Pain point cache hit for industry: {industry}")
        return cache[industry]

    logger.info(f"Generating pain point candidates for industry: {industry} (Haiku call)")
    candidates = _generate_candidates(industry)
    cache[industry] = candidates
    _save_cache(cache)
    logger.info(f"Cached {len(candidates)} candidates for '{industry}'")
    return candidates


def detect_pain_points(
    text: str,
    industry: str,
    threshold: float | None = None,
) -> list[dict]:
    """
    Match pain point candidates against document text via cosine similarity.

    Returns list of dicts: [{"label": str, "similarity_score": float}, ...]
    sorted by similarity score descending.

    threshold defaults to settings.pain_point_threshold (0.65).
    """
    if threshold is None:
        threshold = settings.pain_point_threshold

    candidates = get_pain_point_candidates(industry)
    if not candidates:
        logger.warning(f"No pain point candidates for industry '{industry}'")
        return []

    # Embed document text (truncate to ~2000 chars for speed — signal is dense)
    doc_snippet = text[:2000]
    all_texts = [doc_snippet] + candidates
    embeddings = embed(all_texts)

    doc_vec = embeddings[0]
    candidate_vecs = embeddings[1:]

    similarities = cosine_similarity_matrix(doc_vec, candidate_vecs)

    results = []
    for candidate, score in zip(candidates, similarities):
        if score >= threshold:
            results.append({"label": candidate, "similarity_score": round(float(score), 4)})

    results.sort(key=lambda x: x["similarity_score"], reverse=True)
    return results
