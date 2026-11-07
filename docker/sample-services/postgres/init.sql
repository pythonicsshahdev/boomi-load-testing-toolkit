-- Sample data for JDBC load tests. Runs once, when the postgres-target volume is first created.
CREATE TABLE customers (
    id         integer PRIMARY KEY,
    name       text        NOT NULL,
    email      text        NOT NULL,
    city       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO customers (id, name, email, city)
SELECT g,
       'Customer ' || g,
       'customer' || g || '@example.com',
       (ARRAY['Boston','Denver','Austin','Seattle','Chicago','Atlanta','Phoenix','Dallas'])[1 + g % 8]
FROM generate_series(1, 100000) AS g;

CREATE INDEX customers_city_idx ON customers (city);

CREATE TABLE orders (
    id          integer PRIMARY KEY,
    customer_id integer        NOT NULL REFERENCES customers (id),
    total       numeric(10, 2) NOT NULL,
    created_at  timestamptz    NOT NULL DEFAULT now()
);

INSERT INTO orders (id, customer_id, total)
SELECT g,
       1 + ((g::bigint * 7919) % 100000)::integer,
       round((random() * 500 + 5)::numeric, 2)
FROM generate_series(1, 300000) AS g;

CREATE INDEX orders_customer_idx ON orders (customer_id);

-- Scratch table for write tests (allow_writes: true): nothing else reads it.
CREATE TABLE load_test_events (
    id         bigserial PRIMARY KEY,
    payload    text,
    created_at timestamptz NOT NULL DEFAULT now()
);

ANALYZE;
