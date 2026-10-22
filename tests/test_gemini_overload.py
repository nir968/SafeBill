"""
Gemini rate-limit (429) handling: GeminiVisionProvider turns Gemini's 429
into VisionProviderOverloadedError, and POST /bills/upload answers that
with a structured 503 and saves nothing. Other vision failures must keep
saving the placeholder bill exactly as before. Fakes only - no network,
no database.
"""

import httpx
import pytest
from fastapi import HTTPException
from google.genai import errors as genai_errors

import app.routers.sanitizer as sanitizer
from app.vision import (
    GeminiRateLimitError,
    GeminiVisionProvider,
    VisionProviderError,
    VisionProviderOverloadedError,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _provider_raising(exc):
    class _Interactions:
        async def create(self, **kwargs):
            raise exc

    client = type("Client", (), {})()
    client.aio = type("Aio", (), {"interactions": _Interactions()})()
    provider = object.__new__(GeminiVisionProvider)
    provider._model = "test-model"
    provider._client = client
    return provider


def _interactions_429():
    request = httpx.Request("POST", "https://generativelanguage.googleapis.com")
    return GeminiRateLimitError(
        "rate limited", response=httpx.Response(429, request=request), body=None
    )


async def test_interactions_rate_limit_becomes_overloaded_error():
    with pytest.raises(VisionProviderOverloadedError):
        await _provider_raising(_interactions_429()).extract_bill(_PNG)


async def test_legacy_client_error_429_becomes_overloaded_error():
    exc = genai_errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
    with pytest.raises(VisionProviderOverloadedError):
        await _provider_raising(exc).extract_bill(_PNG)


async def test_other_client_errors_are_not_reported_as_overload():
    exc = genai_errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
    with pytest.raises(VisionProviderError) as exc_info:
        await _provider_raising(exc).extract_bill(_PNG)
    assert not isinstance(exc_info.value, VisionProviderOverloadedError)


# --- POST /bills/upload ------------------------------------------------------


class _FakeRedis:
    async def delete(self, *keys):
        pass

    async def enqueue_job(self, *args):
        pass


class _FakeRequest:
    headers = {}

    def __init__(self):
        self.app = type("App", (), {"state": type("State", (), {"redis": _FakeRedis()})()})()

    async def stream(self):
        yield _PNG


class _FakeResult:
    def scalar_one(self):
        return object()


class _FakeDb:
    def __init__(self):
        self.added = []

    async def execute(self, stmt):
        return _FakeResult()

    async def commit(self):
        pass

    def add(self, obj):
        self.added.append(obj)

    async def refresh(self, obj):
        obj.id = 1


async def test_upload_returns_structured_503_and_saves_nothing(monkeypatch):
    async def overloaded(_bytes):
        raise VisionProviderOverloadedError("429")

    monkeypatch.setattr(sanitizer.vision_client, "extract_bill_fields", overloaded)
    db = _FakeDb()

    with pytest.raises(HTTPException) as exc_info:
        await sanitizer.upload_bill(_FakeRequest(), user_id=7, db=db)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "ai_overloaded"
    assert exc_info.value.headers["Retry-After"] == "60"
    assert db.added == [], "a rate-limited scan must not save a placeholder bill"


async def test_other_vision_failures_still_save_placeholder(monkeypatch):
    async def unavailable(_bytes):
        raise VisionProviderError("Gemini server error")

    monkeypatch.setattr(sanitizer.vision_client, "extract_bill_fields", unavailable)
    db = _FakeDb()

    response = await sanitizer.upload_bill(_FakeRequest(), user_id=7, db=db)

    assert response["extracted"] is False
    assert len(db.added) == 1
    assert db.added[0].status == "extraction_failed"
