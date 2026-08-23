"""
Portfolio-level entity and timeline extraction.

A document dump arrives as N unrelated files, and the first two questions a
consultant asks are "who is involved?" and "what happened when?". Both answers
live across documents rather than inside any one of them: a vendor named once
in a contract and again in a risk memo matters more than either mention alone,
and a chronology only becomes readable when dated events from every file are
merged into a single sequence.

Two-stage design, mirroring the contradiction analysis:

  Stage 1 (per document, N calls) — extract named entities and dated events.
    Parallel, because N sequential calls would make the request take N times
    one call's latency.

  Stage 2 (no LLM) — merge. Unlike contradiction detection, the cross-document
    step here is pure bookkeeping: grouping identical names and ordering dates
    are deterministic operations, and spending a call on them would only add
    latency and a chance to hallucinate a mention that no document made.

Results are cached on Portfolio.entities_json because the analysis costs N LLM
calls; `refresh=true` forces recomputation after documents change.
"""

import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

from app.database import Portfolio, get_session
from app.pipelines.llm_client import call_llm

logger = logging.getLogger(__name__)

router = APIRouter()

# Cap the fan-out: 20 documents is 20 LLM calls, already a slow request.
MAX_DOCS = 20

# Per-document text budget. Entity introductions and the dates that anchor a
# narrative cluster in the opening of consulting documents (cover page, exec
# summary, status table), so a head truncation loses less than it saves.
DOC_CHAR_LIMIT = 4000

MAX_ENTITIES_PER_DOC = 10
MAX_EVENTS_PER_DOC = 8

# Response caps — beyond this the UI stops being a summary and becomes a second
# document to read.
MAX_ENTITIES = 30
MAX_EVENTS = 40

VALID_TYPES = {"company", "person", "vendor", "regulator", "other"}

EXTRACTION_PROMPT = """\
Extract the named entities and the dated events from the document below.

ENTITIES — organizations and people the document actually names. Classify each:
  - "company"   — the client, its subsidiaries, partners, competitors, acquirers
  - "person"    — a named individual (executive, signatory, author, contact)
  - "vendor"    — a supplier, contractor, consultancy, or software provider
  - "regulator" — a government body, regulator, agency, or standards authority
  - "other"     — anything named that fits none of the above

Use the entity's name as written, without titles or roles ("Jane Okafor" not \
"CFO Jane Okafor", "Northwind Logistics" not "Northwind Logistics Inc., the carrier"). \
Skip generic references that are not names ("the vendor", "our client", "the board"). \
Return AT MOST {max_entities} entities, the most substantively involved ones.

EVENTS — things that happened or are scheduled, ONLY where the document states a \
concrete date. A date qualifies if it names a day, month, quarter, or year: \
"March 14", "Q2 2025", "by year-end 2024", "January 2026" all qualify. Vague timing \
does NOT qualify: "recently", "soon", "in the coming months", "ongoing".

Dates and years — use the document's own date context:
  - If the document states a full date, copy it VERBATIM and set "inferred": false.
  - If a date lacks a year ("March 14", "the June board meeting") or is relative \
("next quarter", "the coming fiscal year"), RESOLVE it using the document's own \
date (header date, letterhead, period covered) and other dates it states — e.g. a \
memo dated October 2025 saying "commissioning next quarter" means Q1 2026. Write \
the resolved date ("Q1 2026", "March 14, 2026") and set "inferred": true.
  - If the document gives you no basis to resolve a year, keep the date as written \
and set "inferred": true. Never invent a year that has no support in the document.

Each description must name its subject and be self-contained — "Meridian Health \
completed the ERP cutover" not "the cutover was completed", because these events \
will be read in a list mixed with events from other documents. \
Return AT MOST {max_events} events.

Document:
{text}

Return ONLY a JSON object with keys "entities" and "events". Use empty arrays if the \
document has none.
Example:
{{
  "entities": [
    {{"name": "Meridian Health", "type": "company"}},
    {{"name": "Jane Okafor", "type": "person"}},
    {{"name": "SAP", "type": "vendor"}},
    {{"name": "FDA", "type": "regulator"}}
  ],
  "events": [
    {{"date": "March 14, 2025", "description": "Meridian Health signed the SAP \
implementation contract", "inferred": false}},
    {{"date": "Q2 2025", "description": "Meridian Health ERP go-live is scheduled", \
"inferred": true}}
  ]
}}"""


def _strip_fences(raw: str) -> str:
    """Unwrap ```json ... ``` fencing the model sometimes adds despite instructions."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    return raw


def _extract(filename: str, text: str) -> tuple[list[dict], list[dict]]:
    """
    Stage 1: reduce one document to its named entities and dated events.

    A failure here degrades rather than aborts — one unparseable document should
    not cost the reviewer the analysis of the other nineteen, so this returns
    empty lists and the document simply contributes nothing to the merge.
    """
    prompt = EXTRACTION_PROMPT.format(
        max_entities=MAX_ENTITIES_PER_DOC,
        max_events=MAX_EVENTS_PER_DOC,
        text=text[:DOC_CHAR_LIMIT],
    )
    try:
        parsed = json.loads(_strip_fences(call_llm(user_message=prompt, max_tokens=1200)))
    except Exception as e:
        logger.warning(f"Entity extraction failed for {filename!r}: {e}")
        return [], []

    if not isinstance(parsed, dict):
        return [], []

    entities = []
    raw_entities = parsed.get("entities")
    if isinstance(raw_entities, list):
        for item in raw_entities[:MAX_ENTITIES_PER_DOC]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            etype = str(item.get("type", "")).strip().lower()
            # An unrecognized type is a labeling miss, not a reason to drop a
            # real entity — "other" is the honest fallback.
            if etype not in VALID_TYPES:
                etype = "other"
            entities.append({"name": name, "type": etype})

    events = []
    raw_events = parsed.get("events")
    if isinstance(raw_events, list):
        for item in raw_events[:MAX_EVENTS_PER_DOC]:
            if not isinstance(item, dict):
                continue
            date = str(item.get("date", "")).strip()
            description = str(item.get("description", "")).strip()
            # Both halves are required: a date with no description is
            # unreadable, and a description with no date is not a timeline entry.
            if not date or not description:
                continue
            events.append({
                "date": date,
                "description": description,
                # Marks dates the model resolved from context rather than
                # copied verbatim — the UI shows these as "(inferred)".
                "inferred": bool(item.get("inferred", False)),
            })

    return entities, events


def _normalize_name(name: str) -> str:
    """Case/whitespace-tolerant key for merging the same entity across documents."""
    return " ".join(name.lower().split())


def _merge_entities(per_doc: list[tuple[str, list[dict]]]) -> list[dict]:
    """
    Group mentions of the same entity across documents.

    Matching is on a normalized name only — deliberately conservative. Fuzzy
    matching ("Acme" ≈ "Acme Corp") would merge genuinely distinct parties as
    often as it fixed a casing difference, and a wrongly merged entity shows the
    reviewer a mention list they cannot verify.
    """
    order: list[str] = []              # first-seen order, for stable tie-breaking
    display: dict[str, str] = {}       # key -> first-seen casing
    types: dict[str, Counter] = {}
    mentions: dict[str, list[str]] = {}

    for filename, entities in per_doc:
        for ent in entities:
            key = _normalize_name(ent["name"])
            if key not in display:
                order.append(key)
                display[key] = ent["name"]
                types[key] = Counter()
                mentions[key] = []
            types[key][ent["type"]] += 1
            # A document that names an entity three times is still one mention;
            # the list is "which documents talk about this", not a raw count.
            if filename not in mentions[key]:
                mentions[key].append(filename)

    merged = []
    for key in order:
        # most_common breaks ties by insertion order, so the first-seen type
        # wins when two labels are equally frequent.
        merged.append({
            "name": display[key],
            "type": types[key].most_common(1)[0][0],
            "mentions": mentions[key],
        })

    # Entities named by several documents are the ones that matter at portfolio
    # level, so mention count leads; name breaks ties for a stable ordering.
    merged.sort(key=lambda e: (-len(e["mentions"]), e["name"].lower()))
    return merged[:MAX_ENTITIES]


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _date_sort_key(date: str) -> tuple[int, int, int] | None:
    """
    Best-effort (year, month, day) for ordering a verbatim date string.

    The date is kept verbatim for display and only parsed for sequencing, so
    this can afford to be partial: it recognizes the forms consulting documents
    actually use and returns None for anything else. A quarter maps to the month
    its period starts (Q2 -> April), which orders it correctly against
    month-level dates in the same year. None means "unorderable", and the caller
    parks those at the end rather than guessing a position for them.
    """
    text = date.strip().lower()
    if not text:
        return None

    # "Q2 2025" / "2025 Q2" / "FY2025 Q3". The year is not anchored on a left
    # word boundary so "FY2026" and "CY2025" still resolve; the right boundary
    # keeps it from matching the first four digits of a longer number.
    q = re.search(r"\bq([1-4])\b", text)
    year_match = re.search(r"(1[5-9]\d{2}|20\d{2}|21\d{2})\b", text)
    if q and year_match:
        return (int(year_match.group(1)), (int(q.group(1)) - 1) * 3 + 1, 0)

    # Any month name, with or without a day: "March 14, 2025", "March 2025",
    # "14 March 2025", "by end of March 2025".
    month = None
    for token in re.findall(r"[a-z]+", text):
        if token in _MONTHS:
            month = _MONTHS[token]
            break

    if month is not None and year_match:
        # A day is only meaningful next to a month; take a 1-2 digit number that
        # is not the year.
        day = 0
        for candidate in re.findall(r"\b(\d{1,2})\b", text):
            value = int(candidate)
            if 1 <= value <= 31:
                day = value
                break
        return (int(year_match.group(1)), month, day)

    # Bare year: "2025", "by year-end 2024", "FY2026".
    if year_match:
        return (int(year_match.group(1)), 0, 0)

    # Month with no year is unanchored — ordering it against dated events would
    # require guessing a year, so leave it unordered.
    return None


MAX_UNDATED = 10


def _sort_events(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    (chronological_events, undated_events).

    Events whose dates can't be anchored to a year are returned SEPARATELY, not
    appended after the dated timeline — interleaving "May" after "Nov 2030"
    reads as a chronological claim we can't actually make. The UI renders them
    in their own "Undated" strip. Most yearless dates never reach here anymore:
    the extraction prompt resolves them from document context (marked inferred).
    """
    dated = []
    undated = []
    for index, event in enumerate(events):
        key = _date_sort_key(event["date"])
        if key is None:
            undated.append((index, event))
        else:
            # Index tie-breaks equal dates so the sort stays stable and
            # deterministic across runs.
            dated.append((key, index, event))

    dated.sort(key=lambda t: (t[0], t[1]))
    undated.sort(key=lambda t: t[0])

    return (
        [event for _, _, event in dated][:MAX_EVENTS],
        [event for _, event in undated][:MAX_UNDATED],
    )


def compute_and_store(portfolio, session) -> dict:
    """
    Extract entities and events for a portfolio and persist the result on
    portfolio.entities_json. Called from the endpoint below AND (once wired up)
    from portfolio creation, so the first view of a new portfolio is instant.
    """
    records = list(portfolio.records)
    # Documents classified before doc_text was stored have no text to read.
    usable = [r for r in records if (r.doc_text or "").strip()]
    considered = usable[:MAX_DOCS]
    skipped = len(records) - len(considered)

    # One extraction call per document, run in parallel: sequentially this would
    # take N × call latency, which is minutes for a full portfolio.
    with ThreadPoolExecutor(max_workers=8) as pool:
        extracted = list(pool.map(
            lambda r: (r.filename, _extract(r.filename, r.doc_text)),
            considered,
        ))

    entities_per_doc: list[tuple[str, list[dict]]] = []
    all_events: list[dict] = []
    for filename, (entities, events) in extracted:
        entities_per_doc.append((filename, entities))
        for event in events:
            all_events.append({
                "date": event["date"],
                "description": event["description"],
                "source": filename,
            })

    events, undated_events = _sort_events(all_events)
    result = {
        "entities": _merge_entities(entities_per_doc),
        "events": events,
        "undated_events": undated_events,
    }

    portfolio.entities_json = json.dumps(result)
    session.commit()

    return {
        **result,
        "checked_docs": len(considered),
        "skipped_docs": skipped,
        "cached": False,
    }


@router.post("/portfolios/{portfolio_id}/entities")
def extract_entities(portfolio_id: int, refresh: bool = False):
    """
    Extract recurring entities and a dated chronology across a portfolio.

    Returns the cached analysis unless `refresh=true`. Empty entity/event lists
    are a real, cacheable answer ("nothing named, nothing dated"), not a miss.
    """
    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        records = list(portfolio.records)

        if portfolio.entities_json and not refresh:
            try:
                cached = json.loads(portfolio.entities_json)
            except json.JSONDecodeError:
                # Corrupt cache shouldn't be a dead end — fall through and recompute.
                logger.warning(f"Corrupt entities cache on portfolio {portfolio_id}; recomputing")
            else:
                if isinstance(cached, dict):
                    usable = [r for r in records if (r.doc_text or "").strip()]
                    return {
                        "entities": cached.get("entities", []),
                        "events": cached.get("events", []),
                        # Older cached results predate the dated/undated split;
                        # .get keeps them readable until a refresh recomputes.
                        "undated_events": cached.get("undated_events", []),
                        "checked_docs": min(len(usable), MAX_DOCS),
                        "skipped_docs": len(records) - min(len(usable), MAX_DOCS),
                        "cached": True,
                    }
                logger.warning(f"Unexpected entities cache shape on portfolio {portfolio_id}; recomputing")

        try:
            return compute_and_store(portfolio, session)
        except Exception as e:
            logger.error(f"Entity extraction failed for portfolio {portfolio_id}: {e}")
            raise HTTPException(status_code=502, detail="Entity extraction failed")
