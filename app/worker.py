import logging
from datetime import datetime, timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import delete, select

from app import telegram_client
from app.config import settings
from app.db import AsyncSessionLocal, engine
from app.models import Bill, InstitutionStats, User
from app.telegram_client import TelegramDeliveryError, send_anomaly_alert, send_bill_reminder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("safebill.worker")

MIN_HISTORY_FOR_ANOMALY_CHECK = 3
ANOMALY_Z_THRESHOLD = 3.0
ALERT_WINDOW_DAYS = 2
MAX_ALERT_ATTEMPTS = 5
ENFORCER_BATCH_SIZE = 100
RETENTION_DAYS = 365


async def analyze_bill(ctx, bill_id: int) -> None:
    """
    Analyst Agent. Triggered per-bill by the Sanitizer via arq.

    Anomaly check is O(1) against a rolling per-institution baseline — it
    never re-reads a user's bill history. The baseline itself is maintained
    incrementally with Welford's online algorithm, so this scales flat
    regardless of how many bills a user has accumulated.

    Anomaly alerts fire inline, right here, rather than through a
    periodic scan like the Enforcer's due-date reminders. The two don't
    need the same shape: "due soon" is a recurring window a bill can
    enter and needs to be rechecked against on every scan, so it needs
    the alert_sent_at/alert_attempts bookkeeping the Enforcer has.
    "Anomalous" is decided exactly once, right here, and never changes
    again - there's nothing for a periodic scan to add except delay, and
    it would need its own dedup column to avoid re-alerting on every
    Enforcer run. Sending immediately is both simpler and faster.
    """
    async with AsyncSessionLocal() as db:
        bill = await db.get(Bill, bill_id)
        if bill is None:
            logger.warning("analyze_bill: bill %s not found", bill_id)
            return

        result = await db.execute(
            select(InstitutionStats).where(
                InstitutionStats.user_id == bill.user_id,
                InstitutionStats.institution == bill.institution,
            )
        )
        stats = result.scalar_one_or_none()

        amount = float(bill.amount)

        if stats is None:
            stats = InstitutionStats(
                user_id=bill.user_id,
                institution=bill.institution,
                count=0,
                mean_amount=0.0,
                m2=0.0,
            )
            db.add(stats)
            is_anomaly, reason = False, "insufficient_history"
        elif stats.count < MIN_HISTORY_FOR_ANOMALY_CHECK:
            is_anomaly, reason = False, "insufficient_history"
        else:
            variance = stats.m2 / stats.count if stats.count > 1 else 0.0
            stddev = variance**0.5
            z = abs(amount - stats.mean_amount) / stddev if stddev > 1e-9 else 0.0
            is_anomaly = z > ANOMALY_Z_THRESHOLD
            reason = (
                f"amount z-score {z:.2f} vs. {ANOMALY_Z_THRESHOLD} threshold"
                if is_anomaly
                else None
            )
            # Reserve an LLM call for genuinely ambiguous cases only (e.g. a
            # borderline z-score just under the hard threshold) rather than
            # invoking one on every bill:
            # if not is_anomaly and z > ANOMALY_Z_THRESHOLD * 0.7:
            #     is_anomaly, reason = await llm_adjudicate_anomaly(bill, stats)

        # Welford's online mean/variance update — O(1), no history re-scan.
        stats.count += 1
        delta = amount - stats.mean_amount
        stats.mean_amount += delta / stats.count
        stats.m2 += delta * (amount - stats.mean_amount)

        bill.status = "analyzed"
        bill.is_anomaly = is_anomaly
        bill.anomaly_reason = reason

        # Commit before attempting delivery: the analysis result must be
        # durable regardless of whether the Telegram send succeeds. This
        # also matters for correctness, not just durability - if a
        # TelegramDeliveryError here were left to propagate, arq would
        # retry this whole job, and a retry would re-read stats (now
        # already reflecting this bill) and re-apply the Welford update a
        # second time, double-counting this bill into the rolling mean.
        # The try/except below is what prevents that: a failed send is
        # logged and swallowed, never re-raised.
        await db.commit()
        logger.info("analyze_bill: bill %s analyzed (anomaly=%s)", bill.id, is_anomaly)

        if is_anomaly:
            user = await db.get(User, bill.user_id)
            if user is None:
                logger.warning("analyze_bill: bill %s has no matching user %s", bill.id, bill.user_id)
            else:
                try:
                    await send_anomaly_alert(user.telegram_chat_id, bill)
                    logger.info("analyze_bill: anomaly alert sent for bill %s", bill.id)
                except TelegramDeliveryError as exc:
                    logger.warning(
                        "analyze_bill: failed to send anomaly alert for bill %s (%s) - "
                        "not retrying the job over this, the anomaly flag is already saved",
                        bill.id,
                        exc,
                    )


async def enforce_due_alerts(ctx) -> None:
    """
    Enforcer Agent. Runs on a cron schedule (see WorkerSettings below).

    Claims a batch of due-soon, unalerted bills with `FOR UPDATE SKIP
    LOCKED` so that concurrent runs — or multiple worker replicas — never
    claim the same row twice; each run/replica just gets a disjoint batch
    instead of blocking on the others' locks. The transaction commits (and
    releases the row locks) only after each delivery attempt in the batch
    has been resolved one way or the other.
    """
    cutoff = datetime.utcnow().date() + timedelta(days=ALERT_WINDOW_DAYS)

    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                select(Bill)
                .where(
                    Bill.due_date <= cutoff,
                    # Bug fix: this query had no status filter at all, so a
                    # bill paid via /pay (status -> "paid") between upload
                    # and the next cron tick still matched here and got a
                    # reminder sent for it anyway. "!= paid" not "== pending"
                    # - status is never literally "pending" in this codebase,
                    # it's "pending_analysis" -> "analyzed" -> optionally
                    # "paid" (same convention already used in main.py's
                    # /bills/pending).
                    Bill.status != "paid",
                    Bill.alert_sent_at.is_(None),
                    Bill.alert_attempts < MAX_ALERT_ATTEMPTS,
                )
                .with_for_update(skip_locked=True)
                .limit(ENFORCER_BATCH_SIZE)
            )
            due_bills = result.scalars().all()
            logger.info("enforce_due_alerts: %d due-soon bill(s) claimed this run", len(due_bills))

            for bill in due_bills:
                user = await db.get(User, bill.user_id)
                if user is None:
                    continue
                try:
                    await send_bill_reminder(user.telegram_chat_id, bill)
                    bill.alert_sent_at = datetime.utcnow()
                    logger.info("enforce_due_alerts: reminder sent for bill %s", bill.id)
                except TelegramDeliveryError:
                    bill.alert_attempts += 1
                    logger.warning(
                        "Telegram delivery failed for bill %s (attempt %s/%s)",
                        bill.id,
                        bill.alert_attempts,
                        MAX_ALERT_ATTEMPTS,
                    )
                    if bill.alert_attempts >= MAX_ALERT_ATTEMPTS:
                        logger.error(
                            "Bill %s exhausted alert retries — dead-lettered, needs manual follow-up",
                            bill.id,
                        )
            # Commit on context exit persists alert_sent_at / alert_attempts
            # and releases the row locks for the next run.


async def cleanup_old_bills(ctx) -> None:
    """
    Data Retention Agent. Runs once daily (see WorkerSettings below).

    Hard-deletes Bill rows older than RETENTION_DAYS, based on created_at.

    No file-deletion step here, and that's not an omission: this system
    never writes bill images or PDFs to disk, or to any table, in the
    first place. The Sanitizer (app/routers/sanitizer.py::upload_bill)
    reads an upload into an in-memory buffer, hands it to
    app/vision.py, and explicitly zeroes and drops that buffer within
    the same request - see the "Explicit destruction" comment there.
    There has never been a file on this server for this job to find;
    "0 files deleted" in the log line below is the expected, correct
    outcome every run, not a sign anything is missing.

    Also doesn't touch InstitutionStats' rolling Welford baseline
    (see analyze_bill above) - a deleted bill's contribution to a user's
    per-institution mean/variance isn't retroactively removed. Same
    class of limitation already flagged on PUT /bills/{id}/amount in
    app/main.py: "uncounting" a point from Welford's algorithm is
    possible but adds real complexity this request didn't ask for.
    """
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)

    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                delete(Bill).where(Bill.created_at < cutoff).returning(Bill.id)
            )
            deleted_ids = result.scalars().all()

    logger.info(
        "cleanup_old_bills: deleted %d bill record(s) older than %d days "
        "(cutoff=%s). 0 files deleted - bill images/PDFs are never persisted "
        "to disk by this system; see function docstring.",
        len(deleted_ids),
        RETENTION_DAYS,
        cutoff.date().isoformat(),
    )


async def shutdown(ctx) -> None:
    await telegram_client.close()
    await engine.dispose()


class WorkerSettings:
    functions = [analyze_bill]
    cron_jobs = [
        cron(enforce_due_alerts, minute=set(range(0, 60, 2))),
        cron(cleanup_old_bills, hour=3, minute=0),
    ]
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 20
