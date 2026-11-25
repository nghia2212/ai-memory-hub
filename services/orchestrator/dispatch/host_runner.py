"""Narrow authenticated host runner polling /v2/jobs/claim with leases, checkpoints & telemetry."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Protocol

from .cli_runner import UnsafeCLIRequestError, build_cli_command, parse_cli_telemetry


class RunnerBridgeProtocol(Protocol):
    def claim_job(self, runner_id: str) -> dict[str, Any] | None:
        ...

    def heartbeat(self, job_id: str, lease_id: str, peak_context_tokens: int | None) -> dict[str, Any]:
        ...

    def emit_event(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def checkpoint(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def complete(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def post_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def post_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class HttpRunnerBridge:
    """Authenticated HTTP client for Memory Bridge /v2 runner endpoints."""

    def __init__(self, base_url: str, runner_token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = runner_token
        self.timeout = timeout

    def _request(self, path: str, method: str = "POST", payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 204:
                    return None
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 204:
                return None
            raise RuntimeError(f"Bridge {method} {path} HTTP {exc.code}") from exc

    def claim_job(self, runner_id: str) -> dict[str, Any] | None:
        return self._request("/v2/jobs/claim", payload={"runner_id": runner_id})

    def heartbeat(self, job_id: str, lease_id: str, peak_context_tokens: int | None) -> dict[str, Any]:
        return (
            self._request(
                f"/v2/jobs/{job_id}/heartbeat",
                payload={"lease_id": lease_id, "peak_context_tokens": peak_context_tokens},
            )
            or {}
        )

    def emit_event(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(f"/v2/jobs/{job_id}/events", payload=payload) or {}

    def checkpoint(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(f"/v2/jobs/{job_id}/checkpoint", payload=payload) or {}

    def complete(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(f"/v2/jobs/{job_id}/complete", payload=payload) or {}

    def post_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v2/sessions", payload=payload) or {}

    def post_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v2/usage", payload=payload) or {}


class HostRunner:
    """Executes one bounded approved subtask per fresh CLI session."""

    def __init__(
        self,
        bridge: RunnerBridgeProtocol,
        *,
        runner_id: str = "win-host-runner-1",
        executable_overrides: dict[str, str] | None = None,
        subprocess_runner: Any = None,
    ) -> None:
        self.bridge = bridge
        self.runner_id = runner_id
        self.executable_overrides = executable_overrides or {}
        self._run_proc = subprocess_runner or self._default_run_proc

    @staticmethod
    def _default_run_proc(cmd: list[str], cwd: str, timeout: float) -> tuple[int, str, str]:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def run_once(self) -> dict[str, Any]:
        job = self.bridge.claim_job(self.runner_id)
        if not job:
            return {"claimed": False}

        job_id = str(job["job_id"])
        lease_id = str(job["lease_id"])
        plan_rev_id = job.get("plan_revision_id")
        rev_hash = job.get("revision_hash")
        if not plan_rev_id or not rev_hash:
            self.bridge.complete(
                job_id,
                {"lease_id": lease_id, "status": "failed", "error_code": "unapproved_revision"},
            )
            return {"claimed": True, "job_id": job_id, "status": "failed", "reason": "unapproved_revision"}

        cli_family = str(job["cli_family"])
        model_id = str(job["model_id"])
        effort = job.get("effort")
        worktree_path = str(job["worktree_path"])
        prompt = str(job["prompt"])
        timeout_s = float(job.get("timeout_seconds") or 120.0)
        budget_cost = job.get("budget_cost_usd")
        budget_in = job.get("budget_input_tokens")

        session_id = hashlib.sha256(f"{job_id}:{lease_id}".encode("utf-8")).hexdigest()[:32]
        now_iso = datetime.now(timezone.utc).isoformat()
        self.bridge.post_session(
            {
                "id": session_id,
                "project_id": job["project_id"],
                "worktree_id": job["worktree_id"],
                "plan_id": job.get("plan_id"),
                "plan_revision_id": plan_rev_id,
                "task_id": job.get("task_id"),
                "attempt_id": job.get("attempt_id"),
                "role": "executor",
                "provider": job.get("provider") or cli_family,
                "model_id": model_id,
                "reasoning_effort": effort,
                "auth_mode": job.get("auth_mode") or "subscription_cli",
                "status": "running",
                "started_at": now_iso,
            }
        )

        try:
            cmd, cwd = build_cli_command(
                cli_family=cli_family,
                model_id=model_id,
                worktree_path=worktree_path,
                prompt=prompt,
                effort=effort,
                executable=self.executable_overrides.get(cli_family),
                max_budget_usd=float(budget_cost) if budget_cost is not None else None,
            )
        except UnsafeCLIRequestError as exc:
            self.bridge.complete(
                job_id,
                {"lease_id": lease_id, "status": "failed", "error_code": "unsafe_request", "summary": str(exc)},
            )
            return {"claimed": True, "job_id": job_id, "status": "failed", "reason": "unsafe_request"}

        self.bridge.heartbeat(job_id, lease_id, peak_context_tokens=None)
        try:
            rc, stdout, _stderr = self._run_proc(cmd, cwd, timeout_s)
        except subprocess.TimeoutExpired:
            self.bridge.checkpoint(
                job_id,
                {
                    "lease_id": lease_id,
                    "session_id": session_id,
                    "reason": "timeout_checkpoint",
                    "handoff_summary": "Subtask timed out; resume in fresh continuation session.",
                },
            )
            self.bridge.complete(job_id, {"lease_id": lease_id, "status": "failed", "error_code": "timeout"})
            return {"claimed": True, "job_id": job_id, "status": "failed", "reason": "timeout"}

        counters, upstream_sid, summary = parse_cli_telemetry(cli_family=cli_family, stdout=stdout)
        self.bridge.post_usage(
            {
                "source_event_id": f"{session_id}:final",
                "session_id": session_id,
                "project_id": job["project_id"],
                "worktree_id": job["worktree_id"],
                "sequence_no": 1,
                "counter_mode": counters.counter_mode,
                "measurement_kind": counters.measurement_kind,
                "input_tokens": counters.input_tokens,
                "output_tokens": counters.output_tokens,
                "cache_read_tokens": counters.cache_read_tokens,
                "cache_write_tokens": counters.cache_write_tokens,
                "reasoning_tokens": counters.reasoning_tokens,
                "cost_usd": counters.cost_usd,
                "source": counters.source,
                "observed_at": counters.observed_at or datetime.now(timezone.utc).isoformat(),
            }
        )

        if budget_in is not None and counters.input_tokens is not None and counters.input_tokens >= int(budget_in):
            self.bridge.checkpoint(
                job_id,
                {
                    "lease_id": lease_id,
                    "session_id": session_id,
                    "reason": "budget_checkpoint",
                    "handoff_summary": summary,
                },
            )
            self.bridge.complete(
                job_id,
                {
                    "lease_id": lease_id,
                    "session_id": session_id,
                    "upstream_session_id": upstream_sid,
                    "status": "budget_exceeded",
                    "handoff_summary": summary,
                },
            )
            return {"claimed": True, "job_id": job_id, "status": "budget_exceeded"}

        final_status = "succeeded" if rc == 0 else "failed"
        self.bridge.complete(
            job_id,
            {
                "lease_id": lease_id,
                "session_id": session_id,
                "upstream_session_id": upstream_sid,
                "status": final_status,
                "handoff_summary": summary,
            },
        )
        return {"claimed": True, "job_id": job_id, "status": final_status, "session_id": session_id}


def main() -> int:
    bridge_url = os.environ.get("AI_MEMORY_BRIDGE_URL", "http://127.0.0.1:8082")
    runner_token = os.environ.get("BRIDGE_RUNNER_TOKEN", "")
    runner_id = os.environ.get("AI_MEMORY_RUNNER_ID", "win-host-runner-1")
    runner = HostRunner(HttpRunnerBridge(bridge_url, runner_token), runner_id=runner_id)
    res = runner.run_once()
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
