# Changelog

Notable changes to this project. Versions follow
[Semantic Versioning](https://semver.org/).

## 1.0.0 (2026-10-08)

First public release.

- Four modes: audit, design review, fix and re-audit.
- Ten lenses, a severity and confidence rubric with calibration examples,
  and 27 failure patterns taken from real incidents.
- `recon.py` maps a system before the audit starts. `validate_report.py`
  checks a finished report, including that every cited `file:line` exists.
- 68 unit tests, four test systems with answer keys, and behavioural evals.
  The results are in the README.
