# ledger

Double-entry ledger for a marketplace: it records what we owe each seller and
pays them out daily. About 2,000 sellers, ~50 requests/second at peak.

## Components

| Component | Runs as | What it does |
|---|---|---|
| API (`app/main.py`) | 3 replicas (`k8s/api.yaml`) | Transfers between accounts, balances, entry history |
| Outbox relay (`app/outbox_relay.py`) | 1 replica (`k8s/relay.yaml`) | Publishes ledger events to the event bus |
| Payouts (`app/jobs/payouts.py`) | Kubernetes CronJob, daily (`k8s/payouts-cronjob.yaml`) | Moves seller balances to the bank |

- **PostgreSQL 16** (managed). Point-in-time recovery for 7 days plus nightly
  snapshots kept 30 days. A restore is rehearsed every quarter:
  `docs/runbooks/restore.md`.
- **Callers:** the order service (service token) creates transfers; the seller
  dashboard (seller tokens) reads balances. Tokens are JWTs from our identity
  provider, verified here with its public key.
- **Bank:** payouts go through the bank's transfer API, which honours an
  `Idempotency-Key` header.
- **Statement importer** (separate repo) reads the bank statement each
  morning, marks submitted payouts `confirmed` and books clearing → bank.

Design decisions are recorded in `docs/adr/`. Read ADR 0001 before changing
anything that writes money.

## Invariants

1. Every transfer writes exactly two entries that sum to zero.
2. An account's `balance_minor` equals the sum of its entries.
3. Seller and clearing accounts never go negative (a CHECK constraint).

`ops/reconcile.sql` checks (1) and (2). On-call runs it weekly.

## Local development

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=dev postgres:16
export LEDGER_DATABASE_URL=postgresql://postgres:dev@localhost/postgres ...
psql "$LEDGER_DATABASE_URL" -f migrations/001_init.sql -f migrations/002_entries_account_idx.sql
uvicorn app.main:app --reload
TEST_DATABASE_URL=$LEDGER_DATABASE_URL pytest
```
