"""Shared HTTP client used by shop services for internal calls (retries, timeouts, tracing)."""
from __future__ import annotations

import http.client
import json
import os
import socket
import time
from urllib.parse import urlparse

from libs.httpclient import policy

with open(os.path.join(os.path.dirname(__file__), "VERSION"), encoding="utf-8") as _fh:
    CLIENT_VERSION = _fh.read().strip()


class ClientError(Exception):
    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


class ClientTimeout(ClientError):
    pass


class ClientConnectionError(ClientError):
    pass


class Response:
    def __init__(self, status: int, body: bytes, attempts: int):
        self.status = status
        self.body = body
        self.attempts = attempts

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8") or "{}")
        except ValueError:
            return {}


class ServiceClient:
    def __init__(self, base_url: str, tracer, metrics=None, peer: str = "",
                 timeout_ms: int | None = None, max_retries: int | None = None,
                 retry_on=None, backoff_ms: int | None = None):
        self.base_url = base_url.rstrip("/")
        u = urlparse(self.base_url)
        self.host, self.port = u.hostname, u.port or 80
        self.tracer = tracer
        self.metrics = metrics
        self.peer = peer or self.host
        self.timeout_ms = timeout_ms if timeout_ms is not None else policy.DEFAULT_TIMEOUT_MS
        self.max_retries = max_retries if max_retries is not None else policy.MAX_RETRIES
        self.retry_on = tuple(retry_on if retry_on is not None else policy.RETRY_ON)
        self.backoff_ms = backoff_ms if backoff_ms is not None else policy.BACKOFF_MS

    def describe(self) -> dict:
        return {"base_url": self.base_url, "timeout_ms": self.timeout_ms,
                "max_retries": self.max_retries, "retry_on": list(self.retry_on),
                "backoff_ms": self.backoff_ms, "client_version": CLIENT_VERSION}

    def post_json(self, path: str, payload: dict) -> Response:
        body = json.dumps(payload).encode("utf-8")
        attempt = 0
        while True:
            attempt += 1
            kind, detail, resp = self._attempt(path, body, attempt)
            if kind == "ok":
                return resp
            retryable = (kind in self.retry_on) and attempt <= self.max_retries
            if not retryable:
                if kind == "5xx":
                    return resp
                if kind == "timeout":
                    raise ClientTimeout(f"timed out after {self.timeout_ms}ms", attempt)
                raise ClientConnectionError(detail, attempt)
            if self.backoff_ms:
                time.sleep(self.backoff_ms / 1000.0)

    def _attempt(self, path, body, attempt):
        if self.metrics:
            self.metrics.inc("upstream_attempts")
        with self.tracer.span(f"POST {path}", kind="client",
                              attributes={"peer.service": self.peer, "http.attempt": attempt,
                                          "http.timeout_ms": self.timeout_ms}) as sp:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_ms / 1000.0)
            try:
                conn.request("POST", path, body=body, headers={
                    "Content-Type": "application/json", "traceparent": sp.traceparent()})
                r = conn.getresponse()
                data = r.read()
                sp.set("http.status_code", r.status)
                resp = Response(r.status, data, attempt)
                if r.status >= 500:
                    sp.fail(f"HTTP {r.status}")
                    if self.metrics:
                        self.metrics.inc("upstream_errors")
                    return "5xx", f"HTTP {r.status}", resp
                return "ok", "", resp
            except (socket.timeout, TimeoutError):
                sp.fail(f"timeout after {self.timeout_ms}ms")
                if self.metrics:
                    self.metrics.inc("upstream_errors")
                return "timeout", "timeout", None
            except (ConnectionError, OSError) as exc:
                sp.fail(f"connect error: {exc}")
                if self.metrics:
                    self.metrics.inc("upstream_errors")
                return "connect_error", f"{type(exc).__name__}: {exc}", None
            finally:
                conn.close()
