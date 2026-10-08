import logging
import uuid

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, request

from . import db
from .config import Config
from .jobs.reconcile import reconcile_sales_counts
from .routes.health import bp as health_bp
from .routes.orders import bp as orders_bp
from .routes.payments import bp as payments_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db.init_pool(Config.DATABASE_URL, Config.DB_POOL_MAX)

    @app.before_request
    def assign_request_id():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex

    app.register_blueprint(health_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(payments_bp)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(reconcile_sales_counts, "cron", hour=2, minute=0)
    scheduler.start()

    return app
