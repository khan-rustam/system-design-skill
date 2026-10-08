import os


class Config:
    DATABASE_URL = os.environ["DATABASE_URL"]
    DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))

    PAYGATE_API_KEY = os.environ["PAYGATE_API_KEY"]
    PAYGATE_WEBHOOK_SECRET = os.environ["PAYGATE_WEBHOOK_SECRET"]

    CARRIER_API_URL = os.environ.get("CARRIER_API_URL", "https://api.carrier.example/v2")
    CARRIER_API_KEY = os.environ["CARRIER_API_KEY"]

    SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
    SMTP_FROM = os.environ.get("SMTP_FROM", "orders@shop.example")
