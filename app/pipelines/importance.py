"""
Importance level classification pipeline.

Uses Claude Haiku with:
- A locked rubric in the system prompt
- Pain points AND confidentiality label passed explicitly as inputs
- 2-3 few-shot examples per level anchoring how both inputs change ratings
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

HIGH: Actionable pain points AND (material financial content OR confidential/restricted \
classification). Requires both: something operationally urgent AND either financial \
materiality or sensitivity. A document is NOT high importance on financial content \
or sensitivity alone — it must also have actionable pain points.

MEDIUM: Some pain points OR confidential/restricted content, but not both together \
with high-materiality content. Includes: significant content with limited \
actionability, or sensitive content with no detected pain points.

LOW: No pain points AND public/internal confidentiality. Background research, \
administrative, or informational content that requires no consulting response. \
A $5B merger announcement with no pain points is LOW — informational, not actionable.

## FEW-SHOT EXAMPLES

### Example 1 — HIGH (pain points + confidential + material financials)
Document excerpt: "Q3 revenue declined 18% YoY. FDA placed a clinical hold on our \
lead drug candidate pending safety review. Cash runway is 7 months. DRAFT — DO NOT DISTRIBUTE."
Detected pain points: ["FDA approval delays", "liquidity risk", "revenue decline"]
Confidentiality: confidential
Output:
{
  "label": "high",
  "rationale": "Confidential document with material revenue decline and 7-month cash \
runway. FDA clinical hold is a binding regulatory action requiring immediate response. \
Pain points are actionable and document is not yet public.",
  "confidence": 0.94
}

### Example 2 — HIGH (pain points + restricted)
Document excerpt: "Patient cohort analysis shows 23% readmission rate for DRG 470 \
cases. Cost per episode exceeds Medicare reimbursement by $4,200. \
[Contains PHI — restricted access]"
Detected pain points: ["reimbursement rate compression", "readmission risk"]
Confidentiality: restricted
Output:
{
  "label": "high",
  "rationale": "Restricted PHI-containing document with actionable reimbursement \
and readmission pain points requiring operational response. Sensitivity elevates priority.",
  "confidence": 0.91
}

### Example 3 — MEDIUM (pain points, public document)
Document excerpt: "Global semiconductor supply constraints continued to impact \
production volumes in Q2. Management expects normalization by H2 2025."
Detected pain points: ["supply chain delay", "production volume impact"]
Confidentiality: public
Output:
{
  "label": "medium",
  "rationale": "Pain points present and relevant, but document is publicly available \
market context — not client-specific or confidential. Useful for ongoing monitoring.",
  "confidence": 0.83
}

### Example 4 — MEDIUM (confidential, no pain points)
Document excerpt: "Internal pricing model for the Apex account renewal. \
Proposed discount: 18%. Competitor pricing benchmarks attached. INTERNAL ONLY."
Detected pain points: []
Confidentiality: confidential
Output:
{
  "label": "medium",
  "rationale": "Confidential pricing document with competitive intelligence, but no \
actionable pain points detected. Sensitivity warrants attention even without urgent issues.",
  "confidence": 0.81
}

### Example 5 — LOW (no pain points, public)
Document excerpt: "We are pleased to announce the successful completion of our \
$4.2B acquisition of Horizon Analytics. Integration is proceeding on schedule."
Detected pain points: []
Confidentiality: public
Output:
{
  "label": "low",
  "rationale": "Public press release with no detected pain points. Despite material \
transaction size, no operational issues require a consulting response.",
  "confidence": 0.92
}

### Example 6 — LOW (administrative, internal)
Document excerpt: "Welcome to GlobalTech! This guide covers your first two weeks. \
Pick up your laptop from IT. Complete your I-9 with HR."
Detected pain points: []
Confidentiality: internal
Output:
{
  "label": "low",
  "rationale": "Internal onboarding guide with no financial, strategic, or operational \
relevance to consulting engagement.",
  "confidence": 0.97
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
Confidentiality: {confidentiality_label}

Document text (truncated):
{text}
"""


def _build_user_prompt(text: str, pain_points: list[dict], confidentiality_label: str) -> str:
    pain_point_labels = [p["label"] for p in pain_points]
    return USER_PROMPT_TEMPLATE.format(
        pain_points_json=json.dumps(pain_point_labels),
        confidentiality_label=confidentiality_label,
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
    confidentiality_label: str = "internal",
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
    user_message = _build_user_prompt(text, pain_points, confidentiality_label)

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
