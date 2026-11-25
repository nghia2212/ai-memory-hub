import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "hermes"))

from app.config import HermesConfig  # noqa: E402
from app.server import HermesServiceState, make_handler  # noqa: E402


class TestHermesServer(unittest.TestCase):
    def _start_server(self, env: dict[str, str]) -> tuple[ThreadingHTTPServer, int]:
        cfg = HermesConfig.from_env(env)
        state = HermesServiceState(cfg)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv, srv.server_address[1]

    def test_disabled_server_health_and_livez(self) -> None:
        srv, port = self._start_server({"HERMES_ENABLED": "false"})
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/livez", timeout=2) as r:
                self.assertEqual(r.status, 200)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                self.assertEqual(r.status, 200)
                data = json.loads(r.read().decode("utf-8"))
                self.assertTrue(data["ready"])
                self.assertFalse(data["enabled"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_enabled_without_key_returns_503(self) -> None:
        srv, port = self._start_server({"HERMES_ENABLED": "true", "HERMES_PROVIDER": "deepseek"})
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
            self.assertEqual(ctx.exception.code, 503)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
