from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bill, User



async def get_or_create_user(db: AsyncSession, user_id: int) -> User:
    """
    Ensures a User row exists for a Telegram-sourced user_id. Safe under
    concurrent calls for the same brand-new user (e.g. /last and /stats
    landing at once) - the INSERT is a single atomic statement at the
    database level, so there is no check-then-insert race window that
    could raise a duplicate-key error.

    `id` here IS the Telegram user id, not a surrogate key - see the note
    on User in models.py. `telegram_chat_id` defaults to the same value,
    which is correct for a private (DM) bot chat: Telegram's own
    convention is that a user's private chat_id equals their user id. If
    this bot is ever used from a group chat, that assumption needs
    revisiting - the group's chat_id would need to come from the request.
    """
    insert_stmt = (
        pg_insert(User)
        .values(id=user_id, telegram_chat_id=str(user_id))
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await db.execute(insert_stmt)
    await db.commit()

    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one()


async def get_total_paid(db: AsyncSession, user_id: int) -> float:
    """Per-currency would be more correct (see GET /dashboard's own
    pending_by_currency, which deliberately never blends currencies for
    exactly this reason - a $50 bill and a ₪50 bill don't add up to
    "100" of anything real) but this matches what was asked: a single
    total across every currency a user happens to have paid bills in.
    Worth revisiting if that ever actually matters for an account with
    more than one currency."""
    stmt = select(func.sum(Bill.amount)).where(
        Bill.user_id == user_id,
        # The status column stores the English literal "paid" (see
        # _EDITABLE_STATUSES in app/main.py and PUT /bills/{id}/pay) -
        # comparing against the Hebrew word here meant this could never
        # match a real row. Not something that crashed the app (a
        # 0-row match is a completely valid, silent SQL result, not an
        # error) - it would just have always returned 0.0.
        Bill.status == "paid",
    )
    result = await db.execute(stmt)
    total = result.scalar()
    return float(total) if total else 0.0