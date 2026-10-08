# Failure patterns from real incidents

Each pattern here caused a real production incident. Most of them look like
correct code at a glance, which is why they shipped. Use this list to
recognise a pattern in unfamiliar code. The lens checklists say what to
look at; these say what it looks like when it's wrong.

Format: **What it looks like** · **Why it breaks** · **How to spot it** ·
**Fix**.

## Contents

Data & state: [1](#1-the-upsert-that-adds-a-recount) ·
[2](#2-the-non-fatal-persist-that-never-succeeds) ·
[3](#3-check-then-act-on-a-scarce-quantity) ·
[4](#4-the-side-effect-that-rewrites-the-core-state) ·
[5](#5-the-dual-write)
Reliability: [6](#6-the-call-with-no-deadline) ·
[7](#7-fixing-pool-exhaustion-with-a-bigger-pool) ·
[8](#8-the-misclassified-error-that-triggers-the-wrong-recovery) ·
[9](#9-the-backoff-that-speeds-up) ·
[10](#10-the-retry-multiplier)
Concurrency: [11](#11-the-lease-that-isnt-renewed-on-one-path) ·
[12](#12-release-then-acquire) ·
[13](#13-the-scheduler-inside-every-web-worker) ·
[14](#14-concurrent-whole-table-maintenance) ·
[15](#15-one-credential-many-holders) ·
[16](#16-the-dev-worker-that-mails-production) ·
[27](#27-the-foreign-key-lock-that-defeats-lock-ordering)
Contracts & capacity: [17](#17-the-implicit-positional-contract) ·
[18](#18-the-maximum-that-is-never-enforced) ·
[19](#19-the-scarce-resource-taken-before-the-cache-check) ·
[20](#20-capacity-measured-against-the-wrong-constraint)
Operations: [21](#21-the-backup-that-succeeds-at-producing-nothing) ·
[22](#22-the-deploy-that-isnt-running) ·
[23](#23-the-deploy-script-that-rewrites-itself) ·
[24](#24-the-hidden-cross-service-database) ·
[25](#25-secrets-on-the-command-line) ·
[26](#26-the-audit-of-the-wrong-revision)

---

### 1. The upsert that adds a recount

- **What it looks like:** `INSERT … SELECT key, count(*) … ON CONFLICT (key) DO UPDATE SET total = t.total + EXCLUDED.total`, run after every batch.
- **Why it breaks:** `EXCLUDED.total` is a fresh count over the **whole** source table, not a delta. Each run adds the full count again. One counter grew past three times the size of the table it counted.
- **How to spot it:** `x = x + EXCLUDED.x`, or `SET n = n + sub.total`, in anything that can run more than once. Check whether the right-hand side is a delta or a recount. A cheap invariant catches it: no derived count may exceed the row count of its source.
- **Fix:** overwrite with the recount (`SET total = EXCLUDED.total`), or use `GREATEST(t.total, EXCLUDED.total)` for monotonic counters, `COALESCE` for scalars, set-union for arrays. Make the job idempotent first, then schedule it.

### 2. The non-fatal persist that never succeeds

- **What it looks like:** `try: save(record) except Exception as e: log.warning("persist failed (non-fatal): %s", e)`.
- **Why it breaks:** a format change made the value longer than its `varchar` column, so the write failed **every time** for weeks. "Non-fatal" meant nobody looked, and a feature flag that would one day require the record was a time bomb.
- **How to spot it:** catches labelled non-fatal, best-effort or ignore around a write that something else later reads. Compare column sizes with the current output format.
- **Fix:** count the failure (metric or alert), size columns for the real format (`text` when there's no reason to cap), and add a test that writes a real, current-format value.

### 3. Check-then-act on a scarce quantity

- **What it looks like:** `if product.stock >= qty: product.stock -= qty; save()`, or `SELECT balance` → compute → `UPDATE … SET balance = :new`.
- **Why it breaks:** two concurrent requests both pass the check. Stock goes negative, balances lose an update. Tests never run two requests at once.
- **How to spot it:** a read of a quantity and a later write of a value computed in application code, with no row lock, version check or conditional update between them.
- **Fix:** one atomic conditional statement, `UPDATE products SET stock = stock - :q WHERE id = :id AND stock >= :q RETURNING stock`, and treat zero rows as sold out. Back it with `CHECK (stock >= 0)`. For balances, use an append-only ledger or `SELECT … FOR UPDATE` inside the transaction.

### 4. The side effect that rewrites the core state

- **What it looks like:** the job did the paid work, then the email or notification failed, and the error handler calls the same `mark_failed()` used for real processing failures.
- **Why it breaks:** a mail-provider throttle turned completed, paid work into "failed", which also triggered the automatic refund. The work was done, the result binned and the money returned.
- **How to spot it:** follow the error path of every side effect that runs *after* the core work. Does it share a status or a compensating action with core failures?
- **Fix:** give delivery its own state (`delivery_status`, attempts, next retry) separate from the work state. Retry delivery independently and alert on a stuck delivery, never on the work.

### 5. The dual write

- **What it looks like:** `db.commit(); queue.publish(event)`, or `queue.add(job); db.insert(row)`.
- **Why it breaks:** a crash or error between the two leaves them inconsistent: a row with no job (stuck forever) or a job with no row (the worker fails or acts on missing data).
- **How to spot it:** two writes to different systems in one function, with no outbox and no reconciler.
- **Fix:** a transactional outbox (write the event row in the same DB transaction; a relay publishes it) or a sweeper that re-enqueues rows stuck in a pending state. At minimum, order the writes so the failure mode is "retryable", not "lost".

### 6. The call with no deadline

- **What it looks like:** `requests.post(url, json=body)`, `smtplib.SMTP(host)`, a DB conninfo with no `connect_timeout` or keepalives.
- **Why it breaks:** a pooled DB connection died silently behind NAT. The pool's pre-checkout `SELECT 1` then blocked forever on the half-open socket, so slots leaked one by one until every request raised a pool timeout. This happened on an **idle** app, and `/health` hung too, because it shared the pool.
- **How to spot it:** every client constructor and call site. HTTP, SMTP, DB connect, DB statement, TCP keepalive.
- **Fix:** explicit connect and read timeouts everywhere. For DB connections: `connect_timeout`, keepalives, `tcp_user_timeout`, a statement timeout, and a pool checkout timeout that raises.

### 7. Fixing pool exhaustion with a bigger pool

- **What it looks like:** a PR that raises `max_size` from 8 to 32 after pool timeouts.
- **Why it breaks:** the pool was exhausted by leaked or hung connections, not by load. A bigger pool only delays the symptom, and processes × instances × the new size can exhaust a database shared with other services.
- **How to spot it:** pool timeouts at low traffic; connections idle in the DB for hours; the size was documented as deliberate.
- **Fix:** find the leak or hang (pattern 6, or a connection not returned on an error path). Keep the documented size.

### 8. The misclassified error that triggers the wrong recovery

- **What it looks like:** a vendor answers "not entitled to this endpoint" and "session expired" with the same status code, and the client treats both as an expired session: re-login, fail over to the next credential, cool the failed one down.
- **Why it breaks:** one request to an endpoint no credential could use cooled down **every** healthy credential in turn, until the pool was empty and all traffic failed. The outage was self-inflicted.
- **How to spot it:** recovery code (failover, re-login, cooldown, refund, circuit-open) triggered by a broad error class. Ask what else produces that error.
- **Fix:** classify before recovering. Probe a known-good cheap endpoint: if the session is live, the error is permanent for this request, so raise it and don't fail over. Make the probe fail closed.

### 9. The backoff that speeds up

- **What it looks like:** `self.delay = next_backoff()` when throttled, and `self.delay = 0` after a clean response.
- **Why it breaks:** a caller deliberately running at 15 s per request dropped to 2 s the moment the upstream first throttled it, then to 0. It sped up into the block it was backing off from.
- **How to spot it:** adaptive delays that *assign* instead of taking a maximum with the configured floor.
- **Fix:** `delay = max(configured_floor, adaptive_delay)`. The caller's pace is a floor that backoff may raise but never lower.

### 10. The retry multiplier

- **What it looks like:** the HTTP client retries 3×, the job retries 5×, the queue redelivers 3×.
- **Why it breaks:** 45 attempts per message against a rate-limited upstream during its outage. That is a retry storm that extends the outage, and without idempotency each attempt may repeat a side effect.
- **How to spot it:** retry settings at every layer of one call path. Multiply them.
- **Fix:** retry at one layer, usually the outermost one that owns idempotency, with exponential backoff, jitter and a cap.

### 11. The lease that isn't renewed on one path

- **What it looks like:** `def heartbeat(self): if not self.request_id: return; renew_lease()`, called by a code path where `request_id` is always empty.
- **Why it breaks:** the lease expired while the worker kept using the resource. The broker handed it to a second worker. The vendor allows one session per credential, so the two logged each other out, and hundreds of jobs failed in a retry storm.
- **How to spot it:** every early return in heartbeat or renew functions. List every caller that holds a lease and check that each one renews.
- **Fix:** renew unconditionally while holding the lease. Renew before each unit of work, and cool down before releasing a credential that failed.

### 12. Release-then-acquire

- **What it looks like:** `swap(): release(current); current = acquire()`, called from several threads.
- **Why it breaks:** two threads failing over at once each released and re-acquired, and ended up holding each other's resource.
- **How to spot it:** any swap, rotate or replace on a shared resource that isn't a single atomic operation.
- **Fix:** compare-and-swap. Acquire the new one first, then release the old one only if it is still yours.

### 13. The scheduler inside every web worker

- **What it looks like:** `scheduler = BackgroundScheduler(); scheduler.add_job(nightly, "cron", hour=2); scheduler.start()` in `create_app()`, served by gunicorn with 4 workers on 2 instances.
- **Why it breaks:** the nightly job runs 8 times. Combined with pattern 1 or a non-idempotent email, that is 8× corruption or 8 emails.
- **How to spot it:** a scheduler started in app or module init, combined with the process count from the deploy config.
- **Fix:** run schedules in one dedicated process (cron, a systemd timer, a k8s CronJob, or a single-replica worker), or guard each run with a DB advisory lock, **and** make the job idempotent.

### 14. Concurrent whole-table maintenance

- **What it looks like:** each worker runs "merge duplicates / backfill / recompute" over the whole table after every unit of work.
- **Why it breaks:** three workers produced hundreds of deadlocks a night, and a connection leak on the deadlock error path crashed the workers more than a hundred times.
- **How to spot it:** set-based maintenance SQL called from worker loops.
- **Fix:** serialize it with a session advisory **try**-lock on a dedicated connection. A worker that finds it running skips its own run, because the next pass covers its rows. Every other bulk writer to those tables must take the same lock.

### 15. One credential, many holders

- **What it looks like:** several processes or services read the same vendor credentials from config and log in independently.
- **Why it breaks:** for vendors with one session per login, each login evicts the other. Failures look random and show up as "the vendor is flaky".
- **How to spot it:** shared credential files or tables read by more than one process, with no lease.
- **Fix:** one broker that leases credentials with a TTL, implemented in one place (often a DB function, when several codebases call it). Reserve a minimum per consumer so one side can't starve the other.

### 16. The dev worker that mails production

- **What it looks like:** a background sweep ("send undelivered reports", "retry failed charges") with no environment predicate in its query.
- **Why it breaks:** a developer running the worker locally against a production-like database would mail real customers or burn their attempt counters.
- **How to spot it:** background jobs with external side effects. Check their claim query for an environment or tenant filter, and check the default of their enable flag.
- **Fix:** scope claims by environment, and default side-effecting sweeps to off outside production.

### 17. The implicit positional contract

- **What it looks like:** consumers read `workbook.sheetnames[0]`, `sheet1.xml`, `row[3]`, or "the first element".
- **Why it breaks:** inserting a new first sheet or column silently broke three consumers in two repos. One of them produced a customer download that was quietly locked.
- **How to spot it:** index-based access to data produced by another component.
- **Fix:** write the contract down where the producer lives ("order is load-bearing, names are free"). Pin it with a test, and prefer lookup by name when consumers can change.

### 18. The maximum that is never enforced

- **What it looks like:** `ABSOLUTE_MAX = 5000` that appears only inside `min(explicit_limit, ABSOLUTE_MAX)`, while the `explicit_limit is None` path means unlimited.
- **Why it breaks:** any caller without an explicit limit gets unlimited work. The constant reads like a guard but isn't one.
- **How to spot it:** grep each `MAX_*` constant and check that every path is bounded by it, especially the `None` / default path. Check the input schema for an upper bound.
- **Fix:** enforce the bound at the boundary (schema `max`) and as a floor in the resolver: `min(limit or ABSOLUTE_MAX, ABSOLUTE_MAX)`.

### 19. The scarce resource taken before the cache check

- **What it looks like:** `client = pool.acquire_account(); if cached := cache.get(key): return cached`.
- **Why it breaks:** cache hits still consume the scarcest resource, so capacity is capped by credentials even when the answer is free.
- **How to spot it:** read the order of operations on hot paths that use leases, quotas or paid calls.
- **Fix:** check the cache first, and acquire only on a miss.

### 20. Capacity measured against the wrong constraint

- **What it looks like:** "load 0.2, 60% RAM free, so we can take 10× the users."
- **Why it breaks:** the real ceiling was a fixed set of upstream credentials, each good for a limited number of requests before throttling. CPU was irrelevant.
- **How to spot it:** list every quota, credential pool, rate limit, connection cap and single-threaded worker on the core path. Capacity is the minimum across them.
- **Fix:** state capacity against the binding constraint, and spend first on what decouples demand from it (caching, batching, more credentials).

### 21. The backup that succeeds at producing nothing

- **What it looks like:** `pg_dump -U postgres db > /backups/db.sql` in cron. Later: a backup list that misses a newly added database, or a dump command that depends on a container which was since removed.
- **Why it breaks:** the role couldn't log in, the shell created the file anyway, and cron reported success. That gave seven weeks of 0-byte backups. A second time, a new 150 GB database was never added to the list.
- **How to spot it:** missing `set -euo pipefail`, no verification step, rotation that runs unconditionally, and a hard-coded database list.
- **Fix:** dump to a temp path, verify (`pg_restore -l`, or a test restore), then rename atomically. Exit non-zero loudly on failure. Rotate only after a verified success. Derive the list of databases, or alert when a database is missing from it.

### 22. The deploy that isn't running

- **What it looks like:** CI says deployed, the right commit is on disk, `/health` answers 200.
- **Why it breaks:** the long-lived process still ran the old code. Either the pull didn't restart it, or `pm2 restart … >/dev/null 2>&1` failed silently, and the old process kept answering health checks for days.
- **How to spot it:** deploy scripts that don't restart, that hide restart output, or that "verify" with an endpoint the old process also answers.
- **Fix:** verify by process start time or by a version endpoint that reports the running commit. Never discard restart output.

### 23. The deploy script that rewrites itself

- **What it looks like:** `deploy.sh` runs `git fetch && git reset --hard origin/main`, then continues with more steps.
- **Why it breaks:** git replaces the file while bash is still reading it. On the push that **adds** a step, the old body runs and the new step silently doesn't.
- **How to spot it:** a deploy script that updates the working tree it lives in.
- **Fix:** re-exec the script after updating (`exec "$0" "$@"`, guarded by an env flag), or keep the update step in a separate wrapper.

### 24. The hidden cross-service database

- **What it looks like:** service A has a second DB config pointing at service B's database (for a shared table, a lock broker or a report table).
- **Why it breaks:** moving B's database without repointing A split a shared broker in two. Each half handed out the same credentials. Separately, a lazily built pool for the second DB failed silently until the first request.
- **How to spot it:** count database settings per service. Any second one is a cross-service dependency.
- **Fix:** document it in both repos. Treat a migration as a coordinated change list. Connect eagerly at startup so misconfiguration fails at deploy time.

### 25. Secrets on the command line

- **What it looks like:** `sudo psql "postgres://user:PASS@…"`, `mysql -pPASS`, `PGPASSWORD=… pg_dump`, or a rotation script that takes the new password as an argument.
- **Why it breaks:** process lists and sudo's audit log record full command lines. Passwords leaked into a world-readable log and had to be rotated.
- **How to spot it:** scripts and runbooks that pass secrets as arguments or inline env assignments under sudo.
- **Fix:** read secrets from a file or stdin. Use `.pgpass` or service files.

### 26. The audit of the wrong revision

- **What it looks like:** the local checkout sits on an old feature branch while production deploys `main`.
- **Why it breaks:** an analysis "found" a bug that `main` had already fixed, and missed defects that exist only on `main`.
- **How to spot it:** compare the branch and HEAD with the upstream default branch before reading code.
- **Fix:** audit the deployed revision, and record it in the report header.

### 27. The foreign-key lock that defeats lock ordering

- **What it looks like:** a transaction inserts a row that references two parent rows (`INSERT INTO transfers (from_account, to_account, …)`), then locks those parents "in a fixed order so nothing can deadlock": `SELECT … FROM accounts WHERE id = ANY(…) ORDER BY id FOR UPDATE`.
- **Why it breaks:** the insert's foreign-key check has already taken a shared lock on each parent row (`FOR KEY SHARE` in PostgreSQL, a shared record lock in InnoDB). Two transactions that share an account both hold that shared lock, and then each one's `FOR UPDATE` waits for the other's. The database detects the deadlock and aborts one of them, whatever the lock order. An account that takes part in most transfers (a platform or clearing account) makes this routine under load.
- **How to spot it:** an `INSERT`, or an `UPDATE` of a foreign-key column, followed in the same transaction by `FOR UPDATE` on the rows it references. A comment about lock ordering is the tell: ordering covers only the locks taken in that one statement.
- **Fix:** in PostgreSQL, lock with `FOR NO KEY UPDATE`. It doesn't conflict with `FOR KEY SHARE`, and it is the lock a plain `UPDATE` of non-key columns takes anyway. Elsewhere, lock the parents before inserting the child. Verify with a test that runs two concurrent transactions sharing a parent row and asserts that both commit.
