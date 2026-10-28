"""checkout-svc: accepts orders and charges them through payment-svc."""
from __future__ import annotations

import argparse
import os
import threading
import time

from libs.httpclient import ClientConnectionError, ClientTimeout, ServiceClient
from libs.telemetry import Logger, Metrics, Tracer
from libs.web import JsonHandler, Service, load_config, read_build_info

NAME = "checkout-svc"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAX_NOTE_CHARS = 400

_inflight = 0
_inflight_lock = threading.Lock()


class Handler(JsonHandler):
    def do_GET(self):
        if not self.handle_admin():
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.handle_admin():
            return
        if self.path != "/orders":
            self.send_json(404, {"error": "not found"})
            return
        global _inflight
        with _inflight_lock:
            _inflight += 1
        t0 = time.time()
        status = 500
        try:
            with TRACER.span("POST /orders", kind="server",
                             traceparent=self.headers.get("traceparent"),
                             attributes={"http.route": "/orders"}) as sp:
                status = self._create_order(sp)
                sp.set("http.status_code", status)
                if status >= 500:
                    sp.fail(f"HTTP {status}")
        finally:
            with _inflight_lock:
                _inflight -= 1
            METRICS.inc("requests")
            if status >= 500:
                METRICS.inc("errors")
            METRICS.observe("latency_ms", (time.time() - t0) * 1000.0)

    def _create_order(self, sp) -> int:
        order = self.read_json()
        required = ("order_id", "customer_id", "item", "qty", "amount")
        if order.get("__invalid__") or any(k not in order for k in required):
            LOG.warning("rejected order: invalid payload")
            self.send_json(400, {"error": "invalid order"})
            return 400
        sp.set("order.id", order["order_id"])
        fields = dict(order_id=order["order_id"], customer_id=order["customer_id"],
                      amount=order["amount"], item=order["item"])
        note = str(order.get("note") or "")[:MAX_NOTE_CHARS]
        if note:
            fields["note"] = note
        LOG.info("order received", **fields)
        charge = {"order_id": order["order_id"], "customer_id": order["customer_id"],
                  "amount": order["amount"], "currency": order.get("currency", "USD")}
        try:
            resp = PAYMENT.post_json("/charge", charge)
        except ClientTimeout as exc:
            LOG.error(f"payment call timed out after {PAYMENT.timeout_ms}ms",
                      order_id=order["order_id"], attempts=exc.attempts)
            self.send_json(504, {"error": "payment timeout"})
            return 504
        except ClientConnectionError as exc:
            LOG.error("payment call failed: connection error", order_id=order["order_id"],
                      attempts=exc.attempts, detail=str(exc)[:200])
            self.send_json(502, {"error": "payment unavailable"})
            return 502
        if resp.status >= 500:
            LOG.error(f"payment service returned HTTP {resp.status}", order_id=order["order_id"],
                      attempts=resp.attempts, body=resp.body.decode("utf-8", "replace")[:200])
            self.send_json(502, {"error": "payment failed"})
            return 502
        if resp.status >= 400:
            LOG.error(f"payment service rejected charge with HTTP {resp.status}",
                      order_id=order["order_id"], body=resp.body.decode("utf-8", "replace")[:200])
            self.send_json(502, {"error": "payment rejected"})
            return 502
        result = resp.json()
        if result.get("status") != "approved":
            LOG.warning("payment not approved", order_id=order["order_id"],
                        result=str(result)[:200])
            self.send_json(402, {"error": "payment declined"})
            return 402
        LOG.info("order completed", order_id=order["order_id"],
                 payment_id=result.get("payment_id"), attempts=resp.attempts)
        self.send_json(201, {"order_id": order["order_id"], "payment_id": result.get("payment_id")})
        return 201


def main():
    global LOG, TRACER, METRICS, PAYMENT
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "checkout-svc.yaml"))
    args = ap.parse_args()
    cfg = load_config(args.config)
    LOG = Logger(NAME, cfg.get("log_level", "INFO"))
    METRICS = Metrics(NAME)
    build = read_build_info(ROOT)
    TRACER = Tracer(NAME, {"service.version": build.get("version", "dev")})
    pay = cfg.get("payment", {})
    PAYMENT = ServiceClient(pay["base_url"], TRACER, METRICS, peer="payment-svc",
                            timeout_ms=pay.get("timeout_ms"), max_retries=pay.get("max_retries"),
                            retry_on=pay.get("retry_on"), backoff_ms=pay.get("backoff_ms"))
    METRICS.rate("requests")
    METRICS.ratio("error_rate", "errors", "requests")
    METRICS.percentile("latency_p50_ms", "latency_ms", 50)
    METRICS.percentile("latency_p95_ms", "latency_ms", 95)
    METRICS.rate("upstream_attempts")
    METRICS.rate("upstream_errors")
    METRICS.gauge("inflight_requests", lambda: _inflight)
    svc = Service(NAME, int(cfg.get("port", 18101)), Handler, LOG, METRICS, ROOT)
    svc.serve(payment_client=PAYMENT.describe())


if __name__ == "__main__":
    main()
