"""
Content-addressed caching for LLM pipeline calls.

Why: temperature-0 sampling is *close* to deterministic but not guaranteed —
re-classifying the same document could still drift (different pain point
wording, slightly different confidence). Caching the first answer and replaying
it for identical inputs makes the pipeline strictly deterministic for unchanged
documents, and makes repeat classification instant and free.

Usage:
    result = cached_call(
        ["pain_points_v2", text],          # key parts: pipeline + version + inputs
        lambda: expensive_llm_extraction() # only runs on a cache miss
    )

Rules:
  - Bump the version string in the key parts whenever the prompt or output
    shape changes, or stale cached shapes will replay forever.
  - compute() raising means NOTHING is cached — failures are never frozen in.
  - Values must be JSON-serializable.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.database import LLMCache, get_session

logger = logging.getLogger(__name__)


def _key(parts: list) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x1f")  # separator so ["ab","c"] != ["a","bc"]
    return h.hexdigest()


def cached_call(key_parts: list, compute: Callable[[], Any]) -> Any:
    key = _key(key_parts)

    try:
        with get_session() as session:
            row = session.get(LLMCache, key)
            if row is not None:
                return json.loads(row.value)
    except Exception as e:
        # A broken cache read must never break classification.
        logger.warning(f"LLM cache read failed ({e}); computing fresh")

    value = compute()  # raises → nothing cached, caller's error handling applies

    try:
        with get_session() as session:
            session.merge(LLMCache(
                key=key,
                value=json.dumps(value),
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()
    except Exception as e:
        logger.warning(f"LLM cache write failed ({e}); result still returned")

    return value
