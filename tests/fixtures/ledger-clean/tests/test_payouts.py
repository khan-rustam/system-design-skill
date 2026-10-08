import datetime as dt

import pytest

from app.jobs.payouts import create_pending_payouts

DAY = dt.date(2026, 10, 1)


@pytest.mark.asyncio
async def test_rerun_creates_one_payout_per_seller_per_day(pool):
    await pool.execute("UPDATE accounts SET balance_minor = 5000 WHERE id = 2")
    await pool.execute("UPDATE accounts SET balance_minor = -5000 WHERE id = 1")
    assert await create_pending_payouts(pool, DAY) == 1
    assert await create_pending_payouts(pool, DAY) == 0
    assert await pool.fetchval("SELECT count(*) FROM payouts") == 1
    assert await pool.fetchval("SELECT balance_minor FROM accounts WHERE id = 2") == 0
