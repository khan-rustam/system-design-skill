#!/usr/bin/env python3
"""Check a system design audit report against the skill's format and evidence rules.

Errors (exit 1) mean the report is wrong or unreadable:
  - a required section is missing, or a finding heading can't be parsed
  - a severity, confidence or status value is not one of the allowed values
  - a finding lacks a required field, or its Evidence cites no location
  - the findings table and the finding sections disagree (IDs, severity)
  - the summary's counts don't match the findings
  - with --root: a cited file doesn't exist, or a cited line is past its end
  - something that looks like a live secret appears in the report

Warnings are worth fixing but don't fail the check.

Usage:
    validate_report.py REPORT.md [--root AUDITED_DIR] [--json]
    validate_report.py - [--root AUDITED_DIR] [--json] < REPORT.md
Standard library only, Python 3.8+.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict

SEVERITIES = OrderedDict([("critical", "Critical"), ("normal", "Normal"),
                          ("good practice", "Good Practice")])
CONFIDENCE = {"confirmed": "Confirmed", "likely": "Likely",
              "needs verification": "Needs verification"}
STATUSES = ("open", "in progress", "fixed", "accepted risk", "won't fix",
            "wont fix", "regressed", "stale")
SCORE_RATINGS = {"strong", "adequate", "weak", "not assessed"}
LENS_KEYWORDS = ("architecture", "boundar", "data", "integrity", "reliab", "failure",
                 "concurren", "job", "async", "scal", "perform", "capacity", "api",
                 "contract", "integration", "security", "observab", "deploy",
                 "operation", "recovery", "maintain", "evolv")

REQUIRED_SECTIONS = [  # (key, prefix that the H2 title must start with)
    ("summary", "summary"),
    ("system map", "system map"),
    ("scorecard", "scorecard"),
    ("findings", "findings"),
    ("strengths", "strengths"),
    ("not assessed", "not assessed"),
]

HEADING = re.compile(
    r"^###\s+(SDA-\d{3,})\s*[·•|:—–\-]+\s*(?:[^\w\s]+\s*)?(Critical|Normal|Good Practice)\s*"
    r"[·•|:—–\-]+\s*(.+?)\s*$", re.I)
FIELD = re.compile(r"^\s*[-*]\s*\*\*([A-Za-z' ]+?)\s*:?\s*\*\*\s*:?\s*(.*)$")
TABLE_ROW = re.compile(r"^\s*\|\s*`?(SDA-\d{3,})`?\s*\|(.*)\|\s*$")

KNOWN_EXT = set("""
py js mjs cjs jsx ts tsx go rb java kt kts scala cs php rs swift m mm dart ex exs erl
clj sql sh bash zsh fish ps1 bat yml yaml json jsonc toml ini cfg conf env md mdx rst
txt html htm css scss sass less vue svelte astro tf tfvars hcl proto graphql gql xml
gradle properties service timer socket prisma lua r pl pm c cc cpp h hpp cmake mk
dockerfile j2 jinja tpl tmpl csv tsv lock example sample template ipynb tex
""".split())
EXTLESS = {"Dockerfile", "Makefile", "Procfile", "Jenkinsfile", "Gemfile", "Caddyfile",
           "Vagrantfile", "Pipfile", "crontab", "Brewfile"}
LOCATION = re.compile(
    r"(?<![\w/@:.\-])((?:\.{0,2}/)?(?:[\w.\-@+]+/)*[\w.\-@+]+):(\d+)(?:\s*[-–]\s*(\d+))?(?![\w.])")
SECTION_REF = re.compile(r"§\s*\d+(?:\.\d+)*")

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("live payment key", re.compile(r"\b(sk|rk)_live_[0-9A-Za-z]{10,}|\brzp_live_[0-9A-Za-z]{8,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}")),
    ("Slack token", re.compile(r"\bxox[abpr]-[0-9A-Za-z-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("credentials in URL", re.compile(r"://[^/\s:@`'\"<]+:(?!\*\*\*|…|<|\$\{|\{)[^@\s/`'\"]{3,}@")),
    ("password assignment", re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"](?!\*|…|<|\$\{|\{|x+['\"])[^'\"\s]{8,}['\"]")),
]


def strip_md(s):
    return re.sub(r"[*_`]", "", s).strip()


def norm(s):
    return re.sub(r"[^a-z' ]", "", strip_md(s).lower()).strip()


class Report(object):
    def __init__(self, text):
        self.lines = text.splitlines()
        self.in_code = []
        fence = None
        for line in self.lines:
            m = re.match(r"^\s*(```+|~~~+)", line)
            if m:
                tok = m.group(1)[:3]
                if fence is None:
                    fence = tok
                    self.in_code.append(True)
                    continue
                if tok == fence:
                    fence = None
                    self.in_code.append(True)
                    continue
            self.in_code.append(fence is not None)
        self.sections = OrderedDict()   # normalised title -> (start, end) line indexes
        starts = [(i, l[3:].strip()) for i, l in enumerate(self.lines)
                  if l.startswith("## ") and not self.in_code[i]]
        for n, (i, title) in enumerate(starts):
            end = starts[n + 1][0] if n + 1 < len(starts) else len(self.lines)
            self.sections[norm(title)] = (i, end)

    def section(self, prefix):
        for title, span in self.sections.items():
            if title.startswith(prefix):
                return span
        return None


def parse_findings(rep, errors, warnings):
    span = rep.section("findings")
    if not span:
        return []
    start, end = span
    findings, cur = [], None
    field_name = None
    for i in range(start + 1, end):
        line = rep.lines[i]
        if not rep.in_code[i] and line.startswith("### "):
            m = HEADING.match(line)
            if not m:
                errors.append(("heading", "line %d: can't parse finding heading %r. Use "
                               "'### SDA-001 · Critical · Title'" % (i + 1, line.strip()[:80])))
                cur = None
                continue
            cur = OrderedDict(id=m.group(1).upper(), severity=SEVERITIES[m.group(2).lower()],
                              title=m.group(3).strip(), line=i + 1, fields=OrderedDict())
            findings.append(cur)
            field_name = None
            continue
        if cur is None:
            continue
        fm = FIELD.match(line) if not rep.in_code[i] else None
        if fm:
            field_name = norm(fm.group(1))
            cur["fields"][field_name] = fm.group(2).strip()
        elif field_name:
            cur["fields"][field_name] += "\n" + line
    return findings


def parse_table(rep):
    span = rep.section("findings")
    rows = OrderedDict()
    if not span:
        return rows
    for i in range(span[0] + 1, span[1]):
        if rep.in_code[i]:
            continue
        m = TABLE_ROW.match(rep.lines[i])
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            rows[m.group(1).upper()] = cells
    return rows


def find_locations(text):
    locs = []
    for m in LOCATION.finditer(text):
        path, a, b = m.group(1), int(m.group(2)), m.group(3)
        base = os.path.basename(path)
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        if not ("/" in path or ext in KNOWN_EXT or base in EXTLESS
                or base.startswith("Dockerfile")):
            continue                      # host:port, times, versions …
        if re.match(r"^[\d.]+$", path):
            continue
        locs.append((path, a, int(b) if b else a))
    return locs


class Resolver(object):
    SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
            ".next", "vendor", "target", ".tox", "coverage"}

    def __init__(self, root):
        self.root = root
        self._index = None
        self._counts = {}

    def _all(self):
        if self._index is None:
            self._index = []
            for dp, dns, fns in os.walk(self.root):
                dns[:] = [d for d in dns if d not in self.SKIP]
                for fn in fns:
                    rel = os.path.relpath(os.path.join(dp, fn), self.root).replace(os.sep, "/")
                    self._index.append(rel)
        return self._index

    def resolve(self, path):
        """Return (relpath or None, note)."""
        clean = re.sub(r"^\./", "", path)
        if os.path.isfile(os.path.join(self.root, clean)):
            return clean, None
        matches = [r for r in self._all() if r == clean or r.endswith("/" + clean)]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return matches[0], "ambiguous: %d files end with %s" % (len(matches), clean)
        return None, None

    def line_count(self, rel):
        if rel not in self._counts:
            try:
                with open(os.path.join(self.root, rel), "rb") as fh:
                    data = fh.read()
                self._counts[rel] = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
            except OSError:
                self._counts[rel] = 0
        return self._counts[rel]


def validate(text, root=None):
    errors, warnings = [], []
    rep = Report(text)

    if not rep.lines or not rep.lines[0].startswith("# "):
        warnings.append(("title", "report should start with '# System Design Audit: <name>'"))

    positions = []
    for key, prefix in REQUIRED_SECTIONS:
        span = rep.section(prefix)
        if not span:
            errors.append(("section", "missing section '## %s'" % key.title()))
        else:
            positions.append(span[0])
    if positions != sorted(positions):
        warnings.append(("section", "sections are out of the template's order"))

    findings = parse_findings(rep, errors, warnings)
    counts = Counter(f["severity"] for f in findings)

    ids = Counter(f["id"] for f in findings)
    for fid, n in ids.items():
        if n > 1:
            errors.append((fid, "ID used by %d findings" % n))

    if (counts["Critical"] or counts["Normal"]) and not rep.section("remediation plan"):
        errors.append(("section", "missing section '## Remediation plan' (required when there are Critical or Normal findings)"))

    resolver = Resolver(root) if root else None
    for f in findings:
        fid, sev, fields = f["id"], f["severity"], f["fields"]
        required = ["lens", "confidence", "evidence", "recommendation", "status"]
        if sev != "Good Practice":
            required += ["failure scenario", "verify"]
        for name in required:
            if not strip_md(fields.get(name, "")):
                errors.append((fid, "missing field '%s'" % name.title()))
        if sev == "Good Practice" and not (fields.get("failure scenario") or fields.get("why it matters")):
            errors.append((fid, "Good Practice findings need 'Why it matters' (or a failure scenario)"))
        if "effort" not in fields:
            warnings.append((fid, "no Effort (S / M / L)"))

        conf = norm(fields.get("confidence", ""))
        conf_key = next((k for k in CONFIDENCE if conf.startswith(k)), None)
        if fields.get("confidence") and not conf_key:
            errors.append((fid, "confidence %r is not one of %s" % (strip_md(fields["confidence"])[:30], ", ".join(CONFIDENCE.values()))))
        f["confidence"] = CONFIDENCE.get(conf_key)
        if sev == "Critical" and conf_key == "needs verification":
            warnings.append((fid, "Critical with 'Needs verification': make sure the check is concrete"))

        status = norm(fields.get("status", ""))
        f["status"] = next((s for s in STATUSES if status.startswith(s)), None)
        if fields.get("status") and not f["status"]:
            errors.append((fid, "status %r must start with one of: Open, In progress, Fixed, Accepted risk, Won't fix, Regressed, Stale" % strip_md(fields["status"])[:30]))

        lens = norm(fields.get("lens", ""))
        f["lens"] = strip_md(fields.get("lens", ""))
        if lens and not any(k in lens for k in LENS_KEYWORDS):
            warnings.append((fid, "lens %r doesn't match one of the ten lenses" % f["lens"][:40]))

        evidence = fields.get("evidence", "")
        locs = find_locations(evidence)
        f["locations"] = ["%s:%d%s" % (p, a, ("-%d" % b) if b != a else "") for p, a, b in locs]
        if evidence and not locs and not SECTION_REF.search(evidence):
            errors.append((fid, "Evidence cites no location (path:line, path:start-end, or § for a document)"))
        if resolver:
            for p, a, b in locs:
                rel, note = resolver.resolve(p)
                if rel is None:
                    errors.append((fid, "Evidence cites %s:%d but no such file exists under the root" % (p, a)))
                    continue
                if note:
                    warnings.append((fid, note))
                n = resolver.line_count(rel)
                if a < 1 or b < a:
                    errors.append((fid, "Evidence cites an invalid line range %s:%d-%d" % (p, a, b)))
                elif b > n:
                    errors.append((fid, "Evidence cites %s:%d but the file has only %d lines" % (p, b, n)))

    table = parse_table(rep)
    detailed = OrderedDict((f["id"], f) for f in findings)
    for fid, cells in table.items():
        if fid not in detailed:
            errors.append((fid, "in the findings table but has no '### %s' section" % fid))
            continue
        sev_cell = norm(cells[0]) if cells else ""
        sev = next((v for k, v in SEVERITIES.items() if sev_cell.endswith(k) or sev_cell.startswith(k)), None)
        if sev != detailed[fid]["severity"]:
            errors.append((fid, "severity in table (%s) differs from its section (%s)" % (strip_md(cells[0]) if cells else "?", detailed[fid]["severity"])))
        if len(cells) >= 2 and detailed[fid].get("confidence") and not norm(cells[1]).startswith(detailed[fid]["confidence"].lower()):
            warnings.append((fid, "confidence in table differs from its section"))
        if len(cells) >= 5 and resolver:
            for p, a, b in find_locations(cells[-1]):
                rel, _ = resolver.resolve(p)
                if rel is None:
                    errors.append((fid, "table Location cites %s:%d but no such file exists" % (p, a)))
                elif b > resolver.line_count(rel):
                    errors.append((fid, "table Location cites %s:%d past the end of the file" % (p, b)))
    for fid in detailed:
        if fid not in table:
            errors.append((fid, "has a section but no row in the findings table"))

    summary = rep.section("summary")
    if summary:
        stext = "\n".join(rep.lines[summary[0]:summary[1]])
        m = re.search(r"\*\*Findings:?\*\*:?(.*)", stext)
        if not m:
            errors.append(("summary", "no '**Findings:** N Critical · N Normal · N Good Practice' line"))
        else:
            line = strip_md(m.group(1))
            for sev in SEVERITIES.values():
                cm = re.search(r"(\d+)\s*(?:[^\w\s]+\s*)?" + sev, line, re.I)
                if not cm:
                    errors.append(("summary", "Findings line has no count for %s" % sev))
                elif int(cm.group(1)) != counts[sev]:
                    errors.append(("summary", "summary says %s %s but the report has %d" % (cm.group(1), sev, counts[sev])))
        mentioned = set(re.findall(r"SDA-\d{3,}", stext))
        for fid in mentioned - set(detailed) - set(table):
            warnings.append(("summary", "%s is mentioned in the summary but not in Findings" % fid))
        for f in findings:
            if f["severity"] == "Critical" and f["id"] not in mentioned:
                warnings.append((f["id"], "Critical finding not listed in the summary's top risks"))

    score = rep.section("scorecard")
    if score:
        rows = [l for l in rep.lines[score[0] + 1:score[1]] if l.strip().startswith("|")]
        rated = 0
        for l in rows[2:] if len(rows) > 2 else []:
            cells = [c.strip() for c in l.strip().strip("|").split("|")]
            if len(cells) >= 2:
                rating = norm(cells[1])
                if rating:
                    rated += 1
                    if rating not in SCORE_RATINGS:
                        warnings.append(("scorecard", "rating %r for %r is not Strong / Adequate / Weak / Not assessed" % (strip_md(cells[1]), strip_md(cells[0]))))
        if not rated:
            warnings.append(("scorecard", "scorecard has no rated lenses"))

    for i, line in enumerate(rep.lines):
        for label, rx in SECRET_PATTERNS:
            if rx.search(line):
                errors.append(("secret", "line %d looks like it contains a %s; redact it" % (i + 1, label)))

    return OrderedDict([
        ("ok", not errors),
        ("counts", OrderedDict((s, counts[s]) for s in SEVERITIES.values())),
        ("findings", [OrderedDict((k, f.get(k)) for k in ("id", "severity", "confidence", "status", "lens", "title", "locations")) for f in findings]),
        ("errors", ["[%s] %s" % e for e in errors]),
        ("warnings", ["[%s] %s" % w for w in warnings]),
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a system design audit report.")
    ap.add_argument("report", help="report file, or - to read it from stdin")
    ap.add_argument("--root", help="audited directory: check that every cited path:line exists")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.report == "-":
            text = sys.stdin.read()
        else:
            with open(args.report, encoding="utf-8") as fh:
                text = fh.read()
    except OSError as e:
        print("cannot read report: %s" % e, file=sys.stderr)
        return 2
    if args.root and not os.path.isdir(args.root):
        print("--root is not a directory: %s" % args.root, file=sys.stderr)
        return 2
    result = validate(text, args.root)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        c = result["counts"]
        print("Report: %s" % args.report)
        print("Findings: %d Critical · %d Normal · %d Good Practice (%d total)" % (
            c["Critical"], c["Normal"], c["Good Practice"], sum(c.values())))
        for e in result["errors"]:
            print("ERROR  " + e)
        for w in result["warnings"]:
            print("WARN   " + w)
        print("Result: %s (%d errors, %d warnings)" % (
            "PASS" if result["ok"] else "FAIL", len(result["errors"]), len(result["warnings"])))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
