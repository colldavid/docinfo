"""
Importance level classification pipeline.

Uses Claude Haiku with:
- A locked rubric in the system prompt
- Pain points passed explicitly as input (they directly influence the rating)
- 2-3 few-shot examples per level anchoring how pain points change ratings
- Temperature 0 for determinism, structured JSON output, schema validation + retries
- Automatic consistency check when confidence < threshold:
    Re-run N times at temp 0.4, check agreement. Disagreement → needs_review=True.
"""

import json
import logging
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

IMPORTANCE_LEVELS = {"low", "medium", "high"}

SYSTEM_PROMPT = """\
You are a document analyst for a consulting firm. Classify the importance/priority \
level of a document using the rubric and examples below.

## RUBRIC

HIGH: Material financial figures or binding commitments AND actionable pain points \
detected (e.g. regulatory deadlines, liquidity risk, executive-level decisions \
requiring a response). A document is NOT high importance purely due to material \
financial content — it must also have actionable pain points.

MEDIUM: Market context or supporting analysis with some pain points, OR significant \
content but limited actionability.

LOW: Background research, administrative, introductory, or duplicate content. OR \
significant content with no pain points detected. A $5B merger announcement with \
no detected pain points is LOW — it is informational, not operationally important.

## FEW-SHOT EXAMPLES

### Example 1 — HIGH
Document excerpt: "Q3 revenue declined 18% YoY. FDA placed a clinical hold on our \
lead drug candidate pending safety review. Cash runway is 7 months without additional \
financing."
Detected pain points: ["FDA approval delays", "liquidity risk", "revenue decline"]
Output:
{
  "label": "high",
  "rationale": "Material revenue decline and a 7-month cash runway create urgent \
liquidity risk. FDA clinical hold on the lead asset is a binding regulatory action \
requiring immediate executive response. All three pain points are directly actionable.",
  "confidence": 0.92
}

### Example 2 — MEDIUM
Document excerpt: "Global semiconductor supply constraints continued to impact \
production volumes in Q2. Management expects normalization by H2 2025, though \
the timeline carries uncertainty."
Detected pain points: ["supply chain delay", "production volume impact"]
Output:
{
  "label": "medium",
  "rationale": "Supply chain pain points are present and relevant, but no binding \
commitments or immediate financial risk requiring urgent action. Useful market context \
for ongoing monitoring.",
  "confidence": 0.84
}

### Example 3 — LOW (high financial content, no pain points)
Document excerpt: "We are pleased to announce the successful completion of our \
$4.2B acquisition of Horizon Analytics. The integration is proceeding on schedule \
and we expect synergies to materialize in 18-24 months."
Detected pain points: []
Output:
{
  "label": "low",
  "rationale": "Despite material transaction size, no actionable pain points were \
detected. The announcement is informational — integration is on track and no \
operational issues require a consulting response.",
  "confidence": 0.88
}

### Example 4 — LOW (administrative)
Document excerpt: "This document outlines the agenda for the Q4 all-hands meeting. \
Topics include team updates, holiday schedule, and benefits enrollment reminder."
Detected pain points: []
Output:
{
  "label": "low",
  "rationale": "Administrative content with no financial, strategic, or operational \
relevance to consulting engagement.",
  "confidence": 0.97
}

### Example 5 — MEDIUM (significant content, limited actionability)
Document excerpt: "Industry analysis of the European renewable energy market projects \
15% CAGR through 2030, driven by regulatory tailwinds and declining solar costs. \
Key risks include grid infrastructure bottlenecks and permitting delays."
Detected pain points: ["regulatory risk", "infrastructure bottleneck"]
Output:
{
  "label": "medium",
  "rationale": "Relevant market context with two pain points identified. Pain points \
are structural/sector-level rather than client-specific actionable issues. Useful \
as supporting analysis.",
  "confidence": 0.79
}

## OUTPUT FORMAT
Respond with ONLY valid JSON. No explanation, no markdown, no extra text.
Schema:
{
  "label": "high" | "medium" | "low",
  "rationale": "<1-2 sentences>",
  "confidence": <float 0.0-1.0>
}
"""

USER_PROMPT_TEMPLATE = """\
Classify the following document.

Detected pain points: {pain_points_json}

Document text (truncated):
{text}
"""


def _build_user_prompt(text: str, pain_points: list[dict]) -> str:
    pain_point_labels = [p["label"] for p in pain_points]
    return USER_PROMPT_TEMPLATE.format(
        pain_points_json=json.dumps(pain_point_labels),
        text=text[:3000],
    )


def _call_haiku(
    client: anthropic.Anthropic,
    user_message: str,
    temperature: float = 0,
) -> dict[str, Any]:
    """Single Haiku call. Returns parsed JSON dict or raises on schema failure."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        temperature=temperature,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        raw = raw.rsplit("```", 1)[0].strip()

    parsed = json.loads(raw)

    # Schema validation
    if parsed.get("label") not in IMPORTANCE_LEVELS:
        raise ValueError(f"Invalid label: {parsed.get('label')!r}")
    if not isinstance(parsed.get("rationale"), str) or not parsed["rationale"].strip():
        raise ValueError("Missing rationale")
    if not isinstance(parsed.get("confidence"), (int, float)):
        raise ValueError("Missing or invalid confidence")

    parsed["confidence"] = max(0.0, min(1.0, float(parsed["confidence"])))
    return parsed


def _call_with_retry(
    client: anthropic.Anthropic,
    user_message: str,
    temperature: float = 0,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Call Haiku with retry on schema validation failure."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return _call_haiku(client, user_message, temperature=temperature)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            logger.warning(f"Importance LLM response failed schema validation (attempt {attempt}): {e}")
    raise RuntimeError(f"Importance classification failed after {max_retries} retries: {last_error}")


def _consistency_check(
    client: anthropic.Anthropic,
    user_message: str,
) -> tuple[str, bool]:
    """
    Re-run the classification N times at higher temperature to check stability.
    Returns (majority_label, needs_review).
    needs_review=True if runs disagree (genuinely ambiguous document).
    """
    n = settings.consistency_check_runs
    temp = settings.consistency_check_temperature

    labels = []
    for i in range(n):
        try:
            result = _call_with_retry(client, user_message, temperature=temp)
            labels.append(result["label"])
        except Exception as e:
            logger.warning(f"Consistency check run {i+1} failed: {e}")

    if not labels:
        return "low", True  # Fallback: flag for review

    majority = max(set(labels), key=labels.count)
    all_agree = len(set(labels)) == 1
    needs_review = not all_agree

    if needs_review:
        logger.info(f"Consistency check disagreement: {labels} — flagging for review")

    return majority, needs_review


def classify_importance(
    text: str,
    pain_points: list[dict],
) -> dict[str, Any]:
    """
    Classify document importance level.

    Returns dict matching ImportanceResult schema:
    {
        "label": "high" | "medium" | "low",
        "rationale": str,
        "confidence": float,
        "needs_review": bool,
    }
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    user_message = _build_user_prompt(text, pain_points)

    # Primary classification at temp 0
    result = _call_with_retry(client, user_message, temperature=0)
    confidence = result["confidence"]

    needs_review = False
    if confidence < settings.consistency_check_confidence_threshold:
        logger.info(
            f"Confidence {confidence:.2f} below threshold "
            f"{settings.consistency_check_confidence_threshold} — running consistency check"
        )
        majority_label, needs_review = _consistency_check(client, user_message)
        # If the consistency check produced a different majority, update the label
        # but keep the original rationale (it came from temp=0, most coherent)
        if majority_label != result["label"]:
            logger.info(
                f"Consistency check overriding label: {result['label']} → {majority_label}"
            )
            result["label"] = majority_label

    result["needs_review"] = needs_review
    return result
