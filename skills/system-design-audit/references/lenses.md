# The ten lenses

Work through each lens for the system you modelled. Every lens has four
parts:

- **Ask**: the questions to answer from the code.
- **Look in**: where the answer usually lives.
- **Usually real**: patterns that are defects far more often than not.
- **Usually fine**: patterns that look alarming but often aren't. Check these
  before you report them.

Severity hints are starting points. The final rank always comes from the
failure scenario (`severity.md`).

## Contents

1. [Architecture & boundaries](#1-architecture--boundaries)
2. [Data model & integrity](#2-data-model--integrity)
3. [Reliability & failure handling](#3-reliability--failure-handling)
4. [Concurrency, jobs & async work](#4-concurrency-jobs--async-work)
5. [Scalability, performance & capacity](#5-scalability-performance--capacity)
6. [API & integration contracts](#6-api--integration-contracts)
7. [Security architecture](#7-security-architecture)
8. [Observability](#8-observability)
9. [Deployment, operations & recovery](#9-deployment-operations--recovery)
10. [Maintainability & evolvability](#10-maintainability--evolvability)

---

## 1. Architecture & boundaries

**Ask**
- Does every important entity (order, balance, user, subscription) have exactly
  one owner that writes it? Who else writes it, and how do they stay in step?
- Do services share a database, or read each other's tables? Is that coupling
  written down anywhere, and does it show up in either service's code
  review?
- Is a business rule implemented twice, in two services, two languages or
  two repos? Rules that must stay identical drift apart when they deploy on
  different cadences.
- Does the user-facing request path do slow or fragile work inline:
  scraping, report generation, bulk email, third-party fan-out?
- Can one component's outage take down unrelated features, for example
  through a synchronous call chain or a shared pool?

**Look in**: service entry points, DB connection configs (count them: a
second `DATABASE_URL`-like setting often points at *another* service's DB),
HTTP clients calling sibling services, shared libraries, deploy manifests.

**Usually real**
- An allocation rule ("each account is used by one worker", "stock never
  goes below zero") enforced in application code in two places that deploy
  separately. Put it in one place: a DB function or constraint, or a single
  service.
- One service's correctness depends on another service's schema, with no
  contract test and no note in either repo.
- Request handlers that call three or more remote systems in sequence, with
  no timeout budget.

**Usually fine**
- A "monolith". Size alone is not a defect; mixed ownership of state is.
- A shared database that is read-only for every consumer except its owner,
  through a restricted role. Check the grants before reporting it.

**Severity hints**: duplicated exclusive-allocation rules → Critical when the
duplicates can disagree. Undocumented cross-service DB reads → Normal.
Layering or style concerns → Good Practice.

---

## 2. Data model & integrity

**Ask**
- Which invariants are enforced by the **database** (NOT NULL, UNIQUE, FK,
  CHECK, exclusion), and which only by application code that can race?
- Are multi-step writes wrapped in a transaction? What state is left behind
  if the process dies between step 2 and step 3?
- How is money stored? Integer minor units or exact decimals, never binary
  floating point. Is there an append-only ledger, or a balance column that is
  overwritten?
- Is every write path that can be **repeated** idempotent? Repeats come from
  retries, duplicate deliveries, double clicks, re-run jobs and overlapping
  schedules.
- Do columns fit the data that is actually written to them now (length,
  precision, enum values), after the last format change?
- Do migrations stay compatible with the code version that is still running
  during the deploy? Are any of them destructive, or likely to lock a large
  table?
- Has every migration the code depends on actually run in every
  environment?
- Is there a cache in front of the data? What invalidates it, and what serves
  when it is cold or stale? Has the cache quietly become a source of truth?
- Does one action write to two systems (DB + queue, DB + email, DB + search
  index) without an outbox or reconciliation?
- Do tables grow without bound? Is there retention, archiving or
  partitioning, and a retention rule for PII?

**Look in**: schema/migration files, ORM models, repository/DAO code, raw SQL
strings, job handlers, anything named `sync`, `reconcile`, `backfill`, `merge`,
`fold`, `import`.

**Usually real**
- Check-then-act: `SELECT` stock/balance/uniqueness, decide in code, then
  `UPDATE`/`INSERT` in a separate statement without a lock or condition.
- `balance = balance + x` (or `obj.balance += x; save()`) in a handler that
  can run twice.
- Upserts that add a *recount* to the stored value
  (`SET total = t.total + EXCLUDED.total`, where `EXCLUDED.total` is a fresh
  `count(*)`). They inflate on every re-run.
- `float`/`REAL`/`DOUBLE` for prices, totals, balances, tax.
- A `try/except` around a persist call that logs "non-fatal" and continues.
  The write can then fail 100% of the time unnoticed, for example
  value-too-long after a format change.
- A migration that drops or renames a column the previous release still
  reads, deployed in the same step as the code change.
- Dual writes where the second write's failure is only logged.

**Usually fine**
- Increments done atomically *in SQL* (`UPDATE … SET n = n + 1 WHERE id = $1`)
  in a path that runs exactly once per event, guarded by a unique event key.
- Eventual consistency that is designed in: there is a reconciler or
  outbox relay, and the lag is acceptable to the product.

**Severity hints**: lost updates or double-applies on money or stock →
Critical. Silent persist failures → Critical when users or money depend on
the data, otherwise Normal. Float money → Critical when it feeds charges,
invoices or ledgers, otherwise Normal. Missing retention → Normal (Critical
when the data is PII under a legal regime). Missing FK/CHECK where app code
is currently correct → Good Practice.

---

## 3. Reliability & failure handling

**Ask**
- Does **every** network call have a deadline? That includes HTTP clients,
  SDKs, SMTP, DB connect, DB statements and TCP keepalive on pooled
  connections. A call with no deadline can hold a worker or pool slot
  forever.
- Are retries bounded, backed off exponentially with jitter, limited to
  retryable errors, and only applied to idempotent operations? Are they
  stacked at several layers (client × job × queue) and multiplying?
- Are errors **classified**? A permanent refusal ("not entitled", "invalid
  request") treated as a transient one ("session expired", "timeout")
  triggers the wrong recovery. Recovery actions such as failover, re-login,
  cooldown and refund are expensive when misfired.
- What does each component do when a dependency is down: fail fast, degrade,
  queue, or hang? Is that a deliberate choice of fail-open vs fail-closed for
  each control?
- Can a failure in a **side effect** (email, notification, analytics, cache
  write) flip the state of the **core work** (mark a paid order failed,
  refund a completed job)?
- Are resources (connections, file handles, locks, leases) released on the
  error path, not just the happy path?
- What does the health check prove? Can it hang (sharing an exhausted pool),
  and can it pass while the core path is broken?
- Does the process fail fast at startup on missing or invalid config, or
  fail late, on the first request that needs it?

**Look in**: HTTP/SMTP/DB client construction, `retry`/`backoff`/`attempt`
code, `except`/`catch` blocks, failover and "stale session" handlers, health
endpoints, startup/config modules.

**Usually real**
- `requests.post(url, json=…)` with no `timeout`; `smtplib.SMTP(host)` with no
  timeout; DB connection strings without connect/statement timeouts or
  keepalives behind NAT or a cloud DB. Symptom: pool exhaustion on an idle
  app, and health checks that hang.
- Raising the pool size to "fix" pool exhaustion. That treats a leak or a hang
  as a capacity problem and can exhaust a shared database.
- `except Exception: pass`, an empty `catch {}`, or `.catch(() => {})` on a path
  whose result matters.
- A delivery or notification failure that calls the same "mark failed" path
  as a real processing failure.
- Fixed-interval retry loops (`sleep(5)` × N) against a rate-limited upstream.
- Adaptive backoff that can *lower* a deliberately configured slow pace.
- Lazily built clients or pools whose misconfiguration only shows up at the
  first real request.

**Usually fine**
- A shallow liveness endpoint, *if* a separate readiness check (or the load
  balancer's own check) covers the dependencies, and deploys verify
  something stronger.
- Deliberate fail-open on a non-security control (for example a cosmetic rate
  limiter), when it is documented.

**Severity hints**: missing timeout on a core request path with sync workers →
Critical (one slow upstream takes the site down). On a background or
optional path → Normal. Side-effect failure corrupting core state →
Critical. Swallowed errors → by what they hide.

---

## 4. Concurrency, jobs & async work

**Ask**
- How many processes run each piece of code (workers × instances ×
  replicas)? Is any **scheduler** (APScheduler, `setInterval`, node-cron,
  in-app cron) started inside a web process that runs N copies?
- Can two runs of the same job overlap (the job is slower than its interval,
  or a deploy restarts it mid-run)? What stops them colliding?
- Are queue consumers idempotent under at-least-once delivery? What happens
  to a message that always fails (poison): dead-letter, max attempts,
  alert, or silent deletion?
- Are claimed or "in progress" rows recovered if the worker dies (visibility
  timeout, lease expiry, sweeper)?
- For leases and locks: TTL? Renewed on **every** code path that keeps
  using the resource? Released on error? Is a swap a compare-and-swap, or
  release-then-acquire (which lets two holders race)?
- Do whole-table maintenance tasks (merge, dedupe, backfill) run concurrently
  from several workers? Expect deadlocks.
- Does a transaction lock rows `FOR UPDATE` after inserting or updating a row
  that references them? The foreign-key check already holds a shared lock on
  those rows, so two such transactions deadlock whatever the lock order. In
  PostgreSQL, `FOR NO KEY UPDATE` avoids it (failure pattern 27).
- Are there fire-and-forget threads or tasks whose work is lost on restart?
- Can a background worker in dev or staging act on production data or
  customers (mailing real users, charging real cards)?
- Is an external resource that allows **one session per credential**
  (vendor logins, single-session APIs) shared by several processes without
  a broker?

**Look in**: job/worker modules, queue configuration (`attempts`, `backoff`,
`removeOnFail`, visibility timeouts), schedulers, lock/lease code
(`FOR UPDATE`, `SKIP LOCKED`, advisory locks, `SET NX`), anything that loops
forever.

**Usually real**
- An in-process scheduler in a multi-worker web server. The job runs once per
  worker per instance, and combined with a non-idempotent job that is data
  corruption.
- Lease or heartbeat renewal skipped on an early-return path, so the resource
  is re-leased to a second holder while the first still uses it, and the two
  evict each other in a retry storm.
- `removeOnFail: true` or equivalent with no dead-letter or alert. Failures
  disappear.
- Retries at the queue level around a handler whose side effect (an SMS, a
  charge) is not idempotent. Users get duplicates.

**Usually fine**
- A scheduler in a dedicated single-replica process, or guarded by a
  DB/advisory lock.
- `SELECT … FOR UPDATE SKIP LOCKED` claim loops with a recovery sweeper.

**Severity hints**: duplicate side effects on money, messages or stock →
Critical. Lost or silently dropped jobs → Critical if users paid or wait for
the result, else Normal. Overlap with idempotent jobs → Good Practice.

---

## 5. Scalability, performance & capacity

**Ask**
- What is the **binding constraint**: an upstream quota, a set of
  credentials, DB connections, a single worker, a single host, CPU? Is
  stated capacity computed against it?
- Are user-controlled sizes bounded (page size, record count, file size,
  date range)? Is a declared maximum actually enforced on **every** path,
  or does it only appear inside a `min()` that one caller uses?
- Are list endpoints paginated? Are there N+1 query loops on hot paths?
- Do hot queries have supporting indexes? Do expensive results get cached,
  and is the cache checked **before** acquiring the scarce resource?
- Does pool size × processes × instances fit the database's connection
  limit, including other tenants of that database?
- Is any state per-process (in-memory sessions, rate-limit counters, caches
  used as truth, uploads on local disk)? That breaks as soon as there are
  two instances.
- Is rate limiting matched to measured capacity, and fair across tenants?

**Look in**: list/search endpoints, request schemas, loops containing queries,
cache code, pool configuration, process manager / container replica counts,
README or ops docs for traffic numbers.

**Usually real**
- `SELECT … FROM orders WHERE user_id = $1` with no LIMIT, for users who can
  have thousands of rows. Or `findMany()` / `.all()` returned straight to
  the client.
- A quota or rate counter kept in a module-level dict or `Map` in a
  multi-instance deployment. The real limit is N × the configured one, and
  it resets on deploy.
- A constant like `MAX_RECORDS = 5000` that is never applied when the
  caller passes nothing.

**Usually fine**
- Unpaginated queries over tables that are bounded by design (countries,
  plans, settings).
- N+1 loops in admin or one-off scripts that are not on a hot path.

**Severity hints**: unbounded work that one request can trigger (memory or DB
exhaustion) → Normal, Critical if it can take a shared core path down.
Per-process quota state → Normal, Critical when the quota protects money or
an external account that gets banned. Missing indexes → by measured impact.

---

## 6. API & integration contracts

**Ask**
- Are the contracts between components explicit: schemas, versioned APIs,
  contract tests? Are there **implicit** contracts, where a consumer depends
  on order, position or a magic string (the first sheet, the third column,
  an error message's text)?
- Will an API change break consumers in other repos or older mobile
  clients? Is there a compatibility window?
- Do create and payment endpoints accept an idempotency key, so a client
  retry doesn't create a second order or charge?
- **Inbound webhooks**: signature verified with a constant-time compare?
  Event ID deduplicated (unique constraint)? Acknowledged quickly, with the
  work done asynchronously? Out-of-order and replayed events handled?
- **Outbound calls to third parties**: quotas and rate limits known and
  enforced client-side? Vendor-specific error meanings mapped (the same
  status code can mean "expired" or "forbidden")? Sandbox vs live
  credentials impossible to mix up (which source wins when both env and DB
  hold a key)?
- Is email, SMS or push on the critical path of a state change?

**Look in**: route definitions, webhook handlers, API clients and SDK
wrappers, error-mapping code, OpenAPI specs, consumers in sibling repos.

**Usually real**
- A webhook handler that verifies the signature (good) but applies the
  effect every time the same event arrives. Providers deliver at least once.
- A status update endpoint from a provider with no signature check. Anyone
  can mark messages delivered or payments paid.
- A payment capture call retried without an idempotency key.

**Usually fine**
- Webhook handlers that re-fetch the object from the provider's API before
  acting. That verifies authenticity by other means, but still needs
  deduplication.

**Severity hints**: duplicate or unauthenticated effects on money or state →
Critical. Implicit contracts across repos → Normal (Critical if the
breakage is silent and customer-facing). Missing versioning → Good Practice
until there are external consumers.

---

## 7. Security architecture

Design level only: trust boundaries, isolation and secret flow. A dedicated
security review should cover the rest. Say so in *Not assessed* when it is
out of scope.

**Ask**
- Is authentication enforced at the edge **and** authorization in the
  handler or service? Is every multi-tenant query filtered by the tenant from
  the session, never from the request?
- Where do secrets live, how many copies exist, and can they be rotated
  without an outage? Are they kept out of git, command lines (process lists
  and sudo logs record arguments), URLs (proxies log them) and application
  logs?
- Are internal endpoints and admin tools reachable from the internet?
- Do database roles follow least privilege (separate read-only roles for
  analytics, CRM or reporting)?
- Does any background job or dev instance have production credentials it
  doesn't need?

**Usually real**: tenant ID taken from the request body; a bearer token in a
query string that ends up in access logs; one superuser DB role shared by
every service; TLS verification disabled on a call that carries credentials.

**Severity hints**: tenant-isolation breach → Critical. Secrets in logs or
argv on a shared host → Critical if they grant production access. Shared
superuser role → Normal.

---

## 8. Observability

**Ask**
- Can an operator answer "is it working right now?" and "what happened to
  user X's request?" Are there request or job correlation IDs across
  components?
- Are failures logged with cause and context, or as "failed"/"error"? Do
  errors that are caught and survived still emit a metric or counter?
- Are there alerts on symptoms users feel (error rate, queue age, payment
  failures), and on **absence**: the nightly job didn't run, the backup is
  empty, no orders in an hour? Do alerts reach someone who acts on them?
- Are logs drowning in noise (crash loops, debug spam) that hides real
  signals?
- Do logs contain secrets, tokens or PII?

**Usually real**: a critical background job whose only failure signal is a
log line nobody reads; error handlers that log without the exception; no
alert on queue depth or age for a job system users wait on.

**Severity hints**: a silent failure mode on a critical path with no signal
→ raises *that* finding's severity, so record it there. Missing metrics in
general → Normal. Log format → Good Practice.

---

## 9. Deployment, operations & recovery

**Ask**
- How does code reach production? Is it repeatable, and how is a deploy
  **verified**? A 200 from `/health` can come from the old process. Check
  the process start time, a version endpoint, or the commit SHA the process
  reports.
- Can a bad deploy be rolled back? Are migrations safe to roll back, or at
  least compatible with the previous version (expand → migrate → contract)?
- Is deploy order between components written down when it matters
  (migration → config → consumer A → consumer B)?
- Does the deploy script modify itself while it runs (for example
  `git reset --hard` or `git pull` on the script being executed)? The shell
  keeps running the old body, so new steps silently skip once.
- Is config validated at startup, and consistent across environments? Is
  config that lives only on the server (proxy rules, timers, cron) kept in
  version control or at least documented?
- **Backups**: do they cover every data store? Do they fail loudly? Are they
  verified (restore, or at least a restore listing)? Are old copies deleted
  only after a successful new one? Has a restore ever been rehearsed? What
  are the RPO and RTO?
- Is any host, process or credential a single point of failure for
  everything? Is that acceptable at this stage, and documented?
- Do process managers restart crashed processes, and does anyone notice a
  crash loop?

**Look in**: deploy scripts, CI/CD workflows, Dockerfiles, compose and k8s
manifests, systemd units and timers, process manager configs, backup
scripts, runbooks.

**Usually real**
- `pg_dump … > file` in cron with no `set -e`/`pipefail` and no
  verification. A failure leaves a 0-byte file and cron reports success.
- Rotation (`find … -mtime +7 -delete`) that runs even when tonight's backup
  failed, aging out the last good copy.
- A new database or bucket added to the system but not to the backup list.
- Deploys that pull code without restarting the long-lived process, or whose
  restart failure is hidden (`>/dev/null 2>&1`).

**Severity hints**: backups that can silently fail or don't cover a store →
Critical (the loss shows up only when you need them). Unverifiable deploys
→ Normal. Single host for a small product, documented → Good Practice.

---

## 10. Maintainability & evolvability

**Ask**
- Do tests pin the **critical state transitions**: money moving, access
  granted, job state changes, refunds? Would a refactor that breaks them
  fail CI?
- Is there dead code, a duplicate implementation, or a legacy path still
  wired in, that someone will "fix" in the wrong place?
- Are environment-specific values (hosts, IPs, account names) hard-coded?
- Are the system's non-obvious rules written down where the next engineer
  (or agent) will read them: README, ADRs, CLAUDE.md, contributor docs?
- Are dependencies maintained, or is the system pinned to abandoned or
  end-of-life versions?

**Usually real**: a money or state transition with zero tests; two copies of
a pricing or permission function where only one is called; tests that
assert a literal that a constant is supposed to own.

**Severity hints**: untested critical transitions → Normal (the defect they
let through gets its own rank). Dead or duplicate code → Good Practice,
Normal when the duplicate is the one being called. Docs → Good Practice.
