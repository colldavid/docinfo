"""
Portfolio deliverable export — a Current State Assessment draft in Markdown.

Design notes:
  - ZERO LLM calls. Everything here is assembled from analysis already stored on
    the portfolio's records, so the export is deterministic, instant, and works
    with no network. Two exports of an unchanged portfolio are byte-identical.
  - The output is a STARTING DRAFT. A consultant pastes it into their own deck or
    memo and edits; it is deliberately structured (inventory, themes, findings,
    open questions, appendix) rather than prose-complete.
  - Human corrections win. Where a consultant has overridden a classifier label
    (user_doc_type / user_industry), the corrected value is what appears in the
    document, marked with an asterisk and explained by a footnote.

Endpoint:
  GET /portfolios/{portfolio_id}/deliverable -> text/markdown attachment
"""

import html as html_lib
import io
import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from app.database import Portfolio, get_session

logger = logging.getLogger(__name__)

router = APIRouter()

# Findings whose category the extractor left blank still belong in the report —
# they are grouped under this heading rather than dropped.
UNCATEGORIZED = "Uncategorized"


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Portfolio name -> filename-safe slug ('Project Atlas!' -> 'project-atlas')."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "portfolio"


def _escape_cell(value: str) -> str:
    """Make a value safe inside a Markdown table cell."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|").strip()


def _pct(value) -> str:
    """0.873 -> '87%'. Returns an em dash when confidence was never scored."""
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _labeled(value: str, corrected: bool) -> str:
    """Append the human-verified marker to a corrected label."""
    if not value:
        return "—"
    return f"{value}*" if corrected else value


def _resolved_doc_type(record) -> tuple[str, bool]:
    """(label, was_corrected_by_human) for document type."""
    user = (record.user_doc_type or "").strip()
    if user:
        return user, True
    return (record.doc_type_label or "").strip(), False


def _resolved_industry(record) -> tuple[str, bool]:
    """(label, was_corrected_by_human) for industry."""
    user = (record.user_industry or "").strip()
    if user:
        return user, True
    return (record.industry or "").strip(), False


def _needs_review_flags(record) -> list[str]:
    """Names of the dimensions this record was flagged on, in report order."""
    flags = []
    if record.doc_type_needs_review:
        flags.append("document type")
    if record.industry_needs_review:
        flags.append("industry")
    if record.confidentiality_needs_review:
        flags.append("confidentiality")
    if record.importance_needs_review:
        flags.append("importance")
    return flags


# ---------------------------------------------------------------------------
# Section builders — each returns a list of Markdown lines
# ---------------------------------------------------------------------------

def _build_header(portfolio, records, generated: datetime) -> list[str]:
    doc_word = "document" if len(records) == 1 else "documents"
    return [
        f"# Current State Assessment — {portfolio.name}",
        "",
        f"**Generated:** {generated.strftime('%B %d, %Y')}  ",
        f"**Documents analyzed:** {len(records)} {doc_word}",
        "",
        "> Draft assembled automatically from DocInfo's stored analysis. "
        "Review and edit before sharing with the client.",
        "",
    ]


def _build_inventory(records) -> list[str]:
    lines = [
        "## Document Inventory",
        "",
        "| File | Type | Industry | Confidentiality | Importance |",
        "| --- | --- | --- | --- | --- |",
    ]

    any_corrected = False
    for record in records:
        doc_type, dt_corrected = _resolved_doc_type(record)
        industry, ind_corrected = _resolved_industry(record)
        any_corrected = any_corrected or dt_corrected or ind_corrected
        lines.append(
            "| {file} | {dtype} | {industry} | {conf} | {imp} |".format(
                file=_escape_cell(record.filename),
                dtype=_escape_cell(_labeled(doc_type, dt_corrected)),
                industry=_escape_cell(_labeled(industry, ind_corrected)),
                conf=_escape_cell(record.confidentiality_label or "—"),
                imp=_escape_cell(record.importance_label or "—"),
            )
        )

    lines.append("")
    if any_corrected:
        lines.extend(["\\* human-verified", ""])
    return lines


def _build_themes(portfolio) -> list[str]:
    """
    Render the portfolio theme.

    synthesize_theme() stores a JSON string of [{"headline", "detail"}, ...], so
    the raw column value is not presentable prose — dumping it verbatim would put
    literal JSON in a client-facing document. We render the structured form when
    it parses and fall back to the stored text verbatim otherwise (older rows, or
    a future change to a plain-prose theme).
    """
    theme = (portfolio.theme or "").strip()
    if not theme:
        return []  # Section is skipped entirely when no theme was synthesized.

    lines = ["## Key Themes", ""]

    try:
        parsed = json.loads(theme)
    except (ValueError, TypeError):
        parsed = None

    if isinstance(parsed, list) and parsed:
        rendered_any = False
        for item in parsed:
            if not isinstance(item, dict):
                continue
            headline = str(item.get("headline", "")).strip()
            detail = str(item.get("detail", "")).strip()
            if not (headline or detail):
                continue
            if headline:
                lines.append(f"**{headline}**")
                lines.append("")
            if detail:
                lines.append(detail)
                lines.append("")
            rendered_any = True
        if rendered_any:
            return lines

    # Not the structured form — emit the stored theme as-is.
    lines.extend([theme, ""])
    return lines


def _collect_findings(records) -> tuple[dict, int]:
    """
    Group every pain point across the portfolio by its category field.

    Returns (ordered {category: [finding, ...]}, total_count). Each finding carries
    its source filename so the reader can trace it back to a document.
    """
    grouped: dict[str, list[dict]] = {}
    total = 0

    for record in records:
        try:
            points = record.pain_points
        except (TypeError, ValueError):
            logger.warning("Unreadable pain_points_json on record %s", record.id)
            continue

        for point in points or []:
            if not isinstance(point, dict):
                continue
            label = str(point.get("label", "")).strip()
            if not label:
                continue
            category = str(point.get("category", "")).strip() or UNCATEGORIZED
            grouped.setdefault(category, []).append({
                "label": label,
                "context": str(point.get("context", "")).strip(),
                "question": str(point.get("question", "")).strip(),
                "source": record.filename,
            })
            total += 1

    # Largest categories first — the biggest clusters lead the findings section.
    ordered = dict(sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])))
    return ordered, total


def _build_findings(grouped: dict, total: int) -> list[str]:
    lines = ["## Findings by Theme", ""]

    if not total:
        lines.extend([
            "_No pain points were extracted from this portfolio's documents._",
            "",
        ])
        return lines

    finding_word = "finding" if total == 1 else "findings"
    lines.extend([
        f"{total} {finding_word} across {len(grouped)} "
        f"{'category' if len(grouped) == 1 else 'categories'}.",
        "",
    ])

    for category, findings in grouped.items():
        share = len(findings) / total * 100
        lines.extend([
            f"### {category} — {len(findings)} of {total} ({share:.0f}%)",
            "",
        ])
        for finding in findings:
            lines.append(f"- **{finding['label']}**")
            if finding["context"]:
                lines.append(f"  {finding['context']}")
            if finding["question"]:
                lines.append(f"  _{finding['question']}_")
            lines.append(f"  ({finding['source']})")
            lines.append("")

    return lines


def _build_open_questions(grouped: dict) -> list[str]:
    """Deduplicated diligence questions, in first-seen order."""
    lines = ["## Open Questions for the Client", ""]

    seen: set[str] = set()
    questions: list[str] = []
    for findings in grouped.values():
        for finding in findings:
            question = finding["question"]
            if not question:
                continue
            key = question.lower().rstrip(" .?")
            if key in seen:
                continue
            seen.add(key)
            questions.append(question)

    if not questions:
        lines.extend(["_No diligence questions were generated._", ""])
        return lines

    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question}")
    lines.append("")
    return lines


def _build_appendix(records) -> list[str]:
    lines = ["## Appendix — Per-Document Detail", ""]

    for record in records:
        lines.extend([f"### {record.filename}", ""])

        summary = (record.summary or "").strip()
        lines.extend([summary if summary else "_No summary available._", ""])

        doc_type, dt_corrected = _resolved_doc_type(record)
        industry, ind_corrected = _resolved_industry(record)

        lines.append(
            f"- **Document type:** {_labeled(doc_type, dt_corrected)}"
            + ("" if dt_corrected else f" ({_pct(record.doc_type_probability)} confidence)")
        )
        lines.append(
            f"- **Industry:** {_labeled(industry, ind_corrected)}"
            + ("" if ind_corrected else f" ({_pct(record.industry_probability)} confidence)")
        )
        lines.append(
            f"- **Confidentiality:** {record.confidentiality_label or '—'} "
            f"({_pct(record.confidentiality_confidence)} confidence)"
        )
        lines.append(
            f"- **Importance:** {record.importance_label or '—'} "
            f"({_pct(record.importance_confidence)} confidence)"
        )
        lines.append("")

        flags = _needs_review_flags(record)
        if flags:
            lines.extend([
                f"> **Flagged for review:** low classifier confidence on {', '.join(flags)}. "
                "Confirm before relying on these labels.",
                "",
            ])

    return lines


def build_deliverable_markdown(portfolio, generated: datetime | None = None) -> str:
    """
    Assemble the full Current State Assessment for a portfolio.

    Pure function of stored state — no LLM calls, no network, no side effects.
    Kept separate from the route so it can be unit-tested directly.
    """
    generated = generated or datetime.now()
    records = sorted(portfolio.records or [], key=lambda r: (r.filename or "").lower())

    grouped, total_findings = _collect_findings(records)

    lines: list[str] = []
    lines.extend(_build_header(portfolio, records, generated))
    lines.extend(_build_inventory(records))
    lines.extend(_build_themes(portfolio))
    lines.extend(_build_findings(grouped, total_findings))
    lines.extend(_build_open_questions(grouped))
    lines.extend(_build_appendix(records))

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Print-ready HTML (the "PDF" path — consultant hits Print → Save as PDF)
# ---------------------------------------------------------------------------

# Hard caps keep the printed document at ~3 pages. The full detail lives in the
# app; the deliverable is the executive cut.
MAX_CATEGORIES_PRINT = 4
MAX_FINDINGS_PER_CATEGORY = 3
MAX_QUESTIONS_PRINT = 6

PURPLE = "#A100FF"  # Accenture Core Purple

_PRINT_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root { color-scheme: light; }
  @page { size: A4; margin: 16mm 15mm; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body {
    font-family: 'Segoe UI', Arial, Helvetica, sans-serif;
    font-size: 10.5pt; line-height: 1.5; color: #1c1c28; background: #fff;
    max-width: 780px; margin: 0 auto; padding: 28px 24px 48px;
  }
  .band { height: 8px; background: PURPLE; margin: -28px -24px 26px; }
  h1 { font-size: 21pt; letter-spacing: -0.02em; line-height: 1.15; }
  .sub { color: #6b6b7b; margin-top: 4px; font-size: 9.5pt; }
  .draftnote { font-size: 8.5pt; color: #6b6b7b; border-left: 3px solid PURPLE;
    padding: 4px 10px; margin: 14px 0 20px; background: #faf5ff; }
  .stats { display: flex; gap: 10px; margin: 0 0 22px; }
  .stat { flex: 1; border: 1px solid #e4e0ee; border-top: 3px solid PURPLE; padding: 8px 10px; }
  .stat b { display: block; font-size: 16pt; color: PURPLE; line-height: 1.2; }
  .stat span { font-size: 8pt; text-transform: uppercase; letter-spacing: 0.06em; color: #6b6b7b; }
  h2 { font-size: 12.5pt; color: PURPLE; margin: 22px 0 10px; break-after: avoid; }
  .theme { margin-bottom: 12px; break-inside: avoid; }
  .theme b { display: block; margin-bottom: 2px; }
  .cat { margin-bottom: 14px; break-inside: avoid; }
  .cat-head { font-weight: 600; font-size: 10.5pt; margin-bottom: 6px; }
  .cat-head small { font-weight: 400; color: #6b6b7b; }
  .finding { border-left: 3px solid PURPLE; padding: 6px 12px; margin-bottom: 8px;
    background: #fbf9ff; break-inside: avoid; }
  .finding b { display: block; }
  .finding .ctx { color: #44444f; }
  .finding .q { color: PURPLE; font-style: italic; }
  .finding .src { color: #9a97a8; font-size: 8.5pt; }
  .more { color: #6b6b7b; font-size: 9pt; font-style: italic; }
  ol.questions { padding-left: 20px; }
  ol.questions li { margin-bottom: 5px; }
  table { width: 100%; border-collapse: collapse; font-size: 9pt; margin-top: 6px; }
  th { background: PURPLE; color: #fff; text-align: left; padding: 5px 8px;
    font-size: 8pt; text-transform: uppercase; letter-spacing: 0.05em; }
  td { padding: 4px 8px; border-bottom: 1px solid #eceaf2; }
  tr { break-inside: avoid; }
  .foot { margin-top: 26px; padding-top: 10px; border-top: 1px solid #eceaf2;
    color: #9a97a8; font-size: 8.5pt; }
  .printbtn { position: fixed; top: 14px; right: 14px; background: PURPLE; color: #fff;
    border: none; padding: 10px 18px; font-size: 10pt; font-weight: 600;
    border-radius: 3px; cursor: pointer; box-shadow: 0 2px 10px rgba(161,0,255,0.35); }
  .printbtn:hover { opacity: 0.88; }
  @media print { .no-print { display: none !important; } body { padding: 0; } .band { margin: 0 0 22px; } }
""".replace("PURPLE", PURPLE)


def _esc(value) -> str:
    return html_lib.escape(str(value or ""))


def _parse_theme(portfolio) -> tuple[list[dict], str]:
    """([{headline, detail}, ...], fallback_raw). Items empty → use the raw string."""
    theme = (portfolio.theme or "").strip()
    if not theme:
        return [], ""
    try:
        parsed = json.loads(theme)
    except (ValueError, TypeError):
        return [], theme
    items = []
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                headline = str(item.get("headline", "")).strip()
                detail = str(item.get("detail", "")).strip()
                if headline or detail:
                    items.append({"headline": headline, "detail": detail})
    return (items, "") if items else ([], theme)


def build_deliverable_html(portfolio, generated: datetime | None = None) -> str:
    """
    Print-optimized Current State Assessment (~3 pages, A4).

    Same stored-analysis-only rule as the Markdown builder — no LLM, no network.
    The reader saves it as PDF via the browser print dialog.
    """
    generated = generated or datetime.now()
    records = sorted(portfolio.records or [], key=lambda r: (r.filename or "").lower())
    grouped, total_findings = _collect_findings(records)

    high_importance = sum(1 for r in records if r.importance_label == "high")
    restricted = sum(1 for r in records if r.confidentiality_label == "restricted")

    parts: list[str] = [
        f"<title>Current State Assessment — {_esc(portfolio.name)}</title>",
        f"<style>{_PRINT_CSS}</style>",
        '<div class="band"></div>',
        '<button class="printbtn no-print" onclick="window.print()">Print / Save as PDF</button>',
        f"<h1>Current State Assessment<br>{_esc(portfolio.name)}</h1>",
        f'<p class="sub">Generated {generated.strftime("%B %d, %Y")} · '
        f"{len(records)} document{'s' if len(records) != 1 else ''} analyzed</p>",
        '<p class="draftnote">Draft assembled automatically from DocInfo analysis. '
        "Review and edit before sharing with the client. Full per-document detail is in the app.</p>",
        # Snapshot
        '<div class="stats">',
        f'<div class="stat"><b>{len(records)}</b><span>Documents</span></div>',
        f'<div class="stat"><b>{total_findings}</b><span>Findings</span></div>',
        f'<div class="stat"><b>{high_importance}</b><span>High importance</span></div>',
        f'<div class="stat"><b>{restricted}</b><span>Restricted</span></div>',
        "</div>",
    ]

    # Key themes
    theme_items, theme_raw = _parse_theme(portfolio)
    if theme_items or theme_raw:
        parts.append("<h2>Key Themes</h2>")
        if theme_items:
            for item in theme_items:
                parts.append('<div class="theme">')
                if item["headline"]:
                    parts.append(f"<b>{_esc(item['headline'])}</b>")
                if item["detail"]:
                    parts.append(f"<span>{_esc(item['detail'])}</span>")
                parts.append("</div>")
        else:
            parts.append(f'<div class="theme">{_esc(theme_raw)}</div>')

    # Findings — capped for the printed cut
    parts.append("<h2>Findings by Theme</h2>")
    if not total_findings:
        parts.append('<p class="more">No pain points were extracted from this portfolio.</p>')
    else:
        shown_categories = list(grouped.items())[:MAX_CATEGORIES_PRINT]
        for category, findings in shown_categories:
            share = len(findings) / total_findings * 100
            parts.append('<div class="cat">')
            parts.append(
                f'<div class="cat-head">{_esc(category)} '
                f"<small>— {len(findings)} of {total_findings} ({share:.0f}%)</small></div>"
            )
            for finding in findings[:MAX_FINDINGS_PER_CATEGORY]:
                parts.append('<div class="finding">')
                parts.append(f"<b>{_esc(finding['label'])}</b>")
                if finding["context"]:
                    parts.append(f'<span class="ctx">{_esc(finding["context"])}</span><br>')
                if finding["question"]:
                    parts.append(f'<span class="q">{_esc(finding["question"])}</span><br>')
                parts.append(f'<span class="src">{_esc(finding["source"])}</span>')
                parts.append("</div>")
            hidden = len(findings) - MAX_FINDINGS_PER_CATEGORY
            if hidden > 0:
                parts.append(f'<p class="more">+ {hidden} more in DocInfo</p>')
            parts.append("</div>")
        hidden_cats = len(grouped) - MAX_CATEGORIES_PRINT
        if hidden_cats > 0:
            parts.append(
                f'<p class="more">+ {hidden_cats} smaller '
                f"{'category' if hidden_cats == 1 else 'categories'} in DocInfo</p>"
            )

    # Open questions — deduped, capped
    seen: set[str] = set()
    questions: list[str] = []
    for findings in grouped.values():
        for finding in findings:
            q = finding["question"]
            if not q:
                continue
            key = q.lower().rstrip(" .?")
            if key not in seen:
                seen.add(key)
                questions.append(q)
    if questions:
        parts.append("<h2>Open Questions for the Client</h2>")
        parts.append('<ol class="questions">')
        for q in questions[:MAX_QUESTIONS_PRINT]:
            parts.append(f"<li>{_esc(q)}</li>")
        parts.append("</ol>")

    # Inventory
    parts.append("<h2>Document Inventory</h2>")
    parts.append("<table><tr><th>File</th><th>Type</th><th>Industry</th>"
                 "<th>Confidentiality</th><th>Importance</th></tr>")
    any_corrected = False
    for record in records:
        doc_type, dt_corrected = _resolved_doc_type(record)
        industry, ind_corrected = _resolved_industry(record)
        any_corrected = any_corrected or dt_corrected or ind_corrected
        parts.append(
            "<tr>"
            f"<td>{_esc(record.filename)}</td>"
            f"<td>{_esc(_labeled(doc_type, dt_corrected))}</td>"
            f"<td>{_esc(_labeled(industry, ind_corrected))}</td>"
            f"<td>{_esc(record.confidentiality_label or '—')}</td>"
            f"<td>{_esc(record.importance_label or '—')}</td>"
            "</tr>"
        )
    parts.append("</table>")
    if any_corrected:
        parts.append('<p class="more">* human-verified label</p>')

    parts.append('<p class="foot">Generated by DocInfo · internal draft — not for external '
                 "distribution without review</p>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/portfolios/{portfolio_id}/deliverable")
def export_deliverable(portfolio_id: int, format: str = "html"):
    """
    Current State Assessment draft for a portfolio.

    Default (`format=html`): print-ready branded page — opens in the browser,
    one click to save as PDF. `format=md`: raw Markdown download for pasting
    into docs. Both are built entirely from stored analysis — no LLM calls.
    """
    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        if not portfolio:
            raise HTTPException(
                status_code=404, detail=f"Portfolio {portfolio_id} not found."
            )

        generated = datetime.now()
        slug = _slugify(portfolio.name)
        # Rendered inside the session — record access is lazy-loaded.
        if format == "md":
            markdown = build_deliverable_markdown(portfolio, generated)
        else:
            page = build_deliverable_html(portfolio, generated)

    if format == "md":
        filename = f"docinfo_assessment_{slug}_{generated.strftime('%Y%m%d')}.md"
        buffer = io.StringIO(markdown)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return HTMLResponse(content=page)
