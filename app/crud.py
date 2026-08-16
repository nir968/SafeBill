from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


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
