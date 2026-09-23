"""
Outbound email delivery via SMTP (Gmail by default - SMTP_HOST/SMTP_PORT
are both configurable, not hardcoded, so any other SMTP-compatible
provider works too). Mirrors app/telegram_client.py's own separation:
third-party outbound communication lives in its own module, independent
of whichever route happens to trigger it. The one caller today is
POST /auth/forgot-password (app/routers/auth.py), via FastAPI's
BackgroundTasks - nothing here is specific to that one call site.

smtplib has no async API, unlike telegram_client.py's httpx-based sends -
every function here is a plain, blocking `def`, deliberately never
`async def`. That's exactly what BackgroundTasks wants: a sync callable
it runs in a thread pool automatically, so this blocking I/O never ties
up the event loop that's serving other requests.
"""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger("safebill.email")


class EmailDeliveryError(Exception):
    pass


def is_configured() -> bool:
    """False until every SMTP setting has a real value - see
    app/config.py: all five are optional, empty-string-default settings
    (not hard-required like secret_key), so a deployment that hasn't set
    up Gmail yet doesn't fail to start, and a forgot-password request
    doesn't fail with a confusing SMTPAuthenticationError against an
    empty username/password. Callers check this first and fall back to
    logging the content instead when it's False - see
    POST /auth/forgot-password in app/routers/auth.py."""
    return bool(settings.smtp_username and settings.smtp_password and settings.from_email)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=8),
    retry=retry_if_exception_type(EmailDeliveryError),
    reraise=True,
)
def send_email(to_email: str, subject: str, html_body: str) -> None:
    """
    Synchronous and blocking, by design - see this module's own
    docstring for why. Meant to be handed to FastAPI's
    BackgroundTasks.add_task, never called directly from a route handler
    or awaited (it isn't awaitable at all).

    Raises EmailDeliveryError (not smtplib's own exceptions directly) on
    failure, matching telegram_client.py's TelegramDeliveryError
    convention - reraise=True means the third attempt's failure still
    propagates. BackgroundTasks runs this after the response has already
    gone out, so there's no request left to report a failure to either
    way; letting it raise at least makes a persistent failure visible in
    the server logs (Starlette logs an unhandled background-task
    exception) instead of silently vanishing.
    """
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.from_email
    message["To"] = to_email
    message.attach(MIMEText(html_body, "html"))

    # An explicit, modern SSL context for STARTTLS rather than smtplib's
    # own implicit default - ssl.create_default_context() turns on
    # certificate verification and hostname checking with current best-
    # practice settings, instead of relying on whatever smtplib's
    # internal default happens to be across Python versions.
    context = ssl.create_default_context()

    try:
        # `with`, not a bare server = smtplib.SMTP(...) - guarantees
        # server.quit()/close() runs even if starttls/login/sendmail
        # raises partway through, so a failed attempt never leaks an
        # open socket into the next retry (see the @retry decorator
        # above - up to 3 attempts per call).
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls(context=context)
            server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.from_email, to_email, message.as_string())
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(f"Could not send email via {settings.smtp_host}: {exc}") from exc
    except OSError as exc:
        # Connection-level failures (DNS, refused, timeout, or the
        # server dropping the connection mid-handshake) raise plain
        # OSError/socket.error from smtplib, not SMTPException -
        # smtplib.SMTPServerDisconnected is actually a subclass of
        # SMTPException, not OSError, so it's already caught by the
        # branch above; this branch is for lower-level socket failures
        # underneath that. Both need to retry the same way, so both are
        # normalized to EmailDeliveryError here.
        raise EmailDeliveryError(f"Could not reach {settings.smtp_host}: {exc}") from exc

    logger.info("Email sent to %s (%s)", to_email, subject)


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    """The one caller today: POST /auth/forgot-password, via
    BackgroundTasks - see that route's own comment for why it's never
    called directly."""
    # dir="rtl" + style="direction: rtl; text-align: right;" repeated on
    # every element, not just the outer <div> - HTML email clients (Outlook
    # in particular, which renders via Word's engine) are notoriously
    # inconsistent about applying an inherited direction down to child
    # elements, so each block sets it explicitly rather than relying on
    # inheritance from its parent. This is also what actually fixes the
    # embedded-Latin-text bug: without an explicit RTL context, the
    # Unicode bidi algorithm is free to reorder a run like "ב-SafeBill" on
    # its own guess, which is exactly what made "SafeBill" appear to jump
    # to the wrong visual position in some clients even though the
    # underlying string always had the Hebrew sentence first.
    html_body = f"""
    <div dir="rtl" style="direction: rtl; text-align: right; font-family: Arial, Helvetica, sans-serif; max-width: 480px; margin: 0 auto; color: #1e293b;">
        <h2 dir="rtl" style="direction: rtl; text-align: right; color: #4f46e5;">איפוס סיסמה - SafeBill</h2>
        <p dir="rtl" style="direction: rtl; text-align: right;">קיבלנו בקשה לאיפוס הסיסמה לחשבון שלך ב-SafeBill.</p>
        <p dir="rtl" style="direction: rtl; text-align: right; margin: 24px 0;">
            <a href="{reset_link}"
               style="display: inline-block; background: linear-gradient(135deg, #6366f1, #a855f7, #ec4899);
                      color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 999px;
                      font-weight: 600;">
                לחצו כאן לאיפוס הסיסמה
            </a>
        </p>
        <p dir="rtl" style="direction: rtl; text-align: right; color: #64748b; font-size: 13px;">
            הקישור תקף ל-30 דקות ואפשר להשתמש בו פעם אחת בלבד.
            אם לא ביקשתם לאפס סיסמה, אפשר להתעלם מהודעה זו - החשבון שלכם בטוח.
        </p>
    </div>
    """
    send_email(to_email, "איפוס סיסמה - SafeBill", html_body)
