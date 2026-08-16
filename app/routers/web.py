from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import AgentError, ask_agent
from app.db import get_db
from app.models import Bill

router = APIRouter(tags=["web"])

# Path relative to this file, not the process's current working directory -
# robust regardless of where uvicorn is actually launched from. templates/
# lives under app/, not the project root, specifically so the Dockerfile's
# existing `COPY app ./app` picks it up automatically - putting it at the
# project root would work locally and then silently 404/500 in the
# container, since nothing outside app/ is ever copied into the image.
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Same table, duplicated (not imported) from telegram_bot.py /
# app/telegram_client.py - a small, fixed lookup, kept independent per
# module rather than adding a cross-import between otherwise-unrelated
# concerns (web dashboard vs. outbound Telegram messaging) just to save
# a few lines. Keep all three in sync if a currency is added.
_CURRENCY_SYMBOLS = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}


def _format_money(amount, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount:.2f}" if symbol else f"{amount:.2f} {currency}"


templates.env.filters["money"] = _format_money


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user_id: int, db: AsyncSession = Depends(get_db)):
    """
    No authentication - user_id is a plain, unauthenticated query
    parameter, exactly like every other GET endpoint in this app
    (/bills/recent, /bills/pending, etc.). That convention is fine for a
    bot-driven API where the caller is trusted backend code, but this is
    a browser-facing page: anyone who has or guesses a user's Telegram id
    can view their full bill and spending history at this URL with no
    login prompt. Telegram ids aren't small sequential integers the way
    the old bill_id IDOR issue was, but they also aren't a secret. Real
    auth (a login flow, a signed per-user link, etc.) is a separate piece
    of work - flagging the gap rather than quietly shipping a page that
    looks secured and isn't.
    """
    # Per-currency, not a single blended sum - same reasoning as
    # /bills/stats: a $50 bill and a ₪50 bill do not add up to "100" of
    # anything real.
    pending_result = await db.execute(
        select(Bill.currency, func.count(Bill.id), func.sum(Bill.amount))
        .where(Bill.user_id == user_id, Bill.status != "paid")
        .group_by(Bill.currency)
    )
    pending_by_currency = [
        {"currency": currency, "count": count, "total": float(total)}
        for currency, count, total in pending_result.all()
    ]
    pending_count = sum(row["count"] for row in pending_by_currency)

    recent_result = await db.execute(
        select(Bill).where(Bill.user_id == user_id).order_by(Bill.id.desc()).limit(25)
    )
    recent_bills = recent_result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user_id": user_id,
            "pending_count": pending_count,
            "pending_by_currency": pending_by_currency,
            "bills": recent_bills,
        },
    )


class ChatRequest(BaseModel):
    user_id: int
    message: str


class ChatResponse(BaseModel):
    reply: str


@router.post("/api/chat", tags=["chat"])
async def chat(payload: ChatRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    """
    Backs both the dashboard's chat widget and telegram_bot.py's text-
    message handler - the bot calls this exact endpoint over HTTP rather
    than importing app.agent directly, same as every other bot command;
    it's a deliberately separate deployable process with no dependency on
    the app package.

    Same unauthenticated-user_id caveat as GET /dashboard, but sharper
    here: this endpoint answers arbitrary natural-language questions
    using another user's real financial data if you pass their id, and
    every call - even a question the tools can't help with - spends real
    Anthropic API budget. GET /dashboard is a read-only page for a chosen
    id; this is an interactive, costed surface with no rate limiting and
    no check that the caller is who they claim to be. Shipping it exactly
    as specified, matching this app's established no-auth-yet posture,
    but flagging it more forcefully than the dashboard's version of the
    same gap given the added cost-abuse angle.
    """
    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "Empty message")

    try:
        reply = await ask_agent(db, payload.user_id, message)
    except AgentError as exc:
        raise HTTPException(502, str(exc))

    return ChatResponse(reply=reply)
