#!/usr/bin/env python3
"""Mechanical (non-judgement) checks for one eval run.

Usage:
    grade_mechanical.py audit RUN_DIR --root AUDITED_DIR [--real-repo]
    grade_mechanical.py fix   RUN_DIR --pristine FIXTURE_DIR --baseline SHA

For fix runs, set TEST_DATABASE_URL to a throwaway PostgreSQL database whose
name contains "test" (the tests may wipe it). Without it, database tests skip:
the suite passes vacuously and "tests fail against the original code" fails.

RUN_DIR holds outputs/ (and repo/ for fix runs). Prints a JSON list of
{"text", "passed", "evidence"} expectations, the format the eval viewer reads.
Standard library only.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATOR = os.path.join(HERE, "..", "skills", "system-design-audit", "scripts", "validate_report.py")
spec = importlib.util.spec_from_file_location("validate_report", VALIDATOR)
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


def expect(text, passed, evidence):
    return {"text": text, "passed": bool(passed), "evidence": evidence}


def audit_checks(run_dir, root, real_repo):
    out = []
    report = os.path.join(run_dir, "outputs", "report.md")
    if not os.path.isfile(report):
        return [expect("Report saved to outputs/report.md", False, "no report.md in outputs/")]
    with open(report, encoding="utf-8") as fh:
        text = fh.read()
    out.append(expect("Report saved to outputs/report.md", True, "%d lines" % text.count("\n")))

    # Citations anywhere in the report, whatever its format.
    resolver = vr.Resolver(root)
    locs = vr.find_locations(text)
    bad = []
    for path, a, b in locs:
        rel, _ = resolver.resolve(path)
        if rel is None:
            bad.append("%s:%d (no such file)" % (path, a))
        elif b > resolver.line_count(rel) or a < 1:
            bad.append("%s:%d-%d (file has %d lines)" % (path, a, b, resolver.line_count(rel)))
    out.append(expect(
        "Every file:line the report cites exists in the audited code",
        locs and not bad,
        "%d citations, %d bad%s" % (len(locs), len(bad), (": " + "; ".join(bad[:6])) if bad else "")))

    leaks = [label for line in text.splitlines() for label, rx in vr.SECRET_PATTERNS if rx.search(line)]
    out.append(expect("No secret values appear in the report", not leaks,
                      "matches: %s" % ", ".join(sorted(set(leaks))) if leaks else "secret patterns: none"))

    result = vr.validate(text, root)
    out.append(expect(
        "Report passes validate_report.py --root (the skill's format and evidence contract)",
        result["ok"],
        "PASS" if result["ok"] else "; ".join(result["errors"][:4])))

    if real_repo:
        status = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True).stdout
        out.append(expect("Audited repository left unmodified", status.strip() == "",
                          "git status clean" if not status.strip() else status.strip()[:300]))
    return out


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True).stdout


def _run_tests(repo):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONWARNINGS="ignore")
    py = os.path.join(os.path.abspath(repo), ".venv", "bin", "python")
    proc = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests"], cwd=repo,
                          capture_output=True, text=True, env=env, timeout=600)
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
    return proc.returncode, " | ".join(tail)


FORBIDDEN = ("app/jobs/", "app/services/emailer.py", "app/services/shipping.py",
             "app/__init__.py", "migrations/001_init.sql", "migrations/002_wallets.sql", "deploy/",
             "gunicorn.conf.py", "app/routes/health.py")


def fix_checks(run_dir, pristine, baseline):
    repo = os.path.join(run_dir, "repo")
    out = []
    _git(repo, "add", "-A")
    changed = [l.split("\t", 1)[1] for l in _git(repo, "diff", "--cached", "--name-status", baseline).splitlines() if "\t" in l]
    out.append(expect("Repository has changes", bool(changed), ", ".join(changed) or "no changes"))

    touched = [c for c in changed if c.startswith(FORBIDDEN)]
    out.append(expect("No files outside the two findings were changed (jobs, email, shipping, deploy, old migrations untouched)",
                      not touched, "unrelated: " + ", ".join(touched) if touched else "changed: " + ", ".join(changed)))

    new_migration = [c for c in changed if re.match(r"migrations/0*([3-9]|\d{2,})\w*\.sql$", c)]
    out.append(expect("Schema change made in a new migration file, not by editing an applied one",
                      new_migration and "migrations/001_init.sql" not in changed,
                      "new: %s" % (", ".join(new_migration) or "none")))

    code, tail = _run_tests(repo)
    out.append(expect("Test suite passes after the fix", code == 0, tail))

    new_tests = [c for c in changed if c.startswith("tests/") and c.endswith(".py")]
    if not new_tests:
        out.append(expect("New or changed tests fail against the original code (they reproduce the findings)", False, "no test files changed"))
    else:
        tmp = tempfile.mkdtemp(prefix="sda-pristine-")
        try:
            dst = os.path.join(tmp, "repo")
            shutil.copytree(pristine, dst)
            os.symlink(os.path.realpath(os.path.join(repo, ".venv")), os.path.join(dst, ".venv"))
            for t in new_tests:
                os.makedirs(os.path.dirname(os.path.join(dst, t)), exist_ok=True)
                shutil.copy(os.path.join(repo, t), os.path.join(dst, t))
            code, tail = _run_tests(dst)
            out.append(expect("New or changed tests fail against the original code (they reproduce the findings)",
                              code != 0, "on original code: " + tail))
        finally:
            shutil.rmtree(tmp)

    report = os.path.join(repo, "docs", "audits", "system-design-audit-2026-10-08.md")
    statuses = {}
    if os.path.isfile(report):
        r = vr.validate(open(report, encoding="utf-8").read(), None)
        statuses = {f["id"]: f["status"] for f in r["findings"]}
    updated = [i for i in ("SDA-001", "SDA-002") if statuses.get(i) not in (None, "open")]
    out.append(expect("Audit report status updated for SDA-001 and SDA-002",
                      len(updated) == 2, "statuses: %s" % {i: statuses.get(i) for i in ("SDA-001", "SDA-002")}))
    others = [i for i, s in statuses.items() if i not in ("SDA-001", "SDA-002") and s != "open"]
    out.append(expect("Other findings left Open (not silently fixed or closed)", not others,
                      "changed: " + ", ".join(others) if others else "all others Open"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["audit", "fix"])
    ap.add_argument("run_dir")
    ap.add_argument("--root")
    ap.add_argument("--real-repo", action="store_true")
    ap.add_argument("--pristine")
    ap.add_argument("--baseline")
    a = ap.parse_args()
    res = audit_checks(a.run_dir, a.root, a.real_repo) if a.mode == "audit" else fix_checks(a.run_dir, a.pristine, a.baseline)
    json.dump(res, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
