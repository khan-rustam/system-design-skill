import asyncio
import random

import httpx

from .config import settings

RETRYABLE = {429, 500, 502, 503, 504}


class PermanentBankError(Exception):
    pass


class BankClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=str(settings.bank_api_url),
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"Authorization": "Bearer " + settings.bank_api_token.get_secret_value()},
        )

    async def create_transfer(self, *, idempotency_key: str, amount_minor: int, currency: str,
                              beneficiary: str) -> str:
        """Returns the bank's reference. Safe to retry: the bank dedupes on Idempotency-Key."""
        body = {"amount": amount_minor, "currency": currency, "beneficiary": beneficiary}
        for attempt in range(3):
            try:
                resp = await self._client.post(
                    "/transfers", json=body, headers={"Idempotency-Key": idempotency_key}
                )
            except httpx.TransportError:
                if attempt == 2:
                    raise
            else:
                if resp.status_code < 400:
                    return resp.json()["reference"]
                if resp.status_code not in RETRYABLE:
                    raise PermanentBankError(f"bank rejected transfer: {resp.status_code}")
                if attempt == 2:
                    resp.raise_for_status()
            await asyncio.sleep(random.uniform(0, 0.5 * 2 ** attempt))
        raise AssertionError("unreachable")

    async def close(self) -> None:
        await self._client.aclose()
