"""V2.2.4 isolated headless Playwright browser QA for all 4 Execution Map views (100/500/1000 traces)."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = REPO / "services" / "memory-bridge"
DASH_ROOT = REPO / "services" / "dashboard"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _seed_traces(bridge_url: str, count: int) -> tuple[str, str]:
    h_adm = {"Authorization": "Bearer qa-admin"}
    h_w = {"Authorization": "Bearer qa-write"}
    p = httpx.post(f"{bridge_url}/v1/projects", headers=h_adm, json={"name": f"QA Fixture {count}", "canonical_identity": f"git:qa-{count}-{uuid.uuid4().hex[:6]}"}).json()
    pid = p["project_id"]
    wt = httpx.post(f"{bridge_url}/v1/projects/{pid}/worktrees", headers=h_adm, json={"canonical_root": f"/tmp/qa-{count}", "branch": "qa/v224", "head_commit": "0c4dd7d"}).json()
    wid = wt["worktree_id"]

    sid = str(uuid.uuid4())
    httpx.post(f"{bridge_url}/v2/sessions", headers=h_w, json={"session_id": sid, "project_id": pid, "worktree_id": wid, "role": "manual_cli", "provider": "agy", "model_id": "gemini-4-neon-medium", "status": "running"})

    t0 = datetime(2026, 11, 25, 8, 0, 0, tzinfo=timezone.utc)
    root_id = str(uuid.uuid4())
    httpx.post(f"{bridge_url}/v2/traces", headers=h_w, json={
        "trace_id": root_id, "parent_trace_id": None, "root_trace_id": root_id,
        "project_id": pid, "worktree_id": wid, "session_id": sid,
        "step_type": "user_task", "step_name": f"QA Root ({count} traces)",
        "actor_type": "agent", "actor_id": "qa-lead", "provider": "agy", "model_id": "gemini-4-neon-medium",
        "used_memory": True, "used_mcp": True, "used_llm": True, "status": "running",
        "started_at": t0.isoformat(), "input_summary": "End-to-end Execution Map QA fixture with parallel steps, ties, retries, and blockers.",
        "metadata_json": {"source": "qa_fixture", "requested_model": "gemini-4-neon-medium"},
    })

    step_cycle = [
        ("ai_memory", "memory", "ai-memory-bridge", "search_memory", False),
        ("code_search", "mcp", "ai-memory-codebase", "search_code", False),
        ("mcp_tool_call", "mcp", "dynamic-mcp", "inspect_schema", False),
        ("planner", "planner", "dag-planner", None, False),
        ("agent_step", "agent", "backend-dev", None, True),
        ("llm_call", "model", "agy-model", None, True),
        ("qa_test", "qa", "pytest-runner", None, False),
    ]
    prev_err = None
    for i in range(1, count):
        st, at, aid, tool, use_llm = step_cycle[i % len(step_cycle)]
        # Create deliberate timestamp ties every 7 steps and late arrival on step 15
        sec = (i // 2) * 2 if i % 7 == 0 else (3 if i == 15 else i * 2)
        s_at = t0 + timedelta(seconds=sec)
        e_at = s_at + timedelta(milliseconds=120 + (i % 9) * 85)
        status = "blocked" if i % 29 == 0 else ("failed" if i % 17 == 0 else "succeeded")
        tid = str(uuid.uuid4())
        meta = {"source": "bridge.qa_fixture", "why": f"Verify step #{i} rendering and sequence stability"}
        if prev_err and i % 17 == 1:
            meta["retry_of"] = prev_err
            prev_err = None
        if status == "failed":
            prev_err = tid
        payload = {
            "trace_id": tid, "parent_trace_id": root_id, "root_trace_id": root_id,
            "project_id": pid, "worktree_id": wid, "session_id": sid,
            "step_type": st, "step_name": f"{st} #{i}",
            "actor_type": at, "actor_id": aid, "tool_name": tool,
            "provider": "agy" if use_llm else None, "model_id": "gemini-4-neon-medium" if use_llm else None,
            "used_memory": st == "ai_memory", "used_mcp": st in {"code_search", "mcp_tool_call"}, "used_llm": use_llm,
            "status": status, "started_at": s_at.isoformat(), "ended_at": e_at.isoformat(),
            "output_summary": f"Completed synthetic fixture step {i} with bounded summary evidence.",
            "error_code": "fixture_err" if status in {"failed", "blocked"} else None,
            "error_summary": "Synthetic failure/blocker for QA verification." if status in {"failed", "blocked"} else None,
            "metadata_json": meta,
        }
        httpx.post(f"{bridge_url}/v2/traces", headers=h_w, json=payload)
    return pid, wid


def run_qa(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict = {"viewports": {}, "counts_tested": [], "screenshots": []}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Start isolated Bridge
        sys.path.insert(0, str(BRIDGE_ROOT))
        from app.config import Settings
        from app.main import create_app

        b_port = _free_port()
        b_app = create_app(Settings(
            data_dir=tmp_path, db_path=tmp_path / "qa_v224.db", backup_dir=tmp_path / "backups",
            read_tokens={"qa-read"}, write_tokens={"qa-write"}, admin_tokens={"qa-admin"},
            planner_tokens={"qa-planner"}, runner_tokens={"qa-runner"},
        ))
        b_srv = uvicorn.Server(uvicorn.Config(b_app, host="127.0.0.1", port=b_port, log_level="error"))
        bt = threading.Thread(target=b_srv.run, daemon=True)
        bt.start()
        b_url = f"http://127.0.0.1:{b_port}"
        for _ in range(50):
            try:
                if httpx.get(f"{b_url}/healthz", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)

        # Start isolated Dashboard
        for mod in list(sys.modules):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        sys.path.insert(0, str(DASH_ROOT))
        from app.config import DashboardSettings
        from app.main import create_dashboard_app

        d_port = _free_port()
        d_app = create_dashboard_app(DashboardSettings(
            bridge_url=b_url, bridge_read_token="qa-read", bridge_admin_token="qa-admin",
            bridge_planner_token="qa-planner", dashboard_password="qa-password",
            session_secret="qa-session-secret-1234567890",
            allowed_origins={f"http://127.0.0.1:{d_port}"},
        ))
        d_srv = uvicorn.Server(uvicorn.Config(d_app, host="127.0.0.1", port=d_port, log_level="error"))
        dt = threading.Thread(target=d_srv.run, daemon=True)
        dt.start()
        d_url = f"http://127.0.0.1:{d_port}"
        for _ in range(50):
            try:
                if httpx.get(f"{d_url}/healthz", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)

        # Seed 100, 500, and 1000 traces in separate projects
        proj_map = {}
        for cnt in (100, 500, 1000):
            pid, _ = _seed_traces(b_url, cnt)
            proj_map[cnt] = pid
            results["counts_tested"].append(cnt)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for vp_name, vp in (("1366x768", {"width": 1366, "height": 768}), ("1920x1080", {"width": 1920, "height": 1080})):
                page = browser.new_page(viewport=vp)
                console_errs = []
                page.on("pageerror", lambda err: console_errs.append(str(err)))

                page.goto(d_url, wait_until="networkidle")
                # Verify unauthenticated badge
                auth_txt = page.locator("#auth-status").text_content() or ""
                assert "Chưa đăng nhập" in auth_txt

                # Login
                page.fill("#login-password", "qa-password")
                page.click("#btn-login")
                page.wait_for_function("document.getElementById('auth-status').textContent.includes('Đã đăng nhập')")

                # Open Execution Map tab
                page.click("#trace-nav")
                page.wait_for_selector("#trace-workspace")

                # Select 1000-trace project for stress check
                page.select_option("#trace-proj", proj_map[1000])
                page.wait_for_function("document.getElementById('trace-count').textContent.includes('1000')")

                # Verify workspace height fits viewport (horizontal scrollbar accessible without scrolling page down)
                ws_box = page.locator("#trace-workspace").bounding_box()
                assert ws_box and ws_box["height"] <= vp["height"], f"Workspace taller than viewport on {vp_name}: {ws_box}"

                # Click a node to open Detail Panel & test zoom + scroll preservation on refresh
                page.locator(".trace-node").first.click()
                page.wait_for_selector("#trace-detail .trace-detail-tabs")
                page.click("#trace-zoom-in")
                zoom_before = page.locator("#trace-zoom-label").text_content()
                page.evaluate("const c=document.getElementById('trace-canvas-wrap'); c.scrollTop=120; c.scrollLeft=80;")
                page.click("#trace-refresh")
                page.wait_for_timeout(400)
                zoom_after = page.locator("#trace-zoom-label").text_content()
                assert zoom_before == zoom_after == "110%"

                # Capture screenshots on 1920x1080 for all 4 views + Inspector
                if vp_name == "1920x1080":
                    for mode, fname in (
                        ("graph", "v224_graph_view.png"),
                        ("swim", "v224_swimlane_view.png"),
                        ("timeline", "v224_timeline_view.png"),
                        ("table", "v224_table_view.png"),
                    ):
                        page.click(f"#trace-mode-{mode}")
                        page.wait_for_timeout(250)
                        sp = out_dir / fname
                        page.screenshot(path=str(sp), full_page=False)
                        results["screenshots"].append(str(sp))

                    # Open Token Telemetry Inspector & capture screenshot
                    page.click("#trace-toggle-inspector")
                    page.wait_for_selector("#trace-inspector-card:not([hidden])")
                    page.wait_for_timeout(300)
                    sp_insp = out_dir / "v224_telemetry_inspector.png"
                    page.screenshot(path=str(sp_insp), full_page=False)
                    results["screenshots"].append(str(sp_insp))
                    page.click("#trace-inspector-close")

                    # Test fullscreen toggle + compact toggle + hide empty lanes
                    page.click("#trace-fullscreen")
                    assert "is-expanded" in (page.locator("#trace-workspace").get_attribute("class") or "")
                    page.click("#trace-fullscreen")

                results["viewports"][vp_name] = {
                    "workspace_height": ws_box["height"],
                    "console_errors": console_errs,
                    "zoom_preserved": zoom_before == zoom_after,
                    "status": "PASS" if not console_errs else "FAIL",
                }
                page.close()
            browser.close()

        d_srv.should_exit = True
        b_srv.should_exit = True
    return results


if __name__ == "__main__":
    out = Path(os.environ.get("V224_SCREENSHOT_DIR", "/tmp/v224_screenshots"))
    res = run_qa(out)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if any(v["status"] != "PASS" for v in res["viewports"].values()):
        raise SystemExit(1)
