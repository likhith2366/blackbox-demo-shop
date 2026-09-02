"""Tiny HTTP service scaffold shared by shop services (stdlib only)."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def read_build_info(root: str) -> dict:
    """BUILD_INFO.json is written into the artifact by the build pipeline."""
    p = os.path.join(root, "BUILD_INFO.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


class JsonHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    service = None  # set by subclass factory

    def log_message(self, fmt, *args):  # silence stdlib access log
        return

    def read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return {"__invalid__": True}

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_admin(self) -> bool:
        if self.path == "/health" and self.command == "GET":
            self.send_json(200, {"status": "ok", "service": self.service.name,
                                 "version": self.service.version})
            return True
        if self.path == "/admin/shutdown" and self.command == "POST":
            reason = self.headers.get("X-Shutdown-Reason", "unspecified")
            self.service.log.info("shutdown requested", reason=reason,
                                  client=self.client_address[0])
            self.send_json(202, {"status": "stopping"})
            threading.Thread(target=self.service.stop, daemon=True).start()
            return True
        return False


class Service:
    def __init__(self, name: str, port: int, handler_cls, log, metrics, root: str):
        self.name = name
        self.port = port
        self.log = log
        self.metrics = metrics
        self.root = root
        self.build = read_build_info(root)
        self.version = self.build.get("version", "dev")
        handler_cls.service = self
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
        self.httpd.daemon_threads = True

    def serve(self, **startup_fields):
        self.metrics.start()
        self.log.info(f"{self.name} starting", version=self.version,
                      commit=self.build.get("commit", "unknown"),
                      build_id=self.build.get("build_id", "unknown"),
                      artifact_digest=self.build.get("artifact_digest", "unknown"),
                      port=self.port, **startup_fields)
        try:
            self.httpd.serve_forever(poll_interval=0.2)
        finally:
            self.metrics.stop()
            self.log.info(f"{self.name} stopped")

    def stop(self):
        time.sleep(0.3)  # let in-flight responses finish
        self.httpd.shutdown()
