"""
Runtime settings API — the backend for the Settings page.

Lets the operator change LLM provider/model/base_url, the classifier
needs_review thresholds, and the watch directory without editing .env.local and
restarting. Everything is written through app.runtime_settings, which layers an
allowlisted key/value store (app_settings table) over the .env-backed pydantic
Settings object.

Security: *provider* API keys are NEVER settable or readable here. The GET
response only reports *whether* each provider's key is present as a boolean, so
the UI can grey out a provider you can't actually switch to. Provider key
material never leaves the environment.

The screening key (screen_api_key) is the one deliberate exception — see the
comment on SCREEN_API_KEY_MIN_LENGTH. It is app-issued rather than a third-party
secret, and the operator has to be able to read it back to paste it into the
Outlook add-in.

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
import re
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


# A domain label set, not a hostname parser: lowercase alphanumerics, dots and
# hyphens only. Anything with an "@", a space, or no dot at all is far more
# likely to be a pasted email address or a typo than an internal domain, and
# silently accepting it would silently mark every recipient external.
_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+$")

# Below this length a shared secret is guessable enough that "screening is on"
# would be misleading. 8 is a floor, not an endorsement — the UI's generate
# button produces 24.
SCREEN_API_KEY_MIN_LENGTH = 8


def _validate_firm_domains(raw: str) -> str:
    """
    Normalise "Accenture.COM , accenture.mx" to "accenture.com,accenture.mx".

    Returns the canonical stored form. Empty input is valid and means
    "unconfigured" — the caller treats that as every recipient being external.
    """
    parts = [part.strip().lower() for part in str(raw).split(",")]
    domains = [part for part in parts if part]

    for domain in domains:
        if "@" in domain or not _DOMAIN_RE.match(domain) or "." not in domain:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{domain}' is not a valid domain. Use bare domains separated "
                    "by commas, e.g. 'accenture.com, accenture.mx'."
                ),
            )

    # De-duplicate while preserving the operator's ordering, so a paste with a
    # repeated domain round-trips to something stable in the UI.
    return ",".join(dict.fromkeys(domains))


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

    Note for reviewers: `settings` carries screen_api_key in the clear, unlike
    the provider keys above. That is deliberate, not an oversight. A provider key
    is a third-party secret we only ever need to *use*, so it stays in the
    environment and is reported as a boolean. screen_api_key is a credential this
    app issues, and the operator must be able to read it back to paste it into
    the Outlook add-in's configuration — a write-only field would make the
    feature unusable. /settings already sits behind app.main's auth middleware,
    so the exposure is to authenticated operators only.
    """
    effective = runtime_settings.effective()
    return {
        "settings": effective,
        "provider_keys": _provider_keys(),
        "model_suggestions": MODEL_SUGGESTIONS,
        "cache_entries": _cache_entries(),
        # Saves every caller a string comparison to answer "is screening live?".
        "screening_configured": bool(effective.get("screen_api_key", "")),
    }


class SettingsPatch(BaseModel):
    """All fields optional — a PATCH writes only what it names."""
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None
    doc_type_review_threshold: Optional[float] = None
    industry_review_threshold: Optional[float] = None
    watch_dir: Optional[str] = None
    firm_domains: Optional[str] = None
    screen_api_key: Optional[str] = None


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

    if "firm_domains" in provided:
        # Empty is valid and meaningful: unconfigured, so every recipient counts
        # as external and screening warns on all of them.
        writes["firm_domains"] = _validate_firm_domains(provided["firm_domains"])

    if "screen_api_key" in provided:
        key = str(provided["screen_api_key"]).strip()
        # Empty disables screening outright, which is a safe state. A *short*
        # key is the dangerous one: it leaves the endpoint serving document
        # content behind a secret worth guessing.
        if key and len(key) < SCREEN_API_KEY_MIN_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"screen_api_key must be at least {SCREEN_API_KEY_MIN_LENGTH} "
                    "characters, or empty to disable screening."
                ),
            )
        writes["screen_api_key"] = key

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
