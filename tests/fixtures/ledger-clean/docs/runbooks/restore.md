# Restore runbook

1. In the cloud console, create a new instance from point-in-time recovery at
   the chosen timestamp (or from the latest nightly snapshot).
2. Run `ops/reconcile.sql` against the restored instance. It must return no rows.
3. Point `LEDGER_DATABASE_URL` at the new instance and roll the API, relay and
   payouts deployments.

Rehearsed quarterly. Last rehearsal: 2026-07-14 (restore took 22 minutes).
