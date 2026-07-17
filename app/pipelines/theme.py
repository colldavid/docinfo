"""
Portfolio theme synthesis — Haiku identifies cross-document themes given all
document summaries in a portfolio. Returns a small set of distinct, headlined
bullet points (stored as JSON) so the UI can render them cleanly.
"""
import json
import logging
import anthropic
from app.config import settings

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
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        bullet_list = "\n".join(f"- {s}" for s in summaries if s)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Document summaries:\n{bullet_list}"}],
        )
        raw = response.content[0].text.strip()
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
    except Exception as e:
        logger.warning(f"Theme synthesis failed: {e}")
        return ""
