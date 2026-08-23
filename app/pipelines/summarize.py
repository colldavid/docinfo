"""
Document summarization pipeline.
Generates a 2-3 sentence executive summary using Claude Haiku.
"""
import logging
from app.pipelines.llm_cache import cached_call
from app.pipelines.llm_client import call_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a document analyst at a consulting firm. Write exactly 2-3 complete sentences \
summarizing the document below. Be specific and factual — name the company, topic, and \
key finding or action if present. Every sentence must be complete; never cut off mid-sentence. \
Do not mention confidentiality or classification. Plain prose only, no bullet points, no markdown. \
Maximum 60 words.\
"""


def summarize_document(text: str) -> str:
    """Returns a 2-3 sentence plain-text summary, or empty string on failure."""
    try:
        # Same document text → identical summary, replayed from cache.
        return cached_call(
            "summarize", SYSTEM_PROMPT, [text[:3000]],
            lambda: call_llm(
                user_message=f"Document text (truncated):\n{text[:3000]}",
                system=SYSTEM_PROMPT,
                max_tokens=300,
                temperature=0,
            ),
        )
    except Exception as e:
        logger.warning(f"Summarization failed: {e}")
        return ""
