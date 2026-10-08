# System Design Audit: shop-orders

| Field | Value |
|---|---|
| Date | 2026-10-08 |
| Scope | Whole repository: API, jobs, migrations, deploy and backup scripts |
| Revision | shop-orders working copy (no git metadata available) |
| Depth | Standard |
| Stage assumed | Production: real customers and card payments, ~3k orders/day, 10x peaks |

## Summary

The core flows work for one request at a time, but the payment path is not
safe under retries or concurrency, and that is exactly what a sale produces.
Duplicate PayGate deliveries re-apply payments, concurrent checkouts oversell,
a mail outage refunds orders that were paid and shipped, and the nightly job
silently inflates the counts that supplier royalties are paid from. Fix the
Critical items before the sale; most are small, local changes.

**Findings:** 9 Critical · 5 Normal · 2 Good Practice

**Top risks**
1. SDA-001 — A retried PayGate webhook pays cashback, buys a label and emails again.
2. SDA-002 — Two customers buying the last unit both succeed; one unit is sold twice.
3. SDA-003 — An SMTP failure marks a paid, shipped order failed and refunds it.
4. SDA-004 — `sold_count` grows by the full sales total every night, eight times over.
5. SDA-005 — One slow carrier response can stall every API worker.
6. SDA-006 — Float arithmetic can undercharge by a cent.
7. SDA-007 — Concurrent wallet updates lose money; missing wallets lose cashback.
8. SDA-008 — Backups can fail silently, and rotation then deletes the good ones.
9. SDA-009 — Each failed query leaks a DB connection until the worker is unusable.

## System map

| Component | Runs as | Owns |
|---|---|---|
| Flask API (`app/`) | gunicorn, 4 sync workers × 2 VMs (`gunicorn.conf.py:2`) | orders, payments, wallets, products |
| Nightly reconcile (`app/jobs/reconcile.py`) | APScheduler inside every API worker | `products.sold_count` |
| PostgreSQL 15 | single primary | all state |
| PayGate | external, webhook at-least-once | card payments |
| Carrier API, SMTP relay | external | labels, email |

```
customer ──► LB ──► Flask (8 workers) ──► Postgres
                        │  ▲
          checkout URL ─┘  └── PayGate webhook ──► cashback, carrier label, SMTP
```

- **Critical paths:** checkout (`POST /orders`) and payment confirmation
  (`POST /webhooks/paygate`). Both move money or stock.
- **Binding constraint:** 8 synchronous workers in total. Anything that blocks
  a worker (an outbound call, a pool wait) removes an eighth of capacity.
- **Runtime shape:** every worker runs its own scheduler and its own
  connection pool of up to `DB_POOL_MAX` (10).

## Scorecard

| Lens | Rating | Note |
|---|---|---|
| Architecture & boundaries | Adequate | Simple monolith; side effects run inline in the webhook |
| Data model & integrity | Weak | Races on stock and wallet, float money, no idempotency constraints |
| Reliability & failure handling | Weak | Missing timeouts, connection leak, a side effect rewrites core state |
| Concurrency, jobs & async work | Weak | Scheduler in every worker; non-idempotent job |
| Scalability, performance & capacity | Adequate | Unbounded history; otherwise fine at this volume |
| API & integration contracts | Weak | Webhook verified but not deduplicated |
| Security architecture | Strong | HMAC webhook check, hashed sessions, ownership-scoped reads |
| Observability | Adequate | Request IDs generated but not logged |
| Deployment, operations & recovery | Weak | Unverified backups, static health check, manual migrations |
| Maintainability & evolvability | Adequate | No tests on payment transitions |

## Findings

| ID | Severity | Confidence | Lens | Title | Location |
|---|---|---|---|---|---|
| SDA-001 | Critical | Confirmed | API & integration contracts | Duplicate webhook deliveries re-apply payment effects | `app/routes/payments.py:35` |
| SDA-002 | Critical | Confirmed | Data model & integrity | Oversell under concurrent checkout | `app/routes/orders.py:26` |
| SDA-003 | Critical | Confirmed | Reliability & failure handling | Email failure cancels and refunds a paid, shipped order | `app/routes/payments.py:49` |
| SDA-004 | Critical | Confirmed | Concurrency, jobs & async work | Nightly reconcile inflates sold_count, eight times a night | `app/jobs/reconcile.py:7` |
| SDA-005 | Critical | Confirmed | Reliability & failure handling | Carrier and SMTP calls have no timeout on sync workers | `app/services/shipping.py:13` |
| SDA-006 | Critical | Confirmed | Data model & integrity | Money computed and stored as binary floats | `app/routes/orders.py:33` |
| SDA-007 | Critical | Confirmed | Data model & integrity | Wallet balance updates lose writes | `app/services/wallet.py:9` |
| SDA-008 | Critical | Confirmed | Deployment, operations & recovery | Backups can fail silently and rotate away good copies | `deploy/backup.sh:6` |
| SDA-009 | Critical | Confirmed | Reliability & failure handling | A failed query leaks its pooled connection | `app/db.py:25` |
| SDA-010 | Normal | Confirmed | Deployment, operations & recovery | Health check proves nothing; LB and deploy rely on it | `app/routes/health.py:6` |
| SDA-011 | Normal | Confirmed | Scalability, performance & capacity | Order history is unbounded | `app/routes/orders.py:52` |
| SDA-012 | Normal | Confirmed | Data model & integrity | Unpaid orders hold stock forever | `app/routes/orders.py:35` |
| SDA-013 | Normal | Likely | Reliability & failure handling | DB connections have no connect or statement deadline | `app/db.py:11` |
| SDA-014 | Normal | Confirmed | Deployment, operations & recovery | Migrations are applied by hand, outside the deploy | `deploy/deploy.sh:5` |
| SDA-015 | Good Practice | Confirmed | Observability | Request ID is generated but never logged | `app/__init__.py:25` |
| SDA-016 | Good Practice | Confirmed | Deployment, operations & recovery | SMTP host silently falls back to localhost | `app/config.py:14` |

### SDA-001 · Critical · Duplicate webhook deliveries re-apply payment effects

- **Lens:** API & integration contracts
- **Confidence:** Confirmed
- **Evidence:** `app/routes/payments.py:35-47` marks the order paid, inserts a
  payment row, credits cashback and buys a shipping label on every delivery.
  `migrations/001_init.sql:50` declares `event_id TEXT NOT NULL` with no
  unique constraint, so nothing rejects a repeat. The README says PayGate
  retries until it gets a 2xx.
- **Failure scenario:** PayGate delivers `payment.succeeded`; the handler is
  slow (it calls the carrier inline) and PayGate times out → PayGate redelivers
  → the second run inserts a second payment row, credits 2% cashback again,
  buys a second label and emails again. Every retry repeats it.
- **Recommendation:** Let the database decide what has already happened, in
  one transaction, and let a retry finish what hasn't:
  ```sql
  -- migrations/003_payment_event_unique.sql (CONCURRENTLY can't run in a
  -- transaction; resolve existing duplicate event_ids first)
  CREATE UNIQUE INDEX CONCURRENTLY payments_event_id_key ON payments (event_id);
  ```
  ```python
  with transaction() as cur:
      cur.execute("INSERT INTO payments (...) VALUES (...) ON CONFLICT (event_id) DO NOTHING", ...)
      cur.execute("UPDATE orders SET status = 'paid' WHERE id = %s AND status = 'pending_payment' "
                  "RETURNING user_id", (order_id,))
      row = cur.fetchone()
      if row:
          wallet.add_cashback(cur, row["user_id"], cashback)   # takes this cursor: commits once, with the payment
  ```
  Don't return 204 as soon as the event is a duplicate. If the first
  delivery's carrier call failed, that 204 ends PayGate's retries and the
  paid order never ships. Buy the label only while the order has none, under
  a per-order lock (`pg_try_advisory_xact_lock(order_id)`), so a retry
  completes what is missing and repeats nothing. SDA-005's job queue later
  takes the carrier call out of the request entirely.
- **Effort:** S
- **Verify:** A test that posts the same signed event twice (sequentially and
  concurrently) and asserts one payment row, one cashback credit and one label
  request, and one where the first carrier call fails and the redelivery buys
  exactly one label.
- **Status:** Open

### SDA-002 · Critical · Oversell under concurrent checkout

- **Lens:** Data model & integrity
- **Confidence:** Confirmed
- **Evidence:** `app/routes/orders.py:26-31` reads `stock` outside any
  transaction and checks it in Python; `app/routes/orders.py:36-39` then writes
  the absolute value `stock - qty`. `migrations/001_init.sql:26` has no
  `CHECK (stock >= 0)`.
- **Failure scenario:** Two customers buy the last unit at the same moment →
  both read `stock = 1` → both pass the check → both write `stock = 0` and
  both orders are created. With larger quantities, concurrent decrements
  overwrite each other and stock drifts upward.
- **Recommendation:** One conditional update inside the order transaction:
  ```sql
  UPDATE products SET stock = stock - %(qty)s
  WHERE id = %(id)s AND stock >= %(qty)s
  RETURNING price;
  ```
  Treat zero rows as "out of stock" (409). Add `CHECK (stock >= 0)`.
- **Effort:** S
- **Verify:** A test that runs two checkouts for a product with stock 1
  concurrently and asserts exactly one 201 and final stock 0.
- **Status:** Open

### SDA-003 · Critical · Email failure cancels and refunds a paid, shipped order

- **Lens:** Reliability & failure handling
- **Confidence:** Confirmed
- **Evidence:** `app/routes/payments.py:49-55` catches any exception from the
  confirmation email, sets the order to `failed` and calls `paygate.refund`,
  after the payment succeeded (`:37`) and the label was bought (`:47`).
- **Failure scenario:** The SMTP relay throttles during the sale → every
  confirmation raises → paid orders are marked failed and refunded while the
  warehouse ships them from the labels already bought.
- **Recommendation:** Separate delivery state from order state: record
  `confirmation_status` and retry it from a job; never change the order or
  refund because a notification failed.
- **Effort:** S
- **Verify:** A test where the emailer raises and the order stays `paid`, no
  refund is requested, and the email is queued for retry.
- **Status:** Open

### SDA-004 · Critical · Nightly reconcile inflates sold_count, eight times a night

- **Lens:** Concurrency, jobs & async work
- **Confidence:** Confirmed
- **Evidence:** `app/jobs/reconcile.py:7-17` adds the *total* paid quantity
  per product to `sold_count` on every run. `app/__init__.py:32-34` starts
  the scheduler inside `create_app()`, which runs in each of 4 gunicorn
  workers (`gunicorn.conf.py:2`) on both VMs.
- **Failure scenario:** At 02:00 the job runs 8 times; each run adds the
  all-time total again. After one night a product with 100 sales shows
  `sold_count` 800 more than before, and the monthly supplier royalty
  statement pays on it.
- **Recommendation:** Make the job idempotent by recomputing and overwriting
  (`SET sold_count = s.total_sold`), and run it once: a cron or systemd timer
  on one host, or guard it with `pg_try_advisory_lock`.
- **Effort:** S
- **Verify:** Run the job twice in a test and assert `sold_count` equals the
  paid quantity both times.
- **Status:** Open

### SDA-005 · Critical · Carrier and SMTP calls have no timeout on sync workers

- **Lens:** Reliability & failure handling
- **Confidence:** Confirmed
- **Evidence:** `app/services/shipping.py:13-26` calls `requests.post` with no
  `timeout`; `app/services/emailer.py:22` opens `smtplib.SMTP` with no timeout.
  Both run inside the webhook on `sync` workers (`gunicorn.conf.py:3`).
- **Failure scenario:** The carrier API slows down at peak → each webhook
  holds a worker until gunicorn kills it at 60 s → with 8 workers in total,
  checkout and order pages stop responding, and PayGate's retries (SDA-001)
  add more blocked requests.
- **Recommendation:** `timeout=(3.05, 10)` on the carrier call and
  `timeout=15` on SMTP. Better, move label purchase and email out of the
  webhook into a job, so the webhook acknowledges quickly.
- **Effort:** S
- **Verify:** A test with a stubbed carrier that never responds asserts the
  call fails within the timeout.
- **Status:** Open

### SDA-006 · Critical · Money computed and stored as binary floats

- **Lens:** Data model & integrity
- **Confidence:** Confirmed
- **Evidence:** `app/routes/orders.py:33` computes `float(price) * qty`;
  `app/services/paygate.py:17` charges `int(total * 100)`, which truncates.
  `migrations/001_init.sql:36` and `:49` store totals and payments as `REAL`;
  `migrations/002_wallets.sql:3` stores wallet balances as `DOUBLE PRECISION`.
- **Failure scenario:** A $1.15 item: `float(1.15) * 100` is
  114.99999999999999 and `int()` truncates it to 114, so the customer is
  charged $1.14 for a $1.15 order. The same happens for many common prices.
  `REAL` keeps about 7 significant digits, so large totals also round when
  stored.
- **Recommendation:** Keep money in integer cents (or `NUMERIC(12,2)` with
  `Decimal` in Python) end to end; compute the charge from cents, never from a
  float.
- **Effort:** M
- **Verify:** A test that checks out one $1.15 item and asserts a charge of 115 cents.
- **Status:** Open

### SDA-007 · Critical · Wallet balance updates lose writes

- **Lens:** Data model & integrity
- **Confidence:** Confirmed
- **Evidence:** `app/services/wallet.py:9-18` reads the balance, adds or
  subtracts in Python, and writes the result back, with no lock or ledger.
  When the user has no wallet row, `get_balance` returns 0 and the `UPDATE`
  matches nothing, so the credit vanishes.
- **Failure scenario:** A cashback credit and a wallet spend for the same user
  run concurrently → both read 10.00 → one writes 10.20, the other 5.00 → the
  credit is lost. Users without a wallet row silently never receive cashback.
- **Recommendation:** Use an append-only `wallet_entries` table with a unique
  reference per credit, and update the balance atomically in the same
  transaction (`UPDATE … SET balance = balance + %s … RETURNING`). Create the
  wallet row when the user is created, or upsert it.
- **Effort:** M
- **Verify:** Concurrent credit and spend in a test end at the arithmetic sum;
  a credit for a user without a row creates one.
- **Status:** Open

### SDA-008 · Critical · Backups can fail silently and rotate away good copies

- **Lens:** Deployment, operations & recovery
- **Confidence:** Confirmed
- **Evidence:** `deploy/backup.sh:6` pipes `pg_dump` into `gzip` with no
  `set -euo pipefail`, so a failed dump still writes a small valid gzip and
  the script exits 0. `deploy/backup.sh:7` deletes copies older than 7 days
  unconditionally. Backups live only on the VM's own disk.
- **Failure scenario:** The DB password rotates → `pg_dump` fails every night
  → cron sees success → after a week every real backup is deleted → the VM
  disk dies and there is nothing to restore.
- **Recommendation:** `set -euo pipefail`; dump to a temp path, verify with
  `pg_restore -l` (custom format) or `gunzip -t` plus a size check, publish
  atomically, rotate only after success, copy off the host, and alert when no
  good backup is newer than 26 hours.
- **Effort:** S
- **Verify:** Run the script with a wrong password: it exits non-zero, alerts,
  and deletes nothing.
- **Status:** Open

### SDA-009 · Critical · A failed query leaks its pooled connection

- **Lens:** Reliability & failure handling
- **Confidence:** Confirmed
- **Evidence:** `app/db.py:25-31` and `app/db.py:34-39` call `putconn` only on
  the success path; any exception in `execute` skips it. The pool is a
  `ThreadedConnectionPool`, which raises `PoolError` once all connections are
  out (`app/db.py:11`).
- **Failure scenario:** A burst of statement errors (a deadlock, a constraint
  violation, a dropped connection) → each leaks one connection → after 10 the
  worker raises on every request until it is restarted. Nothing restarts it
  automatically.
- **Recommendation:** Return connections in `finally` (or reuse the
  `transaction()` context manager for both helpers).
- **Effort:** S
- **Verify:** A test that makes `cur.execute` raise 20 times, then asserts a
  normal query still succeeds.
- **Status:** Open

### SDA-010 · Normal · Health check proves nothing; LB and deploy rely on it

- **Lens:** Deployment, operations & recovery
- **Confidence:** Confirmed
- **Evidence:** `app/routes/health.py:6-8` returns `ok` without touching
  anything; `deploy/deploy.sh:9` treats it as proof the deploy worked; the
  README says the load balancer health-checks it.
- **Failure scenario:** A VM loses its DB connection (or a worker's pool is
  exhausted, SDA-009) → `/health` still answers 200 → the load balancer keeps
  sending half the traffic to an instance that fails every order.
- **Recommendation:** Add `/ready` that runs `SELECT 1` on its own short-lived
  connection with a 1 s timeout; point the load balancer and the deploy check
  at it.
- **Effort:** S
- **Verify:** Stop Postgres locally: `/ready` returns 503 within 2 s.
- **Status:** Open

### SDA-011 · Normal · Order history is unbounded

- **Lens:** Scalability, performance & capacity
- **Confidence:** Confirmed
- **Evidence:** `app/routes/orders.py:52-60` returns every order for the user
  with no `LIMIT`.
- **Failure scenario:** A reseller account with thousands of orders loads its
  history → one request serialises every row → slow responses and memory
  spikes on a worker that 1/8 of all traffic shares.
- **Recommendation:** Keyset pagination (`WHERE id < %s ORDER BY id DESC LIMIT 50`)
  with a hard maximum page size.
- **Effort:** S
- **Verify:** The endpoint returns at most the page size and a cursor.
- **Status:** Open

### SDA-012 · Normal · Unpaid orders hold stock forever

- **Lens:** Data model & integrity
- **Confidence:** Confirmed
- **Evidence:** `app/routes/orders.py:35-45` decrements stock when the order is
  created as `pending_payment`; nothing ever expires those orders, and if
  `paygate.create_checkout` fails at `app/routes/orders.py:48` the decrement
  is already committed.
- **Failure scenario:** During the sale, shoppers who abandon checkout lock up
  stock → popular items show "out of stock" while unsold.
- **Recommendation:** Expire `pending_payment` orders after 30 minutes and
  return their stock in the same transaction; create the checkout before
  committing, or compensate on failure.
- **Effort:** M
- **Verify:** An expired pending order returns its quantity exactly once.
- **Status:** Open

### SDA-013 · Normal · DB connections have no connect or statement deadline

- **Lens:** Reliability & failure handling
- **Confidence:** Likely — depends on the `DATABASE_URL` in production, which isn't in the repo
- **Evidence:** `app/db.py:11` builds the pool from `DATABASE_URL` with no
  `connect_timeout`, keepalives or `statement_timeout`; `.env.example:1`
  shows none either.
- **Failure scenario:** A network blip leaves a pooled connection half-open →
  the next query on it blocks with no deadline → the worker hangs until
  gunicorn kills it, and repeats on the next leaked socket.
- **Recommendation:** Add `connect_timeout=5 keepalives=1 keepalives_idle=30
  keepalives_interval=10 keepalives_count=3` and
  `options='-c statement_timeout=10000'` to the DSN.
- **Effort:** S
- **Verify:** `SHOW statement_timeout` from the app's connection returns 10s.
- **Status:** Open

### SDA-014 · Normal · Migrations are applied by hand, outside the deploy

- **Lens:** Deployment, operations & recovery
- **Confidence:** Confirmed
- **Evidence:** `deploy/deploy.sh:5-9` pulls, installs and restarts with no
  migration step; the README says migrations are applied by hand with `psql`.
- **Failure scenario:** A release that needs `003_*.sql` is deployed to both
  VMs before anyone runs it → every request touching the new column fails
  until someone notices.
- **Recommendation:** Track applied migrations in a table and run pending ones
  from `deploy.sh` before the restart (once, from one VM), failing the deploy
  if they fail.
- **Effort:** M
- **Verify:** Deploying with a pending migration applies it once and records it.
- **Status:** Open

### SDA-015 · Good Practice · Request ID is generated but never logged

- **Lens:** Observability
- **Confidence:** Confirmed
- **Evidence:** `app/__init__.py:23-25` stores `g.request_id`, but the log
  format at `app/__init__.py:17-20` doesn't include it and no response header
  returns it.
- **Why it matters:** Tracing one customer's failed checkout across the order,
  webhook and email logs means matching timestamps by hand.
- **Recommendation:** Add a logging filter that injects `g.request_id`, and
  echo it in an `X-Request-ID` response header.
- **Effort:** S
- **Status:** Open

### SDA-016 · Good Practice · SMTP host silently falls back to localhost

- **Lens:** Deployment, operations & recovery
- **Confidence:** Confirmed
- **Evidence:** `app/config.py:14` defaults `SMTP_HOST` to `localhost`, while
  the real secrets fail fast with `os.environ[...]` (`app/config.py:5-9`).
- **Why it matters:** A missing variable in production boots fine and fails
  only when the first email is sent, which with SDA-003 refunds the order.
- **Recommendation:** Read `SMTP_HOST` with `os.environ[...]` like the other
  required settings.
- **Effort:** S
- **Status:** Open

## Strengths

- Webhook authenticity is checked with HMAC-SHA256 and a constant-time compare
  (`app/routes/payments.py:16-19`).
- Order reads are scoped to the session user and return 404 otherwise
  (`app/routes/orders.py:63-74`).
- All SQL uses bound parameters; session tokens are stored hashed with an
  expiry (`app/auth.py:14-19`).
- Required secrets fail at startup (`app/config.py:5-9`); PayGate calls have
  timeouts (`app/services/paygate.py:15`).
- `products.price` is `NUMERIC(10, 2)` and quantity is bounded 1–20.

## Not assessed and open questions

- Infrastructure outside the repo: load balancer settings, VM sizing, the
  production `DATABASE_URL`, cron and log shipping.
- The carrier API's own idempotency behaviour: does a repeated `reference`
  return the same label, or buy a new one? This changes the size of SDA-001.
- Whether PayGate can send events out of order (for example a refund before
  `payment.succeeded`).
- Load testing was out of scope; capacity figures are from configuration only.

## Remediation plan

1. **Before the sale:** SDA-001, SDA-002, SDA-003, SDA-005, SDA-009 (all S,
   all in the checkout and webhook path), then SDA-004 and SDA-008.
2. **Next:** SDA-006 and SDA-007 together (money representation and the
   wallet ledger touch the same code), then SDA-010, SDA-012, SDA-013, SDA-014.
3. **When touching the area:** SDA-011, SDA-015, SDA-016.
