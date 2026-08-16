import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.models import Bill

logger = logging.getLogger("safebill.telegram")

_TELEGRAM_API_BASE = "https://api.telegram.org"

# One shared client for the process, not one per send - the Enforcer can
# fire dozens of these in a single scan, and this avoids paying a fresh
# TCP/TLS handshake for every message. Closed via close() in
# worker.py's shutdown hook.
_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))


class TelegramDeliveryError(Exception):
    pass


# Same table, duplicated (not imported) in telegram_bot.py - that script
# is a deliberately separate, independently-deployable process (its own
# requirements-bot.txt, no dependency on the `app` package, talks to this
# backend only over HTTP), so it can't share this by import. Keep the two
# in sync if a currency is added.
_CURRENCY_SYMBOLS = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}


def _format_amount(amount, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}"


async def close() -> None:
    await _client.aclose()


async def send_message(chat_id: str, text: str) -> None:
    """
    Sends a message via the Telegram Bot API's sendMessage endpoint - a
    plain HTTPS call, not a `telebot.TeleBot` instance. The inbound bot
    (telegram_bot.py, long-polling) and this outbound send are independent
    uses of the same bot token; Telegram doesn't require them to share a
    process. Using httpx (already a backend dependency, already async)
    avoids pulling the sync, polling-oriented pyTelegramBotAPI library
    into the worker just to send one message.

    Deliberately no `parse_mode` - `bill.institution` is untrusted,
    LLM-extracted free text, and an unescaped Markdown special character
    in it (`_`, `*`, `` ` ``, `[`) would make the entire sendMessage call
    fail with a 400 from Telegram. For an unattended notification, a
    silently-lost alert is worse than losing bold text.
    """
    if not settings.telegram_bot_token or settings.telegram_bot_token == "mock-token":
        raise TelegramDeliveryError(
            "TELEGRAM_BOT_TOKEN is not configured with a real token - alerts cannot be sent"
        )
    await _send_message_with_retry(chat_id, text)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type(TelegramDeliveryError),
    reraise=True,
)
async def _send_message_with_retry(chat_id: str, text: str) -> None:
    url = f"{_TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"

    try:
        response = await _client.post(url, json={"chat_id": chat_id, "text": text})
    except httpx.RequestError as exc:
        raise TelegramDeliveryError(f"Could not reach Telegram: {exc}") from exc

    if response.status_code != 200:
        raise TelegramDeliveryError(
            f"Telegram API returned HTTP {response.status_code}: {response.text[:200]}"
        )

    payload = response.json()
    if not payload.get("ok"):
        raise TelegramDeliveryError(f"Telegram API error: {payload.get('description')}")


async def send_bill_reminder(chat_id: str, bill: Bill) -> None:
    """
    Raising TelegramDeliveryError is what the Enforcer's caller catches to
    increment `alert_attempts` instead of losing the alert silently - the
    three retries in _send_message_with_retry handle transient blips, the
    attempt counter on the Bill row handles everything past that.
    """
    due_date_str = bill.due_date.isoformat() if bill.due_date else "לא ידוע"
    text = (
        "🔔 תזכורת תשלום\n"
        f"חשבון מ{bill.institution} על סך {_format_amount(bill.amount, bill.currency)} "
        f"לתשלום עד {due_date_str}."
    )
    await send_message(chat_id, text)


async def send_anomaly_alert(chat_id: str, bill: Bill) -> None:
    """Fired inline by the Analyst the moment it flags a bill as
    anomalous - see the note in worker.py::analyze_bill for why this
    isn't a periodic scan."""
    text = (
        "⚠️ זוהתה חריגה בחשבון\n"
        f"חשבון מ{bill.institution} על סך {_format_amount(bill.amount, bill.currency)} "
        "נראה חריג בהשוואה להיסטוריית התשלומים שלך אצל הספק הזה.\n"
        "כדאי לבדוק את החשבונית לפני התשלום."
    )
    await send_message(chat_id, text)
