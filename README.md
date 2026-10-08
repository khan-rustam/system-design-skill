# system-design-audit

A Claude skill that audits the system design of any codebase, multi-repo
product or design document. It finds what will break in production, proves
each problem from the code, ranks it **Critical / Normal / Good Practice**,
and, when you ask, fixes it test-first.

```
> audit the system design of this repo before our launch

  Findings: 9 Critical · 5 Normal · 2 Good Practice
  SDA-001 Critical  Duplicate webhook deliveries re-apply payment effects   app/routes/payments.py:35
  SDA-002 Critical  Oversell under concurrent checkout                       app/routes/orders.py:26
  SDA-003 Critical  Email failure cancels and refunds a paid, shipped order  app/routes/payments.py:49
  ...
  Report: docs/audits/system-design-audit-2026-10-08.md
```

## Why another review skill

Most architecture checklists produce a wall of "consider adding X". This skill
holds every finding to one rule:

> **A finding is a failure scenario backed by evidence, not a missing best practice.**

Each finding names its trigger, mechanism and impact, cites `file:line`, gets a
confidence level, and comes with the *smallest* fix that removes the failure,
plus a way to verify it. Famous practices (circuit breakers, microservices,
Kubernetes) earn no severity on reputation alone. Deliberate decisions written
down in the code or docs are respected rather than "fixed".

## What it covers

Ten lenses: architecture & boundaries, data model & integrity, reliability &
failure handling, concurrency & background jobs, scalability & capacity, API &
integration contracts, security architecture, observability, deployment &
recovery, and maintainability. There is also a catalogue of
[27 failure patterns from real production incidents](skills/system-design-audit/references/failure-patterns.md).
These are the bugs that look like correct code: the upsert that adds a recount,
the lease not renewed on one code path, the backup that "succeeds" at writing
nothing, the deploy script that runs its old body.

| Mode | Ask it to | It does |
|---|---|---|
| Audit | "audit / review / check the system design of …" | Read-only audit, report saved to `docs/audits/` |
| Design review | "review this design doc / RFC" | Same lenses on a document; missing decisions become findings |
| Fix | "fix SDA-003", "fix the critical findings" | One finding per change, reproduce first, smallest fix, report updated |
| Re-audit | "did we fix everything?" | Re-checks earlier findings: Fixed / Open / Regressed / Stale |

It works on any language and stack. The bundled recon script understands
Python, JavaScript/TypeScript, Go, Java/Kotlin, Ruby, PHP, Rust, Dart and .NET
manifests, plus Docker, Compose, Kubernetes, systemd, PM2, Terraform and
common CI files.

## Install

**Claude Code plugin** (recommended):

```
/plugin marketplace add khan-rustam/system-design-skill
/plugin install system-design-audit@system-design-audit
```

**Manual (Claude Code):** copy `skills/system-design-audit/` into
`~/.claude/skills/` (all projects) or `.claude/skills/` (one project).

**claude.ai:** zip the `skills/system-design-audit` folder (the zip's top
level is that folder, containing `SKILL.md`) and upload it as a custom skill
in your claude.ai settings.

Claude picks the skill up on its own when you ask for an architecture or
system-design review. You can also name it: *"use system-design-audit on this
repo"*.

## What's inside

```
skills/system-design-audit/
├── SKILL.md                     workflow, severity rules, ground rules
├── references/
│   ├── lenses.md                the ten lenses: what to ask, where to look, what's usually real
│   ├── severity.md              rubric, modifiers, calibration examples, inflation traps
│   ├── failure-patterns.md      27 field-tested failure patterns
│   ├── report-template.md       the exact report format
│   └── remediation.md           fix mode: the loop, when to stop and ask, fix recipes
└── scripts/
    ├── recon.py                 maps the system and lists risk signals (leads, not findings)
    └── validate_report.py       checks a report: structure, counts, and that every cited file:line exists
```

Both scripts use the Python 3.8+ standard library only. `recon.py` is
read-only, never opens real `.env` files, and redacts secret-looking values.
`validate_report.py` also refuses reports that contain something resembling a
live credential.

## Testing

```bash
python3 -m unittest discover -s tests      # 68 unit tests for both scripts
```

`tests/fixtures/` holds the test systems: a Flask shop with 14 seeded design
flaws, a TypeScript multi-tenant notification service, a deliberately
**well-designed** ledger (the skill must not invent problems in it), and a
design document. The answer keys are in `evals/ground-truth/`, and the
behavioural evals (with and without the skill) are defined in
`evals/evals.json`.

### Results

Benchmarked on Claude Opus 5.5 with Anthropic's skill-creator eval harness.
Each eval ran once with the skill and once without it (the same model, no
skill). Two kinds of grading:

- A script checks the mechanical points: the report exists, every cited
  `file:line` exists, no secrets appear, the audited repo is untouched, and in
  fix mode the tests pass and fail against the original code.
- Blind graders check the rest. Each saw both reports with the labels removed,
  and the answer key.

| Eval | What it tests | With skill | Without |
|---|---|---|---|
| shop-orders | Flask shop with 14 seeded flaws | 10/10 | 8/10 |
| notify-hub | multi-tenant TypeScript service | 10/10 | 7/10 |
| ledger-clean | well-designed ledger: must not invent problems | 10/10 | 8/10 |
| dispatch design | review of a design document | 8/8 | 6/8 |
| fix mode | fix two findings test-first | 11/11 | 11/11 |
| real repo | read-only audit of a private production worker | 9/10 | 7/10 |
| **Total** | | **58/59 (98%)** | **47/59 (80%)** |

Read the totals with care:

- **One run per configuration.** A one-assertion difference is noise.
- **One assertion favours the skill by construction.** Each audit checks the
  skill's own report format, which a run without the skill can't pass.
  Without that assertion the totals are 98% vs 87%. On the blind-graded
  assertions alone they are 32/33 vs 28/33.
- **Where the skill helped:**
  - Severity calibration: no inflated Criticals on the well-designed ledger.
  - It states coverage honestly.
  - It names the check behind an unverified assumption more often. On the
    real repo it did so for 3 of 5 Criticals that needed one, against 2 of 7
    without the skill.
  - It cites `file:line` evidence. The baseline's design review cited none.
  - It keeps secrets out of reports. The baseline copied a fixture's
    database URL with its password.
- **Where it didn't help: recall.** On the ledger, the run without the skill
  found more of the known issues (8 vs 6 of 10). On the real repo, each run
  found serious defects the other missed. The skill improves structure and
  judgement, but it doesn't guarantee completeness.
- **The ledger's deadlock is now failure pattern 27**, so finding it is no
  longer independent evidence.
- **Cost:** about 40% more time and tokens. A run averaged 22 minutes and
  265k tokens, against 16 minutes and 187k without the skill.
- **Improvement between iterations:** from the first iteration to the second,
  the with-skill score went from 96% to 98%. On the 30 blind-graded
  assertions both iterations share, it went from 28/30 to 30/30, while the
  baseline stayed at 27/30.

**Triggering.** The test session had only Claude Code's built-in skills and
this plugin.

- The skill loaded for 10 of 10 audit, review and fix requests. Three of those
  requests named a file that wasn't in the test repo. Each was rerun twice with
  the file present, and loaded the skill both times.
- It stayed out of 10 of 10 near-misses: a PR diff review, one failing test, an
  interview question, a diagram, an OWASP review, feature work, explaining
  code, a UI redesign, writing unit tests and a concept question.

The eval definitions are in `evals/`: `evals.json`, `trigger-evals.json`, the
answer keys and `grade_mechanical.py`.

## Limitations

- It reads code and config. It doesn't load-test, run your system, or see
  infrastructure that isn't in the repo. Findings that depend on runtime
  configuration are marked *Likely* or *Needs verification*, each with the
  check to run.
- The risk signals are heuristics that point where to read. A quiet signal
  list is never treated as a clean bill of health.
- Security is covered at the design level (trust boundaries, tenant
  isolation, secret flow). Pair it with a dedicated security review for depth.

## License

MIT. See [LICENSE](LICENSE).
