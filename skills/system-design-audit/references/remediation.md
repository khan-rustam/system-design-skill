# Fix mode: applying findings cleanly

The audit found the failure modes. Fix mode removes them **one at a time,
with proof**, without collateral changes. A fix that can't be shown to work,
or that drags in unrelated edits, is one the owner can't review or trust.

## Contents

1. [Before touching code](#1-before-touching-code)
2. [The loop for each finding](#2-the-loop-for-each-finding)
3. [Stop and ask](#3-stop-and-ask)
4. [Recording the result](#4-recording-the-result)
5. [Fix recipes](#5-fix-recipes)

## 1. Before touching code

- **Have a report.** Work from a saved audit report, or audit the affected
  area first. A fix without a written failure scenario has no definition of
  done.
- **Agree the batch.** List the IDs you will fix and in what order, and get
  the user's go-ahead. Default order: Critical by blast radius, then any
  Normal findings in the same files, so one area is touched once.
- **Check the ground.** Confirm the branch and that the working tree is
  clean, so your changes are separable from anyone else's. Run the existing
  test suite once and note any pre-existing failures. You need to know which
  failures are yours.
- **Re-read the evidence.** Code moves between an audit and a fix. If the
  finding no longer applies, mark it Stale and say why. Don't fix it
  anyway.

## 2. The loop for each finding

1. **Reproduce.** Write a test, or a script or query when a unit test can't
   reach it, that encodes the failure scenario: two concurrent checkouts, the
   same webhook delivered twice, an upstream that never answers, a job run
   twice. **Run it and watch it fail** for the reason the finding gives. A test
   that passes before the fix proves nothing about the fix.
2. **Make the smallest correct change.** Use the recommendation from the
   report unless the code shows a better minimal fix, and if so, say so.
   Match the surrounding style. No drive-by refactors, renames or
   reformatting. If you notice another defect, add it to the report; don't
   fix it silently.
3. **Watch the reproduction pass**, then run the full suite. A new failure
   elsewhere means the fix changed behaviour someone depends on. Understand
   it before going further.
4. **Check what the fix can't show by itself.** A schema change needs a
   migration. A new config value needs to be in the env example and
   validated at startup. A new failure mode (a raised error where there
   used to be a hang) needs handling by its callers.
5. **Keep it one change.** One finding is one commit, if the user commits,
   referencing the finding ID (`fix(SDA-003): …`). Findings that share a
   single root cause may share one change. Say so.

Never weaken an existing check, test, constraint or permission to make a fix
pass. If a test encodes the old wrong behaviour, show the user and update it
deliberately, with the finding ID as the reason.

## 3. Stop and ask

Pause and get explicit approval before any of these. They are hard to
reverse or reach beyond the code:

- **Data migrations against real data**: backfills, deduplication of
  existing rows before adding a unique constraint, type changes on large
  tables. Write the migration and a dry-run query that counts affected rows,
  and let the owner run it.
- **Deploys, pushes, infrastructure or scheduler changes**: CI, process
  managers, cron/timers, proxies, DNS, cloud resources.
- **Contract changes** that other services, repos or clients consume.
- **Product decisions** hidden in the fix: refund policy, what a user sees
  when stock runs out, whether to fail open or closed.
- **Secret rotation** or anything touching credentials.

For each one, prepare everything (code, migration, runbook steps, rollback),
then hand it over with a one-paragraph explanation.

## 4. Recording the result

- Update the finding's **Status** in the report:
  `Fixed — tests/test_checkout.py::test_concurrent_last_unit (2026-10-08)`.
  If it is only partly fixed, say what remains. If deploying is the owner's
  step, write `Fixed (not deployed)`.
- Rules that future changes must keep go into the project's agent or
  contributor docs (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, an ADR):
  one line for the rule, one for the reason. For example: "Counters in
  `reconcile.sql` use GREATEST, never `+`. The job re-runs, and `+` inflates
  the totals (SDA-004)." This stops the next change from undoing the fix.
- **Deployed is not the same as fixed in production.** When the owner
  deploys, verify the running process picked it up: its start time, or a
  version endpoint reporting the new commit. A 200 from `/health` can come
  from the old process.

## 5. Fix recipes

Minimal, stack-neutral patterns for the most common findings. Adapt them to
the codebase's own idioms. Each one removes a failure mode without adding
infrastructure.

### Idempotent webhook / event handler

```sql
CREATE TABLE processed_events (
  provider   text NOT NULL,
  event_id   text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, event_id)
);
```
```python
with db.transaction():
    inserted = db.execute(
        "INSERT INTO processed_events (provider, event_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING RETURNING event_id", (provider, event["id"]))
    if not inserted:
        return 200          # duplicate delivery: already applied
    apply_effect(event)     # same transaction, so both commit or neither does
```
Verify the signature **before** this point, with a constant-time compare.
Acknowledge fast. If the effect is slow, enqueue it inside the same
transaction (outbox) and return 200.

**Never call an outside service after the marker commits.** If that call
fails, the handler returns 5xx, the provider retries, the retry takes the
duplicate branch, and the effect is lost for good: a paid order that never
ships. Either enqueue the call in the marker's transaction (an outbox row or
job, retried until it succeeds), or don't stop at the duplicate. In that
case, guard each outside effect by its own state, and let a retry fall
through to whatever is still missing. For example, buy a label only while
`label_url IS NULL`, under a per-order lock. The test: fail the outside call
once, redeliver, and assert the effect happens exactly once.

### Atomic conditional update (stock, quota, single-use tokens)

```sql
UPDATE products SET stock = stock - $1
WHERE id = $2 AND stock >= $1
RETURNING stock;            -- zero rows => not enough stock
```
Add `CHECK (stock >= 0)` as a backstop. For balances, prefer an append-only
ledger (`INSERT` entries; the balance is their sum, or is maintained in the
same transaction) over overwriting a balance column.

### Lost update on read-modify-write

Do the arithmetic in SQL (`SET balance = balance + $1`) inside the
transaction that also records *why* (a ledger row with a unique reference).
Alternatively, lock with `SELECT … FOR UPDATE` before computing, or use
optimistic versioning: `UPDATE … SET …, version = version + 1 WHERE id = $1
AND version = $2`, and retry on zero rows.

### Money

Store integer minor units (`amount_cents bigint`) or exact `numeric(p, s)`.
Never `float`/`REAL`/`DOUBLE`. Convert at the edges only. Pin rounding rules
for tax in one function with tests.

### Deadlines on every outbound call

- Python `requests`: `requests.post(url, json=b, timeout=(3.05, 10))`.
  For many call sites, use a session subclass or wrapper that sets a
  default.
- `httpx`: `httpx.Client(timeout=httpx.Timeout(10, connect=3))`.
- SMTP: `smtplib.SMTP(host, port, timeout=15)`.
- Node `fetch`: `fetch(url, { signal: AbortSignal.timeout(10_000) })`. For
  axios, `axios.create({ timeout: 10_000 })`.
- PostgreSQL (libpq conninfo): `connect_timeout=10 keepalives=1
  keepalives_idle=30 keepalives_interval=10 keepalives_count=3
  tcp_user_timeout=30000`, plus `options='-c statement_timeout=30000'`, and a
  pool checkout timeout that raises.

Choose values from the caller's own deadline. A request handler with a 30 s
proxy timeout can't wait 60 s for an upstream.

### Retries

Retry at one layer only, on retryable errors only (timeouts, 429, 503),
with exponential backoff, full jitter and a cap. Only retry operations that
are idempotent, or carry an idempotency key to the provider.

```python
delay = min(cap, base * 2 ** attempt)
time.sleep(random.uniform(0, delay))
```

Honour `Retry-After`. Never let adaptive backoff lower a configured minimum
pace: `delay = max(configured_floor, delay)`.

### Separate side-effect state from core state

Add delivery fields (`delivery_status`, `delivery_attempts`,
`next_delivery_at`) next to the work's own status. A delivery failure
updates only those fields, and a sweeper retries them. Refunds and
"failed" statuses come only from the core work failing.

### Dual write → transactional outbox

```sql
CREATE TABLE outbox (
  id bigserial PRIMARY KEY, topic text NOT NULL, payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz);
```
Insert the outbox row in the same transaction as the business write. A
relay loop claims rows with `FOR UPDATE SKIP LOCKED`, publishes them, and
sets `published_at`. Consumers deduplicate on the outbox ID.

### Schedulers and overlapping jobs

Run schedules in one dedicated process (cron, a systemd timer, a k8s
CronJob, or a single-replica worker), not in web workers. Also guard each
run:

```sql
SELECT pg_try_advisory_lock(hashtext('nightly-reconcile'));  -- false => another run is active; exit
```
And make the job idempotent: recompute and overwrite, or use `GREATEST` /
`COALESCE`, never `+=` a recount.

### Leases and locks

Every lease has a TTL. Renew it on every path that keeps using the resource
(put the renewal in the loop, not behind a condition). Release it in
`finally`. Swap with compare-and-swap (acquire the new one, then release
the old one only if you still hold it). Record a fencing token if the
resource can tell holders apart.

### Queues and workers

Set max attempts with backoff. Send exhausted messages to a dead-letter
store that someone is alerted about, never `removeOnFail` into the void. Make
handlers idempotent under redelivery. Recover stuck "in progress" claims
with a visibility timeout or sweeper. Handle `SIGTERM`: stop claiming, finish
or release in-flight work, then exit.

### Per-process state in a multi-instance deployment

Move counters, quotas, sessions and rate limits that must be global into the
shared store you already run (the DB, or Redis if present), using atomic
operations (`INCR` with expiry; `UPDATE … RETURNING`). Keep in-process caches
only for data where staleness is acceptable, with a TTL.

### Bounded inputs and results

Enforce maxima at the boundary (schema `max`, `le=`), and again in any
resolver that has a "no limit given" path. Paginate list endpoints with a
cursor and a hard maximum page size.

### Health checks and deploy verification

Use a cheap **liveness** check (the process responds) and a **readiness**
check (it can reach what it needs, with a short timeout, on its own
connection, not the shared pool). Deploys should verify the new version is
the one running: a `/version` endpoint that reports the commit, or the
process start time.

### Backups

```bash
set -euo pipefail
tmp="$DEST/.db-$(date +%F).part"
pg_dump -Fd -j 4 -f "$tmp" "$DB"
pg_restore -l "$tmp" > /dev/null          # proves the archive is readable
mv "$tmp" "$DEST/db-$(date +%F)"
find "$DEST" -maxdepth 1 -name 'db-*' -mtime +"$KEEP_DAYS" -exec rm -rf {} +   # only reached on success
```
Alert when it fails **and** when it hasn't succeeded in 26 hours. Cover
every data store, and rehearse a restore.

### Migrations without downtime

Expand → migrate → contract. Add the new column or table (nullable, or with
a default) and deploy code that writes both. Backfill in batches. Switch
reads. Only drop the old shape in a later release. For a unique constraint
on existing data: find and resolve duplicates first (and show the owner the
count), then `CREATE UNIQUE INDEX CONCURRENTLY`, then attach it.

### Config that fails fast

Read and validate all required config at startup. Exit with a clear message
naming the missing key. No silent fallbacks to `localhost` or demo values
for anything that differs between environments.
