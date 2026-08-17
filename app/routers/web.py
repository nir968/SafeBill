from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import AgentError, ask_agent
from app.auth import get_current_user_for_page
from app.db import get_db
from app.models import Bill, User
from app.vision import TICKET_CATEGORY

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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={})


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse(request=request, name="signup.html", context={})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user_for_page),
    db: AsyncSession = Depends(get_db),
):
    """
    Cookie-authenticated as of app/auth.py::get_current_user_for_page -
    previously took user_id as a plain, unauthenticated query parameter
    (see git history), which let anyone who had or guessed a user's
    Telegram id view their full bill and spending history with no login
    prompt at all. An unauthenticated visitor gets redirected to /login
    rather than a bare 401, since this route renders a full page for
    direct browser navigation, not a fetch() call client-side JS could
    otherwise react to.

    main.py's bill CRUD routes, sanitizer.py's upload route, and
    POST /api/chat below are deliberately NOT migrated to this - they're
    called by telegram_bot.py as a trusted server-to-server caller with a
    Telegram-verified id and no browser session to present, so requiring
    a cookie there would break the bot. That's a real gap, not an
    oversight - see app/routers/auth.py's module docstring.
    """
    user_id = current_user.id

    # Per-currency, not a single blended sum - same reasoning as
    # /bills/stats: a $50 bill and a ₪50 bill do not add up to "100" of
    # anything real.
    pending_result = await db.execute(
        select(Bill.currency, func.count(Bill.id), func.sum(Bill.amount))
        .where(
            Bill.user_id == user_id,
            Bill.status != "paid",
            # A placeholder bill (vision extraction was unavailable at
            # upload time - see POST /bills/upload) isn't a real pending
            # charge, just an unresolved $0 row - counting it here would
            # inflate "pending" with something that was never a debt.
            Bill.status != "extraction_failed",
        )
        .group_by(Bill.currency)
    )
    pending_by_currency = [
        {"currency": currency, "count": count, "total": float(total)}
        for currency, count, total in pending_result.all()
    ]
    pending_count = sum(row["count"] for row in pending_by_currency)

    # Two independent "most recent 25" queries, not one shared query split
    # in Python - a user with many tickets and few regular bills (or vice
    # versa) would otherwise have one table starved by the other eating
    # most of a single combined 25-row budget.
    regular_result = await db.execute(
        select(Bill)
        .where(
            Bill.user_id == user_id,
            # `!=` alone would silently drop every bill with category IS
            # NULL - SQL's `NULL != x` evaluates to NULL/unknown, which
            # WHERE treats as "exclude", not "include". is_distinct_from
            # treats NULL correctly: not a ticket, so it belongs here.
            Bill.category.is_distinct_from(TICKET_CATEGORY),
        )
        .order_by(Bill.id.desc())
        .limit(25)
    )
    regular_bills = regular_result.scalars().all()

    ticket_result = await db.execute(
        select(Bill)
        .where(Bill.user_id == user_id, Bill.category == TICKET_CATEGORY)
        .order_by(Bill.id.desc())
        .limit(25)
    )
    ticket_bills = ticket_result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user_id": user_id,
            "pending_count": pending_count,
            "pending_by_currency": pending_by_currency,
            "regular_bills": regular_bills,
            "ticket_bills": ticket_bills,
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

    Still on the unauthenticated user_id parameter GET /dashboard just
    moved off of - deliberately, not an oversight: unlike the dashboard,
    this route is also called directly by telegram_bot.py as a trusted
    server-to-server caller with no browser session to present, so it
    can't take a cookie-based dependency the way GET /dashboard now does
    without breaking the bot (see app/routers/auth.py's module docstring).
    That leaves this the sharper case of the same gap: it answers
    arbitrary natural-language questions using another user's real
    financial data if you pass their id, and every call - even a question
    the tools can't help with - spends real Anthropic API budget, with no
    rate limiting and no check that the caller is who they claim to be.
    Needs its own fix (e.g. a shared service-level API key the bot
    presents, checked here alongside/instead of a user cookie), not a
    reflexive swap to get_current_user.
    """
    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "Empty message")

    try:
        reply = await ask_agent(db, payload.user_id, message)
    except AgentError as exc:
        raise HTTPException(502, str(exc))

    return ChatResponse(reply=reply)
