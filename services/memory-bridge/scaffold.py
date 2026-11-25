"""Explicit placeholder: liveness only, never reports readiness."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 200 if self.path == "/livez" else 503 if self.path == "/healthz" else 404
        body = json.dumps({"service": os.environ["SERVICE_NAME"], "ready": False, "status": "scaffold"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

ThreadingHTTPServer(("0.0.0.0", int(os.environ["PORT"])), Handler).serve_forever()
