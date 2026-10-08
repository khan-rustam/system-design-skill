"""Tests for scripts/recon.py. Standard library only: python3 -m unittest discover tests"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skills", "system-design-audit", "scripts", "recon.py")
FIXTURES = os.path.join(HERE, "fixtures")

spec = importlib.util.spec_from_file_location("recon", SCRIPT)
recon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recon)


def run_json(path, *extra):
    out = subprocess.run([sys.executable, SCRIPT, path, "--json"] + list(extra),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def hits(report, signal_id):
    for s in report["signals"]:
        if s["id"] == signal_id:
            return {"%s:%d" % (h["path"], h["line"]) for h in s["hits"]}
    raise KeyError(signal_id)


class ScratchRepo(object):
    """A throwaway directory of files, for single-signal tests."""

    def __init__(self, files):
        self.files = files

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="sda-recon-")
        for rel, text in self.files.items():
            path = os.path.join(self.dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(textwrap.dedent(text).lstrip("\n"))
        return self.dir

    def __exit__(self, *exc):
        shutil.rmtree(self.dir)


class ShopOrdersInventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_json(os.path.join(FIXTURES, "shop-orders"))

    def test_stack_from_requirements(self):
        stack = self.r["stack"]
        self.assertIn("flask", stack["web framework"])
        self.assertIn("psycopg2-binary", stack["database / ORM"])
        self.assertIn("apscheduler", stack["queue / jobs / scheduler"])
        self.assertIn("requests", stack["HTTP client"])

    def test_datastore_detected(self):
        self.assertIn("PostgreSQL", [d["name"] for d in self.r["datastores"]])

    def test_runtime_shape_and_deploy_files(self):
        hints = " ".join(h["where"] for h in self.r["runtime_hints"])
        self.assertIn("gunicorn.conf.py:2", hints)
        kinds = {d["path"]: d["kind"] for d in self.r["deploy"]}
        self.assertIn("systemd unit", kinds["deploy/shop.service"])
        self.assertIn("Deploy / ops script", kinds["deploy/backup.sh"])

    def test_background_and_migrations(self):
        self.assertIn({"kind": "In-process scheduler", "where": "app/__init__.py:32"}, self.r["background"])
        self.assertEqual(self.r["migrations"][0]["dir"], "migrations")
        self.assertEqual(self.r["migrations"][0]["files"], 2)

    def test_config_key_names_only(self):
        keys = self.r["config_keys"]
        for k in ("DATABASE_URL", "PAYGATE_WEBHOOK_SECRET", "SMTP_HOST"):
            self.assertIn(k, keys)

    def test_seeded_signals_fire(self):
        expect = {
            "http-no-timeout": "app/services/shipping.py:13",
            "smtp-no-timeout": "app/services/emailer.py:22",
            "db-no-timeout": "app/db.py:11",
            "sql-increment": "app/jobs/reconcile.py:9",
            "read-modify-write": "app/services/wallet.py:11",
            "float-money": "app/routes/orders.py:33",
            "in-process-scheduler": "app/__init__.py:32",
            "webhook-handler": "app/routes/payments.py:21",
            "health-endpoint": "app/routes/health.py:6",
            "backup-unverified": "deploy/backup.sh:6",
            "deploy-self-update": "deploy/deploy.sh:5",
            "env-fallback": "app/config.py:14",
        }
        for sid, loc in expect.items():
            self.assertIn(loc, hits(self.r, sid), sid)
        self.assertIn("migrations/002_wallets.sql:3", hits(self.r, "float-money"))
        self.assertIn("app/routes/orders.py:38", hits(self.r, "read-modify-write"))

    def test_no_false_alarm_where_code_is_correct(self):
        self.assertNotIn("app/services/paygate.py:12", hits(self.r, "http-no-timeout"))
        self.assertFalse(any(h.startswith("app/routes/payments.py") for h in hits(self.r, "swallowed-error")))

    def test_tests_are_not_scanned_for_signals(self):
        for s in self.r["signals"]:
            for h in s["hits"]:
                self.assertFalse(h["path"].startswith("tests/"), h)


class NotifyHubInventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_json(os.path.join(FIXTURES, "notify-hub"))

    def test_node_stack_and_stores(self):
        stack = self.r["stack"]
        self.assertIn("express", stack["web framework"])
        self.assertIn("bullmq", stack["queue / jobs / scheduler"])
        stores = [d["name"] for d in self.r["datastores"]]
        self.assertIn("PostgreSQL", stores)
        self.assertIn("Redis", stores)

    def test_replicas_found(self):
        hints = [h["where"] for h in self.r["runtime_hints"]]
        self.assertIn("k8s/api.yaml:6", hints)
        self.assertIn("k8s/worker.yaml:6", hints)

    def test_seeded_signals_fire(self):
        self.assertIn("src/quota.ts:5", hits(self.r, "process-local-state"))
        self.assertIn("src/queue.ts:12", hits(self.r, "queue-drops-failures"))
        self.assertIn("src/queue.ts:10", hits(self.r, "retry-fixed-delay"))
        self.assertIn("src/providers/textblast.ts:31", hits(self.r, "retry-fixed-delay"))
        self.assertIn("src/api/providerWebhooks.ts:7", hits(self.r, "webhook-handler"))
        self.assertIn("src/config.ts:4", hits(self.r, "env-fallback"))

    def test_timeouts_that_exist_are_respected(self):
        self.assertEqual(hits(self.r, "http-no-timeout"), set())   # fetch uses AbortSignal.timeout
        self.assertEqual(hits(self.r, "db-no-timeout"), set())     # pg Pool sets connectionTimeoutMillis

    def test_url_credentials_redacted(self):
        line = [h for s in self.r["signals"] for h in s["hits"] if h["path"] == "src/config.ts" and h["line"] == 3][0]
        self.assertIn("notify:***@", line["text"])


class LedgerCleanInventory(unittest.TestCase):
    """The well-designed control: few leads, and the controls are recognised."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_json(os.path.join(FIXTURES, "ledger-clean"))

    def test_controls_recognised(self):
        counts = {c["name"]: c["count"] for c in self.r["controls"]}
        for name in ("Idempotency keys", "Row locks (FOR UPDATE / SKIP LOCKED)",
                     "Advisory / distributed locks", "Outbox / reconciliation", "Explicit timeouts"):
            self.assertGreater(counts[name], 0, name)

    def test_quiet_where_it_should_be(self):
        for sid in ("http-no-timeout", "db-no-timeout", "float-money", "in-process-scheduler",
                    "backup-unverified", "unbounded-query", "read-modify-write"):
            self.assertEqual(hits(self.r, sid), set(), sid)

    def test_bookkeeping_counters_are_not_leads(self):
        self.assertIn("app/api/transfers.py:89", hits(self.r, "sql-increment"))  # a real lead, to verify and clear
        texts = [h["text"] for s in self.r["signals"] if s["id"] == "sql-increment" for h in s["hits"]]
        self.assertFalse([t for t in texts if "attempts = attempts" in t])

    def test_cronjob_is_background_work(self):
        self.assertIn("k8s CronJob", [b["kind"] for b in self.r["background"]])


class DocOnlyFolder(unittest.TestCase):
    def test_runs_on_a_folder_with_no_code(self):
        r = run_json(os.path.join(FIXTURES, "dispatch-design"))
        self.assertEqual(r["manifests"], [])
        self.assertEqual(sum(s["count"] for s in r["signals"]), 0)
        self.assertIn("ride-dispatch-design.md", r["docs"])


class Safety(unittest.TestCase):
    def test_env_files_never_read_and_examples_print_names_only(self):
        with ScratchRepo({
            ".env": "DATABASE_URL=postgres://admin:TopSecretValue1@db/prod\n",
            ".env.example": "API_TOKEN=example-token-value\n",
            "app.py": "import os\nDB = os.environ['DATABASE_URL']\n",
        }) as d:
            out = subprocess.run([sys.executable, SCRIPT, d], capture_output=True, text=True, check=True).stdout
            self.assertNotIn("TopSecretValue1", out)
            self.assertNotIn("example-token-value", out)
            self.assertIn("API_TOKEN", out)
            self.assertIn("DATABASE_URL", out)

    def test_command_line_secrets_redacted(self):
        with ScratchRepo({"ops/restore.sh": """
            mysql -uroot -pHunter2Secret shop < dump.sql
            PGPASSWORD=AnotherSecret psql -h db -U app
            """}) as d:
            r = run_json(d)
            text = json.dumps(r)
            self.assertNotIn("Hunter2Secret", text)
            self.assertNotIn("AnotherSecret", text)
            self.assertEqual(len(hits(r, "secret-on-command-line")), 2)

    def test_env_file_classifier(self):
        for name in (".env", ".env.local", ".env.production"):
            self.assertTrue(recon.is_env_secret_file(name), name)
        for name in (".env.example", ".env.sample", ".env.template", "env.py", "environment.ts"):
            self.assertFalse(recon.is_env_secret_file(name), name)

    def test_secret_literal_masked_but_env_name_kept(self):
        self.assertEqual(recon.redact('API_KEY = os.getenv("API_KEY", "sk-real-looking-value")'),
                         'API_KEY = os.getenv("API_KEY", "…")')

    def test_paths_with_dash_p_are_not_mangled(self):
        self.assertEqual(recon.redact("cd /srv/my-project && ./run"), "cd /srv/my-project && ./run")

    def test_read_only(self):
        src = os.path.join(FIXTURES, "shop-orders")
        before = sorted((p, os.path.getmtime(os.path.join(dp, p))) for dp, _, fs in os.walk(src) for p in fs)
        run_json(src)
        after = sorted((p, os.path.getmtime(os.path.join(dp, p))) for dp, _, fs in os.walk(src) for p in fs)
        self.assertEqual(before, after)


class SignalPrecision(unittest.TestCase):
    """Each case: a line that must fire, and a near-miss that must not."""

    CASES = [
        ("upsert-adds-incoming", "q.sql",
         "INSERT INTO t (k, n) SELECT k, count(*) FROM s GROUP BY k\nON CONFLICT (k) DO UPDATE SET n = t.n + EXCLUDED.n;\n",
         "INSERT INTO t (k, n) VALUES (1, 2)\nON CONFLICT (k) DO UPDATE SET n = EXCLUDED.n;\n"),
        ("swallowed-error", "a.py",
         "try:\n    save()\nexcept Exception:\n    pass\n",
         "try:\n    save()\nexcept Exception:\n    log.exception('save failed')\n    raise\n"),
        ("swallowed-error", "a.ts",
         "doThing().catch(() => {});\n",
         "doThing().catch((err) => logger.error(err));\n"),
        ("float-money", "m.sql",
         "CREATE TABLE p (unit_price DOUBLE PRECISION NOT NULL);\n",
         "CREATE TABLE p (unit_price NUMERIC(10, 2) NOT NULL, weight REAL);\n"),
        ("process-local-state", "s.py",
         "_rate_limits = {}\n",
         "RATE_LIMITS = {'free': 10, 'pro': 100}\n"),
        ("fire-and-forget", "f.py",
         "import threading\nthreading.Thread(target=work, daemon=True).start()\n",
         "import threading\nt = threading.Thread(target=work)\nt.start()\nt.join()\n"),
        ("unbounded-query", "u.py",
         "rows = db.execute(\"SELECT id, email FROM users\")\n",
         "rows = db.execute(\"SELECT id, email FROM users WHERE org_id = %s LIMIT 50\", (org,))\n"),
        ("destructive-migration", "migrations/003.sql",
         "ALTER TABLE orders DROP COLUMN legacy_total;\n",
         "ALTER TABLE orders ADD COLUMN note TEXT;\n"),
        ("tls-verify-off", "c.py",
         "requests.get(url, timeout=5, verify=False)\n",
         "requests.get(url, timeout=5)\n"),
        ("query-in-loop", "n.py",
         "for user in users:\n    cur.execute('SELECT * FROM orders WHERE user_id = %s', (user.id,))\n",
         "for table in ('a', 'b'):\n    cur.execute('ANALYZE ' + table)\n"),
        ("retry-fixed-delay", "r.py",
         "for attempt in range(5):\n    try:\n        return call()\n    except Exception:\n        time.sleep(2)\n",
         "for attempt in range(5):\n    try:\n        return call()\n    except Exception:\n        time.sleep(min(30, 2 ** attempt))\n"),
        ("http-no-timeout", "h.py",
         "resp = requests.get(url)\n",
         "resp = requests.get(\n    url,\n    timeout=(3, 10),\n)\n"),
        ("deploy-self-update", "deploy.sh",
         "set -e\ngit reset --hard origin/main\nsystemctl restart app\n",
         "set -e\nif [ -z \"$REEXEC\" ]; then git reset --hard origin/main; REEXEC=1 exec \"$0\" \"$@\"; fi\nsystemctl restart app\n"),
        ("backup-unverified", "backup.sh",
         "pg_dump app > /backups/app.sql\n",
         "set -euo pipefail\npg_dump -Fc app -f /backups/app.dump.part\npg_restore -l /backups/app.dump.part > /dev/null\n"),
        ("hardcoded-host", "client.py",
         "BASE = 'http://10.0.3.17:8080/api'\n",
         "BASE = os.environ['API_BASE']  # e.g. http://10.0.3.17:8080/api\n"),
    ]

    def test_cases(self):
        for sid, name, positive, negative in self.CASES:
            with self.subTest(signal=sid, case="fires"):
                with ScratchRepo({name: positive}) as d:
                    self.assertTrue(hits(run_json(d), sid), "%s should fire on %s" % (sid, name))
            with self.subTest(signal=sid, case="quiet"):
                with ScratchRepo({name: negative}) as d:
                    self.assertFalse(hits(run_json(d), sid), "%s should not fire on the near-miss" % sid)

    def test_comment_lines_ignored(self):
        with ScratchRepo({"a.py": "# requests.get(url)  -- old approach\n"}) as d:
            self.assertFalse(hits(run_json(d), "http-no-timeout"))

    def test_every_signal_has_lens_and_check(self):
        for s in recon.SIGNALS:
            self.assertTrue(s.lens and s.title and s.check.endswith(("?", ".")), s.id)


class Cli(unittest.TestCase):
    def test_text_output_states_signals_are_leads(self):
        out = subprocess.run([sys.executable, SCRIPT, os.path.join(FIXTURES, "shop-orders")],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("leads to verify, not findings", out)
        self.assertIn("## Repositories", out)

    def test_max_hits_caps_output(self):
        out = subprocess.run([sys.executable, SCRIPT, os.path.join(FIXTURES, "shop-orders"), "--max-hits", "1"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("more (use --all)", out)

    def test_size_line_does_not_pair_all_files_with_code_lines(self):
        # notify-hub: 20 files in all, but only 15 of them (TypeScript + SQL) are counted as code.
        out = subprocess.run([sys.executable, SCRIPT, os.path.join(FIXTURES, "notify-hub")],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("20 files in total; 316 lines of code in 15 source files", out)

    def test_bad_path_is_a_usage_error(self):
        proc = subprocess.run([sys.executable, SCRIPT, "/no/such/dir"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_exclude(self):
        r = run_json(os.path.join(FIXTURES, "shop-orders"), "--exclude", "deploy/*")
        self.assertFalse(any(d["path"].startswith("deploy/") for d in r["deploy"]))


if __name__ == "__main__":
    unittest.main()
