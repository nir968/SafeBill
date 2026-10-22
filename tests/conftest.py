"""
Throwaway values for every environment variable app.config.Settings (or a
directly-imported SDK client, e.g. google.genai.Client()) requires just to
construct - not real credentials, and never should be. os.environ.setdefault,
not a plain assignment, so:

- Any of these can still be overridden by the real environment (CI sets its
  own DATABASE_URL/REDIS_URL pointing at its service containers - see
  .github/workflows/ci.yml - and this file won't clobber them).
- Running `pytest` locally with zero setup just works, without needing a
  real .env - these tests never make a real network call or open a real DB
  connection (see each test module's own docstring for what it actually
  exercises), so a well-formed-looking fake value is all any of them need.

This has to run before any `app.*` import anywhere in the suite, since
app.config.Settings() and the vision/agent modules' genai.Client() calls
both happen at import time, not lazily - conftest.py is collected by
pytest before test module imports specifically to make that ordering safe.
"""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-api-key-not-for-production-use")
# google.genai.Client() only checks that this is present and non-empty at
# construction time - it never validates the value or calls out to Google
# until a real API method is actually invoked, which nothing in this
# suite does (see the test files' own docstrings).
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-gemini-key")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://safebill:safebill@localhost:5432/safebill_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# Without this, a developer's real .env token leaks into the suite (pydantic-
# settings reads .env too), making local runs differ from CI and letting any
# Telegram send reach real chats. Tests that need 2FA on monkeypatch it.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "mock-token")
