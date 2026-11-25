"""Host Brain Agent: zero-dependency loopback bridge between Docker Dashboard/Bridge and Windows host CLIs.

Security:
- Binds only to 127.0.0.1 (reachable from Docker Desktop containers via host.docker.internal).
- Requires Bearer BRIDGE_RUNNER_TOKEN on every request except /healthz.
- Never auto-activates paid API keys; trial execution requires explicit user_approved=true.
- Runs periodic catalog refresh (default every 6 hours) to detect new/retiring models and Gemini 4 Neon.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HUB_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = HUB_ROOT / ".env"
# The Windows launcher historically invokes this file directly.
if str(HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(HUB_ROOT))


KNOWN_CATALOG_MODELS: list[dict[str, Any]] = [
    {
        "provider": "google",
        "model_id": "gemini-4-hydrogen",
        "cli_family": "agy",
        "auth_mode": "subscription_cli",
        "status": "unavailable",
        "capabilities": {
            "verification_stage": "catalog_listed",
            "display_name": "Gemini 4 Hydrogen",
            "tier_hint": "tier1_light",
            "retiring_soon": False,
        },
        "source_url": "https://antigravity.google/docs/models",
    },
    {
        "provider": "google",
        "model_id": "gemini-4-neon-medium",
        "cli_family": "agy",
        "auth_mode": "subscription_cli",
        "status": "unavailable",
        "capabilities": {
            "verification_stage": "catalog_listed",
            "display_name": "Gemini 4 Neon (Medium)",
            "tier_hint": "tier2_primary",
            "retiring_soon": False,
        },
        "source_url": "https://antigravity.google/docs/models",
    },
    {
        "provider": "google",
        "model_id": "gemini-4-carbon",
        "cli_family": "agy",
        "auth_mode": "subscription_cli",
        "status": "unavailable",
        "capabilities": {
            "verification_stage": "catalog_listed",
            "display_name": "Gemini 4 Carbon",
            "tier_hint": "tier3_advanced",
            "retiring_soon": False,
        },
        "source_url": "https://antigravity.google/docs/models",
    },
    {
        "provider": "deepseek",
        "model_id": "deepseek-flash",
        "cli_family": "api",
        "auth_mode": "api_key",
        "status": "candidate",
        "capabilities": {
            "verification_stage": "catalog_listed",
            "display_name": "DeepSeek Flash (API trả phí — tắt mặc định)",
            "paid_api": True,
            "retiring_soon": False,
        },
        "source_url": "https://api-docs.deepseek.com/",
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_env_map(path: Path = ENV_PATH) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k not in out:
            out[k] = v
    return out


def _run_cmd(args: list[str], *, cwd: str | None = None, timeout: int = 25, managed: bool = False) -> tuple[int, str, str]:
    exe = shutil.which(args[0])
    if not exe:
        return 127, "", f"CLI not found on PATH: {args[0]}"
    cmd = [exe, *args[1:]]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env={**os.environ, "AI_MEMORY_MANAGED_SESSION": "1"} if managed else None,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Timeout after {timeout}s"
    except Exception as exc:
        return 1, "", str(exc)


def _bridge_request(
    env: dict[str, str],
    method: str,
    path: str,
    *,
    token_key: str = "BRIDGE_ADMIN_TOKEN",
    body: dict[str, Any] | None = None,
    timeout: int = 15,
) -> tuple[int, dict[str, Any]]:
    port = env.get("BRIDGE_PORT", "8082")
    url = f"http://127.0.0.1:{port}{path}"
    token = env.get(token_key, "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data: bytes | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"error": raw}
        return exc.code, parsed
    except Exception as exc:
        return 503, {"error": str(exc)}


def discover_host_clis() -> dict[str, Any]:
    """Scan agy, claude, codex, and gemini CLIs on the Windows host."""
    now = utc_now_iso()
    clis: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    live_agy_ids: set[str] = set()

    # 1. Antigravity (agy)
    rc, ver_out, _ = _run_cmd(["agy", "--version"], timeout=10)
    agy_installed = rc == 0
    agy_version = ver_out.splitlines()[0].strip() if agy_installed and ver_out else None
    agy_models_parsed: list[tuple[str, str]] = []
    if agy_installed:
        mrc, mout, _ = _run_cmd(["agy", "models"], timeout=20)
        if mrc == 0:
            for line in mout.splitlines():
                line = line.strip()
                if not line or line.lower().startswith("fetching"):
                    continue
                if "\t" in line:
                    mid, label = line.split("\t", 1)
                    agy_models_parsed.append((mid.strip(), label.strip()))
                else:
                    parts = re.split(r"\s{2,}", line, maxsplit=1)
                    if parts and re.match(r"^[a-z0-9][a-z0-9._-]+$", parts[0]):
                        agy_models_parsed.append((parts[0], parts[1] if len(parts) > 1 else parts[0]))
    for mid, label in agy_models_parsed:
        live_agy_ids.add(mid)
        effort = None
        for eff in ("low", "medium", "high"):
            if mid.endswith(f"-{eff}"):
                effort = eff
                break
        models.append(
            {
                "provider": "agy",
                "model_id": mid,
                "cli_family": "agy",
                "cli_version": agy_version,
                "auth_mode": "subscription_cli",
                "status": "verified",
                "capabilities": {
                    "verification_stage": "cli_available",
                    "display_name": label,
                    "effort": effort,
                    "noninteractive": True,
                    "retiring_soon": False,
                },
                "source_url": "local://agy/models",
                "verified_at": now,
            }
        )
    clis.append(
        {
            "cli_name": "agy",
            "installed": agy_installed,
            "version": agy_version,
            "auth_mode": "subscription_cli",
            "verified_model_ids": sorted(live_agy_ids),
            "verified_at": now,
        }
    )

    # 2. Claude Code (claude)
    rc, cver, _ = _run_cmd(["claude", "--version"], timeout=10)
    claude_installed = rc == 0
    claude_ver = cver.splitlines()[0].strip() if claude_installed and cver else None
    clis.append(
        {
            "cli_name": "claude",
            "installed": claude_installed,
            "version": claude_ver,
            "auth_mode": "subscription_cli",
            "verified_model_ids": [],
            "verified_at": now,
        }
    )

    # 3. Codex CLI (codex)
    rc, xver, _ = _run_cmd(["codex", "--version"], timeout=10)
    codex_installed = rc == 0
    codex_ver = xver.splitlines()[0].strip() if codex_installed and xver else None
    clis.append(
        {
            "cli_name": "codex",
            "installed": codex_installed,
            "version": codex_ver,
            "auth_mode": "subscription_cli",
            "verified_model_ids": [],
            "verified_at": now,
        }
    )

    # 4. Gemini CLI (honestly report uninstalled)
    rc, gver, _ = _run_cmd(["gemini", "--version"], timeout=8)
    gemini_installed = rc == 0
    clis.append(
        {
            "cli_name": "gemini",
            "installed": gemini_installed,
            "version": gver.splitlines()[0].strip() if gemini_installed and gver else None,
            "auth_mode": "unknown",
            "verified_model_ids": [],
            "notes": "Đã gỡ (Google ngừng hỗ trợ Sign in with Google cho Gemini CLI cá nhân; chuyển sang Antigravity agy)."
            if not gemini_installed
            else None,
            "verified_at": now,
        }
    )

    # 5. Merge catalog-listed models that are NOT yet live in agy models
    for known in KNOWN_CATALOG_MODELS:
        if known["model_id"] not in live_agy_ids:
            models.append({**known, "verified_at": now})

    # Detect Gemini 4 Neon live availability
    neon_matches = [
        m["model_id"]
        for m in models
        if "gemini-4-neon" in m["model_id"].lower()
        and m.get("capabilities", {}).get("verification_stage") in {"cli_available", "execution_verified"}
    ]

    return {
        "discovered_at": now,
        "clis": clis,
        "models": models,
        "gemini4_neon_available": bool(neon_matches),
        "gemini4_neon_models": neon_matches,
    }


def sync_catalog_to_bridge(env: dict[str, str]) -> dict[str, Any]:
    disc = discover_host_clis()
    payload = {
        "version": 1,
        "source_urls": ["local://agy/models", "local://claude/cli", "local://codex/cli"],
        "models": disc["models"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "is_stale": False,
    }
    code, resp = _bridge_request(env, "POST", "/v2/catalog/refresh", token_key="BRIDGE_ADMIN_TOKEN", body=payload)
    return {
        "ok": code in (200, 201),
        "status_code": code,
        "discovery": disc,
        "bridge_response": resp,
    }


def probe_model(
    env: dict[str, str],
    *,
    provider: str,
    model_id: str,
    cli_family: str | None = None,
    mode: str = "connection_check",
    user_approved: bool = False,
    allow_paid_api: bool = False,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Verify CLI connection (0 tokens) or run an approved minimal trial prompt."""
    now = utc_now_iso()
    cli = (cli_family or provider or "agy").strip().lower()
    if provider == "deepseek" or cli == "api":
        if not allow_paid_api:
            return {
                "ok": False,
                "stage": "catalog_listed",
                "message": "Từ chối gọi DeepSeek API trả phí khi chưa cấp phép (allow_paid_api=false).",
                "checked_at": now,
            }
        return {
            "ok": False,
            "stage": "catalog_listed",
            "message": "Chưa cấu hình DEEPSEEK_API_KEY trên máy chủ.",
            "checked_at": now,
        }
    if provider == "local" or model_id == "deterministic-rules":
        return {
            "ok": True,
            "stage": "execution_verified",
            "latency_ms": 1,
            "message": "Quy tắc cục bộ (0 LLM) luôn sẵn sàng.",
            "checked_at": now,
        }

    # Connection check (0 tokens)
    disc = discover_host_clis()
    cli_info = next((c for c in disc["clis"] if c["cli_name"] == cli), None)
    if not cli_info or not cli_info["installed"]:
        return {
            "ok": False,
            "stage": "unavailable",
            "message": f"CLI '{cli}' chưa được cài đặt trên host.",
            "checked_at": now,
        }

    if cli == "agy" and model_id not in cli_info["verified_model_ids"]:
        return {
            "ok": False,
            "stage": "catalog_listed",
            "cli_version": cli_info["version"],
            "message": f"Model '{model_id}' chưa xuất hiện trong `agy models` ({cli_info['version']}).",
            "checked_at": now,
        }

    if mode != "trial_run" and cli != "agy":
        return {"ok": True, "stage": "cli_installed", "message": "CLI đã cài. Model chưa được chạy thử.", "checked_at": now}

    if mode != "trial_run":
        return {
            "ok": True,
            "stage": "cli_available",
            "cli_version": cli_info["version"],
            "message": f"Đã xác minh kết nối CLI `{cli}` ({cli_info['version']}) và model `{model_id}` khả dụng (0 token).",
            "checked_at": now,
        }

    # Live trial run requires explicit user approval
    if not user_approved:
        return {
            "ok": False,
            "stage": "cli_available",
            "message": "Chạy thử model yêu cầu xác nhận phê duyệt từ người dùng (user_approved=true).",
            "checked_at": now,
        }

    t0 = time.perf_counter()
    prompt = "Reply strictly with the single word OK."
    if cli == "agy":
        rc, out, err = _run_cmd(["agy", "--mode", "plan", "--model", model_id, "--print", prompt], timeout=60)
    elif cli == "claude":
        rc, out, err = _run_cmd(["claude", "-p", "--tools", "", "--model", model_id, prompt], timeout=60)
    elif cli == "codex":
        rc, out, err = _run_cmd(["codex", "exec", "--sandbox", "read-only", "--model", model_id, prompt], timeout=60)
    else:
        return {"ok": False, "stage": "unavailable", "message": f"Unsupported CLI: {cli}", "checked_at": now}

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    ok = rc == 0 and out.strip().upper() == "OK"
    excerpt = (out or err)[:300]

    # Record observed evaluation in Memory Bridge so catalog upgrades model to execution_verified
    if ok:
        eval_body: dict[str, Any] = {
            "model_id": model_id,
            "provider": provider,
            "auth_mode": "subscription_cli",
            "task_type": "routine",
            "complexity": "XS",
            "evidence_kind": "observed",
            "correctness_score": 1.0,
            "acceptance_rate": 1.0,
            "avg_latency_ms": float(elapsed_ms),
            "failure_rate": 0.0,
            "sample_count": 1,
            "provenance": {"source": "host_brain_agent_trial", "cli": cli, "cli_version": cli_info["version"]},
        }
        if project_id:
            eval_body["project_id"] = project_id
        _bridge_request(env, "POST", "/v2/evaluations", token_key="BRIDGE_PLANNER_TOKEN", body=eval_body)

    return {
        "ok": ok,
        "stage": "execution_verified" if ok else "cli_available",
        "cli_version": cli_info["version"],
        "latency_ms": elapsed_ms,
        "exit_code": rc,
        "output_excerpt": excerpt,
        "message": (
            f"Chạy thử thành công `{cli}` / `{model_id}` trong {elapsed_ms} ms -> đã nâng trạng thái lên execution_verified."
            if ok
            else f"Chạy thử thất bại (exit={rc}): {excerpt}"
        ),
        "checked_at": now,
    }


def execute_approved_plan(
    env: dict[str, str],
    *,
    project_id: str,
    worktree_id: str,
    plan_id: str,
    worktree_path: str | None = None,
) -> dict[str, Any]:
    """Claim approved job from Memory Bridge, execute assigned CLI non-interactively, and report completion."""
    runner_id = f"host-brain-agent-{os.getpid()}"
    code, claim = _bridge_request(
        env,
        "POST",
        "/v2/jobs/claim",
        token_key="BRIDGE_RUNNER_TOKEN",
        body={
            "project_id": project_id,
            "worktree_id": worktree_id,
            "runner_id": runner_id,
            "lease_seconds": 300,
            "plan_id": plan_id,
        },
    )
    if code != 200 or not claim.get("claimed"):
        return {
            "ok": False,
            "status_code": code,
            "message": "Không có tác vụ APPROVED nào sẵn sàng để claim (hãy đảm bảo kế hoạch đã được Phê duyệt).",
            "claim": claim,
        }

    job_id = claim.get("job_id") or claim.get("task_id")
    lease_id = claim["lease_id"]
    session_id = claim.get("session_id")
    provider = claim.get("provider")
    model_id = claim["model_id"]
    effort = claim.get("effort")
    auth_mode = claim.get("auth_mode") or "subscription_cli"
    objective = (claim.get("context_pack") or {}).get("objective") or "Summarize task"
    effective_wt_path = claim.get("worktree_path")

    t0 = time.perf_counter()
    prompt = (
        f"[AI Memory Hub V2 Approved Task]\n"
        f"Objective: {objective}\n"
        f"Constraint: Read-only analysis only. Reply concisely in Vietnamese (<= 4 bullet points)."
    )
    stop = threading.Event()
    heartbeat = {"project_id": project_id, "lease_id": lease_id, "runner_id": runner_id, "lease_seconds": 300}

    lost = threading.Event()

    def keep_alive():
        while not stop.wait(12):
            status, _ = _bridge_request(env, "POST", f"/v2/jobs/{job_id}/heartbeat",
                token_key="BRIDGE_RUNNER_TOKEN", body=heartbeat, timeout=5)
            if status != 200:
                lost.set()
                break

    status, _ = _bridge_request(env, "POST", f"/v2/jobs/{job_id}/heartbeat",
        token_key="BRIDGE_RUNNER_TOKEN", body=heartbeat)
    if status != 200:
        return {"ok": False, "job_id": job_id, "message": "Lease unavailable before CLI start"}
    worker = threading.Thread(target=keep_alive, daemon=True)
    worker.start()
    try:
        cwd = effective_wt_path
        if not cwd or not Path(cwd).is_dir():
            rc, out, err = 1, "", "Registered worktree unavailable"
        elif provider == "local" or model_id == "deterministic-rules":
            rc, out, err = 0, f"[local-rules] Đã kiểm tra nhanh mục tiêu: {objective[:200]}", ""
        elif provider == "claude":
            rc, out, err = _run_cmd(["claude", "-p", "--tools", "", "--output-format", "json", "--model", model_id, prompt], cwd=cwd, timeout=90, managed=True)
        elif provider == "codex":
            rc, out, err = _run_cmd(["codex", "exec", "--json", "--sandbox", "read-only", "--model", model_id, prompt], cwd=cwd, timeout=90, managed=True)
        elif provider == "agy" and auth_mode == "subscription_cli":
            rc, out, err = _run_cmd(["agy", "--mode", "plan", "--model", model_id, "--output-format", "json", "--print", prompt], cwd=cwd, timeout=90, managed=True)
        else:
            rc, out, err = 1, "", "Unsupported approved CLI adapter"
    finally:
        stop.set()
        worker.join(timeout=6)

    if lost.is_set():
        return {"ok": False, "job_id": job_id, "session_id": session_id, "message": "Heartbeat lost; outcome unknown. Inspect before retry."}
    duration_ms = int((time.perf_counter() - t0) * 1000)
    quota_error = re.search(r"(?i)(?:quota exceeded|rate.limit exceeded|resource.exhausted|quota will reset)", (err or out)[:2000])
    succeeded = rc == 0 and bool(out) and not quota_error
    # Use the same parser as the polling runner. A telemetry failure must
    # not trigger another CLI invocation or prevent lease completion.
    usage_status = None
    summary = "No structured result available."
    try:
        from services.orchestrator.dispatch.cli_runner import parse_cli_telemetry, redact_summary
        counters, _, summary = parse_cli_telemetry(cli_family=str(provider), stdout=out)
        if not summary:
            summary = redact_summary(err or "No output")
        if session_id:
            usage_status, _ = _bridge_request(
                env, "POST", "/v2/usage", token_key="BRIDGE_RUNNER_TOKEN",
                body={"source_event_id": f"{session_id}:final", "session_id": session_id,
                      "project_id": project_id, "worktree_id": worktree_id,
                      "sequence_no": 1, "usage": counters.model_dump(mode="json")},
            )
    except Exception:
        usage_status = 503


    # Complete job on Memory Bridge (Bridge updates task, session & plan status atomically)
    cc, comp_resp = _bridge_request(
        env,
        "POST",
        f"/v2/jobs/{job_id}/complete",
        token_key="BRIDGE_RUNNER_TOKEN",
        body={
            "project_id": project_id,
            "lease_id": lease_id,
            "outcome": "succeeded" if succeeded else "failed",
            "accepted": None,
            "tests_passed": None,
            "duration_ms": duration_ms,
            "handoff_summary": summary,
            "error_code": None if succeeded else ("quota_exceeded" if quota_error else f"cli_exit_{rc}"),
            "error_summary": None if succeeded else f"Exit code {rc}",
        },
    )

    return {
        "ok": succeeded and cc == 200,
        "job_id": job_id,
        "session_id": session_id,
        "provider": provider,
        "model_id": model_id,
        "duration_ms": duration_ms,
        "exit_code": rc,
        "message": "Kết quả đã lưu trong lịch sử" if cc == 200 else "Chưa xác nhận lưu kết quả",
        "complete_response": comp_resp,
        "telemetry_status": usage_status,
    }


def make_handler(env: dict[str, str]) -> type[BaseHTTPRequestHandler]:
    expected_token = env.get("BRIDGE_RUNNER_TOKEN", "")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _check_auth(self) -> bool:
            if not expected_token:
                return False
            auth = self.headers.get("Authorization", "")
            return auth == f"Bearer {expected_token}"

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send(200, {"service": "host-brain-agent", "ready": True, "ts": utc_now_iso()})
                return
            if not self._check_auth():
                self._send(401, {"error": {"code": "unauthorized", "message": "Invalid runner token"}})
                return
            if self.path == "/host/clis":
                self._send(200, discover_host_clis())
                return
            self._send(404, {"error": {"code": "not_found", "message": "Not found"}})

        def do_POST(self) -> None:
            if not self._check_auth():
                self._send(401, {"error": {"code": "unauthorized", "message": "Invalid runner token"}})
                return
            try:
                body = self._read_json()
            except Exception:
                self._send(400, {"error": {"code": "bad_json", "message": "Invalid JSON"}})
                return

            if self.path == "/host/discover":
                res = sync_catalog_to_bridge(env)
                self._send(200 if res["ok"] else 502, res)
                return

            if self.path == "/host/probe-model":
                res = probe_model(
                    env,
                    provider=str(body.get("provider") or "agy"),
                    model_id=str(body.get("model_id") or ""),
                    cli_family=body.get("cli_family"),
                    mode=str(body.get("mode") or "connection_check"),
                    user_approved=bool(body.get("user_approved", False)),
                    allow_paid_api=bool(body.get("allow_paid_api", False)),
                    project_id=body.get("project_id"),
                )
                self._send(200, res)
                return

            if self.path == "/host/execute-plan":
                res = execute_approved_plan(
                    env,
                    project_id=str(body.get("project_id") or ""),
                    worktree_id=str(body.get("worktree_id") or ""),
                    plan_id=str(body.get("plan_id") or ""),
                    worktree_path=body.get("worktree_path"),
                )
                self._send(200 if res["ok"] else 409, res)
                return

            self._send(404, {"error": {"code": "not_found", "message": "Not found"}})

    return Handler


def _periodic_sync_loop(env: dict[str, str], interval_sec: int, stop_ev: threading.Event) -> None:
    # Run initial sync shortly after boot, then periodically
    stop_ev.wait(2.0)
    while not stop_ev.is_set():
        try:
            sync_catalog_to_bridge(env)
        except Exception:
            pass
        stop_ev.wait(interval_sec)


def main() -> None:
    env = load_env_map()
    port = int(env.get("HOST_BRAIN_PORT", "8084"))
    interval = max(300, int(env.get("CATALOG_REFRESH_INTERVAL_SEC", "21600")))
    stop_ev = threading.Event()
    t = threading.Thread(target=_periodic_sync_loop, args=(env, interval, stop_ev), daemon=True)
    t.start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(env))
    print(f"Host Brain Agent listening on http://127.0.0.1:{port} (catalog auto-refresh every {interval}s)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_ev.set()
        server.server_close()


if __name__ == "__main__":
    main()
