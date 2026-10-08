# Contributing

Thanks for helping. The most useful contributions are reports of what the
skill got wrong, and failure patterns from real incidents.

## Report a wrong or missed finding

Open an issue with:

- the finding as the report wrote it, or the defect it missed;
- the code it cites, or a minimal example that shows the problem;
- why the finding is wrong, or why the missed defect is real.

Never paste secrets, credentials or private code into an issue. Cut the
example down until it shows only the problem.

## Before you open a pull request

```bash
python3 -m unittest discover -s tests      # all tests must pass
claude plugin validate .                   # the manifests must validate
```

- Keep the scripts on the Python 3.8+ standard library. Users should never
  need to `pip install` anything.
- Make one change per commit, with a message that says what changed and why.

## Adding a failure pattern

A pattern belongs in
[`failure-patterns.md`](skills/system-design-audit/references/failure-patterns.md)
only if it caused a real production problem and looks like correct code at a
glance.

- Use the same four parts as the others: **What it looks like**, **Why it
  breaks**, **How to spot it** and **Fix**.
- Add it to the *Contents* list at the top of that file.
- Update the pattern count in `README.md`, in three places, and the list
  under *The 27 failure patterns*.

## Changing the severity rules

Add or update a calibration example in
[`severity.md`](skills/system-design-audit/references/severity.md), so the
change is visible in a concrete case. If you can, rerun the evals in
[`evals/`](evals/) and include the before and after numbers.

## Test systems

The flaws in `tests/fixtures/` are deliberate: they are what the evals
expect the skill to find. Don't fix them. If you add or change one, update
its answer key in `evals/ground-truth/`.
