-- Reduce write amplification on payments inserts (DEMO-52).
DROP INDEX IF EXISTS idx_payments_customer_created;
