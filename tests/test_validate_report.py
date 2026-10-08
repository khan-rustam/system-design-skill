"""Tests for scripts/validate_report.py. Standard library only: python3 -m unittest discover tests"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skills", "system-design-audit", "scripts", "validate_report.py")
TEMPLATE = os.path.join(HERE, "..", "skills", "system-design-audit", "references", "report-template.md")
FIXTURES = os.path.join(HERE, "fixtures")
SHOP = os.path.join(FIXTURES, "shop-orders")
GOOD = os.path.join(FIXTURES, "reports", "shop-orders-audit.md")

spec = importlib.util.spec_from_file_location("validate_report", SCRIPT)
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

with open(GOOD, encoding="utf-8") as fh:
    GOOD_TEXT = fh.read()


def check(text, root=SHOP):
    return vr.validate(text, root)


def errors(text, root=SHOP):
    return "\n".join(check(text, root)["errors"])


def mutate(old, new, text=GOOD_TEXT, count=1):
    assert text.count(old) >= 1, "fixture text not found: %r" % old[:60]
    return text.replace(old, new, count)


class ValidReports(unittest.TestCase):
    def test_reference_report_passes_with_root(self):
        r = check(GOOD_TEXT)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["warnings"], [])
        self.assertEqual(dict(r["counts"]), {"Critical": 9, "Normal": 5, "Good Practice": 2})

    def test_findings_are_extracted(self):
        f = {x["id"]: x for x in check(GOOD_TEXT)["findings"]}
        self.assertEqual(f["SDA-002"]["severity"], "Critical")
        self.assertEqual(f["SDA-013"]["confidence"], "Likely")
        self.assertEqual(f["SDA-001"]["status"], "open")
        self.assertIn("app/routes/payments.py:35-47", f["SDA-001"]["locations"])

    def test_template_example_passes(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            example = re.search(r"````markdown\n(.*?)\n````", fh.read(), re.S).group(1)
        r = vr.validate(example)
        self.assertTrue(r["ok"], r["errors"])

    def test_emoji_and_dash_separators_accepted(self):
        text = mutate("### SDA-002 · Critical · Oversell", "### SDA-002 — 🔴 Critical — Oversell")
        text = mutate("| SDA-002 | Critical |", "| SDA-002 | 🔴 Critical |", text)
        self.assertTrue(check(text)["ok"], check(text)["errors"])

    def test_status_with_detail_accepted(self):
        text = mutate("- **Status:** Open\n\n### SDA-002", "- **Status:** Fixed — tests/test_webhook.py (2026-10-09)\n\n### SDA-002")
        self.assertTrue(check(text)["ok"])

    def test_heading_inside_code_block_is_not_a_finding(self):
        text = mutate("## Strengths", "```\n### SDA-999 · Critical · not a heading\n```\n\n## Strengths")
        self.assertTrue(check(text)["ok"], check(text)["errors"])

    def test_host_port_and_times_are_not_locations(self):
        text = mutate("- **Effort:** S\n- **Verify:** A test that posts",
                      "- **Effort:** S\n- **Verify:** Against db.internal.example.com:5432 and localhost:6379 at 10:30, a test that posts")
        self.assertTrue(check(text)["ok"], check(text)["errors"])

    def test_suffix_path_resolves_when_unique(self):
        text = mutate("`app/services/wallet.py:9-18` reads", "`wallet.py:9-18` reads")
        self.assertTrue(check(text)["ok"], check(text)["errors"])

    def test_ambiguous_suffix_warns(self):
        text = mutate("`app/routes/health.py:6-8` returns", "`__init__.py:1` and `app/routes/health.py:6-8` returns")
        r = check(text)
        self.assertTrue(r["ok"])
        self.assertTrue(any("ambiguous" in w for w in r["warnings"]))

    def test_document_section_reference_counts_as_evidence(self):
        text = mutate("- **Evidence:** `app/services/wallet.py:9-18` reads the balance,",
                      "- **Evidence:** design §4.2 says the balance is read,")
        self.assertTrue(check(text)["ok"], check(text)["errors"])


class BrokenReports(unittest.TestCase):
    def test_missing_section(self):
        text = mutate("## Strengths", "## What we liked")
        self.assertIn("missing section '## Strengths'", errors(text))

    def test_missing_remediation_plan_when_critical(self):
        text = GOOD_TEXT.split("## Remediation plan")[0]
        self.assertIn("Remediation plan", errors(text))

    def test_unknown_severity_heading(self):
        text = mutate("### SDA-011 · Normal · Order history", "### SDA-011 · High · Order history")
        e = errors(text)
        self.assertIn("can't parse finding heading", e)

    def test_table_and_section_disagree(self):
        text = mutate("| SDA-011 | Normal |", "| SDA-011 | Critical |")
        self.assertIn("severity in table (Critical) differs from its section (Normal)", errors(text))

    def test_finding_missing_from_table(self):
        text = re.sub(r"^\| SDA-016 .*\n", "", GOOD_TEXT, flags=re.M)
        self.assertIn("[SDA-016] has a section but no row", errors(text))

    def test_table_row_without_section(self):
        text = mutate("| SDA-016 |", "| SDA-017 | Good Practice | Confirmed | Observability | ghost | `app/db.py:1` |\n| SDA-016 |")
        self.assertIn("[SDA-017] in the findings table but has no", errors(text))

    def test_summary_count_mismatch(self):
        text = mutate("**Findings:** 9 Critical", "**Findings:** 8 Critical")
        self.assertIn("summary says 8 Critical but the report has 9", errors(text))

    def test_missing_summary_counts_line(self):
        text = mutate("**Findings:** 9 Critical · 5 Normal · 2 Good Practice", "Lots of findings.")
        self.assertIn("no '**Findings:**", errors(text))

    def test_duplicate_ids(self):
        text = mutate("### SDA-016 · Good Practice", "### SDA-015 · Good Practice")
        self.assertIn("ID used by 2 findings", errors(text))

    def test_invalid_confidence(self):
        text = mutate("- **Confidence:** Likely — depends", "- **Confidence:** Pretty sure — depends")
        self.assertIn("confidence 'Pretty sure", errors(text))

    def test_invalid_status(self):
        text = mutate("- **Status:** Open\n\n### SDA-002", "- **Status:** Done\n\n### SDA-002")
        self.assertIn("status 'Done' must start with one of", errors(text))

    def test_critical_needs_failure_scenario_and_verify(self):
        block = GOOD_TEXT.split("### SDA-004")[1].split("### SDA-005")[0]
        cut = re.sub(r"- \*\*Failure scenario:\*\*.*?(?=- \*\*Recommendation)", "", block, flags=re.S)
        cut = re.sub(r"- \*\*Verify:\*\*.*?(?=- \*\*Status)", "", cut, flags=re.S)
        e = errors(GOOD_TEXT.replace(block, cut))
        self.assertIn("[SDA-004] missing field 'Failure Scenario'", e)
        self.assertIn("[SDA-004] missing field 'Verify'", e)

    def test_good_practice_needs_why(self):
        text = mutate("- **Why it matters:** A missing variable", "- **Note:** A missing variable")
        self.assertIn("[SDA-016] Good Practice findings need 'Why it matters'", errors(text))

    def test_evidence_without_location(self):
        text = mutate("- **Evidence:** `app/routes/orders.py:52-60` returns every order",
                      "- **Evidence:** the order list handler returns every order")
        self.assertIn("[SDA-011] Evidence cites no location", errors(text))

    def test_cited_file_does_not_exist(self):
        text = mutate("`app/routes/orders.py:52-60` returns", "`app/routes/order_history.py:52-60` returns")
        self.assertIn("app/routes/order_history.py:52 but no such file exists", errors(text))

    def test_cited_line_past_end_of_file(self):
        text = mutate("`app/routes/health.py:6-8` returns", "`app/routes/health.py:6-80` returns")
        self.assertIn("app/routes/health.py:80 but the file has only 8 lines", errors(text))

    def test_table_location_checked_too(self):
        text = mutate("| `app/routes/health.py:6` |", "| `app/routes/health.py:600` |")
        self.assertIn("table Location cites app/routes/health.py:600 past the end", errors(text))

    def test_without_root_paths_are_not_checked(self):
        text = mutate("`app/routes/orders.py:52-60` returns", "`app/routes/order_history.py:52-60` returns")
        self.assertTrue(vr.validate(text, None)["ok"])


class SecretGuard(unittest.TestCase):
    def test_live_secrets_fail(self):
        for leak in ("postgres://shop:Sup3rS3cret@db.prod:5432/shop",
                     "AKIAIOSFODNN7EXAMPLE",
                     'password = "correct-horse-battery"',
                     "rzp_live_AbCdEf123456",
                     "-----BEGIN RSA PRIVATE KEY-----"):
            with self.subTest(leak=leak[:20]):
                text = mutate("## Strengths", "Found: %s\n\n## Strengths" % leak)
                self.assertIn("looks like it contains", errors(text))

    def test_redacted_values_pass(self):
        for ok in ("postgres://shop:***@db.prod:5432/shop",
                   "postgres://shop:${DB_PASSWORD}@db/shop",
                   'password = "<redacted>"'):
            with self.subTest(ok=ok):
                text = mutate("## Strengths", "Found: %s\n\n## Strengths" % ok)
                self.assertTrue(check(text)["ok"], check(text)["errors"])


class Cli(unittest.TestCase):
    def run_cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, input=stdin)

    def test_pass_exit_code(self):
        p = self.run_cli(GOOD, "--root", SHOP)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("Result: PASS", p.stdout)

    def test_fail_exit_code(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(GOOD_TEXT.replace("## Strengths", "## Nice things"))
        try:
            p = self.run_cli(fh.name)
            self.assertEqual(p.returncode, 1)
            self.assertIn("Result: FAIL", p.stdout)
        finally:
            os.unlink(fh.name)

    def test_json_output(self):
        p = self.run_cli(GOOD, "--json")
        self.assertIn('"ok": true', p.stdout)

    def test_unreadable_report_is_usage_error(self):
        self.assertEqual(self.run_cli("/no/such/report.md").returncode, 2)

    def test_report_from_stdin(self):
        p = self.run_cli("-", "--root", SHOP, stdin=GOOD_TEXT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Result: PASS", p.stdout)

    def test_broken_report_from_stdin_fails(self):
        p = self.run_cli("-", stdin=GOOD_TEXT.replace("## Strengths", "## Nice things"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("Result: FAIL", p.stdout)


if __name__ == "__main__":
    unittest.main()
