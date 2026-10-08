import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import Principal, require_service

router = APIRouter()


class TransferIn(BaseModel):
    from_account: int = Field(gt=0)
    to_account: int = Field(gt=0)
    amount_minor: int = Field(gt=0, le=100_000_000_00)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reference: str = Field(min_length=1, max_length=200)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()


class TransferOut(BaseModel):
    id: int
    from_account: int
    to_account: int
    amount_minor: int
    currency: str
    reference: str


@router.post("/transfers", status_code=status.HTTP_201_CREATED, response_model=TransferOut)
async def create_transfer(
    body: TransferIn,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    principal: Principal = Depends(require_service),
):
    if body.from_account == body.to_account:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "from and to must differ")

    pool = request.app.state.pool
    async with pool.acquire(timeout=2) as conn:
        async with conn.transaction():
            # Claim the idempotency key first. A concurrent request with the same
            # key blocks here on the unique index until this transaction ends.
            transfer_id = await conn.fetchval(
                """
                INSERT INTO transfers
                    (from_account, to_account, amount_minor, currency, reference,
                     idempotency_key, request_hash, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (from_account, idempotency_key) DO NOTHING
                RETURNING id
                """,
                body.from_account, body.to_account, body.amount_minor, body.currency,
                body.reference, idempotency_key, body.fingerprint(), principal.subject,
            )
            if transfer_id is None:
                return await _replay(conn, body, idempotency_key)

            # Lock both accounts in a fixed order so two opposite transfers can't deadlock.
            accounts = await conn.fetch(
                """
                SELECT id, kind, currency, balance_minor
                FROM accounts WHERE id = ANY($1::bigint[])
                ORDER BY id
                FOR UPDATE
                """,
                [body.from_account, body.to_account],
            )
            by_id = {row["id"]: row for row in accounts}
            source, dest = by_id.get(body.from_account), by_id.get(body.to_account)
            if source is None or dest is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
            if source["currency"] != body.currency or dest["currency"] != body.currency:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "currency mismatch")
            if source["kind"] != "platform" and source["balance_minor"] < body.amount_minor:
                raise HTTPException(status.HTTP_409_CONFLICT, "insufficient funds")

            await conn.executemany(
                "INSERT INTO ledger_entries (transfer_id, account_id, amount_minor) VALUES ($1, $2, $3)",
                [
                    (transfer_id, body.from_account, -body.amount_minor),
                    (transfer_id, body.to_account, body.amount_minor),
                ],
            )
            await conn.executemany(
                "UPDATE accounts SET balance_minor = balance_minor + $2 WHERE id = $1",
                [(body.from_account, -body.amount_minor), (body.to_account, body.amount_minor)],
            )
            await conn.execute(
                "INSERT INTO outbox (topic, payload) VALUES ('transfer.created', $1::jsonb)",
                json.dumps({"transfer_id": transfer_id, **body.model_dump()}),
            )

    return TransferOut(id=transfer_id, **body.model_dump())


async def _replay(conn, body: TransferIn, idempotency_key: str) -> TransferOut:
    row = await conn.fetchrow(
        """
        SELECT id, from_account, to_account, amount_minor, currency, reference, request_hash
        FROM transfers WHERE from_account = $1 AND idempotency_key = $2
        """,
        body.from_account, idempotency_key,
    )
    if row["request_hash"] != body.fingerprint():
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Idempotency-Key was already used with a different request"
        )
    return TransferOut(**{k: row[k] for k in TransferOut.model_fields})
