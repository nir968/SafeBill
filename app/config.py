from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every value can be overridden by the same name in UPPER_CASE in .env
    (e.g. gemini_model -> GEMINI_MODEL). The defaults below only apply when
    .env doesn't set that variable."""

    # ------------------------------------------------------------------
    # Database & Redis
    # ------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://safebill:safebill@localhost:5432/safebill"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # ------------------------------------------------------------------
    # Security - required, no defaults
    # ------------------------------------------------------------------
    # The app refuses to start without these, rather than run with a
    # guessable value baked into source control. Generate each with:
    #   python -c "import secrets; print(secrets.token_hex(32))"

    # Signs the login session cookie (JWT).
    secret_key: str
    access_token_expire_minutes: int = 60 * 24 * 14  # 14 days

    # Shared secret telegram_bot.py sends (X-Internal-Api-Key header) to
    # prove it's the trusted bot - see app/auth.py::get_authorized_user_id.
    internal_api_key: str

    # ------------------------------------------------------------------
    # Bill scanning (AI vision)
    # ------------------------------------------------------------------
    # Which AI reads uploaded bills. "anthropic" also needs ANTHROPIC_API_KEY,
    # "gemini" needs GEMINI_API_KEY - both read directly by their SDKs.
    vision_provider: Literal["gemini", "anthropic"] = "gemini"
    # The Gemini model used for scanning (only when vision_provider="gemini").
    gemini_model: str = "gemini-3-flash-preview"

    # Max scans running at once in this process (caps in-RAM image buffers).
    max_concurrent_extractions: int = 8
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB
    # Per-scan timeout. A real call to a hosted model takes ~1-4s.
    vlm_timeout_seconds: float = 15.0

    # ------------------------------------------------------------------
    # AI chat assistant (dashboard chat) - separate from bill scanning
    # ------------------------------------------------------------------
    # Independent of gemini_model on purpose, so the chat can move to a
    # stronger (Pro-tier) model without changing the scanner.
    gemini_agent_model: str = "gemini-3.6-flash"

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------
    # Token from @BotFather. Left as "mock-token", alerts are skipped with
    # a "not configured" warning instead of failing.
    telegram_bot_token: str = "mock-token"
    # Bot's public @username, without the @. Empty = the dashboard's
    # "Connect Telegram" button is hidden.
    telegram_bot_username: str = ""

    # ------------------------------------------------------------------
    # Email (password reset) - optional
    # ------------------------------------------------------------------
    # If any of these is empty, forgot-password still works: the reset
    # link is written to the server log instead of emailed (see
    # app/email_client.py::is_configured). smtp_password must be a Gmail
    # App Password, not the real account password.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_email: str = ""

    # ------------------------------------------------------------------
    # Optional UI
    # ------------------------------------------------------------------
    # UserWay accessibility widget account id. Empty = widget not shown.
    userway_account_id: str = ""

    # extra="ignore": unknown keys in .env (e.g. production-only ones like
    # DOMAIN) don't stop the app from starting.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
