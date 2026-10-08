# Severity and confidence

Three severities, three confidence levels. The two are independent: a
finding can be *Critical / Likely* (severe if true, one assumption
unverified) or *Good Practice / Confirmed*.

## Severity

### Critical

Fix before the next release. A finding is Critical when a **plausible**
trigger, meaning normal operation, ordinary concurrency, a routine deploy,
a provider's documented behaviour or a common outage, leads to at least one
of:

- **Data loss or corruption**: lost writes, double-applied effects,
  inflated or wrong stored values, backups that cannot restore.
- **Wrong money**: a double charge, a missed charge, a wrong refund, a wrong
  invoice or tax amount, a balance that drifts.
- **A security or isolation breach**: one tenant reads or changes another's
  data; anyone can trigger a privileged state change.
- **An outage of a core path with no automatic recovery**: it needs a human
  to restart, clean up or replay before users can work again.
- **Silently wrong results reaching users or decisions**: reports, counts or
  statuses that are wrong with no error anywhere.

### Normal

Fix in planned work. A real defect or risk with **bounded or recoverable**
impact:

- degraded performance or a recoverable outage of a non-core path;
- a scaling wall that will be hit, but not imminently at current load;
- a failure that is loud and self-healing but wastes effort or money;
- an operational gap that makes incidents slow to detect or diagnose;
- a maintainability hazard that is actively producing bugs.

### Good Practice

Fix when touching the area. **No current failure scenario.** An improvement
in robustness, operability, clarity or future-proofing. Most "consider X"
items belong here, *if* they belong in the report at all.

## Decision procedure

1. Write the failure scenario: trigger → mechanism → impact.
2. If you can't, it is Good Practice at most. Or move it to open questions.
3. Pick the starting level from the impact column above.
4. Apply the modifiers:

| Modifier | Effect |
|---|---|
| Failure is **silent** (no error, no alert, wrong data persists) | Up one level, or stay at Critical |
| Involves **money, identity, PII or an irreversible action** | Up one level |
| Trigger is **routine**: every deploy, every duplicate delivery, every second worker | Keeps or raises severity |
| Trigger requires a rare coincidence of several independent faults | Down one level |
| Blast radius is one user, one admin or one internal tool | Down one level |
| A guard already limits the damage (constraint, reconciler, alert) | Down one level, and mention the guard |
| Already happening in production (logs or data show it) | Critical if the impact category qualifies; state the evidence |

5. Sanity check against the calibration examples below. If your finding is
   rated differently from its closest example, you should be able to say
   why.

## Stage and stakes

Judge the system against what it is, and state your assumption in the
report:

- **Prototype / internal tool**: few users, low stakes. Single host, manual
  deploys and thin observability are often acceptable (Good Practice).
  Data-integrity and money defects keep their severity: wrong data is wrong
  at any scale.
- **Production product**: real customers or revenue. The full rubric
  applies.
- **Regulated, or money / PII at scale**: payments, health, KYC, invoicing
  under tax law. Integrity, audit trail and isolation findings rarely rank
  below Normal.

## Calibration examples

| Finding | Severity | Why |
|---|---|---|
| Payment webhook verifies the signature but has no event-ID dedupe; it credits the wallet on every delivery | **Critical** | Providers deliver at least once (routine trigger) → double money |
| `SELECT stock` then `UPDATE stock = stock - qty` in two statements, no lock, no `CHECK (stock >= 0)` | **Critical** | Concurrent checkout of the last unit (routine at any real traffic) → oversell, silent |
| Email-send failure after a successful paid job marks the job failed and triggers a refund | **Critical** | A side effect corrupts core state and loses money; a mail provider throttle is routine |
| Nightly backup is `pg_dump > file` with no exit check; rotation deletes copies older than 7 days regardless | **Critical** | A silent failure destroys the recovery path; found only when needed |
| In-process scheduler in each of 4 gunicorn workers × 2 instances runs a job that does `count = count + recount` | **Critical** | Runs 8×; non-idempotent → inflated stored values, silent |
| Same scheduler, but the job is idempotent (recomputes and overwrites) | **Normal** | Wasted work and lock contention, no wrong data |
| Outbound HTTP call with no timeout inside a sync web worker on the checkout path | **Critical** | One slow upstream exhausts all workers → core path down until it recovers or someone restarts |
| Same missing timeout in a nightly batch job with its own process | **Normal** | Hangs one job; bounded, visible next morning |
| `GET /orders` returns all of a user's orders with no LIMIT | **Normal** | Grows into slow responses or memory pressure; bounded per user |
| Health endpoint returns static 200; load balancer and deploy script both rely on it | **Normal** | Dead instances keep traffic and bad deploys look green, but errors are visible |
| Prices stored as `float` and summed for display only; charges computed by the provider in minor units | **Normal** | Cosmetic rounding errors; money itself is correct |
| Prices stored as `float` and used to compute the charged amount | **Critical** | Wrong money |
| A provider status callback without a signature check that can mark messages "delivered" | **Normal** | Spoofable status; no money or data loss. **Critical** if it can mark payments paid |
| No structured logging / correlation IDs | **Good Practice** | No failure scenario, only slower diagnosis. **Normal** if there is a documented recurring incident it would have caught |
| Config falls back to `localhost` when an env var is missing | **Good Practice**, or **Normal** if production has hit it | It fails late and confusingly rather than at startup |
| Two services share one database, read-only for the second, through a restricted role | Not a finding / **Good Practice** to document | Deliberate, bounded coupling |
| No Kubernetes, no microservices, no message queue | **Not a finding** | Absence of technology is not a defect |

## Inflation traps

- **Famous practice, no scenario.** Circuit breakers, CQRS, event sourcing,
  multi-region, service mesh. Their absence is not a finding unless a
  specific failure follows from it.
- **Theoretical attacker, trivial impact.** Rank by what the attacker gains.
- **Counting the same root cause several times.** Ten endpoints missing a
  timeout through one shared client is one finding with ten locations.
- **Severity by how much code is affected.** A one-line race on the balance
  outranks a sprawling but harmless style problem.

## Deflation traps

- **"It's caught and logged."** If the result is a lost write or a wrong
  status, it is not handled.
- **"It only happens on retries."** Retries are routine.
- **"We only run one worker."** Check the process manager, container and
  replica config. Then check whether that is enforced or just true today.
- **"Nobody has complained."** Silent defects produce no complaints by
  definition.

## Confidence

| Level | Use when | You must also |
|---|---|---|
| **Confirmed** | You traced the trigger, mechanism and impact in code or config you read | Cite the lines |
| **Likely** | The mechanism is in the code, but one step depends on something you couldn't see (runtime config, infra, provider behaviour) | Name the assumption |
| **Needs verification** | Plausible from the evidence, but unproven | Write the exact check that would confirm or clear it |

**Rank by what the evidence proves, not by the worst case of what you
couldn't see.** Sometimes the bad outcome needs an unseen fact to go one
particular way: a provider's retention window, another repo's behaviour, a
setting you couldn't read. When nothing you read makes that likely, the
finding is **Normal at most**, and the question that decides it goes first
in its Verify step. It can be Critical when the fact is documented, is the
platform default, or is already happening. Say which one applies.

An in-code default that an environment variable can override is runtime
config too: `int(os.getenv("BLOCKED_SLEEP", 21600))` tells you the fallback,
not what production runs. Say you assumed the default, and name the variable
whose deployed value settles it.

A finding about code you could not read belongs in *Open questions*, unless
code you did read fails on its own. Before you write a scenario that crosses
into unseen code, check that the part you can see actually lets it happen.
