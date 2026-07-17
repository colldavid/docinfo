"""
Pain point detection.

Design (document level):
  - Extract SPECIFIC, distinct pain points — not forced into boxy categories.
    Each has: the issue, one line of context grounding it in the doc, and a
    brainstorming/diligence question a consultant would pursue.
  - The number returned scales with document length: floor of 2, +1 per ~500 words,
    so a dense 10-page report can surface more than a half-page memo.
  - Only STRUCTURAL problems (fixable process/capability/strategy gaps), never bare
    outcomes ("revenue down 18%") or external conditions ("port congestion").
  - Each point is also tagged with a broad CATEGORY, used only for portfolio-level
    aggregation — at the document level we show the specific point, not the category.

The broad categories are intentionally coarse (7 buckets) because they only need to
support percentage rollups across a batch, not describe an individual document.
"""

import json
import logging

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

# Coarse buckets for portfolio-level rollup only. Kept small on purpose.
BROAD_CATEGORIES = [
    "People & Talent",
    "Operations & Process",
    "Financial & Cost",
    "Growth & Commercial",
    "Data & Technology",
    "Risk & Compliance",
    "Strategy & Governance",
]

EXTRACT_PROMPT = """\
You are a senior consultant reviewing a client document. Identify the STRUCTURAL pain \
points — problems the client could actually fix through strategy, process change, or \
capability investment.

A pain point qualifies only if it describes HOW something is broken (a missing process, \
undefined policy, wrong incentive, absent capability, reactive planning). It does NOT \
qualify if it is merely:
  - an outcome or result (revenue down, target missed, margin compressed)
  - an external condition (market softness, port congestion, competitor pricing)
  - a raw statistic with no described root cause (24% turnover, 19% vacancy)

Return the {n} SHARPEST pain points — the most clearly structural and most central to the \
document. If fewer than {n} genuinely qualify, return fewer. Better to return 1 sharp point \
than pad with soft ones. Each must be DISTINCT (do not restate the same issue twice).

For each pain point provide:
  - "label": the specific problem, 4-10 words, concrete to THIS document
  - "context": one sentence quoting or closely paraphrasing the supporting evidence
  - "question": one sharp diligence/brainstorming question a consultant would raise
  - "category": exactly one of {categories}

Document:
{text}

Return ONLY a JSON array of objects with those four keys. If nothing structural exists, return [].
Example:
[
  {{
    "label": "no defined partnership track for associates",
    "context": "Exit interviews cite limited clarity on partnership track; first promotions in 14 months.",
    "question": "What would a transparent, milestone-based promotion framework look like here?",
    "category": "People & Talent"
  }}
]"""


def _cap_for_length(text: str) -> int:
    """Floor of 2 pain points, +1 per additional ~500 words, capped at 8."""
    words = len(text.split())
    cap = 2 + max(0, (words - 500) // 500)
    return min(cap, 8)


def _extract_pain_points(client: anthropic.Anthropic, text: str) -> list[dict]:
    n = _cap_for_length(text)
    doc_snippet = text[:6000]
    prompt = EXTRACT_PROMPT.format(
        n=n,
        categories=", ".join(f'"{c}"' for c in BROAD_CATEGORIES),
        text=doc_snippet,
    )

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []

    valid_categories = set(BROAD_CATEGORIES)
    out = []
    for item in parsed[:n]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        category = str(item.get("category", "")).strip()
        if category not in valid_categories:
            category = "Operations & Process"  # safe default rather than drop
        out.append({
            "label": label,
            "context": str(item.get("context", "")).strip(),
            "question": str(item.get("question", "")).strip(),
            "category": category,
            "similarity_score": 1.0,
        })
    return out


def detect_pain_points(
    text: str,
    industry: str = "",
    threshold: float | None = None,
) -> list[dict]:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        points = _extract_pain_points(client, text)
        logger.debug(f"Pain points ({len(points)}): {[p['label'] for p in points]}")
        return points
    except Exception as e:
        logger.error(f"Pain point extraction failed: {e}")
        return []
