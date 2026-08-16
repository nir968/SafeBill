from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://safebill:safebill@localhost:5432/safebill"
    redis_url: str = "redis://localhost:6379/0"

    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Backpressure valve for the Sanitizer hot path — caps concurrent VLM
    # calls (and therefore concurrent in-RAM image buffers) per process.
    max_concurrent_extractions: int = 8
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB
    # A real network round trip to a hosted vision model runs 1-4s typically,
    # more on a cold structured-output schema compile. The old 5.0s default
    # was sized for the in-process mock and was too tight for a live call.
    vlm_timeout_seconds: float = 15.0

    telegram_bot_token: str = "mock-token"

    vision_provider: Literal["anthropic", "ollama", "gemini"] = "gemini"
    # Only read if vision_provider is ever set to "ollama" - app/vision.py's
    # get_vision_provider() still has a live OllamaVisionProvider branch, so
    # these need to exist even though gemini is the active default.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2-vision"
    gemini_model: str = "gemini-3.6-flash"
    # Separate from gemini_model on purpose - app/agent.py wants a Pro-tier
    # (reasoning/tool-use) model, not the Flash-tier one vision extraction
    # uses. Both are pinned to flash here to cut cost; if agent tool
    # selection or answer quality degrades, split this back out to a Pro
    # model first.
    gemini_agent_model: str = "gemini-3.6-flash"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
