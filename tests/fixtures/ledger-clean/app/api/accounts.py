from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..auth import Principal, require_principal

router = APIRouter()

SCOPED = "($2::bigint IS NULL OR seller_id = $2)"


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: int, request: Request, principal: Principal = Depends(require_principal)
):
    row = await request.app.state.pool.fetchrow(
        f"SELECT id, seller_id, kind, currency, balance_minor FROM accounts WHERE id = $1 AND {SCOPED}",
        account_id, principal.seller_scope,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    return dict(row)


@router.get("/accounts/{account_id}/entries")
async def list_entries(
    account_id: int,
    request: Request,
    before_id: Optional[int] = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_principal),
):
    pool = request.app.state.pool
    owns = await pool.fetchval(
        f"SELECT 1 FROM accounts WHERE id = $1 AND {SCOPED}", account_id, principal.seller_scope
    )
    if owns is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    rows = await pool.fetch(
        """
        SELECT id, transfer_id, amount_minor, created_at
        FROM ledger_entries
        WHERE account_id = $1 AND ($2::bigint IS NULL OR id < $2)
        ORDER BY id DESC
        LIMIT $3
        """,
        account_id, before_id, limit,
    )
    next_cursor = rows[-1]["id"] if len(rows) == limit else None
    return {"data": [dict(r) for r in rows], "next_before_id": next_cursor}
