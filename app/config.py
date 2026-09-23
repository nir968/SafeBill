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

    # No default on purpose - a JWT signing secret must never ship with a
    # guessable fallback baked into source control. Missing this now makes
    # the whole app fail to start (a clear pydantic-settings validation
    # error) instead of silently signing every session token with a
    # well-known value. Set via SECRET_KEY in .env - e.g.
    # `python -c "import secrets; print(secrets.token_hex(32))"`.
    secret_key: str
    access_token_expire_minutes: int = 60 * 24 * 14  # 14 days

    # No default, same reasoning as secret_key above - this is the shared
    # secret telegram_bot.py presents (as the X-Internal-Api-Key header,
    # see app/auth.py::get_authorized_user_id) to prove it's the trusted
    # bot process entitled to act on behalf of whichever user_id it
    # supplies, rather than an arbitrary caller impersonating any user it
    # likes. A guessable/missing default would silently defeat the entire
    # point of that check, so this fails the app at startup instead - the
    # same tradeoff already made for secret_key. Set via INTERNAL_API_KEY
    # in .env - e.g. `python -c "import secrets; print(secrets.token_hex(32))"`.
    internal_api_key: str

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

    # Empty by default, not a hard-required field like secret_key above -
    # this isn't a secret (it's visible in every page's rendered HTML
    # source regardless), just an opt-in third-party embed. Empty means
    # "widget disabled"; GET /dashboard's template only emits the
    # <script data-account=...> tag when this is set (see
    # app/routers/web.py), so a deployment that hasn't signed up for a
    # UserWay account yet doesn't ship a broken embed. Set via
    # USERWAY_ACCOUNT_ID in .env - the account id comes from UserWay's own
    # dashboard after creating a (free) account for this site's domain.
    userway_account_id: str = ""

    # Empty by default, same "opt-in, feature hides itself" pattern as
    # userway_account_id above - the dashboard's "Connect Telegram"
    # button (app/templates/dashboard.html) only renders once this is
    # set. Not derivable from telegram_bot_token (that authenticates API
    # calls; it doesn't reveal the bot's public @username) and
    # deliberately not fetched live via Telegram's getMe API either - a
    # static setting can't silently break page rendering if Telegram's
    # API has a bad moment, and it only ever needs to be set once. Set
    # via TELEGRAM_BOT_USERNAME in .env - without the @ prefix.
    telegram_bot_username: str = ""

    # Optional, same "opt-in, feature degrades gracefully" pattern as
    # userway_account_id/telegram_bot_username above - see
    # app/email_client.py::is_configured(). Missing these doesn't fail
    # the app at startup, or even fail a forgot-password request:
    # POST /auth/forgot-password falls back to logging the reset link
    # (its original mocked-email behavior) whenever is_configured() is
    # False, so a deployment that hasn't set up Gmail yet keeps working.
    # smtp_username and from_email are typically the same Gmail address;
    # smtp_password must be a Gmail *App Password* (myaccount.google.com
    # -> Security -> 2-Step Verification -> App Passwords), not the
    # actual account password - Gmail rejects normal password auth for
    # SMTP entirely.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_email: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
