import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = ROOT / "integrations" / "claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from common import resolve_git_scope  # noqa: E402


class TestClaudeHooks(unittest.TestCase):
    def _init_temp_git_repo(self, base_dir: Path) -> Path:
        repo = base_dir / "repo with spaces"
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo), check=True)
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)
        return repo

    def test_resolve_git_scope_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_temp_git_repo(Path(tmp))
            scope = resolve_git_scope(str(repo))
            self.assertIsNotNone(scope)
            assert scope is not None
            self.assertEqual(Path(scope.worktree_root).resolve(), repo.resolve())
            self.assertFalse(scope.dirty)

    def test_hooks_fail_open_when_bridge_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_temp_git_repo(Path(tmp))
            env = dict(os.environ)
            env["AI_MEMORY_BRIDGE_URL"] = "http://127.0.0.1:59999"
            for script, extra in [
                (HOOKS_DIR / "session_start.py", []),
                (HOOKS_DIR / "session_event.py", ["--kind", "stop"]),
                (HOOKS_DIR / "session_event.py", ["--kind", "session_end"]),
            ]:
                proc = subprocess.run(
                    [sys.executable, str(script), *extra],
                    input=json.dumps({"cwd": str(repo), "session_id": "s-123"}),
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(proc.returncode, 0)

    def test_hooks_with_mock_bridge_and_no_transcript_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = self._init_temp_git_repo(tmp_path)
            secret_transcript = tmp_path / "transcript.jsonl"
            secret_transcript.write_text("SECRET_TRANSCRIPT_DO_NOT_SEND\n", encoding="utf-8")

            captured_events: list[dict[str, Any]] = []

            class MockBridge(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: Any) -> None:
                    return

                def _json(self, code: int, body: dict[str, Any]) -> None:
                    raw = json.dumps(body).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

                def do_GET(self) -> None:
                    if self.path == "/v1/projects":
                        self._json(200, {"items": [{"id": "proj-1"}]})
                    elif self.path == "/v1/projects/proj-1/worktrees":
                        self._json(200, {"items": [{"id": "wt-1", "host_path": str(repo)}]})
                    else:
                        self._json(404, {})

                def do_POST(self) -> None:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if self.path == "/v1/search/memory":
                        self._json(
                            200,
                            {
                                "items": [
                                    {"id": "m1", "title": "Use FTS5", "excerpt": "Bounded search works."}
                                ]
                            },
                        )
                    elif self.path == "/v1/events":
                        captured_events.append(body)
                        self._json(201, {"accepted": True})
                    else:
                        self._json(404, {})

            srv = ThreadingHTTPServer(("127.0.0.1", 0), MockBridge)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                port = srv.server_address[1]
                env = dict(os.environ)
                env["AI_MEMORY_BRIDGE_URL"] = f"http://127.0.0.1:{port}"
                env["AI_MEMORY_BRIDGE_READ_TOKEN"] = "tok-read"
                env["AI_MEMORY_BRIDGE_WRITE_TOKEN"] = "tok-write"

                p_start = subprocess.run(
                    [sys.executable, str(HOOKS_DIR / "session_start.py")],
                    input=json.dumps({"cwd": str(repo), "session_id": "s-123"}),
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_start.returncode, 0)
                self.assertIn("Use FTS5", p_start.stdout)
                self.assertLessEqual(len(p_start.stdout), 1500)

                p_end = subprocess.run(
                    [sys.executable, str(HOOKS_DIR / "session_event.py"), "--kind", "session_end"],
                    input=json.dumps(
                        {
                            "cwd": str(repo),
                            "session_id": "s-123",
                            "transcript_path": str(secret_transcript),
                        }
                    ),
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_end.returncode, 0)
                self.assertEqual(len(captured_events), 1)
                serialized = json.dumps(captured_events[0])
                self.assertNotIn("SECRET_TRANSCRIPT_DO_NOT_SEND", serialized)
                self.assertEqual(captured_events[0]["kind"], "session_end")
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
