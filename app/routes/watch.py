"""
Watched-folder API — status feed and directory configuration.

Thin HTTP surface over app.watcher, which owns the polling thread and all state.
This module deliberately holds no state of its own: the watcher is a process
singleton, so the route can only ever read or nudge it.

Included by app.main via:
    from app.routes import watch
    app.include_router(watch.router)

All paths live under /watch, which app.main's auth middleware already protects
(/watch is in _PROTECTED_PREFIXES) — no per-route auth needed here.

Note on POST /watch/dir vs PATCH /settings: both write the same runtime setting
("watch_dir"). This endpoint additionally *validates* that the path is a real
directory, so a typo is rejected at save time rather than silently leaving the
watcher idle. Prefer this one from the watch UI.
"""

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import runtime_settings, watcher

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/watch/status")
def watch_status():
    """
    Current watcher state: whether the thread is alive, the configured folder,
    last scan time, how many files it has classified, and the most recent
    outcomes (newest first).
    """
    return watcher.get_status()


class WatchDirBody(BaseModel):
    path: str


@router.post("/watch/dir")
def set_watch_dir(body: WatchDirBody):
    """
    Point the watcher at a directory, or disable it.

    An empty string is valid and means "stop watching" — that's how the UI turns
    the feature off, so it must not be treated as a validation failure. Any
    non-empty value must resolve to an existing directory: accepting a path that
    doesn't exist would leave the watcher permanently idle with no visible
    reason why.
    """
    path = (body.path or "").strip()

    if path:
        if not os.path.exists(path):
            raise HTTPException(
                status_code=422,
                detail=f"No such folder: {path}",
            )
        if not os.path.isdir(path):
            raise HTTPException(
                status_code=422,
                detail=f"Not a folder (it's a file): {path}",
            )
        # Catching this now beats surfacing it as a silent no-op every 10s.
        if not os.access(path, os.R_OK):
            raise HTTPException(
                status_code=422,
                detail=f"Folder is not readable: {path}",
            )

    runtime_settings.set_setting("watch_dir", path)
    logger.info("Watch directory set to %r", path or "(disabled)")

    return watcher.get_status()
