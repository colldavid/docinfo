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

Category assignment (portfolio rollups) is CLASSICAL ML, not LLM: each extracted
pain point is embedded locally and matched to the nearest category prototype by
cosine similarity. Deterministic, instant, free — the LLM never chooses buckets,
so portfolio percentages cannot drift between runs.

Determinism: the whole extraction is cached content-addressed (see llm_cache) —
re-classifying an unchanged document replays the identical result.
"""

import json
import logging

import numpy as np

from app.pipelines.embeddings import embed, embed_one
from app.pipelines.llm_cache import cached_call
from app.pipelines.llm_client import call_llm

logger = logging.getLogger(__name__)

# Coarse buckets for portfolio-level rollup only. Kept small on purpose.
# Each maps to a prototype description; extracted pain points are assigned to
# the nearest prototype by local embedding similarity.
CATEGORY_PROTOTYPES: dict[str, str] = {
    "People & Talent": (
        "employee retention and turnover, hiring and recruiting, compensation and "
        "incentives, career development and promotion paths, partnership track and "
        "career progression, staffing shortages, workforce skills and training, "
        "morale and burnout"
    ),
    "Operations & Process": (
        "operational inefficiency, manual or fragmented processes, capacity planning, "
        "production and throughput, quality control, supply chain and procurement "
        "execution, logistics and fulfillment, maintenance"
    ),
    "Financial & Cost": (
        "cash flow and working capital, cost structure and margins, budgeting and "
        "forecasting, debt and covenants, liquidity, pricing of inputs, financial "
        "planning discipline"
    ),
    "Growth & Commercial": (
        "sales pipeline discipline, customer churn and retention, pricing strategy, "
        "go-to-market, market expansion, client concentration, distribution channels, "
        "product-market fit"
    ),
    "Data & Technology": (
        "legacy systems and technology debt, poor data visibility and reporting, "
        "system integration and sprawl, cybersecurity and data governance, IT "
        "infrastructure, platform migrations"
    ),
    "Risk & Compliance": (
        "regulatory compliance and licensing, legal exposure, audit findings, risk "
        "controls, vendor and supplier concentration, safety incidents, "
        "contractual obligations"
    ),
    "Strategy & Governance": (
        "strategic direction and priorities, governance and decision rights, unclear "
        "ownership, organizational alignment, M&A integration, board oversight, "
        "change management"
    ),
}

BROAD_CATEGORIES = list(CATEGORY_PROTOTYPES)

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

Document:
{text}

Return ONLY a JSON array of objects with those three keys. If nothing structural exists, return [].
Example:
[
  {{
    "label": "no defined partnership track for associates",
    "context": "Exit interviews cite limited clarity on partnership track; first promotions in 14 months.",
    "question": "What would a transparent, milestone-based promotion framework look like here?"
  }}
]"""

# Prototype embedding matrix — computed once per process, order matches BROAD_CATEGORIES.
_prototype_matrix: np.ndarray | None = None


def _prototypes() -> np.ndarray:
    global _prototype_matrix
    if _prototype_matrix is None:
        _prototype_matrix = embed([CATEGORY_PROTOTYPES[c] for c in BROAD_CATEGORIES])
    return _prototype_matrix


def _assign_category(label: str, context: str) -> str:
    """
    Nearest-prototype classification of one pain point. Local embeddings only —
    deterministic for identical text, no API involved.
    """
    vec = embed_one(f"{label}. {context}".strip())
    protos = _prototypes()
    protos_norm = protos / (np.linalg.norm(protos, axis=1, keepdims=True) + 1e-10)
    vec_norm = vec / (np.linalg.norm(vec) + 1e-10)
    scores = protos_norm @ vec_norm
    return BROAD_CATEGORIES[int(np.argmax(scores))]


def _cap_for_length(text: str) -> int:
    """Floor of 2 pain points, +1 per additional ~500 words, capped at 8."""
    words = len(text.split())
    cap = 2 + max(0, (words - 500) // 500)
    return min(cap, 8)


def _extract_pain_points(text: str) -> list[dict]:
    n = _cap_for_length(text)
    doc_snippet = text[:6000]
    prompt = EXTRACT_PROMPT.format(n=n, text=doc_snippet)

    raw = call_llm(user_message=prompt, max_tokens=1200)
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []

    out = []
    for item in parsed[:n]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        context = str(item.get("context", "")).strip()
        out.append({
            "label": label,
            "context": context,
            "question": str(item.get("question", "")).strip(),
            # Bucketing is local ML, not an LLM choice — see module docstring.
            "category": _assign_category(label, context),
            "similarity_score": 1.0,
        })
    return out


def detect_pain_points(
    text: str,
    industry: str = "",
    threshold: float | None = None,
) -> list[dict]:
    try:
        points = cached_call(
            "pain_points", EXTRACT_PROMPT, [text[:6000]],
            lambda: _extract_pain_points(text),
        )
        logger.debug(f"Pain points ({len(points)}): {[p['label'] for p in points]}")
        return points
    except Exception as e:
        logger.error(f"Pain point extraction failed: {e}")
        return []
