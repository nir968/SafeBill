"""
Covers app/email_client.py directly: is_configured()'s gating logic, and
send_email()'s actual SMTP call sequence + retry/error-wrapping behavior.
smtplib.SMTP is mocked throughout - nothing here makes a real network
call or needs real Gmail credentials, matching this suite's existing
"no real DB/network access" rule (see tests/conftest.py).
"""

import smtplib
import ssl
from unittest.mock import MagicMock, patch

import pytest

from app import email_client
from app.config import settings


# --- is_configured() -------------------------------------------------------


def test_not_configured_by_default(monkeypatch):
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "from_email", "")
    assert email_client.is_configured() is False


@pytest.mark.parametrize(
    "smtp_username,smtp_password,from_email",
    [
        ("", "pw", "from@example.com"),
        ("user@example.com", "", "from@example.com"),
        ("user@example.com", "pw", ""),
    ],
)
def test_not_configured_when_any_single_value_missing(monkeypatch, smtp_username, smtp_password, from_email):
    """All three are required together - a partially-filled-in .env
    should still fall back to the mocked-email path rather than attempt
    a send that's certain to fail with a confusing SMTP error."""
    monkeypatch.setattr(settings, "smtp_username", smtp_username)
    monkeypatch.setattr(settings, "smtp_password", smtp_password)
    monkeypatch.setattr(settings, "from_email", from_email)
    assert email_client.is_configured() is False


def test_configured_when_all_three_are_set(monkeypatch):
    monkeypatch.setattr(settings, "smtp_username", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "from_email", "user@example.com")
    assert email_client.is_configured() is True


# --- send_email() ------------------------------------------------------


@pytest.fixture(autouse=True)
def smtp_settings(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "sender@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "from_email", "sender@example.com")


def test_send_email_calls_starttls_login_and_sendmail_in_order():
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_server) as mock_smtp_cls:
        email_client.send_email("to@example.com", "Subject line", "<p>body</p>")

    mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
    mock_server.starttls.assert_called_once()
    # A real, explicit SSL context - not starttls() called bare (which
    # would fall back to smtplib's own implicit default) - see
    # send_email's own comment on why this is deliberate.
    _, starttls_kwargs = mock_server.starttls.call_args
    assert isinstance(starttls_kwargs.get("context"), ssl.SSLContext)
    mock_server.login.assert_called_once_with("sender@example.com", "app-password")

    assert mock_server.sendmail.call_count == 1
    from_addr, to_addr, message_string = mock_server.sendmail.call_args[0]
    assert from_addr == "sender@example.com"
    assert to_addr == "to@example.com"
    assert "Subject line" in message_string
    assert "<p>body</p>" in message_string


def test_send_email_wraps_smtp_exception_and_retries_up_to_three_times():
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")

    with patch("smtplib.SMTP", return_value=mock_server):
        with pytest.raises(email_client.EmailDeliveryError):
            email_client.send_email("to@example.com", "Subject", "<p>body</p>")

    # stop_after_attempt(3) - one initial call plus two retries.
    assert mock_server.login.call_count == 3


def test_send_email_wraps_connection_failure_as_delivery_error():
    with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
        with pytest.raises(email_client.EmailDeliveryError):
            email_client.send_email("to@example.com", "Subject", "<p>body</p>")


def test_send_email_succeeds_after_a_transient_failure():
    """The retry isn't just decorative - a failure on the first attempt
    followed by success on the second must not raise."""
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    mock_server.login.side_effect = [smtplib.SMTPException("temporary"), None]

    with patch("smtplib.SMTP", return_value=mock_server):
        email_client.send_email("to@example.com", "Subject", "<p>body</p>")  # must not raise

    assert mock_server.login.call_count == 2
    mock_server.sendmail.assert_called_once()


# --- send_password_reset_email() -------------------------------------------


def test_send_password_reset_email_builds_expected_content_and_delegates():
    with patch.object(email_client, "send_email") as mock_send:
        email_client.send_password_reset_email("user@example.com", "https://safebill.example/reset-password?token=abc")

    mock_send.assert_called_once()
    to_email, subject, html_body = mock_send.call_args[0]
    assert to_email == "user@example.com"
    assert "איפוס סיסמה" in subject
    assert "https://safebill.example/reset-password?token=abc" in html_body
    assert "<a href=" in html_body

    # Real content, not just presence checks - the exact sentence asked
    # for (Hebrew clause first, "SafeBill" at the end), and every content
    # block explicitly RTL rather than relying on inherited direction
    # (see this function's own comment on why: HTML email clients are
    # inconsistent about applying that inheritance).
    assert "קיבלנו בקשה לאיפוס הסיסמה לחשבון שלך ב-SafeBill." in html_body
    assert html_body.count('dir="rtl"') >= 5
    assert html_body.count("text-align: right") >= 5
