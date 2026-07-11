"""
Pain point detection pipeline.

Uses Haiku to directly extract pain points from document text. The LLM reads
the actual content and surfaces operational issues a consulting team would flag,
handling implicit signals and industry-specific language naturally.

Industry context is passed so Haiku uses the right vocabulary, but extraction
is grounded in what the document actually says — not pre-cached candidates.
"""

import json
import logging

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are a consulting analyst reviewing a client document. Extract the specific \
operational pain points, risks, or problems present in this document.

Rules:
- Only extract issues that are actually present in the document — do not invent or infer beyond what is stated
- Use concise phrases (3-8 words) that capture the specific issue, not generic terms
- Include both explicit problems ("covenant breach") and implicit signals ("operating below plan for third consecutive quarter")
- Return between 0 and 8 pain points depending on how many genuinely exist
- If the document has no meaningful problems, return an empty array

Industry context: {industry}

Document:
{text}

Return ONLY a JSON array of short phrase strings. No explanation, no numbering.
Example: ["gross margin erosion", "key customer concentration risk", "CFO vacancy during restructuring"]
"""


def _extract_pain_points(text: str, industry: str) -> list[str]:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    doc_snippet = text[:3000]
    prompt = EXTRACTION_PROMPT.format(industry=industry, text=doc_snippet)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected list from Haiku, got: {type(parsed)}")

    return [str(p).strip() for p in parsed if str(p).strip()]


def detect_pain_points(
    text: str,
    industry: str,
    threshold: float | None = None,
) -> list[dict]:
    """
    Extract pain points from document text using Haiku.

    Returns list of dicts: [{"label": str, "similarity_score": float}, ...]
    similarity_score is set to 1.0 for LLM-extracted items (it's a presence
    signal, not a ranked score).
    """
    try:
        pain_points = _extract_pain_points(text, industry)
    except Exception as e:
        logger.error(f"Pain point extraction failed: {e}")
        return []

    return [{"label": p, "similarity_score": 1.0} for p in pain_points]
