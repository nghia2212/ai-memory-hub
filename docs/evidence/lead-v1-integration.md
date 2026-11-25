# Lead Integration Evidence — AI Memory Hub V1 / V1.1 (`lead/v1-integration`)

- **Timestamp:** `2026-11-25T10:12:00+07:00`
- **Base commit (post PR #2/#3/#4 merge):** `d71ae360137d2b06387c5fc7e4a829a2087ca1c3`
- **Host:** Windows 11, WSL2 (`docker-desktop` distro Stopped), Docker Compose `v5.5.0`, Python `3.13.17`

## 1. Các lỗi Integration đã rà soát & sửa trực tiếp

1. **Docker Compose & `.env` bootstrap (`docker-compose.yml`, `.env.example`, `scripts/init-env.ps1`):**
   - Truyền đầy đủ token và biến môi trường nội bộ giữa `codebase`, `memory-bridge`, `dashboard` và `hermes` (`BRIDGE_*_TOKEN`, `CODEBASE_*_TOKEN`, `DASHBOARD_PASSWORD`, `DASHBOARD_SESSION_SECRET`, `HERMES_*`).
   - Giữ `HERMES_ENABLED=false` mặc định, không dùng LLM local, không mount Docker socket, loopback-only host bindings.
   - Bổ sung `scripts/init-env.ps1` tạo `.env` an toàn, idempotent, không ghi đè giá trị đã có, không in secret, không commit `.env`.
2. **Codebase Registry ↔ Memory Bridge (`services/codebase/app/registry.py`):**
   - Sửa lỗi đọc `payload.get("worktrees")` thay vì `payload.get("items")` từ `GET /v1/projects/{project_id}/worktrees`.
   - Chuẩn hóa ánh xạ trường `canonical_root` → `host_root`/`container_root` và `canonical_repo_id` mặc định từ `project_id`.
3. **Claude/Codex/Gemini Hooks (`integrations/claude/hooks/common.py`, `session_start.py`):**
   - Sửa `match_registered_scope()` đọc đúng `project_id`, `worktree_id`, `canonical_root` (vẫn tương thích ngược với mock `id`/`host_path`).
   - Sửa `session_start.py` đọc `items[0].summary` từ `GET /v1/handoffs/{task_id}`.
4. **Handoff CLI (`integrations/common/handoff_cli.py`):**
   - Sửa `create_handoff()` ghi trực tiếp vào `POST /v1/handoffs` (bảng `handoffs`) đồng thời ghi sự kiện `handoff_created` vào `POST /v1/events` để `GET /v1/agents/status` và `GET /v1/handoffs/{task_id}` đồng bộ.
5. **Hermes ↔ Memory Bridge Worker Contract (`services/hermes/app/worker.py`, `config.py`, `services/memory-bridge/app/schemas.py`, `service.py`):**
   - Sửa payload Hermes gửi `POST /v1/worker/results` khớp 100% `WorkerResultRequest` (`extra="forbid"`), loại bỏ các trường ngoài schema (`state`, `provenance`, `task_id`, `source_agent`) và đưa metadata vào `evidence_ref`.
   - Bổ sung `"handoff"` vào `WorkerResultRequest.type`.
   - Bổ sung `approved: true`, `external_llm_approved`, `source_agent` vào phản hồi `GET /v1/worker/events`.
   - Bổ sung chống gọi LLM trùng lặp theo `idempotency_key` (bộ nhớ + file state `HERMES_STATE_DIR/processed_keys.json`).
6. **Đồng bộ Index Job & sửa Deadlock Event Loop (`services/memory-bridge/app/service.py`, `main.py`, `config.py`):**
   - Đồng bộ kết quả `POST /v1/index` và `GET /v1/index/{job_id}` giữa Codebase và bảng `index_jobs` / `worktrees.mount_status` của Memory Bridge, tách `CODEBASE_READ_TOKEN` và `CODEBASE_ADMIN_TOKEN`.
   - **Lỗi phát hiện thêm khi chạy E2E thật:** Các route `async def` gọi `httpx.Client` đồng bộ trong `memory-bridge/app/main.py` gây deadlock event loop khi Codebase gọi ngược `GET /v1/projects/{id}/worktrees` về Bridge. Đã chuyển sang `def` để FastAPI chạy trong threadpool, loại bỏ hoàn toàn deadlock.
   - Dọn 4 bản sao trùng lặp của hàm `get_agents_status` trong `services/memory-bridge/app/service.py`.
7. **Bảo mật MCP & Phê duyệt Event & Redaction (`services/memory-bridge/app/main.py`, `mcp_server.py`, `security.py`, `service.py`):**
   - Ràng buộc `AuthPrincipal` qua `ContextVar` trên `/mcp`; chặn hoàn toàn token `read` gọi `save_decision` và `save_task_result`.
   - Chặn token `write` tự đặt `approved: true` trên `POST /v1/events` (`403 forbidden`); chỉ `admin` mới được phê duyệt (hoặc qua `POST /v1/events/{event_id}/approve`).
   - Bổ sung redaction phòng thủ nhiều lớp (private keys, JWT, AWS/Google/GitHub tokens) trên cả Bridge và Hermes.

## 2. Kết quả Kiểm thử thực tế

- `scripts/init-env.ps1 -DryRun` và `scripts/init-env.ps1`: **PASS**
- `docker compose config --quiet` và `docker compose --profile hermes config --quiet`: **PASS** (exit code 0)
- `pytest tests/bridge tests/dashboard -v`: **10/10 PASSED** (bao gồm kiểm thử chặn `read` token trên MCP write tools và chặn `write` token tự approve event)
- `pytest tests/codebase -v`: **6/6 PASSED**, 1 SKIPPED (`test_real_cbm_integration` cần WSL2 `docker-desktop` đang chạy)
- `python -m unittest discover -s tests/hermes -v`: **10/10 PASSED** (bao gồm kiểm thử chống gọi LLM trùng lặp)
- `python -m unittest discover -s tests/integrations -v`: **8/8 PASSED** (Claude/Gemini/Codex installers, hooks fail-open, multi-agent handoff)
- `pytest tests/integration/test_v1_cross_service.py -v`: **1/1 PASSED** (end-to-end thật giữa Bridge + Codebase + Dashboard + Hermes MockProvider + Hooks + Handoff CLI, không gọi API trả phí)
- **Tổng:** **35 PASSED, 0 FAILED, 1 SKIPPED**.

## 3. Blocker môi trường Host (chưa PASS)

- **Docker Desktop Daemon (`com.docker.service`):** Trạng thái `Stopped`, `StartType=Disabled`, shell hiện tại không có quyền Administrator (`IsInRole(Administrator) == False`), WSL2 distro `docker-desktop` đang `Stopped`.
- Do đó các lệnh `docker info`, `docker compose build`, `docker compose up -d --wait`, `docker compose ps`, `docker stats` và kiểm thử persistence sau restart Docker container **CHƯA CHẠY ĐƯỢC** trên phiên không có quyền Admin này.
- **Quyết định:** Không merge `main` cho đến khi chủ dự án bật `com.docker.service` bằng quyền Administrator và chạy xác nhận `scripts/preflight.ps1` + `docker compose up -d --wait`.
