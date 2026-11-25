import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.orchestrator.dispatch.cli_runner import (  # noqa: E402
    UnsafeCLIRequestError,
    build_cli_command,
    discover_supported_quotas,
    parse_cli_telemetry,
)
from services.orchestrator.dispatch.host_runner import HostRunner  # noqa: E402


class FakeRunnerBridge:
    def __init__(self, job: dict[str, Any] | None) -> None:
        self.job = job
        self.sessions: list[dict[str, Any]] = []
        self.usage: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.completions: list[dict[str, Any]] = []

    def claim_job(self, runner_id: str) -> dict[str, Any] | None:
        j = self.job
        self.job = None
        return j

    def heartbeat(self, job_id: str, lease_id: str, peak_context_tokens: int | None) -> dict[str, Any]:
        return {"ok": True}

    def emit_event(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    def checkpoint(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.checkpoints.append(payload)
        return {"ok": True}

    def complete(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.completions.append(payload)
        return {"ok": True}

    def post_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.sessions.append(payload)
        return {"ok": True}

    def post_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.usage.append(payload)
        return {"ok": True}


class TestHostRunner(unittest.TestCase):
    def test_command_builder_safety_and_no_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UnsafeCLIRequestError):
                build_cli_command(
                    cli_family="claude",
                    model_id="bad;rm -rf /",
                    worktree_path=tmp,
                    prompt="hi",
                    executable="claude",
                )
            with self.assertRaises(UnsafeCLIRequestError):
                build_cli_command(
                    cli_family="claude",
                    model_id="claude-sonnet-5-5-medium",
                    worktree_path=str(Path(tmp) / ".." / "escape"),
                    prompt="hi",
                    executable="claude",
                )

            cmd, _ = build_cli_command(
                cli_family="claude",
                model_id="claude-sonnet-5-5-medium",
                effort="medium",
                worktree_path=tmp,
                prompt="Implement test",
                executable="claude",
            )
            joined = " ".join(cmd)
            self.assertNotIn("--dangerously-skip-permissions", joined)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", joined)

    def test_telemetry_missing_counters_are_none_and_redacted(self) -> None:
        counters, sid, summary = parse_cli_telemetry(
            cli_family="claude",
            stdout=json.dumps({"result": r"Used sk-secret123456789012 in C:\Users\nghia\app.py", "session_id": "s1"}),
        )
        self.assertEqual(sid, "s1")
        self.assertEqual(counters.measurement_kind, "unavailable")
        self.assertIsNone(counters.input_tokens)
        self.assertIsNone(counters.output_tokens)
        self.assertIsNone(counters.cost_usd)
        self.assertNotIn("sk-secret123456789012", summary)
        self.assertNotIn(r"C:\Users\nghia", summary)
        self.assertEqual(discover_supported_quotas("claude"), [])

    def test_host_runner_rejects_unapproved_and_handles_budget_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Unapproved job rejected
            b1 = FakeRunnerBridge({"job_id": "j1", "lease_id": "l1"})
            r1 = HostRunner(b1).run_once()
            self.assertEqual(r1["reason"], "unapproved_revision")
            self.assertEqual(b1.completions[0]["error_code"], "unapproved_revision")

            # 2. Approved job succeeds with codex JSONL telemetry
            job_ok = {
                "job_id": "j2",
                "lease_id": "l2",
                "project_id": str(uuid4()),
                "worktree_id": str(uuid4()),
                "plan_revision_id": str(uuid4()),
                "revision_hash": "a" * 64,
                "cli_family": "codex",
                "model_id": "gpt-oss-120b-medium",
                "worktree_path": tmp,
                "prompt": "Run checks",
                "budget_input_tokens": 5000,
            }
            codex_jsonl = "\n".join(
                [
                    json.dumps({"thread_id": "th-1", "type": "turn.completed", "content": "Done"}),
                    json.dumps({"usage": {"input_tokens": 120, "output_tokens": 45, "cached_input_tokens": 30}}),
                ]
            )
            b2 = FakeRunnerBridge(job_ok)
            r2 = HostRunner(
                b2,
                executable_overrides={"codex": "codex"},
                subprocess_runner=lambda cmd, cwd, t: (0, codex_jsonl, ""),
            ).run_once()
            self.assertEqual(r2["status"], "succeeded")
            self.assertEqual(b2.usage[0]["usage"]["input_tokens"], 120)
            self.assertEqual(b2.usage[0]["usage"]["cache_read_tokens"], 30)
            self.assertEqual(b2.completions[0]["outcome"], "succeeded")

            # 3. Timeout triggers checkpoint (which releases lease on Bridge)
            b3 = FakeRunnerBridge(job_ok)

            def raise_timeout(cmd: list[str], cwd: str, t: float) -> tuple[int, str, str]:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=t)

            r3 = HostRunner(
                b3,
                executable_overrides={"codex": "codex"},
                subprocess_runner=raise_timeout,
            ).run_once()
            self.assertEqual(r3["reason"], "timeout")
            self.assertEqual(len(b3.checkpoints), 1)
            self.assertIn("timed out", b3.checkpoints[0]["handoff_summary"].lower())


if __name__ == "__main__":
    unittest.main()
