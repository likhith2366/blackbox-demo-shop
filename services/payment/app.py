"""payment-svc: authorises charges and records them in the payments database."""
from __future__ import annotations

import argparse
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone

from libs.telemetry import Logger, Metrics, Tracer
from libs.web import JsonHandler, Service, load_config, read_build_info
from services.payment import db

NAME = "payment-svc"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LARGE_AMOUNT = 300.0


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def risk_provider_delay_s(order_id: str, amount: float) -> float:
    """Latency of the external risk-scoring provider.

    The provider is not reachable from the demo environment, so its response time is
    reproduced from a fixed per-order profile: large charges get the slow full review.
    """
    h = int(hashlib.sha1(order_id.encode()).hexdigest()[:8], 16)
    if amount > LARGE_AMOUNT:
        return (250 + h % 450) / 1000.0
    return (10 + h % 40) / 1000.0


class Handler(JsonHandler):
    def do_GET(self):
        if not self.handle_admin():
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.handle_admin():
            return
        if self.path != "/charge":
            self.send_json(404, {"error": "not found"})
            return
        t0 = time.time()
        status = 500
        try:
            with TRACER.span("POST /charge", kind="server",
                             traceparent=self.headers.get("traceparent"),
                             attributes={"http.route": "/charge"}) as sp:
                status = self._charge(sp)
                sp.set("http.status_code", status)
                if status >= 500:
                    sp.fail(f"HTTP {status}")
        finally:
            METRICS.inc("requests")
            if status >= 500:
                METRICS.inc("errors")
            METRICS.observe("latency_ms", (time.time() - t0) * 1000.0)

    def _charge(self, sp) -> int:
        req = self.read_json()
        if req.get("__invalid__") or any(k not in req for k in ("order_id", "customer_id", "amount")):
            LOG.warning("rejected charge: invalid payload")
            self.send_json(400, {"error": "invalid charge request"})
            return 400
        order_id = str(req["order_id"])
        customer_id = int(req["customer_id"])
        amount = float(req["amount"])
        currency = str(req.get("currency", "USD"))
        sp.set("order.id", order_id)

        w0 = time.time()
        try:
            with TRACER.span("db.pool.acquire") as asp:
                conn = POOL.acquire()
                asp.set("db.pool.wait_ms", round((time.time() - w0) * 1000, 2))
        except db.PoolTimeout as exc:
            METRICS.observe("pool_wait_ms", (time.time() - w0) * 1000.0)
            LOG.error(str(exc), order_id=order_id)
            self.send_json(503, {"error": "service unavailable"})
            return 503
        METRICS.observe("pool_wait_ms", (time.time() - w0) * 1000.0)

        try:
            now = datetime.now(timezone.utc)
            existing = db.existing_payment(conn, order_id)
            if existing:
                POOL.release(conn)
                LOG.info("charge already recorded", order_id=order_id, payment_id=existing)
                self.send_json(200, {"payment_id": existing, "status": "approved"})
                return 200
            with TRACER.span("risk.score", attributes={"amount": amount}):
                time.sleep(risk_provider_delay_s(order_id, amount))
            q0 = time.time()
            with TRACER.span("db.query", attributes={"db.statement": "velocity"}):
                count_30d, total_30d = db.velocity(conn, customer_id, _iso(now - timedelta(days=30)))
            with TRACER.span("db.query", attributes={"db.statement": "recent_duplicate"}):
                dup = db.recent_duplicate(conn, customer_id, amount, _iso(now - timedelta(minutes=10)))
            with TRACER.span("db.query", attributes={"db.statement": "insert_payment"}):
                payment_id = db.insert_payment(conn, order_id, customer_id, amount, currency, _iso(now))
            METRICS.observe("query_ms", (time.time() - q0) * 1000.0)
        except Exception as exc:
            POOL.release(conn)
            LOG.error("charge failed", order_id=order_id, error=f"{type(exc).__name__}: {exc}"[:200])
            self.send_json(500, {"error": "internal error"})
            return 500
        POOL.release(conn)
        LOG.info("charge approved", order_id=order_id, payment_id=payment_id,
                 recent_duplicate=dup, orders_30d=count_30d)
        self.send_json(201, {"payment_id": payment_id, "status": "approved"})
        return 201


def main():
    global LOG, TRACER, METRICS, POOL
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "payment-svc.yaml"))
    args = ap.parse_args()
    cfg = load_config(args.config)
    build = read_build_info(ROOT)
    LOG = Logger(NAME, cfg.get("log_level", "INFO"))
    METRICS = Metrics(NAME)
    TRACER = Tracer(NAME, {"service.version": build.get("version", "dev")})
    dbcfg = cfg.get("db", {})
    db_path = os.environ.get("PAYMENT_DB_PATH", dbcfg.get("path", "payments.db"))
    # Operators may override pool sizing at deploy time without a code change.
    pool_size = int(os.environ.get("PAYMENT_DB_POOL_SIZE", dbcfg.get("pool_size", 4)))
    db.migrate(db_path, os.path.join(ROOT, "db", "migrations"), LOG)
    POOL = db.ConnectionPool(db_path, pool_size, int(dbcfg.get("acquire_timeout_ms", 1000)))
    METRICS.rate("requests")
    METRICS.ratio("error_rate", "errors", "requests")
    METRICS.percentile("latency_p50_ms", "latency_ms", 50)
    METRICS.percentile("latency_p95_ms", "latency_ms", 95)
    METRICS.percentile("db_pool_wait_p95_ms", "pool_wait_ms", 95)
    METRICS.percentile("db_query_p95_ms", "query_ms", 95)
    METRICS.gauge("db_pool_in_use", lambda: POOL.in_use)
    svc = Service(NAME, int(cfg.get("port", 18102)), Handler, LOG, METRICS, ROOT)
    svc.serve()


if __name__ == "__main__":
    main()
