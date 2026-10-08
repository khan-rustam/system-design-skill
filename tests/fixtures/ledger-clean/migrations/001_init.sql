CREATE TABLE sellers (
    id                BIGINT PRIMARY KEY,
    name              TEXT NOT NULL,
    bank_beneficiary  TEXT NOT NULL
);

CREATE TABLE accounts (
    id             BIGSERIAL PRIMARY KEY,
    seller_id      BIGINT REFERENCES sellers (id),
    kind           TEXT NOT NULL CHECK (kind IN ('seller', 'platform', 'payout_clearing')),
    currency       CHAR(3) NOT NULL,
    balance_minor  BIGINT NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (kind = 'platform' OR balance_minor >= 0),
    CHECK ((kind = 'seller') = (seller_id IS NOT NULL))
);
CREATE UNIQUE INDEX accounts_one_clearing_per_currency
    ON accounts (currency) WHERE kind = 'payout_clearing';
CREATE INDEX accounts_seller_idx ON accounts (seller_id);

CREATE TABLE transfers (
    id               BIGSERIAL PRIMARY KEY,
    from_account     BIGINT NOT NULL REFERENCES accounts (id),
    to_account       BIGINT NOT NULL REFERENCES accounts (id),
    amount_minor     BIGINT NOT NULL CHECK (amount_minor > 0),
    currency         CHAR(3) NOT NULL,
    reference        TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    request_hash     CHAR(64) NOT NULL,
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_account, idempotency_key),
    CHECK (from_account <> to_account)
);

CREATE TABLE ledger_entries (
    id            BIGSERIAL PRIMARY KEY,
    transfer_id   BIGINT NOT NULL REFERENCES transfers (id),
    account_id    BIGINT NOT NULL REFERENCES accounts (id),
    amount_minor  BIGINT NOT NULL CHECK (amount_minor <> 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outbox (
    id            BIGSERIAL PRIMARY KEY,
    topic         TEXT NOT NULL,
    payload       JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at  TIMESTAMPTZ
);
CREATE INDEX outbox_unpublished_idx ON outbox (id) WHERE published_at IS NULL;

CREATE TABLE payouts (
    id            BIGSERIAL PRIMARY KEY,
    seller_id     BIGINT NOT NULL REFERENCES sellers (id),
    account_id    BIGINT NOT NULL REFERENCES accounts (id),
    payout_date   DATE NOT NULL,
    amount_minor  BIGINT NOT NULL CHECK (amount_minor > 0),
    currency      CHAR(3) NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending', 'retrying', 'submitted', 'confirmed', 'failed')),
    bank_ref      TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (seller_id, payout_date)
);
