"""
Content-addressed caching for LLM pipeline calls.

Why: temperature-0 sampling is *close* to deterministic but not guaranteed —
re-classifying the same document could still drift. Caching the first answer
and replaying it for identical inputs makes the pipeline strictly repeatable
for unchanged documents, and makes repeat classification instant and free.

Keying (all automatic — nothing to remember when editing a prompt):
    key = sha256(provider, model, pipeline, prompt_hash, *inputs)
where prompt_hash = sha256(the prompt template itself). Editing a prompt
changes its hash, which both (a) makes old entries unreachable and (b) triggers
a purge: each row stores its pipeline + prompt_hash, and the first cached_call
for a pipeline in a process deletes that pipeline's rows written under any
other prompt hash. No manual version strings, no stale replays, no dead rows.

Size is bounded (settings.llm_cache_max_entries, default 5000 ≈ 1000 documents'
worth): oldest entries are evicted first. This is disk hygiene, not a
performance need — key lookups are indexed and fast at any realistic size.

Rules:
  - compute() raising means NOTHING is cached — failures are never frozen in.
  - Values must be JSON-serializable.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import settings
from app.database import LLMCache, get_session

logger = logging.getLogger(__name__)

# Pipelines whose stale-prompt rows were already purged this process.
_purged: set[str] = set()


def _hash(parts: list) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x1f")  # separator so ["ab","c"] != ["a","bc"]
    return h.hexdigest()


def _purge_stale(pipeline: str, prompt_hash: str) -> None:
    """
    Delete this pipeline's rows written under a different (older) prompt hash,
    plus any legacy rows from before metadata columns existed. Runs once per
    pipeline per process; failures never block the actual call.
    """
    if pipeline in _purged:
        return
    _purged.add(pipeline)
    try:
        with get_session() as session:
            stale = session.query(LLMCache).filter(
                (LLMCache.pipeline.is_(None))
                | ((LLMCache.pipeline == pipeline) & (LLMCache.prompt_hash != prompt_hash))
            )
            removed = stale.delete(synchronize_session=False)
            session.commit()
            if removed:
                logger.info(f"Purged {removed} stale cache entries for pipeline {pipeline!r}")
    except Exception as e:
        logger.warning(f"Cache purge failed for {pipeline!r} ({e}); continuing")


def _evict_over_cap(session) -> None:
    """Oldest-first eviction beyond the configured cap. Caller owns the commit."""
    cap = settings.llm_cache_max_entries
    count = session.query(LLMCache).count()
    excess = count - cap
    if excess <= 0:
        return
    oldest = session.query(LLMCache.key).order_by(LLMCache.created_at).limit(excess).all()
    keys = [row.key for row in oldest]
    session.query(LLMCache).filter(LLMCache.key.in_(keys)).delete(synchronize_session=False)
    logger.info(f"Evicted {len(keys)} cache entries over the {cap}-entry cap")


def cached_call(pipeline: str, prompt: str, inputs: list, compute: Callable[[], Any]) -> Any:
    """
    Replay the cached result for (provider, model, prompt, inputs), or compute
    and store it.

    `prompt` is the pipeline's prompt template — passed so its hash keys the
    entry and so prompt edits self-invalidate. `inputs` is everything else that
    shapes the output (document text, labels, ...).
    """
    # The effective provider/model is part of every key: switching models in
    # Settings must never replay another model's cached answers.
    from app.pipelines.llm_client import _resolve
    provider, model, _ = _resolve()

    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    key = _hash([provider, model, pipeline, prompt_hash, *inputs])

    _purge_stale(pipeline, prompt_hash)

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
                pipeline=pipeline,
                prompt_hash=prompt_hash,
                value=json.dumps(value),
                created_at=datetime.now(timezone.utc),
            ))
            _evict_over_cap(session)
            session.commit()
    except Exception as e:
        logger.warning(f"LLM cache write failed ({e}); result still returned")

    return value
