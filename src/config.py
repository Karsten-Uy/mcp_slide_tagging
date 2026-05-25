"""Application settings, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://postgres:postgres@localhost:5432/slidecorpus"
    openai_api_key: str = ""
    corpus_path: Path = Path("../slide_tagging/reference_data/hand_labels")
    thumbnail_base_path: Path | None = None
    # Where recurring-element image_paths ("assets/<slug>/x.png") resolve on disk.
    # Defaults to the deployed layout (corpus/assets); for local dev against the
    # sibling repo set ASSETS_PATH=../slide_tagging/reference_data/assets
    assets_path: Path = Path("corpus/assets")
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8000
    log_level: str = "INFO"
    embedding_model: str = "text-embedding-3-small"
    clip_model: str = "ViT-L/14"


settings = Settings()
