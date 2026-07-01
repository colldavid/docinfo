from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
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

    # Paths
    model_dir: Path = Path("model")
    cache_dir: Path = Path("cache")
    data_dir: Path = Path("data")

    class Config:
        env_file = ".env.local"
        env_file_encoding = "utf-8"


settings = Settings()
