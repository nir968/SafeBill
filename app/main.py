import csv
import io
from contextlib import asynccontextmanager
from decimal import Decimal

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy import func, select
# Every route in this file depends on get_db(), which yields an
# AsyncSession (see app/db.py) - never sqlalchemy.orm.Session, and never
# db.query(...). This has broken three separate times in this project by
# a route being written against the sync ORM API by habit; if a new route
# throws AttributeError on db.query, this is why.
from sqlalchemy.ext.asyncio import AsyncSession

from app import models  # noqa: F401 -- registers tables on Base.metadata
from app.config import settings
from app.crud import get_or_create_user
from app.db import Base, engine, get_db
from app.models import Bill
from app.routers import sanitizer, web


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        # Scaffold only — swap for Alembic migrations before this touches
        # real data; create_all is not safe for evolving a live schema.
        await conn.run_sync(Base.metadata.create_all)

    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.redis.close()
    await engine.dispose()


app = FastAPI(title="SafeBill", lifespan=lifespan)
app.include_router(sanitizer.router)
app.include_router(web.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/bills/recent")
async def get_recent_bills(user_id: int, db: AsyncSession = Depends(get_db)):
    await get_or_create_user(db, user_id)

    result = await db.execute(
        select(Bill).where(Bill.user_id == user_id).order_by(Bill.id.desc()).limit(5)
    )
    recent_bills = result.scalars().all()

    return [
        {
            "institution": bill.institution,
            "amount": float(bill.amount),
            "currency": bill.currency,
            "category": bill.category,
        }
        for bill in recent_bills
    ]


@app.get("/bills/stats")
async def get_stats(user_id: int, db: AsyncSession = Depends(get_db)):
    """
    Returns a per-currency breakdown, not a single blended total - once
    bills can be in different currencies, summing them into one number
    (e.g. $50 + ₪50 = "100") would be actively wrong, not just imprecise.
    This is an intentional breaking change to this endpoint's response
    shape (`total_spent: float` -> `totals: {currency: float}`);
    telegram_bot.py's handle_stats was updated to match.
    """
    await get_or_create_user(db, user_id)

    result = await db.execute(
        select(Bill.currency, func.sum(Bill.amount))
        .where(Bill.user_id == user_id)
        .group_by(Bill.currency)
    )
    totals = {currency: float(total) for currency, total in result.all()}

    return {"totals": totals}


def _serialize_bill(bill: Bill) -> dict:
    return {
        "id": bill.id,
        "institution": bill.institution,
        "amount": float(bill.amount),
        "currency": bill.currency,
        "category": bill.category,
        # null, not the string "None", when no due date was ever extracted
        # - telegram_bot.py is expected to substitute its own friendly
        # placeholder text for a null value.
        "due_date": bill.due_date.isoformat() if bill.due_date else None,
        "asmachta": bill.asmachta,
    }


@app.get("/bills/search")
async def search_bills(user_id: int, query: str, db: AsyncSession = Depends(get_db)):
    """Filters by institution name, case-insensitive substring match."""
    result = await db.execute(
        select(Bill)
        .where(Bill.user_id == user_id, Bill.institution.ilike(f"%{query}%"))
        .order_by(Bill.id.desc())
    )
    return [_serialize_bill(bill) for bill in result.scalars().all()]


@app.get("/bills/pending")
async def get_pending_bills(user_id: int, db: AsyncSession = Depends(get_db)):
    """
    "Pending" currently means status != "paid" - as specified. Worth
    knowing: nothing in this codebase ever sets a bill's status to "paid"
    (the Sanitizer sets "pending_analysis", the Analyst sets "analyzed",
    and that's the entire lifecycle today), so this filter is currently a
    no-op that returns every bill. It won't crash or return wrong data,
    but it also won't distinguish anything from /bills/recent until
    something in the app actually marks a bill paid - e.g. a /mark_paid
    bot command, or redefining "pending" as due_date >= today. Flagging
    this rather than guessing which one you want.
    """
    result = await db.execute(
        select(Bill)
        .where(Bill.user_id == user_id, Bill.status != "paid")
        .order_by(Bill.id.desc())
    )
    return [_serialize_bill(bill) for bill in result.scalars().all()]


@app.delete("/bills/last")
async def delete_last_bill(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Bill).where(Bill.user_id == user_id).order_by(Bill.id.desc()).limit(1)
    )
    last_bill = result.scalar_one_or_none()
    if last_bill is None:
        return {"error": "No bills found"}

    deleted = _serialize_bill(last_bill)
    await db.delete(last_bill)
    await db.commit()
    return {"status": "deleted", "id": deleted["id"], "institution": deleted["institution"]}


async def _get_owned_bill(db: AsyncSession, bill_id: int, user_id: int) -> Bill:
    """
    Scoped by bill_id AND user_id together, never bill_id alone - Bill.id
    is a plain autoincrementing integer, trivially guessable/enumerable,
    so a lookup that ignored ownership would let one Telegram user pay off
    or edit another user's bill just by trying nearby ids. A bill that
    exists but belongs to someone else 404s exactly like one that doesn't
    exist at all - that's deliberate, not an oversight: confirming "id 42
    belongs to a different user" would itself leak information.
    """
    result = await db.execute(select(Bill).where(Bill.id == bill_id, Bill.user_id == user_id))
    bill = result.scalar_one_or_none()
    if bill is None:
        raise HTTPException(404, "Bill not found")
    return bill


@app.put("/bills/{bill_id}/pay")
async def pay_bill(
    bill_id: int, user_id: int, asmachta: str | None = None, db: AsyncSession = Depends(get_db)
):
    bill = await _get_owned_bill(db, bill_id, user_id)
    bill.status = "paid"
    bill.asmachta = asmachta
    await db.commit()
    await db.refresh(bill)
    return _serialize_bill(bill)


@app.put("/bills/{bill_id}/amount")
async def update_bill_amount(
    bill_id: int, user_id: int, new_amount: Decimal, db: AsyncSession = Depends(get_db)
):
    """
    Note: this does not retroactively correct InstitutionStats' rolling
    Welford baseline (see app/worker.py::analyze_bill) - that baseline
    was built from the original amount at analysis time, and Welford's
    algorithm as used here is forward-only. A correction here doesn't
    re-run anomaly detection either. Both are out of scope for "update
    the amount" as asked, but worth knowing if numbers look slightly off
    after editing a bill that already went through analysis.
    """
    if new_amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    bill = await _get_owned_bill(db, bill_id, user_id)
    bill.amount = new_amount
    await db.commit()
    await db.refresh(bill)
    return _serialize_bill(bill)


@app.get("/bills/export")
async def export_bills(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Bill).where(Bill.user_id == user_id).order_by(Bill.id.desc())
    )
    bills = result.scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["institution", "amount", "currency", "due_date", "status"])
    for bill in bills:
        writer.writerow(
            [
                bill.institution,
                float(bill.amount),
                bill.currency,
                bill.due_date.isoformat() if bill.due_date else "",
                bill.status,
            ]
        )

    # UTF-8 BOM so Excel opens Hebrew institution names correctly on
    # double-click instead of guessing the wrong encoding and garbling
    # them - a well-known Excel quirk with plain UTF-8 CSVs. chr(0xFEFF),
    # not a literal character in source, so this can't be silently
    # mangled by a future save through a mismatched editor encoding.
    csv_content = chr(0xFEFF) + buffer.getvalue()

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=safebill_export_{user_id}.csv"},
    )
