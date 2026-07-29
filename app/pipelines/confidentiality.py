"""
Confidentiality classification pipeline.

Uses Claude Haiku with:
- A locked rubric in the system prompt
- Temperature 0, structured JSON output, schema validation + retries
- Automatic consistency check when confidence < threshold

Levels (ordered by sensitivity):
  public     — already public or intended for external distribution
  sensitive  — non-public, internal or confidential business content (merged from internal + confidential)
  restricted — PII, legally privileged, HIPAA/regulatory, trade secrets
"""

import json
import logging
from typing import Any

from app.config import settings
from app.pipelines.llm_cache import cached_call
from app.pipelines.llm_client import call_llm

logger = logging.getLogger(__name__)

CONFIDENTIALITY_LEVELS = {"public", "sensitive", "restricted"}

SYSTEM_PROMPT = """\
You are a document security analyst at a consulting firm. Classify the \
confidentiality level of a document using the rubric and examples below.

## RUBRIC

PUBLIC: Already publicly available or explicitly intended for external distribution \
(e.g. press releases, published market research, public SEC filings, product brochures). \
Anyone outside the organization can read this without issue.

SENSITIVE: Any non-public document — from routine internal memos to highly confidential \
client data. Use this for anything not intended for external audiences: meeting notes, \
strategy decks, onboarding guides, non-public financials, M&A analysis, pricing models, \
client lists, internal KPIs, competitive intelligence. The key test: would you hesitate \
to share this with someone outside the organization? If yes, it is SENSITIVE.

RESTRICTED: Contains PII, legally privileged content, HIPAA/regulatory-covered data, \
or trade secrets — serious legal, financial, or personal consequences if disclosed \
(e.g. patient records, attorney-client communications, employee SSNs or compensation, \
proprietary formulas, pre-announcement material non-public information). \
IMPORTANT: Non-public financial results (earnings drafts, revenue figures, guidance) \
are SENSITIVE, not RESTRICTED — financial data requires a specific legal trigger \
(e.g. pre-announcement MNPI with insider trading risk, or regulatory filing with \
personal data) to qualify as RESTRICTED.

## FEW-SHOT EXAMPLES

### Example 1 — PUBLIC
Document excerpt: "FOR IMMEDIATE RELEASE. Acme Corp today announced Q3 earnings of \
$1.2B, up 12% YoY. CEO Jane Smith commented: 'We are pleased with our results.'"
Output:
{
  "label": "public",
  "rationale": "Press release explicitly marked for immediate external release.",
  "confidence": 0.98
}

### Example 2 — PUBLIC
Document excerpt: "European Renewable Energy Market — Sector Overview 2024. \
This report is published by GreenData Research and is available for purchase \
on our website at greendata.com/reports."
Output:
{
  "label": "public",
  "rationale": "Published third-party research report available for external purchase.",
  "confidence": 0.96
}

### Example 3 — SENSITIVE (routine internal)
Document excerpt: "Q4 All-Hands Agenda. Topics: team updates, holiday schedule, \
Q3 retrospective. This communication is for internal distribution only."
Output:
{
  "label": "sensitive",
  "rationale": "Internal meeting agenda not intended for external distribution. \
Not a public document, even though content is not highly sensitive.",
  "confidence": 0.91
}

### Example 4 — SENSITIVE (operational KPIs)
Document excerpt: "Q3 Operations Update — Internal Distribution. Customer satisfaction \
scores improved to 87%. On-time delivery held at 94%. Hiring in the East region \
remains 2 positions behind plan."
Output:
{
  "label": "sensitive",
  "rationale": "Internal operational update with non-public KPIs. Not intended for \
external audiences.",
  "confidence": 0.90
}

### Example 5 — SENSITIVE (non-public financials)
Document excerpt: "ACME Corporation Q3 2024 Earnings Report. Revenue declined 18% \
to $412M. We are withdrawing full-year guidance. Covenant breach risk identified. \
DRAFT — DO NOT DISTRIBUTE."
Output:
{
  "label": "sensitive",
  "rationale": "Non-public draft financial results with material disclosures. Marked \
do not distribute — not yet released to public markets.",
  "confidence": 0.95
}

### Example 6 — SENSITIVE (competitive intelligence)
Document excerpt: "Competitive analysis: pricing comparison vs. our top 5 competitors. \
Our unit cost advantage is 23%. Proposed Q1 pricing strategy enclosed. \
INTERNAL USE ONLY."
Output:
{
  "label": "sensitive",
  "rationale": "Proprietary pricing strategy and competitive cost data not intended \
for external disclosure.",
  "confidence": 0.93
}

### Example 7 — RESTRICTED (PHI)
Document excerpt: "Patient ID 447821. Diagnosis: Stage III pancreatic carcinoma. \
Treatment protocol: gemcitabine 1000mg/m2 weekly. Insurance ID: BC8847291."
Output:
{
  "label": "restricted",
  "rationale": "Contains identifiable patient health information — HIPAA-covered PHI. \
Disclosure carries serious legal consequences.",
  "confidence": 0.99
}

### Example 8 — RESTRICTED (attorney-client privilege)
Document excerpt: "PRIVILEGED AND CONFIDENTIAL — ATTORNEY-CLIENT COMMUNICATION. \
Re: potential liability exposure from the Meridian class action. Our counsel's \
assessment of settlement range: $45M–$80M."
Output:
{
  "label": "restricted",
  "rationale": "Attorney-client privileged communication containing litigation \
strategy and settlement valuation. Disclosure could waive privilege.",
  "confidence": 0.98
}

## OUTPUT FORMAT
Respond with ONLY valid JSON. No explanation, no markdown, no extra text.
Schema:
{
  "label": "public" | "sensitive" | "restricted",
  "rationale": "<1-2 sentences>",
  "confidence": <float 0.0-1.0>
}
"""

USER_PROMPT_TEMPLATE = """\
Classify the confidentiality level of the following document.

Document text (truncated):
{text}
"""


def _build_user_prompt(text: str) -> str:
    return USER_PROMPT_TEMPLATE.format(text=text[:3000])


def _call_llm(
    user_message: str,
    temperature: float = 0,
) -> dict[str, Any]:
    raw = call_llm(
        user_message=user_message,
        system=SYSTEM_PROMPT,
        max_tokens=256,
        temperature=temperature,
    )

    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        raw = raw.rsplit("```", 1)[0].strip()

    parsed = json.loads(raw)

    if parsed.get("label") not in CONFIDENTIALITY_LEVELS:
        raise ValueError(f"Invalid confidentiality label: {parsed.get('label')!r}")
    if not isinstance(parsed.get("rationale"), str) or not parsed["rationale"].strip():
        raise ValueError("Missing rationale")
    if not isinstance(parsed.get("confidence"), (int, float)):
        raise ValueError("Missing or invalid confidence")

    parsed["confidence"] = max(0.0, min(1.0, float(parsed["confidence"])))
    return parsed


def _call_with_retry(
    user_message: str,
    temperature: float = 0,
    max_retries: int = 3,
) -> dict[str, Any]:
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return _call_llm(user_message, temperature=temperature)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            logger.warning(f"Confidentiality response failed schema validation (attempt {attempt}): {e}")
    raise RuntimeError(f"Confidentiality classification failed after {max_retries} retries: {last_error}")


def _consistency_check(
    user_message: str,
) -> tuple[str, bool]:
    n = settings.consistency_check_runs
    temp = settings.consistency_check_temperature

    labels = []
    for i in range(n):
        try:
            result = _call_with_retry(user_message, temperature=temp)
            labels.append(result["label"])
        except Exception as e:
            logger.warning(f"Confidentiality consistency check run {i+1} failed: {e}")

    if not labels:
        return "sensitive", True

    majority = max(set(labels), key=labels.count)
    needs_review = len(set(labels)) > 1

    if needs_review:
        logger.info(f"Confidentiality consistency check disagreement: {labels} — flagging for review")

    return majority, needs_review


def classify_confidentiality(text: str) -> dict[str, Any]:
    """
    Classify document confidentiality level.

    Returns dict:
    {
        "label": "public" | "internal" | "confidential" | "restricted",
        "rationale": str,
        "confidence": float,
        "needs_review": bool,
    }
    """
    # Same document → identical result, including any consistency-check outcome.
    return cached_call(
        ["confidentiality_v1", text[:3000]],
        lambda: _classify_uncached(text),
    )


def _classify_uncached(text: str) -> dict[str, Any]:
    user_message = _build_user_prompt(text)

    result = _call_with_retry(user_message, temperature=0)
    confidence = result["confidence"]

    needs_review = confidence < settings.consistency_check_confidence_threshold
    if needs_review:
        logger.info(
            f"Confidentiality confidence {confidence:.2f} below threshold — running consistency check"
        )
        majority_label, disagreed = _consistency_check(user_message)
        needs_review = needs_review or disagreed
        if majority_label != result["label"]:
            logger.info(f"Consistency check overriding confidentiality: {result['label']} → {majority_label}")
            result["label"] = majority_label

    result["needs_review"] = needs_review
    return result
