# Report template

Use this structure exactly. `scripts/validate_report.py` parses it, and
readers learn where to look. Keep the prose tight: the owner should get the
verdict from *Summary* alone, and every finding should stand on its own
without the rest of the report.

## Format rules the validator enforces

- These `##` sections, in this order: **Summary**, **System map**,
  **Scorecard**, **Findings**, **Strengths**, **Not assessed and open
  questions**, and **Remediation plan** (required when anything is Critical
  or Normal). An optional **Changes since last audit** goes after Summary in
  a re-audit.
- The summary has a line `**Findings:** N Critical · N Normal · N Good Practice`
  whose numbers match the findings.
- Every finding appears **twice**: as a row in the findings table, and as a
  `### SDA-NNN · <Severity> · <Title>` section, with the same severity in
  both.
- Severity is one of `Critical`, `Normal`, `Good Practice`.
- Confidence is one of `Confirmed`, `Likely`, `Needs verification`.
- Status starts with one of `Open`, `In progress`, `Fixed`, `Accepted risk`,
  `Won't fix`, `Regressed`, `Stale`. Text may follow, for example
  `Fixed — test_concurrent_checkout (2026-10-08)`.
- Every finding has **Lens**, **Confidence**, **Evidence** (with at least one
  `path:line` or `path:start-end`, relative to the audited root), **Failure
  scenario** (Good Practice may use **Why it matters** instead),
  **Recommendation**, **Verify** (optional for Good Practice) and
  **Status**. **Effort** (S / M / L) is strongly recommended.
- IDs are `SDA-` followed by three digits, unique, and stable across
  re-audits.

Order findings by severity, then by blast radius within a severity.

---

## The template

````markdown
# System Design Audit: <system name>

| Field | Value |
|---|---|
| Date | YYYY-MM-DD |
| Scope | <repos / paths / document audited> |
| Revision | <repo @ short-sha (branch; clean or dirty; N behind upstream)> |
| Depth | Quick / Standard / Deep |
| Stage assumed | Prototype / Production / Regulated or money at scale |

## Summary

<Two to five sentences: is it sound, what is the single biggest risk, what
should happen first. Write it for the owner, not for engineers.>

**Findings:** 1 Critical · 1 Normal · 1 Good Practice

**Top risks**
1. SDA-001 — <one line: what breaks and for whom>

## System map

<A components table, a small text diagram of data flow, and then:>

- **Critical paths:** <money, identity, core journey: where they run>
- **Binding constraint:** <what actually caps throughput or availability>
- **Runtime shape:** <processes × workers × instances per component>

## Scorecard

| Lens | Rating | Note |
|---|---|---|
| Architecture & boundaries | Adequate | <one line> |
| Data model & integrity | Weak | <one line> |
| Reliability & failure handling | | |
| Concurrency, jobs & async work | | |
| Scalability, performance & capacity | | |
| API & integration contracts | | |
| Security architecture | | |
| Observability | | |
| Deployment, operations & recovery | | |
| Maintainability & evolvability | | |

Ratings: **Strong** (no findings above Good Practice, good controls seen) ·
**Adequate** · **Weak** (any Critical, or several Normals) · **Not assessed**.

## Findings

| ID | Severity | Confidence | Lens | Title | Location |
|---|---|---|---|---|---|
| SDA-001 | Critical | Confirmed | Data model & integrity | Oversell under concurrent checkout | `app/routes/orders.py:31` |
| SDA-002 | Normal | Confirmed | Scalability, performance & capacity | Order history is unbounded | `app/routes/orders.py:60` |
| SDA-003 | Good Practice | Confirmed | Observability | No request correlation ID across services | `app/__init__.py:14` |

### SDA-001 · Critical · Oversell under concurrent checkout

- **Lens:** Data model & integrity
- **Confidence:** Confirmed
- **Evidence:** `app/routes/orders.py:31-38` reads `stock`, compares it in
  Python, then writes `stock - qty` in a second statement with no lock.
  `migrations/001_init.sql:12` declares `stock INTEGER NOT NULL` with no
  `CHECK (stock >= 0)`.
- **Failure scenario:** Two customers check out the last unit at the same
  moment → both reads see `stock = 1` → both updates succeed → stock becomes
  −1 and two paid orders exist for one item. Nothing errors.
- **Recommendation:** Replace the read-and-write with one conditional update
  and treat zero affected rows as sold out:
  ```sql
  UPDATE products SET stock = stock - %(qty)s
  WHERE id = %(id)s AND stock >= %(qty)s
  RETURNING stock;
  ```
  Add `CHECK (stock >= 0)` as a backstop.
- **Effort:** S
- **Verify:** A test that fires two concurrent checkouts for a product with
  stock 1 and asserts exactly one succeeds and stock ends at 0.
- **Status:** Open

### SDA-002 · Normal · Order history is unbounded

- **Lens:** Scalability, performance & capacity
- **Confidence:** Confirmed
- **Evidence:** `app/routes/orders.py:60-66` selects every order for the user
  with no `LIMIT` and serialises them all.
- **Failure scenario:** A wholesale customer with 40k orders opens their
  history → one request loads 40k rows into memory → slow responses and
  worker memory spikes for everyone on that worker.
- **Recommendation:** Keyset pagination (`WHERE id < :cursor ORDER BY id DESC
  LIMIT 50`), with a hard maximum page size.
- **Effort:** S
- **Verify:** The endpoint returns at most the page size and a cursor; a test
  pins the maximum.
- **Status:** Open

### SDA-003 · Good Practice · No request correlation ID across services

- **Lens:** Observability
- **Confidence:** Confirmed
- **Evidence:** `app/__init__.py:14` configures logging without a request ID;
  `worker/main.py:9` logs jobs without the originating request.
- **Why it matters:** Tracing one customer's failed order across the API and
  the worker means matching timestamps by hand.
- **Recommendation:** Generate or accept `X-Request-ID` at the edge, put it in
  the log context, and pass it in job payloads.
- **Effort:** S
- **Status:** Open

## Strengths

- <Controls that are present and correct, with locations. This tells the
  owner what not to break, and shows the audit looked at the whole system.>

## Not assessed and open questions

- <What you didn't read, sampled only, or couldn't see (runtime config,
  infra, traffic numbers).>
- <Questions only the owner can answer, each one tied to the finding it
  would change.>

## Remediation plan

1. **Now (before next release):** SDA-001. <Why these first.>
2. **Next:** SDA-002 … <grouped by area so one change set covers several
   findings.>
3. **When touching the area:** Good Practice items.
````

## Re-audit additions

Add this section after Summary:

```markdown
## Changes since last audit

Previous report: `docs/audits/system-design-audit-2026-06-01.md`

| ID | Was | Now | Proof |
|---|---|---|---|
| SDA-001 | Open | Fixed | `tests/test_checkout.py:44` concurrent checkout test passes |
| SDA-004 | Fixed | Regressed | `app/jobs/reconcile.py:22` re-introduced `count + EXCLUDED.count` |
```
