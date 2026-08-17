"""
Pure validation helpers with no DB/network access - _validate_bill_payload
backstops the same rules the dashboard's edit-modal JS already enforces
client-side (see dashboard.js's validateForm()), and
_validate_password_strength backstops signup.html's live checklist. Both
exist specifically so a raw API call (or JS-disabled browser) can't bypass
what the UI otherwise guarantees - these tests are the server-side half of
that contract.

Importing app.main pulls in the full router chain (including
app.vision/app.agent, each of which constructs its own google.genai.Client()
at import time) - see conftest.py for why that's safe here: a dummy
GEMINI_API_KEY is enough to construct those clients without ever making a
real API call, which nothing in this file does.
"""

import pytest
from fastapi import HTTPException

from app.main import _validate_bill_payload
from app.routers.auth import _generate_web_user_id, _normalize_email, _validate_password_strength


# --- _validate_bill_payload -------------------------------------------


def test_valid_bill_payload_returns_trimmed_institution():
    result = _validate_bill_payload("  חברת חשמל  ", 100, "ILS", "pending_analysis", None)
    assert result == "חברת חשמל"


def test_blank_institution_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        _validate_bill_payload("   ", 100, "ILS", "pending_analysis", None)
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("amount", [0, -1, -100.5])
def test_non_positive_amount_is_rejected(amount):
    with pytest.raises(HTTPException) as exc_info:
        _validate_bill_payload("Institution", amount, "ILS", "pending_analysis", None)
    assert exc_info.value.status_code == 400


def test_unknown_currency_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        _validate_bill_payload("Institution", 100, "XYZ", "pending_analysis", None)
    assert exc_info.value.status_code == 400


def test_unknown_status_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        _validate_bill_payload("Institution", 100, "ILS", "not_a_real_status", None)
    assert exc_info.value.status_code == 400


def test_unknown_category_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        _validate_bill_payload("Institution", 100, "ILS", "pending_analysis", "not_a_real_category")
    assert exc_info.value.status_code == 400


def test_none_category_is_allowed():
    # Category is optional - None means "uncategorized", not invalid.
    result = _validate_bill_payload("Institution", 100, "ILS", "pending_analysis", None)
    assert result == "Institution"


# --- _validate_password_strength ---------------------------------------


def test_strong_password_passes():
    _validate_password_strength("Str0ng!Pass")  # must not raise


@pytest.mark.parametrize(
    "password",
    [
        "Sh0rt!",  # under 8 chars
        "alllowercase1!",  # no uppercase
        "ALLUPPERCASE1!",  # no lowercase
        "NoDigitsHere!",  # no digit
        "NoSpecialChar1",  # no special character
    ],
)
def test_weak_password_is_rejected(password):
    with pytest.raises(HTTPException) as exc_info:
        _validate_password_strength(password)
    assert exc_info.value.status_code == 400


# --- _normalize_email / _generate_web_user_id ---------------------------


def test_normalize_email_strips_and_lowercases():
    assert _normalize_email("  User@Example.COM  ") == "user@example.com"


def test_generated_web_user_id_is_always_negative():
    """Telegram ids are always positive (see models.py's User docstring) -
    a web-signup id must never collide with one, by construction."""
    for _ in range(50):
        assert _generate_web_user_id() < 0
