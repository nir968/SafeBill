from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bill

_UNCATEGORIZED_LABEL = "לא מסווג"
SEARCH_RESULT_LIMIT = 20

"""
Pure data-access functions - no Anthropic/LLM dependency, directly testable
and callable on their own. app/agent.py wraps each of these in a tool
closure the model can invoke; the wrapping is what keeps `user_id` out of
the model's hands (see the security note in app/agent.py::ask_agent) -
these functions themselves are plain, ordinary, per-user-scoped queries,
same as any other endpoint in this app.
"""


async def get_user_bills_summary(db: AsyncSession, user_id: int) -> dict:
    """Pending/paid totals per currency, bill counts, and a breakdown by
    category, for one user."""
    pending_result = await db.execute(
        select(Bill.currency, func.count(Bill.id), func.sum(Bill.amount))
        .where(Bill.user_id == user_id, Bill.status != "paid")
        .group_by(Bill.currency)
    )
    pending_by_currency = {
        currency: {"count": count, "total": float(total)}
        for currency, count, total in pending_result.all()
    }

    paid_result = await db.execute(
        select(Bill.currency, func.count(Bill.id), func.sum(Bill.amount))
        .where(Bill.user_id == user_id, Bill.status == "paid")
        .group_by(Bill.currency)
    )
    paid_by_currency = {
        currency: {"count": count, "total": float(total)}
        for currency, count, total in paid_result.all()
    }

    category_result = await db.execute(
        select(Bill.category, func.count(Bill.id))
        .where(Bill.user_id == user_id)
        .group_by(Bill.category)
    )
    bills_by_category = {
        (category or _UNCATEGORIZED_LABEL): count for category, count in category_result.all()
    }

    total_bill_count = sum(row["count"] for row in pending_by_currency.values()) + sum(
        row["count"] for row in paid_by_currency.values()
    )

    return {
        "total_bill_count": total_bill_count,
        "pending_by_currency": pending_by_currency,
        "paid_by_currency": paid_by_currency,
        "bills_by_category": bills_by_category,
    }


async def search_user_bills(db: AsyncSession, user_id: int, query: str) -> list[dict]:
    """Institution name (case-insensitive substring), or an exact
    category/status match. Capped at SEARCH_RESULT_LIMIT so a broad query
    can't return an unbounded result set into the model's context."""
    like_query = f"%{query}%"
    result = await db.execute(
        select(Bill)
        .where(
            Bill.user_id == user_id,
            or_(
                Bill.institution.ilike(like_query),
                Bill.category == query,
                Bill.status == query,
            ),
        )
        .order_by(Bill.id.desc())
        .limit(SEARCH_RESULT_LIMIT)
    )
    bills = result.scalars().all()
    return [
        {
            "id": bill.id,
            "institution": bill.institution,
            "amount": float(bill.amount),
            "currency": bill.currency,
            "due_date": bill.due_date.isoformat() if bill.due_date else None,
            "status": bill.status,
            "category": bill.category,
        }
        for bill in bills
    ]
