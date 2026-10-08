#!/usr/bin/env python3
"""Map a codebase for a system design audit.

Prints an inventory of the system (repositories and their git state,
languages, stack, data stores, entry points, background work, deploy and
runtime files, migrations, config key names, tests, docs, the safety
controls already present) followed by risk signals: places where known
failure patterns often live.

Signals are LEADS TO VERIFY, never findings. A quiet signal list proves
nothing; most design defects only show up by reading the code.

Read-only. Never opens real .env files and never prints secret-looking
values. Standard library only, Python 3.8+.

Usage:
    recon.py PATH [--json] [--max-hits N] [--all] [--exclude GLOB ...]
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections import Counter, OrderedDict, defaultdict

# ---------------------------------------------------------------- walking

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", ".venv", "venv",
    "env", ".env", "virtualenv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".nox", "site-packages", "dist", "build", "out",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", ".parcel-cache",
    "coverage", ".coverage", "htmlcov", "vendor", "target", ".gradle", ".idea",
    ".vscode", ".terraform", "Pods", ".dart_tool", ".expo", "generated",
    "__generated__", ".serverless", ".aws-sam", "tmp", ".tmp",
}
SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock",
    "Gemfile.lock", "pubspec.lock", "uv.lock",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".zip",
    ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".class",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".pyc", ".pyo",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi",
    ".webm", ".wav", ".ogg", ".psd", ".ai", ".sketch", ".fig", ".xlsx", ".xls",
    ".docx", ".doc", ".pptx", ".db", ".sqlite", ".sqlite3", ".parquet",
    ".avro", ".pkl", ".npy", ".npz", ".h5", ".onnx", ".pt", ".map",
}
MAX_FILE_BYTES = 1500000

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".go": "Go", ".rb": "Ruby", ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".cs": "C#",
    ".php": "PHP", ".rs": "Rust", ".swift": "Swift", ".dart": "Dart",
    ".ex": "Elixir", ".exs": "Elixir", ".sql": "SQL", ".sh": "Shell",
    ".bash": "Shell", ".vue": "Vue", ".svelte": "Svelte", ".tf": "Terraform",
    ".prisma": "Prisma", ".c": "C", ".cpp": "C++", ".h": "C/C++",
}
CODE_EXT = {
    ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rb",
    ".java", ".kt", ".scala", ".cs", ".php", ".rs", ".swift", ".dart", ".ex",
    ".exs", ".vue", ".svelte",
}
PY = {".py"}
JS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"}
SQL_EXT = {".sql"}
SHELL_EXT = {".sh", ".bash", ".zsh"}
CONFIG_EXT = {
    ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf", ".service",
    ".timer", ".env.example",
}
TEXT_EXT = CODE_EXT | SQL_EXT | SHELL_EXT | CONFIG_EXT | {
    ".md", ".txt", ".prisma", ".tf", ".hcl", ".gradle", ".xml",
    ".properties", ".socket", ".cron", ".crontab", ".j2", ".tpl",
}
TEXT_NAMES = {
    "Dockerfile", "Makefile", "Procfile", "Jenkinsfile", "Gemfile",
    "Pipfile", "crontab", "Caddyfile", "Vagrantfile", "Brewfile",
}

SECRET_ENV_FILE = re.compile(r"^\.env(\..+)?$")
ENV_EXAMPLE_FILE = re.compile(r"^\.env\.(example|sample|template|dist|defaults)$|^env\.example$")


def is_env_secret_file(name):
    """Real .env files hold secrets; only examples are read."""
    return bool(SECRET_ENV_FILE.match(name)) and not ENV_EXAMPLE_FILE.match(name)


TEST_PARTS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "cypress",
              "testdata", "fixtures", "__mocks__", "testing"}


def is_test_path(rel):
    parts = rel.split("/")
    if any(p in TEST_PARTS for p in parts[:-1]):
        return True
    name = parts[-1]
    return bool(re.match(r"(test_.*\.py|.*_test\.(py|go|rb)|conftest\.py|"
                         r".*\.(test|spec)\.[cm]?[jt]sx?|.*Test\.(java|kt|cs))$", name))


def is_ui_path(rel):
    """Browser-side code: HTTP-timeout and scheduler signals don't apply."""
    if "/api/" in "/" + rel or rel.startswith("api/"):
        return False
    ext = os.path.splitext(rel)[1]
    if ext in {".tsx", ".jsx", ".vue", ".svelte"}:
        return True
    parts = rel.split("/")
    return any(p in {"components", "hooks", "public", "styles", "client",
                     "frontend-components", "ui"} for p in parts[:-1])


class FileInfo(object):
    __slots__ = ("rel", "abs", "ext", "name", "size", "_lines")

    def __init__(self, rel, abspath, size):
        self.rel = rel
        self.abs = abspath
        self.name = os.path.basename(rel)
        self.ext = ".env.example" if ENV_EXAMPLE_FILE.match(self.name) else os.path.splitext(self.name)[1].lower()
        self.size = size
        self._lines = None

    @property
    def is_text(self):
        return (self.ext in TEXT_EXT or self.name in TEXT_NAMES
                or self.name.startswith("Dockerfile")) and self.size <= MAX_FILE_BYTES

    def lines(self):
        if self._lines is None:
            if not self.is_text:
                self._lines = []
            else:
                try:
                    with open(self.abs, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    if "\x00" in text[:4096]:
                        self._lines = []
                    else:
                        self._lines = text.splitlines()
                except OSError:
                    self._lines = []
        return self._lines

    def text(self):
        return "\n".join(self.lines())


def walk(root, excludes):
    files = []
    repos = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        if ".git" in dirnames or ".git" in filenames:
            repos.append(rel_dir or ".")
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info")
            and not _excluded((rel_dir + "/" + d).lstrip("/"), excludes)
        )
        for fn in sorted(filenames):
            if fn in SKIP_FILES or is_env_secret_file(fn):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in BINARY_EXT or fn.endswith((".min.js", ".min.css", ".bundle.js")):
                continue
            rel = (rel_dir + "/" + fn).lstrip("/")
            if _excluded(rel, excludes):
                continue
            abspath = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(abspath)
            except OSError:
                continue
            files.append(FileInfo(rel, abspath, size))
    return files, repos


def _excluded(rel, excludes):
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(os.path.basename(rel), g)
               for g in excludes)


# -------------------------------------------------------------------- git

def _git(repo, *args):
    try:
        out = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                             text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_state(root, rel):
    path = os.path.join(root, rel)
    info = OrderedDict(path=rel)
    info["branch"] = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if info["branch"] is None:
        info["error"] = "not readable by git"
        return info
    info["head"] = _git(path, "rev-parse", "--short", "HEAD")
    status = _git(path, "status", "--porcelain")
    info["dirty_files"] = len(status.splitlines()) if status else 0
    info["last_commit"] = _git(path, "log", "-1", "--format=%cs")
    upstream = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    info["upstream"] = upstream
    if upstream:
        counts = _git(path, "rev-list", "--left-right", "--count", "HEAD..." + upstream)
        if counts:
            ahead, behind = counts.split()
            info["ahead"], info["behind"] = int(ahead), int(behind)
    default = _git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if not default:
        for cand in ("origin/main", "origin/master"):
            if _git(path, "rev-parse", "--verify", "--quiet", cand):
                default = cand
                break
    info["default_branch"] = default
    if default and default != upstream:
        counts = _git(path, "rev-list", "--left-right", "--count", "HEAD..." + default)
        if counts:
            ahead, behind = counts.split()
            info["vs_default"] = {"ahead": int(ahead), "behind": int(behind)}
    return info


# -------------------------------------------------------------- manifests

def _strip_req(line):
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http")):
        return None
    m = re.match(r"([A-Za-z0-9_.\-]+)", line)
    return m.group(1).lower().replace("_", "-") if m else None


def parse_manifest(f):
    """Return (ecosystem, set(dep names)) or None."""
    name, text = f.name, None
    try:
        if name == "package.json":
            text = f.text()
            data = json.loads(text)
            deps = set()
            for key in ("dependencies", "devDependencies", "peerDependencies",
                        "optionalDependencies"):
                deps.update(k.lower() for k in (data.get(key) or {}))
            return "node", deps
        if re.match(r"requirements.*\.(txt|in)$", name):
            return "python", {d for d in map(_strip_req, f.lines()) if d}
        if name == "pyproject.toml":
            deps = set()
            text = f.text()
            for block in re.findall(r"dependencies\s*=\s*\[(.*?)\]", text, re.S):
                for q in re.findall(r"[\"']([^\"']+)[\"']", block):
                    d = _strip_req(q)
                    if d:
                        deps.add(d)
            for section in re.findall(r"\[tool\.poetry\.(?:dev-)?dependencies\](.*?)(?:\n\[|\Z)", text, re.S):
                for key in re.findall(r"^([A-Za-z0-9_.\-]+)\s*=", section, re.M):
                    if key.lower() != "python":
                        deps.add(key.lower().replace("_", "-"))
            return "python", deps
        if name == "Pipfile":
            deps = set()
            section = None
            for line in f.lines():
                s = line.strip()
                if s.startswith("["):
                    section = s
                elif section in ("[packages]", "[dev-packages]") and "=" in s:
                    deps.add(s.split("=", 1)[0].strip().strip('"').lower())
            return "python", deps
        if name == "go.mod":
            deps = set(re.findall(r"^\s*(?:require\s+)?([a-z0-9.\-]+\.[a-z]+/[^\s]+)\s+v", f.text(), re.M))
            return "go", {d.lower() for d in deps}
        if name == "Cargo.toml":
            m = re.search(r"\[dependencies\](.*?)(?:\n\[|\Z)", f.text(), re.S)
            keys = re.findall(r"^([A-Za-z0-9_\-]+)\s*=", m.group(1), re.M) if m else []
            return "rust", {k.lower() for k in keys}
        if name == "Gemfile":
            return "ruby", {g.lower() for g in re.findall(r"^\s*gem\s+['\"]([^'\"]+)", f.text(), re.M)}
        if name == "composer.json":
            data = json.loads(f.text())
            return "php", {k.lower() for k in list((data.get("require") or {})) + list((data.get("require-dev") or {}))}
        if name == "pom.xml":
            return "java", {a.lower() for a in re.findall(r"<artifactId>([^<]+)</artifactId>", f.text())}
        if name in ("build.gradle", "build.gradle.kts"):
            return "java", {d.split(":")[1].lower() for d in re.findall(r"['\"]([\w.\-]+:[\w.\-]+)(?::[^'\"]*)?['\"]", f.text())}
        if name == "pubspec.yaml":
            m = re.search(r"^dependencies:(.*?)(?:^\S|\Z)", f.text(), re.S | re.M)
            return "dart", set(re.findall(r"^\s{2}([a-z0-9_]+):", m.group(1), re.M)) if m else set()
        if name.endswith(".csproj"):
            return "dotnet", {p.lower() for p in re.findall(r'PackageReference\s+Include="([^"]+)"', f.text())}
    except (ValueError, IndexError, AttributeError):
        return None
    return None


MANIFEST_NAMES = re.compile(
    r"^(package\.json|requirements.*\.(txt|in)|pyproject\.toml|Pipfile|go\.mod|"
    r"Cargo\.toml|Gemfile|composer\.json|pom\.xml|build\.gradle(\.kts)?|"
    r"pubspec\.yaml|.*\.csproj)$")

# category -> names; a trailing "*" means prefix match
DEP_CATEGORIES = OrderedDict([
    ("web framework", "flask django fastapi starlette aiohttp tornado sanic falcon bottle quart "
                      "express koa fastify @nestjs/core next nuxt hapi @hapi/hapi remix @remix-run/node "
                      "github.com/gin-gonic/gin github.com/labstack/echo* github.com/gofiber/fiber* "
                      "spring-boot-starter-web rails sinatra laravel/framework actix-web axum rocket"),
    ("database / ORM", "psycopg2 psycopg2-binary psycopg psycopg-pool asyncpg sqlalchemy sqlmodel "
                       "django-environ pymongo motor mongoengine mysqlclient pymysql peewee tortoise-orm "
                       "pg pg-promise postgres mysql mysql2 mongodb mongoose @prisma/client prisma typeorm "
                       "sequelize knex drizzle-orm kysely better-sqlite3 sqlite3 @supabase/supabase-js "
                       "supabase gorm.io/gorm github.com/jackc/pgx* github.com/lib/pq activerecord "
                       "spring-boot-starter-data-jpa diesel sqlx cassandra-driver @elastic/elasticsearch "
                       "elasticsearch"),
    ("cache / KV", "redis redis-py ioredis aioredis node-cache lru-cache memcached pymemcache "
                   "flask-caching django-redis @upstash/redis cachetools"),
    ("queue / jobs / scheduler", "celery rq dramatiq huey arq apscheduler schedule kombu pika aio-pika "
                                 "kafka-python confluent-kafka aiokafka bullmq bull bee-queue agenda "
                                 "node-cron cron node-schedule amqplib kafkajs @aws-sdk/client-sqs "
                                 "sidekiq resque good_job temporalio @temporalio/client inngest "
                                 "graphile-worker pg-boss"),
    ("HTTP client", "requests httpx aiohttp urllib3 axios node-fetch got undici superagent ky "
                    "github.com/go-resty/resty*"),
    ("payments", "stripe razorpay braintree paypalrestsdk @paypal/checkout-server-sdk adyen "
                 "@adyen/api-library squareup square mollie paddle-sdk"),
    ("email / SMS / push", "sendgrid @sendgrid/mail nodemailer twilio mailgun postmark resend "
                           "flask-mail django-anymail firebase-admin web-push @aws-sdk/client-ses "
                           "@aws-sdk/client-sns"),
    ("auth", "pyjwt jsonwebtoken jose python-jose passport next-auth @auth/core authlib flask-login "
             "flask-jwt-extended django-allauth djangorestframework-simplejwt @clerk/nextjs "
             "@auth0/nextjs-auth0 bcrypt bcryptjs argon2 argon2-cffi passlib better-auth lucia"),
    ("observability", "sentry-sdk @sentry/node @sentry/nextjs prometheus-client prom-client "
                      "opentelemetry* @opentelemetry/* ddtrace dd-trace newrelic structlog winston "
                      "pino loguru elastic-apm"),
    ("cloud SDK / storage", "boto3 botocore aws-sdk @aws-sdk/* google-cloud-* @google-cloud/* azure-* "
                            "@azure/* minio cloudinary"),
    ("testing", "pytest pytest-asyncio hypothesis jest vitest mocha chai ava playwright "
                "@playwright/test cypress supertest testcontainers"),
])


def categorize(deps):
    out = OrderedDict()
    for cat, names in DEP_CATEGORIES.items():
        hits = []
        for token in names.split():
            if token.endswith("*"):
                pre = token[:-1]
                hits.extend(sorted(d for d in deps if d.startswith(pre)))
            elif token in deps:
                hits.append(token)
        if hits:
            out[cat] = sorted(set(hits))
    return out


DATASTORE_RULES = [
    ("PostgreSQL", r"^(psycopg|psycopg2|psycopg2-binary|psycopg-pool|asyncpg|pg|pg-promise|postgres|github\.com/jackc/pgx.*|github\.com/lib/pq)$", r"postgres|postgis|timescale|supabase/postgres", r"POSTGRES|^PG[A-Z_]*$|DATABASE_URL"),
    ("MySQL / MariaDB", r"^(mysqlclient|pymysql|mysql|mysql2|aiomysql)$", r"mysql|mariadb", r"MYSQL"),
    ("MongoDB", r"^(pymongo|motor|mongoengine|mongodb|mongoose)$", r"\bmongo", r"MONGO"),
    ("SQLite", r"^(better-sqlite3|sqlite3|aiosqlite)$", r"$^", r"SQLITE"),
    ("Redis", r"^(redis|ioredis|aioredis|django-redis|@upstash/redis|flask-caching)$", r"\bredis|valkey|keydb", r"REDIS"),
    ("Memcached", r"^(memcached|pymemcache)$", r"memcached", r"MEMCACHE"),
    ("RabbitMQ / AMQP", r"^(pika|aio-pika|amqplib|kombu)$", r"rabbitmq", r"AMQP|RABBIT"),
    ("Kafka", r"^(kafka-python|confluent-kafka|aiokafka|kafkajs)$", r"kafka|redpanda", r"KAFKA"),
    ("Elasticsearch / OpenSearch", r"^(elasticsearch|@elastic/elasticsearch|opensearch-py)$", r"elasticsearch|opensearch", r"ELASTIC|OPENSEARCH"),
    ("Object storage (S3 / Blob / GCS)", r"^(boto3|minio|@aws-sdk/client-s3|azure-storage-blob|@azure/storage-blob|google-cloud-storage)$", r"minio", r"S3_|_BUCKET|BLOB|AZURE_STORAGE|GCS_"),
]


# ------------------------------------------------------------ inventory

BACKGROUND_RULES = [
    ("Celery task", PY, r"@(\w+\.)?(task|shared_task)\b"),
    ("In-process scheduler", PY, r"\b(BackgroundScheduler|AsyncIOScheduler|BlockingScheduler)\(|\bschedule\.every\("),
    ("Thread / task spawn", PY, r"threading\.Thread\(|\b(asyncio\.)?(create_task|ensure_future)\("),
    ("RQ / Dramatiq / Huey job", PY, r"@(dramatiq\.actor|huey\.(task|periodic_task))|\bQueue\(.*connection="),
    ("Queue worker (BullMQ/Bull)", JS, r"new\s+(Worker|Queue|QueueScheduler)\(|\.process\(\s*['\"\w]"),
    ("Cron in code", JS, r"\bcron\.schedule\(|new\s+CronJob\(|schedule\.scheduleJob\("),
    ("Interval timer", JS, r"\bsetInterval\("),
]
BACKGROUND_FILE_RULES = [
    ("systemd timer", lambda f: f.ext == ".timer"),
    ("k8s CronJob", lambda f: f.ext in {".yml", ".yaml"} and re.search(r"^kind:\s*CronJob", f.text(), re.M)),
    ("crontab", lambda f: f.name in {"crontab", "cron"} or f.ext in {".cron", ".crontab"}),
    ("CI schedule", lambda f: f.rel.startswith(".github/workflows/") and re.search(r"^\s*schedule:", f.text(), re.M)),
]

DEPLOY_RULES = [
    ("Dockerfile", lambda f: f.name.startswith("Dockerfile") or f.name.endswith(".dockerfile")),
    ("Docker Compose", lambda f: re.match(r"(docker-)?compose.*\.ya?ml$", f.name)),
    ("Procfile", lambda f: f.name == "Procfile"),
    ("PM2 config", lambda f: re.match(r"ecosystem\.config\.(c?js|json|ya?ml)$", f.name)),
    ("systemd unit", lambda f: f.ext in {".service", ".timer", ".socket"}),
    ("nginx / proxy config", lambda f: "nginx" in f.rel.lower() and f.ext in {".conf", ""} or f.name == "Caddyfile"),
    ("Kubernetes manifest", lambda f: f.ext in {".yml", ".yaml"} and re.search(r"^kind:\s*(Deployment|StatefulSet|DaemonSet|Service|Ingress|CronJob|Job)\b", f.text(), re.M)),
    ("Helm chart", lambda f: f.name == "Chart.yaml"),
    ("Terraform", lambda f: f.ext == ".tf"),
    ("Serverless / PaaS config", lambda f: f.name in {"serverless.yml", "serverless.yaml", "fly.toml", "render.yaml", "vercel.json", "netlify.toml", "app.yaml", "railway.json", "railway.toml", "Caddyfile", "app.json", "heroku.yml"}),
    ("CI/CD pipeline", lambda f: f.rel.startswith(".github/workflows/") or f.name in {".gitlab-ci.yml", "Jenkinsfile", "bitbucket-pipelines.yml", "azure-pipelines.yml", ".travis.yml"} or f.rel.startswith(".circleci/")),
    ("Deploy / ops script", lambda f: f.ext in SHELL_EXT and re.search(r"deploy|release|rollout|backup|restore|migrate|provision|bootstrap", f.name, re.I)),
    ("Makefile", lambda f: f.name == "Makefile"),
    ("App server config", lambda f: f.name in {"gunicorn.conf.py", "gunicorn_config.py", "uwsgi.ini", "supervisord.conf", "Caddyfile", "puma.rb", "unicorn.rb"}),
]

RUNTIME_HINTS = re.compile(
    r"(\bworkers\s*[=:]\s*\S+|--workers[\"'\s,=]*\d+|\bgunicorn\b.*[\"'\s]-w[\"'\s,]*\d+|WEB_CONCURRENCY\S*|"
    r"\binstances\s*:\s*\S+|exec_mode\s*:\s*\S+|\breplicas\s*:\s*\d+|--concurrency[= ]\s*\d+|"
    r"\bcelery\b.*\s-c\s*\d+|\bthreads\s*[=:]\s*\d+|--threads[= ]\s*\d+|worker_class\s*=\s*\S+|"
    r"\bnumprocs\s*=\s*\d+|\bconcurrency\s*:\s*\d+)", re.I)

ENTRY_NAMES = re.compile(
    r"^(main|app|wsgi|asgi|manage|server|index|run|worker|cli|application|Program)\.(py|js|mjs|ts|go|cs)$")
ENTRY_CODE = re.compile(
    r"^if __name__ == ['\"]__main__['\"]|\bapp\.listen\(|\buvicorn\.run\(|\bapp\.run\(|"
    r"^func main\(\)|@SpringBootApplication|\bserve\(\s*app|createServer\(")

CONFIG_KEY_PATTERNS = [
    re.compile(r"os\.(?:getenv|environ\.get)\(\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']"),
    re.compile(r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*\]"),
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]{2,})"),
    re.compile(r"process\.env\[\s*[\"'`]([A-Z][A-Z0-9_]{2,})[\"'`]\s*\]"),
    re.compile(r"\benv\(\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']"),
    re.compile(r"os\.Getenv\(\s*\"([A-Z][A-Z0-9_]{2,})\""),
    re.compile(r"ENV(?:\.fetch\(|\[)\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']"),
    re.compile(r"System\.getenv\(\s*\"([A-Z][A-Z0-9_]{2,})\""),
    re.compile(r"Environment\.GetEnvironmentVariable\(\s*\"([A-Z][A-Z0-9_]{2,})\""),
]

DOC_NAMES = re.compile(
    r"^(README|ARCHITECTURE|DESIGN|CLAUDE|AGENTS|CONTRIBUTING|RUNBOOK|OPERATIONS|DEPLOY(MENT)?|"
    r"SECURITY|ONBOARDING|SYSTEM)[\w\-]*\.(md|rst|txt)$", re.I)

CONTROLS = [
    ("Idempotency keys", r"idempoten"),
    ("Upserts / ON CONFLICT", r"ON\s+CONFLICT|ON\s+DUPLICATE\s+KEY|\bupsert\b"),
    ("Row locks (FOR UPDATE / SKIP LOCKED)", r"FOR\s+UPDATE|SKIP\s+LOCKED|with_for_update|select_for_update|\.forUpdate\(|pessimistic_write"),
    ("Advisory / distributed locks", r"pg_(try_)?advisory|\bredlock\b|\bsetnx\b|\bSET\b.*\bNX\b|acquire_lock|\block_key\b"),
    ("Transactions", r"transaction\.atomic|\.begin\(\)|\bBEGIN\b|\$transaction|\.transaction\(|@Transactional|session\.begin|conn\.transaction|db\.transaction"),
    ("Unique constraints", r"\bUNIQUE\b|unique\s*=\s*True|unique\s*:\s*true|@unique|@@unique|unique_together|UniqueConstraint"),
    ("Explicit timeouts", r"\btimeout\s*[=:]|connect_timeout|statement_timeout|AbortSignal\.timeout|tcp_user_timeout|keepalives"),
    ("Retry with backoff", r"\bbackoff\b|\bjitter\b|tenacity|exponential"),
    ("Signature verification", r"compare_digest|timingSafeEqual|constructEvent|verify_?signature|verifySignature|validate_?signature|createHmac|hmac\.new"),
    ("Rate limiting", r"rate_?limit|RateLimit|\blimiter\b|throttl"),
    ("Outbox / reconciliation", r"\boutbox\b|reconcil"),
    ("Circuit breakers", r"circuit.?breaker|\bbreaker\b"),
    ("Dead-letter handling", r"dead.?letter|\bDLQ\b|deadLetter"),
    ("Health / readiness endpoints", r"['\"`]/(health|healthz|healthcheck|ready|readyz|livez)\b"),
    ("Structured logging / correlation IDs", r"structlog|pino|winston|request_id|requestId|correlation_?id|X-Request-ID"),
]


# --------------------------------------------------------------- signals

class Signal(object):
    def __init__(self, sid, lens, title, check, pattern, exts=None, unless=None,
                 window=0, back=0, skip_ui=False, path_re=None, file_requires=None,
                 file_unless=None, predicate=None, flags=0):
        self.id = sid
        self.lens = lens
        self.title = title
        self.check = check
        self.pattern = re.compile(pattern, flags)
        self.exts = exts
        self.unless = re.compile(unless, flags | re.I) if unless else None
        self.window = window
        self.back = back
        self.skip_ui = skip_ui
        self.path_re = re.compile(path_re, re.I) if path_re else None
        self.file_requires = re.compile(file_requires, re.I | re.M) if file_requires else None
        self.file_unless = re.compile(file_unless, re.I | re.M) if file_unless else None
        self.predicate = predicate

    def applies(self, f):
        if self.exts is not None and f.ext not in self.exts and not (
                "shell" in self.exts and (f.name == "Makefile" or f.name.startswith("Dockerfile"))):
            return False
        if self.path_re and not self.path_re.search(f.rel):
            return False
        if self.skip_ui and is_ui_path(f.rel):
            return False
        return True


def _indent(line):
    return len(line) - len(line.lstrip())


def _py_swallow(lines, i):
    """`except …:` whose whole body is pass / continue / ..."""
    if re.search(r":\s*(pass|continue|\.\.\.)\s*(#.*)?$", lines[i]):
        return True
    indent = _indent(lines[i])
    body = []
    for line in lines[i + 1:i + 10]:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if _indent(line) <= indent:
            break
        body.append(s)
    return len(body) == 1 and body[0] in ("pass", "continue", "...")


def _loop_with_query(lines, i):
    line = lines[i]
    if re.search(r"\bin\s+(\(|\[|[A-Z][A-Z0-9_]+\s*:)|\bof\s+\[", line):
        return False                     # iterating a literal or a CONSTANT: bounded
    indent = _indent(line)
    q = re.compile(r"\.execute\(|\.query\(|\.objects\.(get|filter)\(|session\.(get|query|execute)\(|"
                   r"\.fetchone\(|\bcursor\.|await\s+(\w+\.)*(query|findUnique|findFirst|findOne|"
                   r"findById|execute)\(|await\s+(db|prisma|knex|pool|client|sql)\b")
    for nxt in lines[i + 1:i + 5]:
        if not nxt.strip():
            continue
        if _indent(nxt) <= indent and not line.rstrip().endswith(("{", "(")):
            return False
        if q.search(nxt):
            return True
    return False


def _sleep_in_retry(lines, i):
    ctx = "\n".join(lines[max(0, i - 20):i + 3])
    return bool(re.search(r"retr|attempt|tries", ctx, re.I)) and not re.search(
        r"\*\*|backoff|jitter|random|\*\s*2\b|\b2\s*\*|exponential|<<|\*=|Math\.pow", ctx, re.I)


BOOKKEEPING = re.compile(r"attempt|retr|tries|version|views|hits|seq|position|sort|rank", re.I)


def _business_increment(lines, i):
    """An in-place increment of something other than a retry/version counter."""
    names = re.findall(r"\b(\w+)\s*=\s*(?:\w+\.)?\1\s*[+-]", lines[i])
    return any(not BOOKKEEPING.search(n) for n in names)


MONEY = r"(price|amount|balance|cost|fee|subtotal|tax|refund|payout|charge|wallet|revenue|payment)"
MONEY_NAME = r"\b\w*" + MONEY + r"\w*"

SIGNALS = [
    Signal("http-no-timeout", "Reliability", "Outbound HTTP call with no timeout",
           "Can this call hang a worker or a pool slot forever? Is a default timeout set on a shared session/client elsewhere?",
           r"\brequests\.(get|post|put|patch|delete|head|request)\(|\burlopen\(", exts=PY,
           unless=r"timeout", window=8),
    Signal("http-no-timeout", "Reliability", "Outbound HTTP call with no timeout",
           "Can this call hang a request or job? Is a timeout set on an axios instance or a fetch wrapper?",
           r"\baxios\.(get|post|put|patch|delete|request)\(|\baxios\(|\bgot(\.(get|post|put|patch|delete))?\(|(?<![\w.])fetch\(",
           exts=JS, unless=r"timeout|signal|AbortSignal|AbortController", window=8, skip_ui=True),
    Signal("smtp-no-timeout", "Reliability", "SMTP connection with no timeout",
           "A stalled mail server blocks the caller indefinitely. Is mail sent on a request or job path?",
           r"smtplib\.SMTP(_SSL)?\(", exts=PY, unless=r"timeout", window=3),
    Signal("db-no-timeout", "Reliability", "Database connection or pool built without visible timeouts",
           "Look for connect_timeout / statement_timeout / keepalives in the DSN or options. A half-open pooled connection can block forever.",
           r"\bpsycopg2?\.connect\(|\bpsycopg\.connect\(|\b(Simple|Threaded)?ConnectionPool\(|\bcreate_engine\(|\basyncpg\.(connect|create_pool)\(|\bpymysql\.connect\(",
           exts=PY, unless=r"timeout|keepalive|pool_pre_ping|statement_timeout", window=8),
    Signal("db-no-timeout", "Reliability", "Database connection or pool built without visible timeouts",
           "Look for connectionTimeoutMillis / statement_timeout / query_timeout in the pool options.",
           r"new\s+(Pool|Client)\(", exts=JS, file_requires=r"""(from\s+['"]pg['"]|require\(\s*['"]pg['"]\s*\))""",
           unless=r"timeout", window=8),
    Signal("swallowed-error", "Reliability", "Exception swallowed with no handling",
           "What does the caller end up with? A swallowed failure on a write or delivery can hide a 100% failure rate.",
           r"^\s*except\b[^:]*:", exts=PY, predicate=_py_swallow),
    Signal("swallowed-error", "Reliability", "Exception swallowed with no handling",
           "What does the caller end up with? Empty catches hide failures on writes and deliveries.",
           r"catch\s*(\([^)]*\))?\s*\{\s*\}|\.catch\(\s*\(?\s*\w*\s*\)?\s*=>\s*(\{\s*\}|null|undefined|void 0)\s*\)",
           exts=JS),
    Signal("upsert-adds-incoming", "Data", "Upsert adds the incoming value to the stored one",
           "Idempotent only if the incoming value is a delta and the statement runs exactly once per event. A recount here inflates on every re-run.",
           r"=\s*[\w.\"]+\s*\+\s*(EXCLUDED|excluded)\.\w+|=\s*[\w.`\"]+\s*\+\s*VALUES\(",
           exts=CODE_EXT | SQL_EXT),
    Signal("sql-increment", "Concurrency", "In-place counter/balance increment",
           "Atomic, but is the path guaranteed to run once per event? Retries, duplicate deliveries and overlapping schedules double-apply it.",
           r"\bSET\b[^;]*?\b(\w+)\s*=\s*(\w+\.)?\1\s*[+-]\s*", exts=CODE_EXT | SQL_EXT, flags=re.I,
           predicate=_business_increment),
    Signal("sql-increment", "Concurrency", "In-place counter/balance increment",
           "Atomic, but is the path guaranteed to run once per event? Retries, duplicate deliveries and overlapping schedules double-apply it.",
           r"^\s*(SET\s+)?(\w+)\s*=\s*(\w+\.)?\2\s*\+\s*[\w.%$(]", exts=CODE_EXT | SQL_EXT, flags=re.I,
           predicate=lambda lines, i: bool(re.search(r"\bUPDATE\b|\bSET\b|DO\s+UPDATE", "\n".join(lines[max(0, i - 3):i + 1]), re.I))
           and not re.search(r"\bSET\b[^;]*?\b(\w+)\s*=\s*(\w+\.)?\1\s*[+-]", lines[i], re.I)
           and _business_increment(lines, i)),
    Signal("read-modify-write", "Concurrency", "Quantity computed in application code and written back",
           "Two concurrent requests lose an update unless the row is locked or the write is conditional.",
           r"\.(balance|stock|quantity|qty|inventory|credits?|points|seats|available\w*)\s*[-+]=|"
           r"\b(new_?balance|new_?stock|updated_?balance|remaining_?stock)\s*=\s*[\w.\[\]'\"]+\s*[-+]|"
           r"[(,\[]\s*[\w.]*\[?[\"']?(balance|stock|quantity|inventory|credits?|points|seats)[\"']?\]?\s*[-+]\s*\w+",
           exts=PY | JS | {".rb", ".php", ".java", ".kt", ".cs", ".go"},
           predicate=lambda lines, i: bool(re.search(
               r"\.(balance|stock|quantity|qty|inventory|credits?|points|seats|available\w*)\s*[-+]=|new_?balance|new_?stock|updated_?balance|remaining_?stock", lines[i]))
           or bool(re.search(r"execute|query|UPDATE|\.update\(|save\(", "\n".join(lines[max(0, i - 3):i + 1]), re.I))),
    Signal("float-money", "Data", "Money held in a binary float",
           "Does this value feed charges, invoices, ledgers or tax? Floats can't represent cents exactly.",
           MONEY_NAME + r"\s*(:\s*(float|Optional\[float\])\b|=\s*float\(|=\s*(db\.)?Column\(\s*(db\.)?Float|=\s*models\.FloatField)|"
           r"float\([^)]*" + MONEY + r"|parseFloat\([^)]*" + MONEY + r"|" + MONEY_NAME + r"\s+(Float|REAL|DOUBLE( PRECISION)?|float)\b\s*(\(\d+(,\s*\d+)?\))?\s*(DEFAULT\b|NOT\s+NULL|NULL\b|@|,|\)|;|$)",
           exts=PY | JS | SQL_EXT | {".prisma", ".go", ".java", ".kt", ".cs", ".rb", ".php"}, flags=re.I),
    Signal("in-process-scheduler", "Concurrency", "Scheduler started inside an application process",
           "How many processes run this (workers x instances)? Each one runs the job unless a lock or a single dedicated process guarantees one.",
           r"\b(BackgroundScheduler|AsyncIOScheduler)\(|\bschedule\.every\(|\.add_job\(", exts=PY),
    Signal("in-process-scheduler", "Concurrency", "Scheduler started inside an application process",
           "How many processes run this? Each instance runs its own copy of the schedule.",
           r"\bcron\.schedule\(|new\s+CronJob\(|\bsetInterval\(|schedule\.scheduleJob\(", exts=JS, skip_ui=True),
    Signal("fire-and-forget", "Concurrency", "Fire-and-forget thread or task",
           "Work in flight is lost on restart or deploy, and its errors vanish. Is the result needed?",
           r"threading\.Thread\(.*daemon\s*=\s*True|^\s*(asyncio\.)?(create_task|ensure_future)\(", exts=PY),
    Signal("process-local-state", "Scalability", "Module-level mutable state used as a cache, counter or registry",
           "Correct only with exactly one process. With several workers or instances each has its own copy, and it resets on deploy.",
           r"^[A-Za-z_]\w*(cache|session|sessions|count|counts|counter|counters|limit|limits|quota|quotas|usage|seen|processed|inflight|pending|store|registry)\w*\s*(:\s*[^=]+)?=\s*(\{\}|dict\(|\[\]|set\(|defaultdict\(|OrderedDict\(|TTLCache\(|LRUCache\(|Counter\()",
           exts=PY, flags=re.I),
    Signal("process-local-state", "Scalability", "Module-level mutable state used as a cache, counter or registry",
           "Correct only with exactly one process. With several instances each has its own copy, and it resets on deploy.",
           r"^(export\s+)?(const|let|var)\s+\w*(cache|session|sessions|count|counts|counter|counters|limit|limits|quota|quotas|usage|seen|processed|inflight|pending|store|registry)\w*\s*=\s*(new\s+(Map|Set)\s*(<[^()]*>)?\(\s*\)|\{\}|\[\])\s*;?\s*$",
           exts=JS, skip_ui=True, flags=re.I),
    Signal("unbounded-query", "Scalability", "Query with no visible bound",
           "Is the table bounded by design, or does this grow with users or time? Is the result paginated?",
           r"\.objects\.all\(\)|\.findMany\(\s*\)|\.find\(\s*\{\s*\}\s*\)|[\"'`]\s*SELECT\s+.+\s+FROM\s+\w+",
           exts=CODE_EXT, unless=r"\bWHERE\b|\bLIMIT\b|\bFETCH\b|\bOFFSET\b|COUNT\(|EXISTS|\bTOP\b|paginat|\[:\d+\]|slice\(",
           window=3, flags=re.I),
    Signal("env-fallback", "Operations", "Config silently falls back to a literal when an env var is missing",
           "In production a missing variable boots with this default instead of failing at startup.",
           r"os\.(getenv|environ\.get)\(\s*[\"'][A-Z0-9_]*(URL|URI|HOST|DSN|DATABASE|REDIS|SECRET|KEY|TOKEN|PASSWORD|PASS|ENDPOINT|BUCKET)[A-Z0-9_]*[\"']\s*,\s*(?!None\s*\))[^)\s]",
           exts=PY),
    Signal("env-fallback", "Operations", "Config silently falls back to a literal when an env var is missing",
           "In production a missing variable boots with this default instead of failing at startup.",
           r"process\.env\.[A-Z0-9_]*(URL|URI|HOST|DSN|DATABASE|REDIS|SECRET|KEY|TOKEN|PASSWORD|PASS|ENDPOINT|BUCKET)[A-Z0-9_]*\s*(\|\||\?\?)\s*[\"'`]",
           exts=JS),
    Signal("hardcoded-host", "Operations", "Environment-specific host or IP baked into code",
           "Does this differ between environments? It belongs in config.",
           r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|(\d{1,3}\.){3}\d{1,3})\b",
           exts=CODE_EXT, unless=r"getenv|environ|process\.env|example", window=0),
    Signal("webhook-handler", "API & contracts", "Inbound webhook / callback handler",
           "Check: signature verified with a constant-time compare; event ID deduplicated by a unique constraint; fast ack with async work; replays and out-of-order events handled.",
           r"@[\w.]+\.(route|post|put|api_route)\(\s*[rf]?[\"'][^\"']*(webhook|callback|notify|ipn|/hooks?\b)",
           exts=PY, flags=re.I),
    Signal("webhook-handler", "API & contracts", "Inbound webhook / callback handler",
           "Check: signature verified with a constant-time compare; event ID deduplicated by a unique constraint; fast ack with async work; replays and out-of-order events handled.",
           r"\.(post|all|put)\(\s*[\"'`][^\"'`]*(webhook|callback|notify|ipn|/hooks?\b)",
           exts=JS, flags=re.I),
    Signal("webhook-handler", "API & contracts", "Inbound webhook / callback handler",
           "Check: signature verified with a constant-time compare; event ID deduplicated by a unique constraint; fast ack with async work; replays and out-of-order events handled.",
           r"export\s+(async\s+)?function\s+POST\b|export\s+const\s+POST\b",
           exts=JS, path_re=r"(webhook|callback|ipn|hooks?)[^/]*/"),
    Signal("health-endpoint", "Operations", "Health / readiness endpoint",
           "What does it prove? Can it hang on a shared pool? Can it pass while the core path is broken? Does deploy verification rely on it?",
           r"[\"'`]/(health|healthz|healthcheck|ready|readyz|live|livez|ping)[\"'`/]",
           exts=CODE_EXT, flags=re.I),
    Signal("destructive-migration", "Data", "Destructive or locking schema change",
           "Does code from the previous release still read this during the deploy? Is it expand/contract? Will it lock a large table?",
           r"\bDROP\s+(TABLE|COLUMN)\b|\bRENAME\s+(COLUMN|TO)\b|\bALTER\s+COLUMN\s+\S+\s+(SET\s+DATA\s+)?TYPE\b|\bTRUNCATE\b|op\.drop_(column|table)|\.(removeColumn|dropTable|renameColumn)\(",
           path_re=r"migrat|alembic|prisma/|\.sql$|db/schema", flags=re.I),
    Signal("retry-fixed-delay", "Reliability", "Retry loop with a fixed delay",
           "Fixed-interval retries against a struggling upstream become a retry storm. Exponential backoff with jitter, and only for retryable errors?",
           r"\btime\.sleep\(\s*\d+(\.\d+)?\s*\)|\bawait\s+(asyncio\.)?sleep\(\s*\d+(\.\d+)?\s*\)", exts=PY,
           predicate=_sleep_in_retry),
    Signal("retry-fixed-delay", "Reliability", "Retry loop with a fixed delay",
           "Fixed-interval retries against a struggling upstream become a retry storm. Exponential backoff with jitter, and only for retryable errors?",
           r"\bawait\s+(\w+\.)?(sleep|delay|wait|setTimeoutPromise)\(\s*\d[\d_]*\s*\)", exts=JS,
           predicate=_sleep_in_retry),
    Signal("retry-fixed-delay", "Reliability", "Queue retries configured without backoff",
           "Retries without backoff hammer the dependency that is failing. Is the handler idempotent under these retries?",
           r"\battempts\s*:\s*\d+", exts=JS, unless=r"backoff", window=4, back=4),
    Signal("tls-verify-off", "Security", "TLS certificate verification disabled",
           "Is this call carrying credentials or data across a network you don't control?",
           r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|NODE_TLS_REJECT_UNAUTHORIZED|_create_unverified_context|CERT_NONE",
           exts=CODE_EXT | CONFIG_EXT | SHELL_EXT),
    Signal("secret-on-command-line", "Security", "Credential passed on a command line or in a URL",
           "Process lists, shell history and sudo/audit logs record arguments. Read secrets from a file or stdin instead.",
           r"--password[= ]\S|\bPGPASSWORD=\S|\bsshpass\s+-p\b|\bmysql(dump)?\b.*\s-p\S|://[^/\s:@'\"]+:[^@\s/'\"]+@",
           exts={".sh", ".bash", ".zsh", ".yml", ".yaml", ".service", ".timer", ".cron", "shell"}),
    Signal("deploy-self-update", "Operations", "Script updates the git tree it runs from",
           "Bash reads scripts incrementally: when this replaces the script, the old body keeps running and newly added steps are silently skipped once. Re-exec after updating.",
           r"\bgit\s+(reset\s+--hard|pull|checkout|merge)\b", exts=SHELL_EXT,
           file_requires=r"restart|systemctl|pm2|docker|supervisorctl|deploy",
           file_unless=r"exec\s+[\"']?\$0|exec\s+bash\s+[\"']?\$0|exec\s+[\"']?\$\{?BASH_SOURCE"),
    Signal("backup-unverified", "Operations", "Database dump whose success is never checked",
           "A failed dump can still leave a (0-byte) file while cron reports success. Is the archive verified before old copies are rotated out?",
           r"\b(pg_dump|pg_dumpall|mysqldump|mongodump|mariadb-dump)\b",
           exts=SHELL_EXT | {".yml", ".yaml", ".cron", ".service", "shell"},
           file_unless=r"pg_restore\s+(-l|--list)|pipefail|\$\?|PIPESTATUS|\btest\s+-s\b|\[\s+-s\b|gunzip\s+-t|--verify|verify_backup"),
    Signal("queue-drops-failures", "Observability", "Queue discards failed jobs",
           "Failed jobs disappear with no dead-letter and no alert. Who notices a job that always fails?",
           r"removeOnFail\s*:\s*true", exts=JS),
    Signal("query-in-loop", "Scalability", "Query inside a loop (possible N+1)",
           "How many iterations on a real account? Can it be one query with IN / JOIN?",
           r"^\s*for\s+[\w, ()]+\s+in\s+|for\s*\(\s*(const|let|var)\s+\w+\s+of\b|\.forEach\(|\.map\(\s*async",
           exts=PY | JS, predicate=_loop_with_query),
]

COMMENT_LINE = re.compile(r"^\s*(#(?!!)|//|/\*|\*|--\s|<!--)")
SECRET_LITERAL = re.compile(
    r"(secret|passw|pwd|token|api[_-]?key|private[_-]?key|credential|dsn|bearer)\w*['\"\]]*\s*(=|:|,|\()\s*[rbf]?['\"`]", re.I)
URL_CREDS = re.compile(r"(://[^/\s:@'\"]+):([^@\s/'\"]+)@")
QUOTED = re.compile(r"([\"'`])(?:(?!\1).){4,}\1")


def _mask_quoted(m):
    inner = m.group(0)[1:-1]
    if re.match(r"^[A-Z][A-Z0-9_]*$", inner):      # an env var name, not a value
        return m.group(0)
    return m.group(1) + "…" + m.group(1)


def redact(text):
    """Make a source line safe to print: no credentials, no secret-ish literals."""
    text = URL_CREDS.sub(r"\1:***@", text.strip())
    text = re.sub(r"(--password[= ]|PGPASSWORD=|sshpass\s+-p\s*)\S+", r"\1***", text)
    if re.search(r"\bmysql(dump)?\b", text):
        text = re.sub(r"(?<=\s)-p\S+", "-p***", text)
    if SECRET_LITERAL.search(text):
        text = QUOTED.sub(_mask_quoted, text)
    return text[:160] + ("…" if len(text) > 160 else "")


def scan_signals(files):
    hits = OrderedDict()
    for sig in SIGNALS:
        hits.setdefault(sig.id, {"signal": sig, "hits": []})
    for f in files:
        if is_test_path(f.rel):
            continue
        lines = None
        for sig in SIGNALS:
            if not sig.applies(f):
                continue
            if lines is None:
                lines = f.lines()
                if not lines:
                    break
                text = None
            if sig.file_requires or sig.file_unless:
                text = text if text is not None else "\n".join(lines)
                if sig.file_requires and not sig.file_requires.search(text):
                    continue
                if sig.file_unless and sig.file_unless.search(text):
                    continue
            for i, line in enumerate(lines):
                if len(line) > 2000 or COMMENT_LINE.match(line) or not sig.pattern.search(line):
                    continue
                if sig.unless is not None:
                    ctx = "\n".join(lines[max(0, i - sig.back):i + sig.window + 1])
                    if sig.unless.search(ctx):
                        continue
                if sig.predicate is not None and not sig.predicate(lines, i):
                    continue
                hits[sig.id]["hits"].append({"path": f.rel, "line": i + 1, "text": redact(line)})
    out = []
    for sid, entry in hits.items():
        sig = entry["signal"]
        uniq, seen = [], set()
        for h in sorted(entry["hits"], key=lambda h: (h["path"].count("/"), h["path"], h["line"])):
            key = (h["path"], h["line"])
            if key not in seen:
                seen.add(key)
                uniq.append(h)
        out.append(OrderedDict([("id", sid), ("lens", sig.lens), ("title", sig.title),
                                ("check", sig.check), ("count", len(uniq)), ("hits", uniq)]))
    return out


# --------------------------------------------------------------- assemble

def build(root, excludes):
    files, repo_dirs = walk(root, excludes)
    report = OrderedDict()
    report["root"] = os.path.abspath(root)
    report["repos"] = [git_state(root, r) for r in repo_dirs]

    lang_files, lang_lines = Counter(), Counter()
    total_lines = 0
    for f in files:
        lang = LANG_BY_EXT.get(f.ext)
        if lang and f.is_text:
            n = len(f.lines())
            lang_files[lang] += 1
            lang_lines[lang] += n
            total_lines += n
    report["files"] = len(files)
    report["code_lines"] = total_lines
    report["languages"] = [OrderedDict(language=l, files=lang_files[l], lines=lang_lines[l])
                           for l, _ in lang_lines.most_common()]

    all_deps = set()
    manifests = []
    for f in files:
        if MANIFEST_NAMES.match(f.name) and not is_test_path(f.rel):
            parsed = parse_manifest(f)
            if parsed:
                eco, deps = parsed
                all_deps |= deps
                manifests.append(OrderedDict(path=f.rel, ecosystem=eco, dependencies=len(deps),
                                             stack=categorize(deps)))
    report["manifests"] = manifests
    report["stack"] = categorize(all_deps)

    config_keys = set()
    for f in files:
        if f.ext in CODE_EXT and not is_test_path(f.rel):
            for line in f.lines():
                for pat in CONFIG_KEY_PATTERNS:
                    config_keys.update(pat.findall(line))
        elif f.ext == ".env.example":
            for line in f.lines():
                m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})\s*=", line)
                if m:
                    config_keys.add(m.group(1))
    report["config_keys"] = sorted(config_keys)

    compose_text = "\n".join(f.text() for f in files if re.match(r"(docker-)?compose.*\.ya?ml$", f.name))
    images = re.findall(r"^\s*image:\s*['\"]?([^\s'\"]+)", compose_text, re.M)
    stores = []
    for name, dep_re, img_re, env_re in DATASTORE_RULES:
        ev = ["dependency " + d for d in sorted(all_deps) if re.match(dep_re, d)]
        ev += ["compose image " + i for i in images if re.search(img_re, i, re.I)]
        ev += ["config " + k for k in sorted(config_keys) if re.search(env_re, k)][:3]
        if ev:
            stores.append(OrderedDict(name=name, evidence=ev[:6]))
    report["datastores"] = stores

    entries = []
    for f in files:
        if is_test_path(f.rel):
            continue
        if ENTRY_NAMES.match(f.name) and f.rel.count("/") <= 3:
            entries.append(f.rel)
        elif f.ext in CODE_EXT and f.rel.count("/") <= 4:
            for i, line in enumerate(f.lines()):
                if ENTRY_CODE.search(line):
                    entries.append("%s:%d" % (f.rel, i + 1))
                    break
        if f.name == "package.json":
            try:
                scripts = json.loads(f.text()).get("scripts") or {}
            except ValueError:
                scripts = {}
            for key in ("start", "serve", "worker", "dev", "cron", "jobs", "queue"):
                if key in scripts:
                    entries.append("%s script %s: %s" % (f.rel, key, redact(str(scripts[key]))))
        if f.name == "Procfile":
            entries.extend("Procfile: " + redact(l) for l in f.lines() if l.strip())
    report["entry_points"] = sorted(set(entries))

    bg = []
    for f in files:
        if is_test_path(f.rel):
            continue
        for label, check in BACKGROUND_FILE_RULES:
            if check(f):
                bg.append(OrderedDict(kind=label, where=f.rel))
        for label, exts, pat in BACKGROUND_RULES:
            if f.ext in exts and not (f.ext in JS and is_ui_path(f.rel)):
                for i, line in enumerate(f.lines()):
                    if re.search(pat, line):
                        bg.append(OrderedDict(kind=label, where="%s:%d" % (f.rel, i + 1)))
    report["background"] = bg

    deploy, runtime = [], []
    for f in files:
        kinds = [label for label, check in DEPLOY_RULES if check(f)]
        if kinds:
            deploy.append(OrderedDict(kind=", ".join(kinds), path=f.rel))
            for i, line in enumerate(f.lines()):
                if RUNTIME_HINTS.search(line) and not line.strip().startswith("#"):
                    runtime.append(OrderedDict(where="%s:%d" % (f.rel, i + 1), text=redact(line)))
    report["deploy"] = deploy
    report["runtime_hints"] = runtime

    mig = defaultdict(list)
    for f in files:
        parts = f.rel.split("/")
        mdirs = [i for i, p in enumerate(parts[:-1]) if re.match(r"(migrations?|alembic|versions|migrate|db_migrations)$", p, re.I)]
        if mdirs and f.ext in {".sql", ".py", ".js", ".ts", ".rb", ".go"} and f.name != "__init__.py":
            mig["/".join(parts[:mdirs[-1] + 1])].append(f.name)
        elif re.match(r"V\d+(_\d+)*__.+\.sql$", f.name):
            mig[os.path.dirname(f.rel) or "."].append(f.name)
    report["migrations"] = [OrderedDict(dir=d, files=len(n), latest=sorted(n)[-1]) for d, n in sorted(mig.items())]

    tests = [f for f in files if is_test_path(f.rel) and f.ext in CODE_EXT]
    frameworks = report["stack"].get("testing", [])
    report["tests"] = OrderedDict(files=len(tests), frameworks=frameworks)

    docs = [f.rel for f in files if (DOC_NAMES.match(f.name) or (f.ext == ".md" and "/" not in f.rel))
            and not is_test_path(f.rel)]
    docs += sorted(f.rel for f in files if f.ext in {".md", ".rst"} and re.search(r"(^|/)(docs?|adr|adrs|decisions|runbooks?)/", f.rel, re.I))
    report["docs"] = list(OrderedDict.fromkeys(docs))

    controls = []
    todo = 0
    for name, pat in CONTROLS:
        rx = re.compile(pat, re.I if name not in ("Transactions",) else 0)
        count, examples = 0, []
        for f in files:
            if f.ext not in CODE_EXT | SQL_EXT | {".prisma"} or is_test_path(f.rel):
                continue
            for i, line in enumerate(f.lines()):
                if rx.search(line):
                    count += 1
                    if len(examples) < 4:
                        examples.append("%s:%d" % (f.rel, i + 1))
        controls.append(OrderedDict(name=name, count=count, examples=examples))
    for f in files:
        if f.ext in CODE_EXT and not is_test_path(f.rel):
            todo += sum(1 for l in f.lines() if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", l))
    report["controls"] = controls
    report["todo_markers"] = todo
    report["signals"] = scan_signals(files)
    return report


# ----------------------------------------------------------------- output

def _cap(items, n):
    return items if n is None else items[:n]


def render_text(r, max_hits):
    out = []
    w = out.append
    w("# Recon: %s" % r["root"])
    w("")
    w("Leads for a system design audit. This is a map plus places to look, not a list of findings.")
    w("")
    w("## Repositories")
    if not r["repos"]:
        w("- (no git repository found)")
    for g in r["repos"]:
        if g.get("error"):
            w("- %s: %s" % (g["path"], g["error"]))
            continue
        bits = ["%s @ %s" % (g.get("branch"), g.get("head"))]
        bits.append("clean" if not g.get("dirty_files") else "%d uncommitted changes" % g["dirty_files"])
        if g.get("upstream"):
            bits.append("vs %s: %s ahead, %s behind" % (g["upstream"], g.get("ahead", "?"), g.get("behind", "?")))
        if g.get("vs_default"):
            bits.append("vs %s: %d ahead, %d behind" % (g["default_branch"], g["vs_default"]["ahead"], g["vs_default"]["behind"]))
        bits.append("last commit %s" % g.get("last_commit"))
        w("- %s: %s" % (g["path"], "; ".join(bits)))
    if any(g.get("vs_default", {}).get("behind") or g.get("behind") for g in r["repos"]):
        w("  ! A checkout is behind its upstream/default branch (as of the last fetch). Audit the deployed revision.")
    w("")
    w("## Size")
    w("%d files in total; %d lines of code in %d source files" % (
        r["files"], r["code_lines"], sum(l["files"] for l in r["languages"])))
    for l in _cap(r["languages"], 8):
        w("- %s: %d files, %d lines" % (l["language"], l["files"], l["lines"]))
    w("")
    w("## Stack (from manifests)")
    if not r["manifests"]:
        w("- no dependency manifests found")
    for m in r["manifests"]:
        w("- %s (%s, %d deps)" % (m["path"], m["ecosystem"], m["dependencies"]))
        for cat, names in m["stack"].items():
            w("    %s: %s" % (cat, ", ".join(names)))
    w("")
    w("## Data stores and infrastructure services")
    for s in r["datastores"] or [{"name": "none detected", "evidence": []}]:
        w("- %s%s" % (s["name"], (": " + "; ".join(s["evidence"])) if s["evidence"] else ""))
    w("")
    w("## Entry points")
    for e in _cap(r["entry_points"], 25) or ["none detected"]:
        w("- " + e)
    w("")
    w("## Background work and schedules")
    if not r["background"]:
        w("- none detected")
    for b in _cap(r["background"], 30):
        w("- %s %s" % (b["kind"], b["where"]))
    if max_hits is not None and len(r["background"]) > 30:
        w("- … %d more (use --all)" % (len(r["background"]) - 30))
    w("")
    w("## Deploy and runtime")
    if not r["deploy"]:
        w("- none detected")
    for d in _cap(r["deploy"], 30):
        w("- %s: %s" % (d["kind"], d["path"]))
    if r["runtime_hints"]:
        w("  Concurrency hints (workers / instances / replicas):")
        for h in _cap(r["runtime_hints"], 15):
            w("    %s  %s" % (h["where"], h["text"]))
    w("")
    w("## Migrations")
    if not r["migrations"]:
        w("- none detected")
    for m in r["migrations"]:
        w("- %s: %d files, latest %s" % (m["dir"], m["files"], m["latest"]))
    w("")
    w("## Config keys (names only)")
    keys = r["config_keys"]
    w(", ".join(_cap(keys, 80)) if keys else "none detected")
    if max_hits is not None and len(keys) > 80:
        w("… %d more" % (len(keys) - 80))
    w("")
    w("## Tests")
    w("%d test files; frameworks: %s" % (r["tests"]["files"], ", ".join(r["tests"]["frameworks"]) or "none detected"))
    w("")
    w("## Docs to read first")
    for d in _cap(r["docs"], 25) or ["none found"]:
        w("- " + d)
    w("")
    w("## Controls already present (counts, sample locations)")
    for c in r["controls"]:
        w("- %s: %d%s" % (c["name"], c["count"], ("  e.g. " + ", ".join(c["examples"][:3])) if c["examples"] else ""))
    w("- TODO/FIXME/HACK markers: %d" % r["todo_markers"])
    w("")
    w("## Risk signals: leads to verify, not findings")
    active = [s for s in r["signals"] if s["count"]]
    if not active:
        w("No signals fired. That does NOT mean the design is sound; read the critical paths.")
    for s in active:
        w("")
        w("[%s] %s: %s (%d)" % (s["lens"], s["id"], s["title"], s["count"]))
        w("  check: " + s["check"])
        for h in _cap(s["hits"], max_hits):
            w("  %s:%d  %s" % (h["path"], h["line"], h["text"]))
        if max_hits is not None and s["count"] > max_hits:
            w("  … %d more (use --all)" % (s["count"] - max_hits))
    quiet = [s["id"] for s in r["signals"] if not s["count"]]
    if quiet:
        w("")
        w("Quiet signals (no hits; not proof of absence): " + ", ".join(quiet))
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Map a codebase for a system design audit (read-only).")
    ap.add_argument("path", help="repository or folder of repositories to map")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max-hits", type=int, default=8, help="hits shown per signal (default 8)")
    ap.add_argument("--all", action="store_true", help="show every hit and list entry")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="extra paths to skip (repeatable), e.g. --exclude 'legacy/*'")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.path):
        ap.error("not a directory: %s" % args.path)
    report = build(args.path, args.exclude)
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(report, None if args.all else args.max_hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
