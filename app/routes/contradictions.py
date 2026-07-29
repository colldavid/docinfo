"""
Cross-document contradiction detection for a portfolio.

Consultants review batches of client documents that were written by different
teams at different times, so they routinely disagree with each other (a board
deck says the ERP migration finished in Q2; an ops memo says it slips to Q4).
Those disagreements are exactly what a reviewer needs to surface first, and
they are invisible from any single document.

Two-stage design:

  Stage 1 (per document, N calls) — extract a short list of atomic factual
    claims: dates, numbers, commitments, statuses. Reducing each document to
    ~8 claims is what makes stage 2 affordable: comparing raw document text
    pairwise would be O(N^2) in tokens, whereas comparing claim lists is one
    call over a compact digest.

  Stage 2 (one call) — hand the model every claim labeled by filename and ask
    only for tensions BETWEEN DIFFERENT documents. Within-document
    inconsistency is a different (and much noisier) problem, so it is excluded
    in the prompt and again in validation.

Results are cached on Portfolio.contradictions_json because the analysis costs
N+1 LLM calls; `refresh=true` forces recomputation after documents change.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

from app.database import Portfolio, get_session
from app.pipelines.llm_client import call_llm

logger = logging.getLogger(__name__)

router = APIRouter()

# Cap the fan-out: 20 documents is 21 LLM calls, already a slow request. Beyond
# that the stage-2 prompt also stops fitting comfortably in one context window.
MAX_DOCS = 20

# Per-document text budget for claim extraction. Factual commitments cluster in
# the opening of consulting documents (exec summary, status table), so a head
# truncation loses less than it saves.
DOC_CHAR_LIMIT = 4000

MAX_CLAIMS_PER_DOC = 8

CLAIMS_PROMPT = """\
Extract the factual claims from the document below — the statements that could later \
be confirmed or contradicted by another document.

Prioritize:
  - dates and deadlines (milestones, go-live dates, target quarters)
  - numbers (costs, headcount, revenue, percentages, durations)
  - commitments (who will do what, by when)
  - statuses (complete, in progress, delayed, cancelled, approved)

Ignore opinions, recommendations, and generic background. Each claim must be \
self-contained — name the company/organization AND the subject, so \
"Acme Corp's ERP migration completes in Q2 2025" not "completes in Q2 2025". \
Claims that don't name whose fact it is cannot be compared later.

Return AT MOST {n} claims, the most specific and checkable ones.

Document:
{text}

Return ONLY a JSON array of strings. If the document contains no checkable factual \
claims, return [].
Example:
["ERP migration go-live is Q2 2025", "Headcount reduction target is 120 roles", \
"Vendor contract was signed on March 14"]"""

CONTRADICTIONS_PROMPT = """\
Below are factual claims extracted from {n} different documents in one client portfolio, \
each labeled with its source filename.

Find places where DIFFERENT documents disagree with each other. A contradiction \
qualifies when two documents make claims that cannot both be true, or that a reviewer \
would need to reconcile before relying on either:
  - conflicting dates for the same milestone
  - conflicting numbers for the same metric
  - one document calling something complete while another calls it pending
  - commitments that conflict in scope, owner, or timing

Rules:
  - The two claims MUST come from two DIFFERENT filenames. Never pair a document with \
itself, and never re-attribute a claim to a different document than the one it is \
listed under.
  - The two claims MUST be about the SAME company/organization and the same metric, \
milestone, or commitment. Claims about two different companies are NEVER a \
contradiction, no matter how similar the risk or theme.
  - Do NOT report "compounded risks", "related pressures", or thematic similarity — \
only direct factual conflict. If your explanation would need words like "separate \
entities", "different companies", or "both represent", it is not a contradiction.
  - Do NOT report claims that are merely about different topics, different time periods, \
or different scopes. Different is not contradictory.
  - Copy claim_a and claim_b VERBATIM, character for character, from the lists above.
  - If nothing genuinely conflicts, return an empty array. An empty result is a good \
result — do not pad. Portfolios of unrelated companies usually have NO contradictions.

List the most consequential conflicts first.

Claims by document:
{claims}

Return ONLY a JSON array of objects with keys "doc_a", "doc_b", "claim_a", "claim_b", \
"explanation". "doc_a" and "doc_b" must be filenames exactly as written above. \
"explanation" is ONE sentence saying why the two claims conflict.
Example:
[
  {{
    "doc_a": "board_deck_q3.pdf",
    "doc_b": "ops_memo.docx",
    "claim_a": "ERP migration completed in Q2 2025",
    "claim_b": "ERP migration is scheduled to finish in Q4 2025",
    "explanation": "The board deck reports the migration as finished while the ops memo \
still treats it as six months out."
  }}
]"""


def _strip_fences(raw: str) -> str:
    """Unwrap ```json ... ``` fencing the model sometimes adds despite instructions."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    return raw


def _extract_claims(filename: str, text: str) -> list[str]:
    """
    Stage 1: reduce one document to a short list of checkable factual claims.

    A failure here degrades rather than aborts — one unparseable document should
    not cost the reviewer the analysis of the other nineteen, so this returns []
    and the document simply contributes nothing to the comparison.
    """
    prompt = CLAIMS_PROMPT.format(n=MAX_CLAIMS_PER_DOC, text=text[:DOC_CHAR_LIMIT])
    try:
        parsed = json.loads(_strip_fences(call_llm(user_message=prompt, max_tokens=800)))
    except Exception as e:
        logger.warning(f"Claim extraction failed for {filename!r}: {e}")
        return []

    if not isinstance(parsed, list):
        return []

    claims = []
    for item in parsed[:MAX_CLAIMS_PER_DOC]:
        if isinstance(item, str) and item.strip():
            claims.append(item.strip())
    return claims


def _find_contradictions(claims_by_doc: dict[str, list[str]]) -> list[dict]:
    """
    Stage 2: one call comparing every document's claims against every other's.

    Unlike stage 1, a failure here IS the whole result, so it propagates to the
    caller to be surfaced as a 502 rather than silently reported as "no
    contradictions found" — a false all-clear is worse than an error.
    """
    blocks = []
    for filename, claims in claims_by_doc.items():
        bullets = "\n".join(f"  - {c}" for c in claims)
        blocks.append(f"[{filename}]\n{bullets}")

    prompt = CONTRADICTIONS_PROMPT.format(
        n=len(claims_by_doc),
        claims="\n\n".join(blocks),
    )
    parsed = json.loads(_strip_fences(call_llm(user_message=prompt, max_tokens=2000)))

    if not isinstance(parsed, list):
        logger.warning("Cross-doc analysis returned a non-list; treating as no result")
        return []

    return _validate(parsed, claims_by_doc)


def _normalize_claim(claim: str) -> str:
    """Whitespace/case/punctuation-tolerant form for membership checks."""
    return " ".join(claim.lower().split()).rstrip(".")


def _validate(parsed: list, claims_by_doc: dict[str, list[str]]) -> list[dict]:
    """
    Drop anything the model got structurally wrong.

    Filenames are checked against the real record list because a hallucinated
    filename would render in the UI as a citation the reviewer cannot open, and
    doc_a != doc_b enforces the cross-document contract that the whole feature
    is about. Each claim must additionally APPEAR IN the claim list of the
    document it is attributed to — this blocks the failure mode where the model
    re-attributes a within-document conflict to a second file to satisfy the
    cross-document rule. Invalid items are dropped, not repaired: a
    contradiction with a guessed source is not worth showing.
    """
    known_filenames = set(claims_by_doc)
    normalized = {
        fname: {_normalize_claim(c) for c in claims}
        for fname, claims in claims_by_doc.items()
    }

    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue

        doc_a = str(item.get("doc_a", "")).strip()
        doc_b = str(item.get("doc_b", "")).strip()
        if doc_a not in known_filenames or doc_b not in known_filenames:
            logger.debug(f"Dropping contradiction with unknown filename: {doc_a!r} / {doc_b!r}")
            continue
        if doc_a == doc_b:
            logger.debug(f"Dropping within-document contradiction for {doc_a!r}")
            continue

        claim_a = str(item.get("claim_a", "")).strip()
        claim_b = str(item.get("claim_b", "")).strip()
        if not claim_a or not claim_b:
            continue
        if _normalize_claim(claim_a) not in normalized[doc_a] or \
           _normalize_claim(claim_b) not in normalized[doc_b]:
            logger.debug(
                f"Dropping contradiction whose claims don't belong to their "
                f"attributed documents: {doc_a!r} / {doc_b!r}"
            )
            continue

        out.append({
            "doc_a": doc_a,
            "doc_b": doc_b,
            "claim_a": claim_a,
            "claim_b": claim_b,
            "explanation": str(item.get("explanation", "")).strip(),
        })

    # Model is prompted to list the most consequential conflicts first; keep its order.
    return out


@router.post("/portfolios/{portfolio_id}/contradictions")
def analyze_contradictions(portfolio_id: int, refresh: bool = False):
    """
    Detect contradictions between documents in a portfolio.

    Returns the cached analysis unless `refresh=true`. An empty contradictions
    list is a real, cacheable answer ("these documents agree"), not a miss.
    """
    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        records = list(portfolio.records)

        if portfolio.contradictions_json and not refresh:
            try:
                cached = json.loads(portfolio.contradictions_json)
            except json.JSONDecodeError:
                # Corrupt cache shouldn't be a dead end — fall through and recompute.
                logger.warning(f"Corrupt contradictions cache on portfolio {portfolio_id}; recomputing")
            else:
                usable = [r for r in records if (r.doc_text or "").strip()]
                return {
                    "contradictions": cached,
                    "checked_docs": min(len(usable), MAX_DOCS),
                    "skipped_docs": len(records) - min(len(usable), MAX_DOCS),
                    "cached": True,
                }

        # Documents classified before doc_text was stored have no text to compare.
        usable = [r for r in records if (r.doc_text or "").strip()]
        considered = usable[:MAX_DOCS]
        skipped = len(records) - len(considered)

        try:
            # Stage 1 fans out one claim-extraction call per document; running them
            # sequentially made large portfolios take ~N×(call latency). Parallel
            # threads bring stage 1 down to roughly the latency of one call.
            with ThreadPoolExecutor(max_workers=8) as pool:
                extracted = list(pool.map(
                    lambda r: (r.filename, _extract_claims(r.filename, r.doc_text)),
                    considered,
                ))

            claims_by_doc: dict[str, list[str]] = {}
            for filename, claims in extracted:
                if claims:
                    # Duplicate filenames within a portfolio would collide in the
                    # by-filename map and make citations ambiguous; keep the first.
                    claims_by_doc.setdefault(filename, claims)

            # One document with claims cannot contradict anything else.
            if len(claims_by_doc) < 2:
                contradictions: list[dict] = []
            else:
                contradictions = _find_contradictions(claims_by_doc)
        except Exception as e:
            logger.error(f"Contradiction analysis failed for portfolio {portfolio_id}: {e}")
            raise HTTPException(status_code=502, detail="Contradiction analysis failed")

        portfolio.contradictions_json = json.dumps(contradictions)
        session.commit()

        return {
            "contradictions": contradictions,
            "checked_docs": len(considered),
            "skipped_docs": skipped,
            "cached": False,
        }
