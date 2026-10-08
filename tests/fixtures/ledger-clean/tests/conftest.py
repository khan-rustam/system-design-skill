import os

import asyncpg
import pytest

TEST_DSN = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def pool():
    if not TEST_DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    pool = await asyncpg.create_pool(TEST_DSN)
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE outbox, ledger_entries, transfers, payouts, accounts, sellers RESTART IDENTITY CASCADE"
        )
        await conn.execute("INSERT INTO sellers (id, name, bank_beneficiary) VALUES (1, 's', 'GB00TEST')")
        await conn.execute(
            "INSERT INTO accounts (kind, currency, balance_minor) VALUES ('platform', 'USD', 0)"
        )
        await conn.execute(
            "INSERT INTO accounts (kind, seller_id, currency, balance_minor) VALUES ('seller', 1, 'USD', 0)"
        )
        await conn.execute(
            "INSERT INTO accounts (kind, currency, balance_minor) VALUES ('payout_clearing', 'USD', 0)"
        )
    yield pool
    await pool.close()
