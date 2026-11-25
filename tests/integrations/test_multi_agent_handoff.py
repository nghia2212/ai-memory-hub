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
HANDOFF_CLI = ROOT / "integrations" / "common" / "handoff_cli.py"


class TestMultiAgentHandoff(unittest.TestCase):
    def _init_repo(self, base: Path) -> Path:
        repo = base / "shared-repo"
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo), check=True)
        (repo / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=str(repo), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)
        return repo

    def test_claude_handoff_to_gemini_and_codex_shared_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_repo(Path(tmp))
            events: list[dict[str, Any]] = []
            handoffs: dict[str, dict[str, Any]] = {}

            class SharedMockBridge(BaseHTTPRequestHandler):
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
                        self._json(200, {"items": [{"id": "proj-shared"}]})
                    elif self.path == "/v1/projects/proj-shared/worktrees":
                        self._json(200, {"items": [{"id": "wt-shared", "host_path": str(repo)}]})
                    elif self.path.startswith("/v1/handoffs/TASK-V11-01"):
                        h = handoffs.get("TASK-V11-01")
                        if h:
                            self._json(200, h)
                        else:
                            self._json(404, {"error": {"code": "not_found", "message": "No handoff"}})
                    else:
                        self._json(404, {})

                def do_POST(self) -> None:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if self.path == "/v1/search/memory":
                        self._json(
                            200,
                            {"items": [{"id": "m1", "title": "Shared Index", "excerpt": "Use MCP first."}]},
                        )
                    elif self.path == "/v1/events":
                        events.append(body)
                        if body.get("kind") == "handoff_created" and body.get("task_id"):
                            handoffs[body["task_id"]] = {
                                "task_id": body["task_id"],
                                "project_id": body["project_id"],
                                "worktree_id": body["worktree_id"],
                                "source_agent": body["source_agent"],
                                "summary": body["payload"]["summary"],
                            }
                        self._json(201, {"accepted": True})
                    else:
                        self._json(404, {})

            srv = ThreadingHTTPServer(("127.0.0.1", 0), SharedMockBridge)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                port = srv.server_address[1]
                env = dict(os.environ)
                env["AI_MEMORY_BRIDGE_URL"] = f"http://127.0.0.1:{port}"
                env["AI_MEMORY_BRIDGE_READ_TOKEN"] = "tok-read"
                env["AI_MEMORY_BRIDGE_WRITE_TOKEN"] = "tok-write"

                # 1. Claude creates handoff and ends session
                p_create = subprocess.run(
                    [
                        sys.executable,
                        str(HANDOFF_CLI),
                        "create",
                        "--source-agent",
                        "claude",
                        "--target-agent",
                        "gemini",
                        "--task-id",
                        "TASK-V11-01",
                        "--summary",
                        "Claude finished API schema; Gemini add tests.",
                        "--cwd",
                        str(repo),
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_create.returncode, 0, p_create.stdout + p_create.stderr)

                p_claude_end = subprocess.run(
                    [
                        sys.executable,
                        str(HOOKS_DIR / "session_event.py"),
                        "--kind",
                        "session_end",
                        "--source-agent",
                        "claude",
                        "--task-id",
                        "TASK-V11-01",
                    ],
                    input=json.dumps({"cwd": str(repo), "session_id": "s-claude"}),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_claude_end.returncode, 0)

                # 2. Gemini starts session (JSON format) and receives Claude's handoff
                p_gemini_start = subprocess.run(
                    [
                        sys.executable,
                        str(HOOKS_DIR / "session_start.py"),
                        "--source-agent",
                        "gemini",
                        "--format",
                        "gemini",
                        "--task-id",
                        "TASK-V11-01",
                    ],
                    input=json.dumps({"cwd": str(repo)}),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_gemini_start.returncode, 0)
                gemini_out = json.loads(p_gemini_start.stdout)
                ctx = gemini_out["hookSpecificOutput"]["additionalContext"]
                self.assertIn("TASK-V11-01", ctx)
                self.assertIn("Claude finished API schema", ctx)

                # Gemini hands off to Codex
                p_gemini_end = subprocess.run(
                    [
                        sys.executable,
                        str(HOOKS_DIR / "session_event.py"),
                        "--kind",
                        "session_end",
                        "--source-agent",
                        "gemini",
                        "--task-id",
                        "TASK-V11-01",
                    ],
                    input=json.dumps({"cwd": str(repo), "session_id": "s-gemini"}),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_gemini_end.returncode, 0)

                # 3. Codex retrieves handoff and completes session
                p_codex_get = subprocess.run(
                    [
                        sys.executable,
                        str(HANDOFF_CLI),
                        "get",
                        "--task-id",
                        "TASK-V11-01",
                        "--cwd",
                        str(repo),
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_codex_get.returncode, 0)
                self.assertIn("Claude finished API schema", p_codex_get.stdout)

                p_codex_end = subprocess.run(
                    [
                        sys.executable,
                        str(HOOKS_DIR / "session_event.py"),
                        "--kind",
                        "session_end",
                        "--source-agent",
                        "codex",
                        "--task-id",
                        "TASK-V11-01",
                    ],
                    input=json.dumps({"cwd": str(repo), "session_id": "s-codex"}),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertEqual(p_codex_end.returncode, 0)

                # Verify shared scope and distinct source_agent values
                agents_seen = [e["source_agent"] for e in events]
                self.assertEqual(agents_seen, ["claude", "claude", "gemini", "codex"])
                for ev in events:
                    self.assertEqual(ev["project_id"], "proj-shared")
                    self.assertEqual(ev["worktree_id"], "wt-shared")
                    self.assertEqual(ev["task_id"], "TASK-V11-01")
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
