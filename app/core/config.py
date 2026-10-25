"""Validated application configuration loaded from environment or .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    project_name: str = "IncuBrix Video Editing Engine"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_allowed_origins: list[str] | None = None
    llm_provider: Literal["groq", "mock"] | None = None
    groq_model: str = "openai/gpt-oss-20b"
    groq_api_key: SecretStr | None = None
    idempotency_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    idempotency_ttl_seconds: int = 86400
    idempotency_processing_ttl_seconds: int = 300
    idempotency_memory_limit: int = 4096
    max_idempotency_body_bytes: int = 10_000_000
    storage_backend: Literal["local", "minio", "s3"] = "local"
    storage_path: Path = Path("storage")
    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_bucket_name: str = "incubrix-media"
    max_upload_bytes: int = 2_000_000_000
    max_concurrent_jobs: int = 4
    job_processing_ttl_seconds: int = 300
    job_retention_seconds: int = 604800
    whisper_model_size: str = "base"
    whisper_device: Literal["cpu", "cuda", "auto"] = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str | None = None
    llm_context_max_words: int = 12000

    @model_validator(mode="after")
    def infer_llm_provider(self):
        if self.cors_allowed_origins is None:
            self.cors_allowed_origins = [
                f"http://localhost:{self.port}",
                f"http://127.0.0.1:{self.port}",
            ]
        if self.llm_provider is None:
            api_key = self.groq_api_key.get_secret_value() if self.groq_api_key else ""
            self.llm_provider = "groq" if api_key and not api_key.startswith("your-") else "mock"
        return self

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = []
        for origin in value:
            candidate = origin.strip().rstrip("/")
            parsed = urlsplit(candidate)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in candidate
            ):
                raise ValueError("CORS_ALLOWED_ORIGINS must contain explicit HTTP(S) origins without paths or wildcards.")
            normalized.append(candidate)
        return list(dict.fromkeys(normalized))

    @field_validator("idempotency_ttl_seconds", "idempotency_processing_ttl_seconds", "idempotency_memory_limit", "max_idempotency_body_bytes", "max_upload_bytes", "max_concurrent_jobs", "job_processing_ttl_seconds", "job_retention_seconds", "llm_context_max_words")
    @classmethod
    def values_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Configuration limits must be positive.")
        return value

    @field_validator("port")
    @classmethod
    def port_must_be_valid(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("PORT must be between 1 and 65535.")
        return value

    @field_validator("groq_model")
    @classmethod
    def model_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("GROQ_MODEL must not be blank.")
        return value

    @field_validator("whisper_language")
    @classmethod
    def whisper_language_must_be_supported(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        from faster_whisper.tokenizer import _LANGUAGE_CODES

        normalized = value.strip().lower()
        if normalized not in _LANGUAGE_CODES:
            raise ValueError("WHISPER_LANGUAGE must be a supported Whisper language code, such as 'en' or 'ta'.")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
