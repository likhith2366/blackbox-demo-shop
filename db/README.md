# Payment database migrations

payment-svc applies the files in `db/migrations` in order at startup.

- `001_schema.sql`: payments table
- `002_customer_index.sql`: customer/time index used by velocity checks (DEMO-3)

Add new migrations with the next number; never edit an applied file.
