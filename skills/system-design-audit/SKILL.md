---
name: system-design-audit
description: Audit the system design and architecture of any codebase, multi-repo product or design document, rank every issue as Critical, Normal or Good Practice with file:line evidence, a concrete failure scenario and a right-sized fix, and, when asked, apply the fixes test-first. Use this whenever someone asks to review, audit or assess architecture or system design; to check whether a product is production-ready or ready to scale; to find what will break (data integrity, race conditions, idempotency, timeouts and retries, queues and background jobs, caching, database and migration design, deploys, backups, observability, third-party integrations); to assess technical debt; to review a design doc or RFC; or to run a pre-launch or post-incident review. Use it even when they just say "check this repo", "what's wrong with this system" or "is this built right". Also use it to re-audit after fixes, or to fix findings from an earlier audit. Not for a line-by-line review of a single diff, or for chasing one known bug.
---

# System Design Audit

Find the ways a system will fail in production, prove each one from the code,
rank it honestly, and fix it cleanly when asked.

**The principle:** a finding is a *failure scenario backed by evidence*, not a
missing best practice. "No circuit breaker" is not a finding. "When the carrier
API hangs, every checkout worker blocks on `shipping.py:22`, which has no
timeout, and the site stops taking orders" is a finding. If you cannot say
what triggers it, what breaks, and who notices, you have not found anything
yet. Keep looking, or record it as an open question.

This is what separates a useful audit from a generic checklist dump. The
owner can act on a ranked list of real, proven failure modes. A wall of
"consider adding X" teaches them to ignore the report.

## Modes

| Mode | When | Writes code? |
|---|---|---|
| **Audit** (default) | "review / audit / assess / check this system" | No. Read-only, apart from saving the report |
| **Design review** | The input is a design doc, RFC or plan rather than code | No |
| **Fix** | "fix the findings", "fix SDA-003", "apply the recommendations" | Yes. Follow `references/remediation.md` |
| **Re-audit** | A previous report exists, or "did we fix everything?" | No. Updates statuses in a new report |

An audit never modifies the system under review. Fixing is a separate step
the user asks for, because they need to see the whole picture before
deciding what to change.

## Audit workflow

### 1. Scope and revision

- Pin down what is being audited: one repo, several repos that form one
  product, a sub-system, or a document. A folder holding several git repos is
  one system with seams between them, and the seams are where the worst
  defects hide.
- Record the exact revision: branch, commit, and whether the working tree is
  clean. Compare it with the upstream default branch. An audit of a stale
  local branch reports bugs that production fixed weeks ago. If the checkout
  is behind or on a feature branch, say so in the report header, or audit the
  default branch instead (`git show origin/main:path`).
- Pick a depth: **quick** (critical paths only, top risks), **standard**
  (every lens; this is the default) or **deep** (every lens, every critical
  path traced line by line, every cross-component contract checked).

### 2. Recon: map before you judge

Run the bundled inventory script. Paths in this skill are relative to the
skill's own directory.

```bash
python3 scripts/recon.py <path-to-system>            # human-readable map + risk signals
python3 scripts/recon.py <path-to-system> --json     # same, machine-readable
```

It reports the repositories and their git state, languages and size, the
stack read from the manifests, data stores, entry points, background work
and schedules, deploy and runtime files (with worker/replica counts),
migrations, config key names (never values), tests, the docs worth reading,
the concurrency and safety controls already present, and **risk signals**.

Risk signals are **leads, not findings**. Each one is a place where a known
failure pattern often lives. Verify every lead you use, and expect some to
be fine. A quiet signal list proves nothing either: most design defects are
only visible by reading the code, which is why the next steps exist.

Then read the human context: README, architecture docs, ADRs, runbooks,
`CLAUDE.md` / `AGENTS.md`, deploy scripts. Teams write down the decisions
they made on purpose. You need those so you don't "fix" a deliberate
trade-off.

### 3. Model the system

Before hunting for defects, write down (briefly, in the report's *System
map*):

- **Components** and what each owns. For every important entity, name its
  single source of truth. Two writers for one fact is a design smell by
  itself.
- **Data flow**, sync or async, between components, data stores and third
  parties. Draw a small text diagram.
- **Critical paths**: money moving, identity and access, writes users depend
  on, the core user journey. Depth goes where the damage is. Read these in
  full and trace them end to end.
- **The binding constraint**: the thing that actually caps throughput or
  availability. It is often an external quota, a connection limit, a single
  worker or a single host, not CPU or RAM. Capacity claims that ignore it
  are wrong.
- **Runtime shape**: how many processes, workers and instances run each
  component. In-process state and schedulers only make sense against this.

### 4. Interrogate through the lenses

Work through every lens in `references/lenses.md` (read it now). Each lens
lists the questions to ask, where in the code to look, and the patterns that
usually turn out to be real. They are:

1. Architecture & boundaries
2. Data model & integrity
3. Reliability & failure handling
4. Concurrency, jobs & async work
5. Scalability, performance & capacity
6. API & integration contracts
7. Security architecture (design level)
8. Observability
9. Deployment, operations & recovery
10. Maintainability & evolvability

Also scan `references/failure-patterns.md`. It is a catalogue of failure
patterns taken from real production incidents, written so you can
recognise each one in unfamiliar code. Most of them look like correct code
at a glance, which is why they reach production.

### 5. Verify every candidate: try to disprove it

For each candidate finding, before it goes in the report:

- **Look for the guard elsewhere.** The check you think is missing may sit in
  middleware, a database constraint, a framework default, a reverse proxy, a
  retry wrapper or the caller. Search for it.
- **Write the failure scenario**: trigger → mechanism → impact. Name the
  concrete input or event (a duplicate webhook delivery, two concurrent
  requests, a slow upstream, a deploy mid-job, a second instance) and what
  the user or business sees.
- **Cite evidence** as `path:line` or `path:start-end` for every claim,
  relative to the audited root. Quote the line when it helps. In a design
  review, cite the document's lines or sections.
- **Assign confidence.** *Confirmed*: traced in code. *Likely*: one step
  depends on runtime config you can't see; state which. *Needs
  verification*: plausible but unproven; state exactly what to check.

If you cannot write the scenario, drop the finding, downgrade it to Good
Practice, or move it to *Open questions*. Precision matters more than
volume. One false Critical costs the report its credibility.

### 6. Rank: Critical, Normal, Good Practice

Use `references/severity.md` for the full rubric and calibration examples.

| Severity | Meaning | Typical examples |
|---|---|---|
| **Critical** | Under normal operation, or a plausible event, it causes **data loss or corruption, wrong money, a security or tenant-isolation breach, an outage of a core path with no automatic recovery, or silently wrong results reaching users**. Fix before the next release. | Double charge on duplicate webhook · oversell race · backup that silently writes empty files · paid work marked failed and refunded because the email bounced |
| **Normal** | A real defect or risk with **bounded or recoverable** impact: degraded performance, a recoverable outage, toil, a scaling wall that is not imminent, an incident that would be slow to diagnose. Fix in planned work. | Unbounded list endpoint · no timeout on a non-critical call · health check that cannot detect a dead dependency · retries without backoff |
| **Good Practice** | **No current failure scenario**; an improvement that makes the system more robust, operable or maintainable. Fix when touching the area. | Structured logs · documenting a deliberate trade-off · config validated at startup in a service that already fails fast elsewhere |

Ranking rules that matter most:

- No failure scenario, no Critical. Famous best practices do not earn
  severity by reputation.
- **Silent** wrong results rank above loud failures of the same size. A
  crash gets noticed and fixed. A wrong number ships for months.
- Money, identity, PII and irreversible actions push severity up.
- Judge against the system's actual stage and stakes. A prototype with ten
  users and a payments platform get different answers to "is a single
  instance a problem?". Say which stage you assumed.

### 7. Write and check the report

Use the exact structure in `references/report-template.md`. Save it to the
path the user gave, or by default to
`docs/audits/system-design-audit-YYYY-MM-DD.md` under the audited root. For
a folder of several repos, save it in that folder. Don't commit it, and say
where it is.

If writing the file is refused, that is the user's decision. Don't route
around it through the shell (a heredoc, `tee`, a script). Validate the text
from stdin instead (`validate_report.py - --root <audited-root>`), return the
full report in your reply, and say it was not saved.

Before validating, **re-open every line you cite** and confirm it says what
the finding claims. Off-by-one citations and plausible-looking numbers are
the most common errors in a finished report. If a finding states arithmetic
(a rounding error, a capacity figure, a row count), compute it with a
command instead of estimating it.

Then run the validator and fix anything it reports:

```bash
python3 scripts/validate_report.py <report.md> --root <audited-root>
```

It checks structure, severity, confidence and status values, required
fields per finding, that summary counts match the findings, that no secret
leaked into the text, and, with `--root`, that **every cited `path:line`
exists**. That catches invented evidence before the user does. It cannot
tell whether a real line *means* what you say it does. That part is the
re-read above.

Reply in chat with: the verdict in one or two sentences, the counts per
severity, the Critical findings one line each, and the report's path. The
detail lives in the file.

## Right-size every recommendation

The fix you propose should be the **smallest change that removes the failure
mode**, in the stack the team already runs.

- A unique constraint plus `ON CONFLICT DO NOTHING` beats a new
  deduplication service. A conditional `UPDATE … WHERE stock >= qty` beats
  a distributed lock. A timeout argument beats a circuit-breaker library.
- Introduce new infrastructure (a queue, a cache cluster, Kubernetes,
  microservices, a second region) only when a smaller fix cannot remove the
  failure. Then say why the smaller fix fails.
- Include a short code sketch when the fix fits in one, and give it an
  effort size (S under a day, M a few days, L a project).
- Give each finding a **Verify** step: the test, query or command that
  proves the fix works. A fix nobody can verify will regress.

## Respect deliberate decisions

When code comments, docs or ADRs explain why something is the way it is
(a pool size chosen to fit a shared database's connection cap, a rate
limiter that fails open on purpose), evaluate the reasoning. Don't
pattern-match against it. If the reasoning holds, say so under *Strengths*
or leave it out. If it doesn't, the finding must answer it directly.

## Large systems and multi-repo products

- Recon the whole thing first, then spend reading time in proportion to risk:
  critical paths in full, signal leads verified, everything else sampled.
- **Audit the seams**: databases read by more than one service, shared
  credentials or quotas, cross-repo API contracts, deploy-order dependencies,
  and shared secrets that must rotate together. No single repo shows these
  defects.
- If you can delegate to subagents, give each one component, the lenses file
  and the evidence rules, and ask for candidate findings in the report's
  finding format. Treat what they return as leads: verify and rank each one
  yourself, so that one standard applies to the whole report.
- State coverage honestly under *Not assessed*: what you read in depth, what
  you sampled, and what you never opened.

## Design-doc review

Apply the same lenses to the document. Evidence is `doc.md:line`. A
**missing decision is itself a finding** when the system cannot be built
safely without it. For example: "payment capture retries are described, but
no idempotency key is"; "nothing says what happens to in-flight matches when
the matcher restarts". Rank it by what would happen if the team built it as
written.

## Fix mode

Read `references/remediation.md` first. In short:

1. Start from a report. If there isn't one, audit the affected area first.
2. Agree the batch with the user, Critical first. One finding is one
   change.
3. For each finding: re-read the evidence (code moves), write a test or
   check that reproduces the failure scenario, watch it fail, make the
   smallest fix, watch it pass, and run the suite.
4. Update the finding's **Status** in the report, naming the test that
   proves the fix. When the fix sets a rule future changes must keep (for
   example "counters in this upsert use GREATEST, never +"), add one line
   with the reason to the project's agent or contributor docs.
5. Stop and ask before data migrations on real data, deploy or
   infrastructure changes, public contract changes, or anything
   irreversible. Never push or deploy unless asked.

## Re-audit mode

Find earlier reports (`docs/audits/system-design-audit-*.md`). Keep finding
IDs stable across audits. For each old finding, re-check its evidence and
set its status to Fixed (with the proof), Open, Regressed or Stale (code
gone). New findings continue the numbering. Add a *Changes since last audit*
section, then validate as usual.

## Ground rules

- **Read-only** in audit mode. Never run migrations, deploys, load tests or
  anything that touches a live environment to "confirm" a finding. Write the
  check down for the owner instead.
- **Never put a secret value in the report**, even one found hard-coded.
  Cite the location and redact the value.
- Report what you saw, not what is usual. If you didn't open it, it goes
  under *Not assessed*, not under Strengths.

## Rationalizations to reject

| Thought | Reality |
|---|---|
| "Best practice says X, so missing X is Critical" | Severity comes from the failure scenario, never from the practice's fame. |
| "The framework probably handles it" | Then find where. If you can't, it is a *Likely* finding with the check written down. |
| "It works in the tests" | Tests rarely run two workers, duplicate deliveries, slow upstreams or a deploy mid-job. Those are where design fails. |
| "It's caught and logged, so it's handled" | A catch that logs "non-fatal" and moves on can hide a 100% failure rate for months. Ask what the user ends up with. |
| "/health returns 200, so it's up" | Ask what the health check actually proves. Many answer from a process that cannot reach its database, or from the old process after a failed deploy. |
| "More findings make a more thorough audit" | Padding buries the three things that matter. Rank hard, merge duplicates, drop what you can't prove. |
| "The fix is a rewrite / microservices / Kafka" | That is rarely the smallest change that removes the failure. Find the constraint, the transaction or the timeout that does. |
| "I'll just fix this while I'm here" | Not in audit mode. The owner decides what changes, after seeing everything. |
