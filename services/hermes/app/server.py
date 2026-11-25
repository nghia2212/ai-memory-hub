"""HTTP server and optional background worker loop for Hermes service."""
from __future__ import annotations

import json
import os
import signal
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .bridge_client import BridgeClient
from .config import HermesConfig
from .worker import HermesWorker, RunStats


class HermesServiceState:
    def __init__(self, config: HermesConfig) -> None:
        self.config = config
        self.bridge = BridgeClient(
            base_url=config.bridge_url,
            worker_token=config.bridge_worker_token,
            timeout_seconds=config.request_timeout_seconds,
        )
        self.worker = HermesWorker(config=config, bridge=self.bridge)
        self.last_run: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def trigger_run_once(self) -> dict[str, Any]:
        with self._lock:
            stats: RunStats = self.worker.run_once()
            result = asdict(stats)
            self.last_run = result
            return result


def make_handler(state: HermesServiceState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Suppress default stderr logging to avoid accidental path/header leakage.
            return

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/livez":
                self._send_json(200, {"service": state.config.service_name, "alive": True})
                return
            if self.path == "/healthz":
                ready, reason = state.config.validate_for_run()
                status = 200 if ready else 503
                self._send_json(
                    status,
                    {
                        "service": state.config.service_name,
                        "ready": ready,
                        "enabled": state.config.enabled,
                        "provider": state.config.provider if state.config.enabled else "none",
                        "status": reason,
                    },
                )
                return
            if self.path == "/v1/status":
                ready, reason = state.config.validate_for_run()
                self._send_json(
                    200,
                    {
                        "service": state.config.service_name,
                        "enabled": state.config.enabled,
                        "ready": ready,
                        "status": reason,
                        "provider": state.config.provider,
                        "last_run": state.last_run,
                    },
                )
                return
            self._send_json(404, {"error": {"code": "not_found", "message": "Route not found"}})

        def do_POST(self) -> None:
            if self.path == "/v1/worker/run-once":
                if not state.config.enabled:
                    self._send_json(
                        200,
                        {"enabled": False, "status": "disabled", "processed": 0},
                    )
                    return
                result = state.trigger_run_once()
                self._send_json(200, result)
                return
            self._send_json(404, {"error": {"code": "not_found", "message": "Route not found"}})

    return Handler


def _worker_loop(state: HermesServiceState, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        if state.config.enabled:
            ready, _ = state.config.validate_for_run()
            if ready:
                try:
                    state.trigger_run_once()
                except Exception:
                    pass
        stop_event.wait(state.config.poll_interval_seconds)


def run_server() -> None:
    config = HermesConfig.from_env(dict(os.environ))
    state = HermesServiceState(config)
    stop_event = threading.Event()
    if config.enabled:
        t = threading.Thread(target=_worker_loop, args=(state, stop_event), daemon=True)
        t.start()

    server = ThreadingHTTPServer(("0.0.0.0", config.port), make_handler(state))

    def _handle_signal(_signum: int, _frame: Any) -> None:
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_signal)
            except ValueError:
                pass

    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    run_server()
