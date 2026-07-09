"""
Portfolio theme synthesis — Haiku generates a 2-3 sentence cross-doc insight
given all document summaries in a portfolio.
"""
import logging
import anthropic
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a senior consultant synthesizing insights across multiple client documents. \
Given a list of document summaries from a single engagement or batch, write 2-3 sentences \
identifying the most important cross-cutting themes, risks, or patterns. Be specific — \
name recurring issues, contradictions, or areas of concentrated risk. Plain prose only.\
"""


def synthesize_theme(summaries: list[str]) -> str:
    if not summaries:
        return ""
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        bullet_list = "\n".join(f"- {s}" for s in summaries if s)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Document summaries:\n{bullet_list}"}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Theme synthesis failed: {e}")
        return ""
