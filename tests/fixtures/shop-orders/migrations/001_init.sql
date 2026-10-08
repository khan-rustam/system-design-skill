CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    token_hash  CHAR(64) PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users (id),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE addresses (
    id        BIGSERIAL PRIMARY KEY,
    user_id   BIGINT NOT NULL REFERENCES users (id),
    line1     TEXT NOT NULL,
    city      TEXT NOT NULL,
    postcode  TEXT NOT NULL,
    country   CHAR(2) NOT NULL
);

CREATE TABLE products (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    price       NUMERIC(10, 2) NOT NULL,
    stock       INTEGER NOT NULL,
    sold_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE orders (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users (id),
    product_id       BIGINT NOT NULL REFERENCES products (id),
    address_id       BIGINT NOT NULL REFERENCES addresses (id),
    quantity         INTEGER NOT NULL,
    total            REAL NOT NULL,
    status           TEXT NOT NULL,
    tracking_number  TEXT,
    label_url        TEXT,
    paid_at          TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX orders_user_id_idx ON orders (user_id);

CREATE TABLE payments (
    id            BIGSERIAL PRIMARY KEY,
    order_id      BIGINT NOT NULL REFERENCES orders (id),
    provider_ref  TEXT NOT NULL,
    amount        REAL NOT NULL,
    event_id      TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
