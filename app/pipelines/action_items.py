"""
On-demand action item generation for a single pain point.

Called when a consultant clicks "more" on a pain point in the UI. Given the pain
point and its document context, Haiku suggests concrete next steps a consultant
could take — framed as engagement actions, not generic advice.
"""

import json
import logging

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

PROMPT = """\
You are a senior consultant. A client document surfaced this structural pain point:

Pain point: {label}
Context from document: {context}

Suggest 3 concrete action items a consulting team could take to address it. Each should be \
a specific, doable step — a diagnostic to run, a framework to apply, a stakeholder to align, \
or a capability to build. Avoid generic filler ("improve communication"). Ground them in the \
pain point above.

Return ONLY a JSON array of 3 short action-item strings (one sentence each). No numbering."""


def suggest_action_items(label: str, context: str) -> list[str]:
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": PROMPT.format(label=label, context=context or "(none provided)")}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return [str(p).strip() for p in parsed if str(p).strip()][:3]
    except Exception as e:
        logger.error(f"Action item generation failed: {e}")
        return []
