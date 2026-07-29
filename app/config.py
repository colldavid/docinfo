from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path

# Project root is the directory containing this file's parent (app/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = {"env_file": str(_PROJECT_ROOT / ".env.local"), "env_file_encoding": "utf-8", "protected_namespaces": ()}

    anthropic_api_key: str
    database_url: str = "sqlite:///local.db"

    # ── LLM provider config ────────────────────────────────────────────────
    # The project is provider-agnostic: point it at whatever the firm approves.
    #   llm_provider: "anthropic" (default) or "openai" (covers Azure OpenAI / Copilot-tenant)
    #   llm_model:    the model id for that provider
    #   llm_base_url: optional override for the API endpoint (e.g. an Azure/enterprise gateway)
    # Only the reasoning tasks (confidentiality-refine, importance, pain points,
    # summary, theme, action items) use the LLM. Classification is fully local.
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_base_url: str | None = None
    openai_api_key: str | None = None

    # Embedding model — swap here if upgrading
    embedding_model: str = "all-MiniLM-L6-v2"

    # Pain point matching threshold (cosine similarity)
    pain_point_threshold: float = 0.40

    # Consistency check: re-run count when importance confidence < threshold
    consistency_check_confidence_threshold: float = 0.4
    consistency_check_runs: int = 2
    consistency_check_temperature: float = 0.4

    # Classifier needs_review thresholds — applied to rescaled confidence (0=random, 1=certain)
    doc_type_review_threshold: float = 0.20
    industry_review_threshold: float = 0.15

    # Paths — resolved relative to project root so CLI works from any directory
    model_dir: Path = Field(default=_PROJECT_ROOT / "model")
    cache_dir: Path = Field(default=_PROJECT_ROOT / "cache")
    data_dir: Path = Field(default=_PROJECT_ROOT / "data")


settings = Settings()
