"""Configuration contains no credentials; model construction is lazy."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    google_api_key: SecretStr | None = None
    github_token: SecretStr | None = None
    api_token: SecretStr | None = None
    strong_model_name: str = "gemini-3.8-flash"
    data_dir: Path = Path(".review-data")
    enable_github_posting: bool = False
    max_files: int = Field(default=20, ge=1, le=100)
    max_file_bytes: int = Field(default=100_000, ge=1)
    max_prompt_chars: int = Field(default=180_000, ge=1000)
    model_timeout_seconds: int = Field(default=90, ge=1)
    analyzer_timeout_seconds: int = Field(default=120, ge=1)
    code_review_tools_image: str = "code-review-tools:latest"
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
