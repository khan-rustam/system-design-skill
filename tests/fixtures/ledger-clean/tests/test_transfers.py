import asyncio

import pytest

from app.api.transfers import TransferIn, create_transfer
from app.auth import Principal

SERVICE = Principal(subject="orders", role="service", seller_id=None)


class _Req:
    def __init__(self, pool):
        self.app = type("A", (), {"state": type("S", (), {"pool": pool})()})()


def _body(amount, src=1, dst=2):
    return TransferIn(from_account=src, to_account=dst, amount_minor=amount, currency="USD", reference="r")


@pytest.mark.asyncio
async def test_replay_returns_original_transfer(pool):
    first = await create_transfer(_body(500), _Req(pool), "key-00000001", SERVICE)
    again = await create_transfer(_body(500), _Req(pool), "key-00000001", SERVICE)
    assert first.id == again.id
    assert await pool.fetchval("SELECT count(*) FROM ledger_entries") == 2


@pytest.mark.asyncio
async def test_same_key_different_body_is_rejected(pool):
    await create_transfer(_body(500), _Req(pool), "key-00000002", SERVICE)
    with pytest.raises(Exception) as exc:
        await create_transfer(_body(600), _Req(pool), "key-00000002", SERVICE)
    assert getattr(exc.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_concurrent_spends_never_overdraw(pool):
    await create_transfer(_body(100), _Req(pool), "fund-0000001", SERVICE)  # seller now has 100

    async def spend(i):
        try:
            await create_transfer(_body(10, src=2, dst=1), _Req(pool), "spend-%07d" % i, SERVICE)
            return True
        except Exception:
            return False

    results = await asyncio.gather(*(spend(i) for i in range(25)))
    assert sum(results) == 10
    assert await pool.fetchval("SELECT balance_minor FROM accounts WHERE id = 2") == 0


@pytest.mark.asyncio
async def test_balances_match_entries(pool):
    await create_transfer(_body(700), _Req(pool), "key-00000003", SERVICE)
    drift = await pool.fetchval(
        """
        SELECT count(*) FROM accounts a
        LEFT JOIN (SELECT account_id, sum(amount_minor) s FROM ledger_entries GROUP BY account_id) e
          ON e.account_id = a.id
        WHERE a.balance_minor <> coalesce(e.s, 0)
        """
    )
    assert drift == 0
