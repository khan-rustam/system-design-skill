# ADR 0001: Ledger design

Status: accepted (2025-11-03)

## Decisions

1. **Integer minor units.** Every amount is a `BIGINT` count of the currency's
   minor unit. No floats or decimals anywhere in the write path.
2. **Append-only entries, maintained balance.** `ledger_entries` is never
   updated or deleted. `accounts.balance_minor` is updated in the *same
   transaction* as the entries, so reads are cheap and always consistent with
   the entries. `ops/reconcile.sql` proves it.
3. **Idempotency is enforced by the database.** `transfers` is unique on
   `(from_account, idempotency_key)`. The key is claimed first in the
   transaction; a replay with a different body is a 409.
4. **Row locks in id order.** Transfers lock both accounts with
   `SELECT … FOR UPDATE … ORDER BY id`, so concurrent opposite transfers cannot
   deadlock and balances cannot be overdrawn.
5. **Events through an outbox.** Events are written in the business
   transaction and published by a relay (at-least-once; consumers dedupe on
   `message_id`). There is no dual write.
6. **Payouts are re-runnable.** One payout per seller per day (unique), the
   ledger move is in the same transaction, and the bank call carries a stable
   idempotency key. The CronJob forbids overlap; an advisory lock backs that up
   for manual runs.
