CREATE TABLE tenants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    daily_limit  INTEGER NOT NULL DEFAULT 1000,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    key_hash    CHAR(64) PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES tenants (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE messages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants (id),
    channel       TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
    recipient     TEXT NOT NULL,
    subject       TEXT,
    body          TEXT NOT NULL,
    status        TEXT NOT NULL,
    provider_ref  TEXT,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ
);
CREATE INDEX messages_tenant_created_idx ON messages (tenant_id, created_at DESC);
CREATE INDEX messages_provider_ref_idx ON messages (provider_ref);
