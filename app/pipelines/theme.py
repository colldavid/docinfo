"""
Portfolio theme synthesis — Haiku identifies cross-document themes given all
document summaries in a portfolio. Returns a small set of distinct, headlined
bullet points (stored as JSON) so the UI can render them cleanly.
"""
import json
import logging
from app.pipelines.llm_cache import cached_call
from app.pipelines.llm_client import call_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a senior consultant synthesizing insights across multiple client documents. \
Given a list of document summaries, identify the 3-4 most important cross-cutting themes, \
risks, or patterns. Be specific — name recurring issues, contradictions, or areas of \
concentrated risk, and cite the companies/documents involved.

For each theme provide:
  - "headline": a short 3-6 word title for the theme
  - "detail": one clear sentence explaining it, naming specific documents where relevant

Return ONLY a JSON array of 3-4 objects with those two keys. No markdown, no extra text."""


def synthesize_theme(summaries: list[str]) -> str:
    """Returns a JSON string: [{"headline": str, "detail": str}, ...] or "" on failure."""
    if not summaries:
        return ""
    try:
        bullet_list = "\n".join(f"- {s}" for s in summaries if s)
        # Same set of summaries → identical theme, replayed from cache.
        return cached_call(["theme_v1", bullet_list], lambda: _synthesize(bullet_list))
    except Exception as e:
        logger.warning(f"Theme synthesis failed: {e}")
        return ""


def _synthesize(bullet_list: str) -> str:
    raw = call_llm(
        user_message=f"Document summaries:\n{bullet_list}",
        system=SYSTEM_PROMPT,
        max_tokens=700,
        temperature=0,
    )
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    # Validate it parses as the expected shape; store normalized JSON.
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return ""
    themes = [
        {"headline": str(t.get("headline", "")).strip(), "detail": str(t.get("detail", "")).strip()}
        for t in parsed
        if isinstance(t, dict) and str(t.get("detail", "")).strip()
    ]
    return json.dumps(themes)
