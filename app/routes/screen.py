"""
Send-time confidentiality screening for the Outlook add-in.

When a consultant hits Send, the add-in posts the recipients and the raw
attachment bytes here and shows the returned `message` in Outlook's PromptUser
dialog. The costly mistake this catches is a restricted deck going to a client
distribution list — a mistake that is invisible at Send time because the sender
usually has not read the attachment they forwarded.

Design decisions this endpoint is built around:

  D1 — Attachments only. The email body is not screened: bodies are short,
    self-authored, and the sender already knows what they wrote.

  D2 — Parse + confidentiality only, never the full classification pipeline.
    `classify_confidentiality` is content-addressed-cached, so a document the
    firm has already analyzed returns with no LLM call at all, which is what
    makes a synchronous check at Send time tolerable.

  D3 — Warn only. This endpoint never instructs a block; restricted warns
    strongly, sensitive warns mildly, public allows. The human in the dialog
    makes the call, so every ambiguous case resolves toward "let them decide"
    rather than toward blocking legitimate work.

  D4 — External = recipient domain outside `firm_domains`. An EMPTY setting
    treats everyone as external, which is the conservative reading and is
    harmless under D3: an unconfigured install warns more, never less.

  D5 — No persistence. Screening events are not logged or stored; the endpoint
    reads settings and writes nothing.

  D6 — Auth is the `X-Screen-Key` header compared against the `screen_api_key`
    runtime setting. /screen is deliberately NOT in app.main's
    _PROTECTED_PREFIXES — the add-in has no browser session to present — so the
    check below is this endpoint's COMPLETE authentication. Empty setting means
    the feature is unconfigured and the endpoint refuses (503) rather than
    running unauthenticated.

Included by app.main via:
    from app.routes import screen
    app.include_router(screen.router)
"""

import base64
import binascii
import hmac
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app import runtime_settings
from app.ingestion import parse_document
from app.pipelines.confidentiality import classify_confidentiality

logger = logging.getLogger(__name__)

router = APIRouter()

# Mirrors app.ingestion's parseable set. Anything else is reported "unscanned"
# rather than refused: under D3 we must never warn about a file we never read.
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

# Send-time budget. A mail with more attachments (or a file this large) would
# push the dialog past the few seconds a sender will wait, so the excess is
# reported unscanned instead of screened.
MAX_ATTACHMENTS = 10
MAX_DECODED_BYTES = 10 * 1024 * 1024

# Screening a mail is bounded by the slowest attachment, not their sum.
MAX_WORKERS = 4

# Strongest-first, so the dialog names the worst thing found.
_SEVERITY_BY_LABEL = {"restricted": "warn", "sensitive": "warn", "public": "allow"}
_LABEL_RANK = {"restricted": 2, "sensitive": 1, "public": 0}

# Enough recipients to recognize the mistake; a full list would not fit a dialog.
_MAX_LISTED_RECIPIENTS = 3


class ScreenAttachment(BaseModel):
    filename: str
    content_base64: str


class ScreenRequest(BaseModel):
    recipients: list[str]
    attachments: list[ScreenAttachment] = []


def _require_key(provided: str | None) -> None:
    """D6: shared-key auth. See the module docstring — this is the only gate."""
    expected = runtime_settings.get_setting("screen_api_key").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Screening is not configured. Set screen_api_key in Settings.",
        )
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Screen-Key.")


def _external_recipients(recipients: list[str]) -> list[str]:
    """
    D4: recipients whose domain is not the firm's, preserving request order.

    Subdomains of a firm domain count as internal (mail.accenture.com is still
    Accenture). An address with no "@" has no domain to clear, so it is treated
    as external.
    """
    firm_domains = {
        d.strip().lower().lstrip("@")
        for d in runtime_settings.get_setting("firm_domains").split(",")
        if d.strip()
    }
    if not firm_domains:
        return list(recipients)

    external = []
    for address in recipients:
        _, _, domain = address.rpartition("@")
        domain = domain.strip().lower()
        internal = domain and any(
            domain == fd or domain.endswith("." + fd) for fd in firm_domains
        )
        if not internal:
            external.append(address)
    return external


def _unscanned(filename: str) -> dict:
    """The default verdict for anything not read: never warns (D3)."""
    return {
        "filename": filename,
        "status": "unscanned",
        "label": None,
        "confidence": None,
        "rationale": None,
        "severity": "allow",
    }


def _errored(filename: str) -> dict:
    return {**_unscanned(filename), "status": "error"}


def _screen_attachment(attachment: ScreenAttachment) -> dict:
    """
    Decode, parse and classify one attachment.

    Fails open per attachment: any failure here yields "error"/"allow" so that
    one unreadable file neither warns spuriously nor costs the sender the
    screening of the other attachments in the same mail.
    """
    filename = attachment.filename

    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return _unscanned(filename)

    try:
        raw = base64.b64decode(attachment.content_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        logger.warning("Screening: undecodable base64 for %r: %s", filename, e)
        return _errored(filename)

    if len(raw) > MAX_DECODED_BYTES:
        return _unscanned(filename)

    try:
        # parse_document reads from disk, so the bytes need a real file with the
        # right suffix (the same handling app.main's /classify does for uploads).
        with tempfile.NamedTemporaryFile(
            suffix=Path(filename).suffix.lower(), delete=False
        ) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        try:
            text = parse_document(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if not text or not text.strip():
            return _errored(filename)

        result = classify_confidentiality(text)
    except Exception as e:
        logger.warning("Screening failed for %r: %s", filename, e)
        return _errored(filename)

    label = result["label"]
    return {
        "filename": filename,
        "status": "screened",
        "label": label,
        "confidence": result["confidence"],
        "rationale": result["rationale"],
        # Unknown labels cannot be assumed harmless, but under D3 the worst
        # outcome is still only a warning.
        "severity": _SEVERITY_BY_LABEL.get(label, "warn"),
    }


def _format_recipients(external: list[str]) -> str:
    if len(external) <= _MAX_LISTED_RECIPIENTS:
        return ", ".join(external)
    shown = ", ".join(external[:_MAX_LISTED_RECIPIENTS])
    return f"{shown}, and {len(external) - _MAX_LISTED_RECIPIENTS} more"


def _build_message(results: list[dict], external: list[str]) -> str:
    """
    The text of the Outlook dialog: what was found, who it is going to, and a
    question the sender can answer. Named strongest-label-first because that is
    the fact that decides whether they stop.
    """
    warned = [r for r in results if r["severity"] == "warn"]
    if not warned:
        return ""

    strongest = max(warned, key=lambda r: _LABEL_RANK.get(r["label"], 0))
    label = (strongest["label"] or "sensitive").upper()

    count = len(warned)
    noun = "attachment appears" if count == 1 else "attachments appear"
    rationale = (strongest["rationale"] or "").strip()
    detail = f" ({rationale})" if rationale else ""

    return (
        f"{count} {noun} {label}{detail}. "
        f"Recipients outside the firm: {_format_recipients(external)}. "
        f"Send anyway?"
    )


@router.post("/screen")
def screen(body: ScreenRequest, x_screen_key: str | None = Header(default=None)):
    """
    Screen an outgoing mail's attachments and return warn/allow for the add-in.

    Two shortcuts keep the common case free: an all-internal mail and a mail
    with no attachments both return "allow" without decoding, parsing, or
    calling the LLM. Only mail actually leaving the firm costs anything.
    """
    _require_key(x_screen_key)

    external = _external_recipients(body.recipients)

    if not external or not body.attachments:
        return {
            "external_recipients": external,
            "attachments": [_unscanned(a.filename) for a in body.attachments],
            "verdict": "allow",
            "message": "",
        }

    considered = body.attachments[:MAX_ATTACHMENTS]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(_screen_attachment, considered))
    results.extend(_unscanned(a.filename) for a in body.attachments[MAX_ATTACHMENTS:])

    verdict = "warn" if any(r["severity"] == "warn" for r in results) else "allow"

    return {
        "external_recipients": external,
        "attachments": results,
        "verdict": verdict,
        "message": _build_message(results, external) if verdict == "warn" else "",
    }
