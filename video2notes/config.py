"""
Centralized configuration, loaded from environment variables (or a .env file
in dev). Every subsystem (DB, Redis/Celery, S3, LLM providers) reads from here
instead of touching os.environ directly, so there's one place to see every
knob the service exposes.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "video2notes"
    environment: str = "development"  # development | staging | production
    api_v1_prefix: str = "/api/v1"

    # --- Auth ---
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24  # 24h
    algorithm: str = "HS256"

    # --- Database ---
    # Defaults to a local SQLite file so the service runs out of the box
    # without any external dependencies; point this at Postgres in real
    # deployments, e.g. postgresql+psycopg2://user:pass@host:5432/video2notes
    database_url: str = "sqlite:///./video2notes.db"

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"
    # If true, Celery tasks run synchronously in-process instead of being
    # dispatched to a worker over the broker -- used for local dev without
    # Redis running, and for tests.
    celery_task_always_eager: bool = False

    # --- Object storage (S3-compatible: AWS S3, MinIO, etc.) ---
    s3_endpoint_url: Optional[str] = None  # e.g. http://localhost:9000 for MinIO; None = real AWS
    s3_bucket: str = "video2notes"
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_region: str = "us-east-1"
    # If true, storage falls back to the local filesystem under ./local_storage
    # instead of S3 -- handy for zero-dependency local dev.
    use_local_storage_fallback: bool = True
    local_storage_dir: str = "./local_storage"

    # --- LLM providers ---
    llm_provider: str = "local"  # "local" | "openai"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"

    # --- Transcription ---
    whisper_engine: str = "local"  # "local" | "api"
    whisper_local_model: str = "medium"
    whisper_local_device: str = "auto"
    whisper_local_compute_type: str = "auto"

    # --- Pipeline defaults (mirror video2doc's config.yaml, used unless a
    # request overrides them) ---
    frame_interval_seconds: int = 5
    frame_scene_threshold: float = 27.0
    dedup_hamming_threshold: int = 5
    chunk_similarity_threshold: float = 0.15
    chunk_max_seconds: float = 60.0
    chunk_max_frames: int = 4
    summary_group_size: int = 6
    summary_max_group_seconds: float = 180.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
