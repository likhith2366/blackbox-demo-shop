CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    payment_id TEXT,
    order_id TEXT,
    customer_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
