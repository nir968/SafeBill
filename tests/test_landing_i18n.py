"""
Landing page (GET /) and the language cookie shared by the landing, auth
and dashboard pages (app/i18n.py). Real HTTP requests through the app's
routing via TestClient; none of these pages touch the database or Redis.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.auth import COOKIE_NAME, create_access_token
from app.i18n import LANG_COOKIE, TEXT
from app.main import app

ENTRY_PAGES = ["/", "/login", "/signup", "/forgot-password", "/reset-password"]
HEBREW = re.compile(r"[֐-׿]")


def _get(path, **cookies):
    return TestClient(app, cookies=cookies).get(path, follow_redirects=False)


def _visible_body(html):
    body = html[re.search(r"<body\b[^>]*class=", html).start():]
    return re.sub(r"<!--.*?-->", "", body, flags=re.S)


def test_landing_offers_login_and_signup_in_hebrew_by_default():
    response = _get("/")
    assert response.status_code == 200
    assert '<html dir="rtl" lang="he">' in response.text
    assert 'href="/login"' in response.text and 'href="/signup"' in response.text
    assert TEXT["he"]["landing_login"] in response.text
    assert TEXT["he"]["landing_signup"] in response.text


def test_landing_sends_signed_in_visitors_to_dashboard():
    response = _get("/", **{COOKIE_NAME: create_access_token(-42)})
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"


def test_landing_ignores_an_invalid_session_cookie():
    assert _get("/", **{COOKIE_NAME: "not-a-jwt"}).status_code == 200


def test_unknown_language_cookie_falls_back_to_hebrew():
    assert '<html dir="rtl" lang="he">' in _get("/", **{LANG_COOKIE: "fr"}).text


@pytest.mark.parametrize("path", ENTRY_PAGES)
def test_english_pages_render_ltr_with_no_untranslated_hebrew(path):
    response = _get(path, **{LANG_COOKIE: "en"})
    assert response.status_code == 200
    assert '<html dir="ltr" lang="en">' in response.text
    # "עב" is the switcher's label for Hebrew itself - intentional.
    leftover = HEBREW.findall(_visible_body(response.text).replace("עב", ""))
    assert not leftover, f"untranslated Hebrew on {path}: {''.join(leftover)[:40]}"
    assert "\\u05" not in response.text.split("SAFEBILL_I18N", 1)[-1][:4000]


@pytest.mark.parametrize("path", ENTRY_PAGES)
@pytest.mark.parametrize("lang", ["he", "en"])
def test_switcher_marks_the_current_language(path, lang):
    html = _get(path, **{LANG_COOKIE: lang}).text
    assert re.search(rf'data-set-lang="{lang}"\s+lang="{lang}"\s+aria-pressed="true"', html)
    other = "en" if lang == "he" else "he"
    assert re.search(rf'data-set-lang="{other}"\s+lang="{other}"\s+aria-pressed="false"', html)


def test_hebrew_and_english_tables_have_the_same_keys():
    assert set(TEXT["he"]) == set(TEXT["en"])
    assert all(TEXT["en"].values()), "every English string must be non-empty"
