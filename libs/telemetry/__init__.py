"""Minimal telemetry for the shop services: JSON logs, W3C-style trace spans, metrics.

Everything is written as JSON lines under $TELEMETRY_DIR so a collector (or a human)
can read it back:
  logs/<service>.jsonl      one structured log record per line
  spans/<service>.jsonl     one finished span per line
  metrics/<service>.jsonl   one metric point per line, every METRIC_INTERVAL_S seconds
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

_LOCK = threading.Lock()
_CTX = threading.local()


def now_iso(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(time.time() if ts is None else ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class _Sink:
    def __init__(self, kind: str, service: str):
        root = os.environ.get("TELEMETRY_DIR", "telemetry")
        os.makedirs(os.path.join(root, kind), exist_ok=True)
        self.path = os.path.join(root, kind, f"{service}.jsonl")

    def write(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        with _LOCK:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


class Logger:
    LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

    def __init__(self, service: str, level: str = "INFO"):
        self.service = service
        self.min_level = self.LEVELS.get(level.upper(), 20)
        self._sink = _Sink("logs", service)

    def log(self, level: str, msg: str, **fields) -> None:
        if self.LEVELS.get(level, 20) < self.min_level:
            return
        span = getattr(_CTX, "span", None)
        rec = {"ts": now_iso(), "level": level, "service": self.service, "msg": msg}
        if span is not None:
            rec["trace_id"] = span.trace_id
            rec["span_id"] = span.span_id
        rec.update(fields)
        self._sink.write(rec)

    def info(self, msg, **f):
        self.log("INFO", msg, **f)

    def warning(self, msg, **f):
        self.log("WARNING", msg, **f)

    def error(self, msg, **f):
        self.log("ERROR", msg, **f)


class Span:
    def __init__(self, tracer, name, trace_id, parent_id, kind, attributes):
        self.tracer = tracer
        self.name = name
        self.trace_id = trace_id
        self.span_id = secrets.token_hex(8)
        self.parent_span_id = parent_id
        self.kind = kind
        self.attributes = dict(attributes or {})
        self.status = "ok"
        self.status_message = ""
        self.start = time.time()

    def set(self, key, value):
        self.attributes[key] = value

    def fail(self, message: str):
        self.status = "error"
        self.status_message = message[:300]

    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-01"


class Tracer:
    def __init__(self, service: str, resource: dict | None = None):
        self.service = service
        self.resource = dict(resource or {})
        self._sink = _Sink("spans", service)

    @staticmethod
    def parse_traceparent(header: str | None):
        if not header:
            return None, None
        parts = header.strip().split("-")
        if len(parts) != 4 or len(parts[1]) != 32 or len(parts[2]) != 16:
            return None, None
        return parts[1], parts[2]

    @contextmanager
    def span(self, name: str, kind: str = "internal", traceparent: str | None = None,
             attributes: dict | None = None):
        parent = getattr(_CTX, "span", None)
        if traceparent:
            trace_id, parent_id = self.parse_traceparent(traceparent)
        elif parent is not None:
            trace_id, parent_id = parent.trace_id, parent.span_id
        else:
            trace_id, parent_id = None, None
        if trace_id is None:
            trace_id, parent_id = secrets.token_hex(16), None
        sp = Span(self, name, trace_id, parent_id, kind, attributes)
        _CTX.span = sp
        try:
            yield sp
        except Exception as exc:  # recorded, then re-raised
            sp.fail(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            end = time.time()
            _CTX.span = parent
            self._sink.write({
                "trace_id": sp.trace_id, "span_id": sp.span_id,
                "parent_span_id": sp.parent_span_id, "service": self.service,
                "name": sp.name, "kind": sp.kind, "start": now_iso(sp.start),
                "end": now_iso(end), "duration_ms": round((end - sp.start) * 1000, 2),
                "status": sp.status, "status_message": sp.status_message,
                "attributes": sp.attributes, "resource": self.resource,
            })


def current_span():
    return getattr(_CTX, "span", None)


class Metrics:
    """Counters, latency samples and gauges flushed as points every interval."""

    def __init__(self, service: str, interval_s: float | None = None):
        self.service = service
        self.interval = interval_s or float(os.environ.get("METRIC_INTERVAL_S", "5"))
        self._sink = _Sink("metrics", service)
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._samples: dict[str, list[float]] = {}
        self._gauges: dict[str, callable] = {}
        self._rates: list[str] = []
        self._ratios: list[tuple[str, str, str]] = []
        self._pcts: list[tuple[str, str, int]] = []
        self._stop = threading.Event()

    def inc(self, name: str, value: float = 1.0):
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + value

    def observe(self, name: str, value: float):
        with self._lock:
            self._samples.setdefault(name, []).append(value)

    def gauge(self, name: str, fn):
        self._gauges[name] = fn

    def rate(self, counter: str):
        self._rates.append(counter)

    def ratio(self, out: str, num: str, den: str):
        self._ratios.append((out, num, den))

    def percentile(self, out: str, sample: str, pct: int):
        self._pcts.append((out, sample, pct))

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.flush()

    def _loop(self):
        while not self._stop.wait(self.interval):
            self.flush()

    def flush(self):
        with self._lock:
            counters, self._counters = self._counters, {}
            samples, self._samples = self._samples, {}
        ts = now_iso()
        points = {}
        for c in self._rates:
            points[f"{c}_per_s"] = round(counters.get(c, 0.0) / self.interval, 3)
        for out, num, den in self._ratios:
            d = counters.get(den, 0.0)
            points[out] = round(counters.get(num, 0.0) / d, 4) if d else 0.0
        for out, s, pct in self._pcts:
            vals = sorted(samples.get(s, []))
            if vals:
                k = min(len(vals) - 1, max(0, int(round(pct / 100.0 * len(vals) + 0.5)) - 1))
                points[out] = round(vals[k], 2)
            else:
                points[out] = 0.0
        for name, fn in self._gauges.items():
            try:
                points[name] = float(fn())
            except Exception:
                pass
        for name, value in points.items():
            self._sink.write({"ts": ts, "service": self.service, "metric": name,
                              "value": value, "interval_s": self.interval})
