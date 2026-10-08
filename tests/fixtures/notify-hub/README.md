# notify-hub

Multi-tenant notification API. Our B2B customers ("tenants") send SMS and email
to their own users through one HTTP API. We bill tenants per message, and each
plan includes a daily message allowance.

## Components

- **API** (`src/api`): Express. Tenants authenticate with an API key. Runs as 3
  replicas (`k8s/api.yaml`).
- **Worker** (`src/worker`): BullMQ consumer that sends messages. 2 replicas
  (`k8s/worker.yaml`).
- **PostgreSQL** for messages and tenants, **Redis** for the job queue.
- **SMS provider: TextBlast.** We pay per SMS sent. Their API allows 20
  requests/second per account and accepts an optional `Idempotency-Key`
  header (duplicate keys within 24h return the original result and are not
  charged again). TextBlast posts delivery receipts to
  `POST /webhooks/textblast/status`; each receipt carries an
  `X-TextBlast-Signature` HMAC header.
- **Email** goes through our SMTP relay with nodemailer.

## Message lifecycle

`queued` → `sending` → `sent` → `delivered` | `failed`

Tenants poll `GET /v1/messages/:id` or list `GET /v1/messages`.
