"""Daily payouts. Run by a Kubernetes CronJob (concurrencyPolicy: Forbid).

Safe to re-run for the same date: payouts are unique per (seller, date), the
ledger move happens in the same transaction that creates the payout, and the
bank call carries a stable idempotency key.
"""

import asyncio
import datetime as dt
import hashlib
import json

import httpx
import structlog

from ..bank_client import BankClient, PermanentBankError
from ..config import settings
from ..db import create_pool
from ..logging import configure_logging

log = structlog.get_logger()
LOCK_KEY = 0x5041594F5554  # "PAYOUT"
MAX_ATTEMPTS = 5


async def create_pending_payouts(pool, payout_date: dt.date) -> int:
    created = 0
    sellers = await pool.fetch(
        """
        SELECT a.id AS account_id, a.seller_id, a.currency
        FROM accounts a
        WHERE a.kind = 'seller' AND a.balance_minor >= $1
        """,
        settings.payout_minimum_minor,
    )
    for seller in sellers:
        async with pool.acquire(timeout=5) as conn:
            async with conn.transaction():
                clearing = await conn.fetchval(
                    "SELECT id FROM accounts WHERE kind = 'payout_clearing' AND currency = $1",
                    seller["currency"],
                )
                # Same lock order as transfers (by id), so the two can't deadlock.
                locked = await conn.fetch(
                    "SELECT id, balance_minor FROM accounts WHERE id = ANY($1::bigint[]) ORDER BY id FOR UPDATE",
                    [seller["account_id"], clearing],
                )
                account = next(r for r in locked if r["id"] == seller["account_id"])
                amount = account["balance_minor"]
                if amount < settings.payout_minimum_minor:
                    continue
                payout_id = await conn.fetchval(
                    """
                    INSERT INTO payouts (seller_id, account_id, payout_date, amount_minor, currency, status)
                    VALUES ($1, $2, $3, $4, $5, 'pending')
                    ON CONFLICT (seller_id, payout_date) DO NOTHING
                    RETURNING id
                    """,
                    seller["seller_id"], account["id"], payout_date, amount, seller["currency"],
                )
                if payout_id is None:
                    continue  # already created by an earlier run today
                key = f"payout:{payout_id}"
                transfer_id = await conn.fetchval(
                    """
                    INSERT INTO transfers (from_account, to_account, amount_minor, currency, reference,
                                           idempotency_key, request_hash, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'payouts-job')
                    RETURNING id
                    """,
                    account["id"], clearing, amount, seller["currency"], f"payout {payout_id}",
                    key, hashlib.sha256(key.encode()).hexdigest(),
                )
                await conn.executemany(
                    "INSERT INTO ledger_entries (transfer_id, account_id, amount_minor) VALUES ($1, $2, $3)",
                    [(transfer_id, account["id"], -amount), (transfer_id, clearing, amount)],
                )
                await conn.executemany(
                    "UPDATE accounts SET balance_minor = balance_minor + $2 WHERE id = $1",
                    [(account["id"], -amount), (clearing, amount)],
                )
                await conn.execute(
                    "INSERT INTO outbox (topic, payload) VALUES ('payout.created', $1::jsonb)",
                    json.dumps({"payout_id": payout_id, "seller_id": seller["seller_id"], "amount_minor": amount}),
                )
                created += 1
    return created


async def submit_pending(pool, bank: BankClient) -> None:
    rows = await pool.fetch(
        """
        SELECT p.id, p.amount_minor, p.currency, s.bank_beneficiary
        FROM payouts p JOIN sellers s ON s.id = p.seller_id
        WHERE p.status IN ('pending', 'retrying') AND p.attempts < $1
        ORDER BY p.id
        """,
        MAX_ATTEMPTS,
    )
    for row in rows:
        try:
            ref = await bank.create_transfer(
                idempotency_key=f"payout-{row['id']}",
                amount_minor=row["amount_minor"],
                currency=row["currency"],
                beneficiary=row["bank_beneficiary"],
            )
        except PermanentBankError as exc:
            await pool.execute(
                "UPDATE payouts SET status = 'failed', attempts = attempts + 1, last_error = $2, "
                "updated_at = now() WHERE id = $1",
                row["id"], str(exc),
            )
            log.error("payout rejected by bank; needs manual review", payout_id=row["id"], error=str(exc))
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            status = await pool.fetchval(
                "UPDATE payouts SET attempts = attempts + 1, last_error = $2, updated_at = now(), "
                "status = CASE WHEN attempts + 1 >= $3 THEN 'failed' ELSE 'retrying' END "
                "WHERE id = $1 RETURNING status",
                row["id"], str(exc), MAX_ATTEMPTS,
            )
            if status == "failed":
                log.error("payout gave up after retries; needs manual review", payout_id=row["id"], error=str(exc))
            else:
                log.warning("payout submission failed; will retry next run", payout_id=row["id"], error=str(exc))
        else:
            await pool.execute(
                "UPDATE payouts SET status = 'submitted', bank_ref = $2, attempts = attempts + 1, "
                "updated_at = now() WHERE id = $1",
                row["id"], ref,
            )


async def run(payout_date: dt.date) -> None:
    configure_logging()
    pool = await create_pool()
    bank = BankClient()
    try:
        async with pool.acquire(timeout=5) as lock_conn:
            if not await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
                log.warning("another payout run holds the lock; exiting")
                return
            try:
                created = await create_pending_payouts(pool, payout_date)
                log.info("payouts created", count=created, payout_date=str(payout_date))
                await submit_pending(pool, bank)
            finally:
                await lock_conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    finally:
        await bank.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run(dt.date.today()))
