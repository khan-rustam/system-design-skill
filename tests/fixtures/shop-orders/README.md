# shop-orders

Order and payment backend for a small online store (about 3,000 orders a day,
peaks around 10x during sales).

- **API:** Flask, served by gunicorn (`gunicorn.conf.py`) on two VMs behind a
  load balancer. The load balancer health-checks `GET /health`.
- **Database:** PostgreSQL 15. Migrations in `migrations/` are applied by hand
  with `psql` before a deploy.
- **Payments:** PayGate hosted checkout. PayGate calls `POST /webhooks/paygate`
  when a payment succeeds and retries the webhook until it gets a 2xx.
- **Shipping:** labels are bought from the carrier API when a payment succeeds.
- **Email:** order confirmations go out over SMTP.
- **Cashback:** customers get 2% of every paid order back into their wallet.
- **Sales counts:** `products.sold_count` drives the bestseller list and the
  monthly supplier royalty statement. A nightly job reconciles it.

## Running locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export DATABASE_URL=postgresql://localhost/shop PAYGATE_API_KEY=... PAYGATE_WEBHOOK_SECRET=... CARRIER_API_KEY=...
.venv/bin/flask --app app:create_app run
```

Deploys: `deploy/deploy.sh` on each VM. Backups: `deploy/backup.sh` from cron.
