"""
Runtime settings API — the backend for the Settings page.

Lets the operator change LLM provider/model/base_url, the classifier
needs_review thresholds, and the watch directory without editing .env.local and
restarting. Everything is written through app.runtime_settings, which layers an
allowlisted key/value store (app_settings table) over the .env-backed pydantic
Settings object.

Security: API keys are NEVER settable or readable here. The GET response only
reports *whether* each provider's key is present as a boolean, so the UI can
grey out a provider you can't actually switch to. Key material never leaves the
environment.

Also exposes cache maintenance: the LLM cache makes repeat classification of an
unchanged document byte-identical and free, so clearing it is a deliberate
"re-run everything against the current model" action that costs API credits.

Included by app.main via:
    from app.routes import app_settings
    app.include_router(app_settings.router)

All paths live under /settings, which app.main's auth middleware already
protects — no per-route auth needed here.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import runtime_settings
from app.config import settings
from app.database import LLMCache, get_session

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_PROVIDERS = ("anthropic", "openai")

# Suggestions for the model <datalist>. Deliberately not a closed set — free
# text is still accepted, since enterprise gateways expose custom deployment
# names that no hardcoded list could anticipate.
MODEL_SUGGESTIONS = {
    "anthropic": [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5",
        "claude-opus-4-5",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
    ],
}


def _provider_keys() -> dict[str, bool]:
    """Which providers have a usable API key in the environment. Booleans only."""
    return {
        "anthropic": bool(settings.anthropic_api_key),
        "openai": bool(settings.openai_api_key),
    }


def _cache_entries() -> int:
    with get_session() as session:
        return session.query(LLMCache).count()


@router.get("/settings")
def get_settings():
    """
    Current effective settings plus the context the UI needs to render safely:
    which providers are actually usable, model suggestions, and cache size.
    """
    return {
        "settings": runtime_settings.effective(),
        "provider_keys": _provider_keys(),
        "model_suggestions": MODEL_SUGGESTIONS,
        "cache_entries": _cache_entries(),
    }


class SettingsPatch(BaseModel):
    """All fields optional — a PATCH writes only what it names."""
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None
    doc_type_review_threshold: Optional[float] = None
    industry_review_threshold: Optional[float] = None
    watch_dir: Optional[str] = None


def _validate_threshold(name: str, value: float) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{name} must be a number between 0 and 1.")
    if not (0.0 <= parsed <= 1.0):
        raise HTTPException(
            status_code=422,
            detail=f"{name} must be between 0 and 1 (got {parsed}).",
        )
    return str(parsed)


@router.patch("/settings")
def patch_settings(body: SettingsPatch):
    """
    Update one or more runtime settings.

    Everything is validated *before* the first write, so a request with one bad
    field can never leave a half-applied configuration behind.
    """
    provided = body.model_dump(exclude_unset=True)
    # exclude_unset misses explicit nulls; treat those as "not provided" too.
    provided = {k: v for k, v in provided.items() if v is not None}

    if not provided:
        raise HTTPException(
            status_code=422,
            detail="No settings provided. Include at least one field to update.",
        )

    # ── Validate everything first ──────────────────────────────────────────
    writes: dict[str, str] = {}

    if "llm_provider" in provided:
        provider = str(provided["llm_provider"]).strip()
        if provider not in VALID_PROVIDERS:
            raise HTTPException(
                status_code=422,
                detail=f"llm_provider must be one of: {', '.join(VALID_PROVIDERS)}.",
            )
        # Switching to a provider whose key is absent would break every LLM
        # pipeline at the next classify — refuse it here instead.
        if not _provider_keys()[provider]:
            env_var = f"{provider.upper()}_API_KEY"
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot switch to '{provider}': no API key configured. "
                    f"Set {env_var} in .env.local and restart."
                ),
            )
        writes["llm_provider"] = provider

    if "llm_model" in provided:
        model = str(provided["llm_model"]).strip()
        if not model:
            raise HTTPException(status_code=422, detail="llm_model cannot be empty.")
        writes["llm_model"] = model

    if "llm_base_url" in provided:
        # Empty string is meaningful: "use the provider's default endpoint".
        writes["llm_base_url"] = str(provided["llm_base_url"]).strip()

    if "doc_type_review_threshold" in provided:
        writes["doc_type_review_threshold"] = _validate_threshold(
            "doc_type_review_threshold", provided["doc_type_review_threshold"]
        )

    if "industry_review_threshold" in provided:
        writes["industry_review_threshold"] = _validate_threshold(
            "industry_review_threshold", provided["industry_review_threshold"]
        )

    if "watch_dir" in provided:
        # No existence check — the watcher reports an unreadable path itself,
        # and the directory may legitimately be created after it's configured.
        writes["watch_dir"] = str(provided["watch_dir"]).strip()

    # ── Apply ──────────────────────────────────────────────────────────────
    for key, value in writes.items():
        runtime_settings.set_setting(key, value)

    logger.info("Runtime settings updated: %s", ", ".join(sorted(writes)))
    return runtime_settings.effective()


@router.post("/settings/clear-cache")
def clear_cache():
    """
    Drop every cached LLM result.

    The cache is what makes re-classifying an unchanged document produce
    identical output for free. Clearing it means the next classify re-runs all
    LLM analysis against the current provider/model — which is exactly what you
    want after a model change, and which costs API credits.
    """
    with get_session() as session:
        cleared = session.query(LLMCache).delete()
        session.commit()
    logger.info("LLM cache cleared: %s entries removed", cleared)
    return {"cleared": cleared}
