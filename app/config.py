from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path

# Project root is the directory containing this file's parent (app/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = {"env_file": str(_PROJECT_ROOT / ".env.local"), "env_file_encoding": "utf-8", "protected_namespaces": ()}

    anthropic_api_key: str
    database_url: str = "sqlite:///local.db"

    # Embedding model — swap here if upgrading
    embedding_model: str = "all-MiniLM-L6-v2"

    # Pain point matching threshold (cosine similarity)
    pain_point_threshold: float = 0.65

    # Consistency check: re-run count when importance confidence < threshold
    consistency_check_confidence_threshold: float = 0.7
    consistency_check_runs: int = 3
    consistency_check_temperature: float = 0.4

    # Classifier needs_review thresholds (lower = only flag truly uncertain)
    doc_type_review_threshold: float = 0.5
    industry_review_threshold: float = 0.3

    # Paths — resolved relative to project root so CLI works from any directory
    model_dir: Path = Field(default=_PROJECT_ROOT / "model")
    cache_dir: Path = Field(default=_PROJECT_ROOT / "cache")
    data_dir: Path = Field(default=_PROJECT_ROOT / "data")


settings = Settings()
