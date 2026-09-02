"""payment-svc data access: SQLite connection pool, migrations and queries."""
from __future__ import annotations

import glob
import os
import sqlite3
import threading
import time
import uuid


class PoolTimeout(Exception):
    pass


class ConnectionPool:
    def __init__(self, path: str, size: int, acquire_timeout_ms: int):
        self.path = path
        self.size = size
        self.acquire_timeout_ms = acquire_timeout_ms
        self._idle = [self._connect() for _ in range(size)]
        self._in_use = 0
        self._cond = threading.Condition()

    def _connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @property
    def in_use(self) -> int:
        return self._in_use

    def acquire(self):
        deadline = time.time() + self.acquire_timeout_ms / 1000.0
        with self._cond:
            while not self._idle:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise PoolTimeout(f"db pool acquire timed out after {self.acquire_timeout_ms}ms")
                self._cond.wait(remaining)
            self._in_use += 1
            return self._idle.pop()

    def release(self, conn):
        with self._cond:
            self._in_use -= 1
            self._idle.append(conn)
            self._cond.notify()


def migrate(path: str, migrations_dir: str, log) -> list[str]:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for f in sorted(glob.glob(os.path.join(migrations_dir, "*.sql"))):
        name = os.path.basename(f)
        if name in done:
            continue
        with open(f, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        conn.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (name,))
        conn.commit()
        applied.append(name)
        log.info("applied migration", migration=name)
    conn.close()
    return applied


def velocity(conn, customer_id: int, since: str):
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM payments "
        "WHERE customer_id = ? AND created_at >= ?", (customer_id, since)).fetchone()
    return int(row[0]), float(row[1])


def recent_duplicate(conn, customer_id: int, amount: float, since: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM payments WHERE customer_id = ? AND created_at >= ? AND amount = ? LIMIT 1",
        (customer_id, since, amount)).fetchone()
    return row is not None


def existing_payment(conn, order_id: str):
    row = conn.execute("SELECT payment_id FROM payments WHERE order_id = ?", (order_id,)).fetchone()
    return row[0] if row else None


def insert_payment(conn, order_id: str, customer_id: int, amount: float, currency: str,
                   created_at: str) -> str:
    payment_id = "pay_" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO payments (payment_id, order_id, customer_id, amount, currency, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'approved', ?)",
        (payment_id, order_id, customer_id, amount, currency, created_at))
    conn.commit()
    return payment_id
