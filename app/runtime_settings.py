"""
Runtime-mutable settings, layered over the .env-backed pydantic Settings.

The pydantic `settings` object is read once at startup; this module lets the
Settings UI change a small allowlisted set of values at runtime (stored in the
app_settings table) without a restart. Read paths fall back to the .env value
when no override is stored.

Security note: API keys are deliberately NOT runtime-settable — they live only
in the environment. Provider/model/base_url are; switching provider only works
if that provider's key is present in the environment.
"""

import logging

from app.config import settings
from app.database import AppSetting, get_session

logger = logging.getLogger(__name__)

# Keys the Settings API may write, with their .env-backed fallbacks.
ALLOWED_KEYS = {
    "llm_provider": lambda: settings.llm_provider,
    "llm_model": lambda: settings.llm_model,
    "llm_base_url": lambda: settings.llm_base_url or "",
    "doc_type_review_threshold": lambda: settings.doc_type_review_threshold,
    "industry_review_threshold": lambda: settings.industry_review_threshold,
    "watch_dir": lambda: "",
}


def get_setting(key: str) -> str:
    """Stored override, or the .env-backed default. Always returns a string."""
    if key not in ALLOWED_KEYS:
        raise KeyError(f"Unknown runtime setting: {key}")
    try:
        with get_session() as session:
            row = session.get(AppSetting, key)
            if row is not None:
                return row.value
    except Exception as e:
        logger.warning(f"Runtime settings read failed for {key!r} ({e}); using default")
    return str(ALLOWED_KEYS[key]())


def get_float(key: str) -> float:
    try:
        return float(get_setting(key))
    except (TypeError, ValueError):
        return float(ALLOWED_KEYS[key]())


def set_setting(key: str, value: str) -> None:
    if key not in ALLOWED_KEYS:
        raise KeyError(f"Unknown runtime setting: {key}")
    with get_session() as session:
        session.merge(AppSetting(key=key, value=str(value)))
        session.commit()


def effective() -> dict:
    """Every allowlisted setting's current effective value (for the Settings UI)."""
    return {key: get_setting(key) for key in ALLOWED_KEYS}
