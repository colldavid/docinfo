"""
Watched-folder ingestion: auto-classify documents dropped into a directory.

A consultant points DocInfo at a folder (an export directory, a shared drive
mount) and every new .pdf/.docx/.txt that appears there is parsed, classified,
and persisted exactly as if it had been uploaded through the UI — it shows up in
History alongside manual uploads.

Design:
  A single daemon thread polls the folder every POLL_SECONDS. Polling rather
  than filesystem events (watchdog) is deliberate: no new dependency, and it
  works over network shares and Docker bind mounts where inotify/ReadDirectory
  notifications are unreliable or silently absent.

  This module must never import app.main — the watcher is started *by* the app,
  so importing back would be circular. Persistence therefore goes through
  app.persistence.persist_record, which exists for exactly this reason.

COST AND DETERMINISM — read before pointing this at a large folder:
  Each newly-seen file runs the full classification pipeline, which costs the
  same LLM calls as a manual upload. Dropping 200 documents into the watched
  folder spends 200 documents' worth of API credits, unattended. There is no
  confirmation step.

  Re-adding an *identical* file is cheap: the LLM cache (app.database.LLMCache)
  is content-addressed, so every pipeline result replays from cache and the
  re-classification is free and byte-identical. The cost is only paid for
  content the app has never analyzed before.

Known limitation — restart de-duplication is by filename:
  Processed files are tracked in memory by (path, mtime), which is lost on
  restart. To avoid re-classifying the whole folder on every boot, a file is
  also skipped when its *bare filename* already exists in the classifications
  table. Consequence: a genuinely different document that happens to share a
  filename with something classified earlier (a second "report.pdf" from another
  client) is silently skipped by the watcher. Such files must be uploaded
  manually. This is the conservative direction to err — skipping is free,
  double-classifying costs credits.
"""

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.classify import classify_document
from app.database import ClassificationRecord, get_session
from app.ingestion import parse_document
from app.persistence import persist_record
from app import runtime_settings

logger = logging.getLogger(__name__)

POLL_SECONDS = 10

# A file must be at least this old (by mtime) before we touch it. A large PDF
# being copied into the folder appears in the directory listing long before its
# bytes have landed; parsing it early yields a truncated document or an outright
# parse error. Anything too fresh is simply left for the next poll.
STABILITY_SECONDS = 3

ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}

RECENT_LIMIT = 10

# ---------------------------------------------------------------------------
# Module-level singleton state
#
# _lock guards every field below. The poll loop and the HTTP thread serving
# get_status() run concurrently, so status reads must never observe a
# half-updated list.
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_thread: threading.Thread | None = None
_running = False
_stop_event = threading.Event()

_processed: set[tuple[str, float]] = set()  # (absolute path, mtime) already attempted
_last_scan: datetime | None = None
_processed_count = 0
_recent: list[dict] = []  # newest first, capped at RECENT_LIMIT
_pending_hint = 0  # files seen on the last scan but skipped as too-new


def _record_outcome(filename: str, status: str, detail: str = "") -> None:
    """Push one file outcome onto the recent list (newest first, capped)."""
    global _processed_count
    entry = {
        "filename": filename,
        "status": status,
        "detail": detail,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        _recent.insert(0, entry)
        del _recent[RECENT_LIMIT:]
        if status == "classified":
            _processed_count += 1


# ---------------------------------------------------------------------------
# Selection logic (pure — unit-testable without a filesystem or an LLM)
# ---------------------------------------------------------------------------

def _select_new_files(
    entries: list[tuple[str, float]],
    processed: set[tuple[str, float]],
    known_filenames: set[str],
    now: float,
) -> tuple[list[tuple[str, float]], int]:
    """
    Decide which of `entries` to classify on this poll.

    `entries` is [(absolute path, mtime)] for candidate files — the caller has
    already filtered to allowed suffixes. Returns (selected, too_new_count).

    A file is selected when all of these hold:
      * (path, mtime) has not already been attempted this process lifetime
        — mtime is part of the key, so an edited file is legitimately re-seen;
      * its bare filename is not already in the database (restart de-dup, see
        the module docstring for the false-negative this trades for);
      * it has been untouched for at least STABILITY_SECONDS.

    Kept free of I/O so the decision rules can be tested directly; the real scan
    supplies the entries and the DB filename set.
    """
    selected: list[tuple[str, float]] = []
    too_new = 0

    for path, mtime in sorted(entries):
        if (path, mtime) in processed:
            continue
        if now - mtime < STABILITY_SECONDS:
            # Still being written, most likely. Not marked processed — we want
            # to see it again on the next poll.
            too_new += 1
            continue
        if os.path.basename(path) in known_filenames:
            continue
        selected.append((path, mtime))

    return selected, too_new


def _scan_entries(directory: Path) -> list[tuple[str, float]]:
    """Non-recursive listing of classifiable files as [(abs path, mtime)]."""
    entries: list[tuple[str, float]] = []
    with os.scandir(directory) as it:
        for entry in it:
            try:
                if not entry.is_file():
                    continue
                if Path(entry.name).suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                entries.append((os.path.abspath(entry.path), entry.stat().st_mtime))
            except OSError as e:
                # A file removed mid-scan, or one we lack permission to stat.
                logger.debug("Watcher could not stat %s: %s", entry.path, e)
    return entries


def _known_filenames() -> set[str]:
    """Every filename already in the classifications table."""
    with get_session() as session:
        return {row[0] for row in session.query(ClassificationRecord.filename).all()}


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def _process_file(path: Path) -> None:
    """
    Parse, classify, and persist one document.

    Raises on failure — the caller turns that into an error entry in the status
    feed. Nothing here is allowed to take down the poll loop.
    """
    text = parse_document(path)
    if not text or not text.strip():
        raise ValueError("Could not extract text from document")

    result = classify_document(path, text)

    with get_session() as session:
        persist_record(result, session, text=text)
        session.commit()


def _poll_once() -> None:
    """One pass of the watch loop. Never raises."""
    global _last_scan, _pending_hint

    watch_dir = (runtime_settings.get_setting("watch_dir") or "").strip()

    with _lock:
        _last_scan = datetime.now(timezone.utc)

    if not watch_dir or not os.path.isdir(watch_dir):
        # Idle rather than an error: an unconfigured (or not-yet-created)
        # folder is the normal state for most installs.
        with _lock:
            _pending_hint = 0
        return

    entries = _scan_entries(Path(watch_dir))

    with _lock:
        processed_snapshot = set(_processed)

    selected, too_new = _select_new_files(
        entries, processed_snapshot, _known_filenames(), datetime.now().timestamp()
    )

    with _lock:
        _pending_hint = too_new

    for path_str, mtime in selected:
        path = Path(path_str)
        try:
            _process_file(path)
            _record_outcome(path.name, "classified")
            logger.info("Watcher classified %s", path.name)
        except Exception as e:
            _record_outcome(path.name, "error", str(e))
            logger.error("Watcher failed on %s: %s", path.name, e)
        finally:
            # Marked processed either way — a file that fails to parse would
            # otherwise be retried every 10 seconds forever. Editing the file
            # changes its mtime, which makes it eligible again.
            with _lock:
                _processed.add((path_str, mtime))


def _loop() -> None:
    while not _stop_event.is_set():
        try:
            _poll_once()
        except Exception as e:
            # Defensive: _poll_once already swallows per-file errors, but a
            # failure in the DB or settings read must not end the thread.
            logger.error("Watcher poll failed: %s", e)
        _stop_event.wait(POLL_SECONDS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_watcher() -> bool:
    """
    Start the polling thread if it isn't already running.

    Idempotent — calling twice is safe and leaves exactly one thread alive.
    Returns True if this call started the thread, False if it was already up.
    Called from app startup; under a multi-worker deployment each worker starts
    its own watcher, which is why filename de-duplication matters.
    """
    global _thread, _running

    with _lock:
        if _running and _thread is not None and _thread.is_alive():
            return False
        _stop_event.clear()
        _thread = threading.Thread(target=_loop, name="docinfo-watcher", daemon=True)
        _running = True
        _thread.start()
        logger.info("Folder watcher started (poll every %ss)", POLL_SECONDS)
        return True


def stop_watcher() -> None:
    """Signal the loop to exit. Used by tests; the daemon thread dies with the process."""
    global _running, _thread
    _stop_event.set()
    with _lock:
        _running = False
        _thread = None


def get_status() -> dict:
    """Current watcher state, for GET /watch/status and the Settings UI."""
    watch_dir = ""
    try:
        watch_dir = (runtime_settings.get_setting("watch_dir") or "").strip()
    except Exception as e:
        logger.warning("Watcher could not read watch_dir: %s", e)

    with _lock:
        alive = _running and _thread is not None and _thread.is_alive()
        return {
            "running": alive,
            "watch_dir": watch_dir,
            "last_scan": _last_scan.isoformat() if _last_scan else None,
            "processed_count": _processed_count,
            "recent": list(_recent),
            "pending_hint": _pending_hint,
        }
