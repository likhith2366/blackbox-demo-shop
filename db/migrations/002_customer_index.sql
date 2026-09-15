-- Velocity and duplicate checks filter by customer and time window.
CREATE INDEX IF NOT EXISTS idx_payments_customer_created ON payments(customer_id, created_at);
