"""Publishes outbox rows to the event bus. At-least-once: consumers dedupe on message_id."""

import asyncio
import random
import signal

import httpx
import structlog

from .config import settings
from .db import create_pool
from .logging import configure_logging

log = structlog.get_logger()
BATCH = 20


class Publisher:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=str(settings.event_bus_url),
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers={"Authorization": "Bearer " + settings.event_bus_token.get_secret_value()},
        )

    async def publish(self, topic: str, payload: str, message_id: str) -> None:
        resp = await self._client.post(
            "/publish", json={"topic": topic, "message_id": message_id, "payload": payload}
        )
        resp.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


async def relay_batch(pool, publisher: Publisher) -> int:
    async with pool.acquire(timeout=5) as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, topic, payload::text AS payload FROM outbox
                WHERE published_at IS NULL
                ORDER BY id
                LIMIT $1
                FOR UPDATE SKIP LOCKED
                """,
                BATCH,
            )
            for row in rows:
                await publisher.publish(row["topic"], row["payload"], message_id=str(row["id"]))
            if rows:
                await conn.execute(
                    "UPDATE outbox SET published_at = now() WHERE id = ANY($1::bigint[])",
                    [r["id"] for r in rows],
                )
    return len(rows)


async def _pause(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def main() -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    pool = await create_pool()
    publisher = Publisher()
    backoff = 1.0
    try:
        while not stop.is_set():
            try:
                published = await relay_batch(pool, publisher)
                backoff = 1.0
                if published == 0:
                    await _pause(stop, 1.0)
            except Exception:
                log.exception("outbox relay batch failed")
                await _pause(stop, random.uniform(0, backoff))
                backoff = min(backoff * 2, 60.0)
    finally:
        await publisher.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
