"""
Document summarization pipeline.
Generates a 2-3 sentence executive summary using Claude Haiku.
"""
import logging
import anthropic
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a document analyst at a consulting firm. Write a 2-3 sentence executive summary \
of the document below. Be specific and factual — name the company, topic, and key finding \
or action if present. Do not mention confidentiality or classification. Plain prose only, \
no bullet points, no markdown.\
"""


def summarize_document(text: str) -> str:
    """Returns a 2-3 sentence plain-text summary, or empty string on failure."""
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Document text (truncated):\n{text[:3000]}"}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Summarization failed: {e}")
        return ""
