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
    # Where source .pptx decks live, for serving a single reference slide raw
    # (get_slide_pptx). Defaults to the deployed layout (corpus/source); for local
    # dev against the sibling repo set SOURCE_PPTX_PATH=../slide_tagging/data/source
    source_pptx_path: Path = Path("corpus/source")
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8000
    log_level: str = "INFO"
    embedding_model: str = "text-embedding-3-small"
    clip_model: str = "ViT-L/14"


settings = Settings()
