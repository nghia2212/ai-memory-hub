# Lead Integration Evidence — AI Memory Hub V2 (`lead/v2-orchestrator`)

- **Timestamp:** `2026-11-25T10:38:00+07:00`
- **Merged V2 Branches (zero conflicts):**
  - Agent A (`agent/v2-model-registry` @ `e82b2e3`): Catalog, Deterministic Router, Adaptive Brain Resolver, Forecast Engine & Calibration.
  - Agent B (`agent/v2-observability` @ `59a8f40`): SQLite Migration v3 (21 bảng `v2_*`), Memory Bridge `/v2/*`, RBAC `planner`/`runner`, Restart Reconciliation, Vietnamese Dashboard V2.
  - Agent C (`agent/v2-orchestration` @ `9db2d98`): Adaptive Brain Adapters (`RuleBrainAdapter`, `OptInDeepSeekBrainAdapter`), Bounded DAG Planner, Narrow Host Runner & CLI Telemetry.

## 1. Các lỗi tích hợp V2 Lead phát hiện & sửa trực tiếp

1. **Docker build context & V2 tokens (`services/memory-bridge/Dockerfile`, `docker-compose.yml`, `.env.example`, `scripts/init-env.ps1`):**
   - `memory-bridge/app/v2_schemas.py` import `services.orchestrator.contracts`; đã cập nhật `docker-compose.yml` dùng build context gốc `.` + `dockerfile: services/memory-bridge/Dockerfile` và copy `services/orchestrator/{__init__,contracts}.py` vào image.
   - Bổ sung `BRIDGE_PLANNER_TOKEN` & `BRIDGE_RUNNER_TOKEN` vào `docker-compose.yml`, `.env.example`, và `scripts/init-env.ps1`.
2. **Đồng bộ giao thức Host Runner (Agent C) ↔ Memory Bridge `/v2/*` (Agent B):**
   - Bổ sung `project_id` và `worktree_path` vào phản hồi `POST /v2/jobs/claim` trong `services/memory-bridge/app/v2_service.py`.
   - Căn chỉnh `HttpRunnerBridge` và `HostRunner` (`services/orchestrator/dispatch/host_runner.py`) truyền đúng schema `extra="forbid"` của `v2_schemas.py` (`project_id`, `worktree_id`, `runner_id` cho claim/heartbeat; `session_id` UUID cho `/v2/sessions`; `usage` lồng cho `/v2/usage`; `outcome` cho `/v2/jobs/{id}/complete`; `idempotency_key` cho `/v2/jobs/{id}/checkpoint`).
   - Xác minh end-to-end thật giữa Agent A + Agent B + Agent C qua `tests/integration/test_v2_end_to_end.py` (không gọi API trả phí).

## 2. Tổng hợp kết quả kiểm thử toàn hệ thống (V1 + V1.1 + V2)

- `scripts/init-env.ps1`: **PASS** (bổ sung an toàn `BRIDGE_PLANNER_TOKEN`, `BRIDGE_RUNNER_TOKEN`, không ghi đè giá trị cũ).
- `docker compose config --quiet` & `--profile hermes config --quiet`: **PASS** (exit code 0).
- `tests/orchestrator` (Agent A + Agent C): **15/15 PASSED**
- `tests/bridge` (V1 + V1.1 + MCP + V2): **11/11 PASSED**
- `tests/dashboard` (V1 + V2 tiếng Việt): **3/3 PASSED**
- `tests/integration` (V1/V1.1 cross-service E2E + V2 A+B+C E2E): **2/2 PASSED**
- `tests/codebase`: **6/6 PASSED**, 1 SKIPPED (`test_real_cbm_integration` cần WSL2 `docker-desktop` đang chạy)
- `tests/hermes`: **10/10 PASSED**
- `tests/integrations`: **8/8 PASSED**
- **Tổng cộng:** **55 PASSED, 0 FAILED, 1 SKIPPED**.

## 3. Cổng nghiệm thu còn lại trước khi merge `main`

- **Docker Desktop Daemon (`com.docker.service` Disabled trên Windows host):** Chưa chạy được `docker info`, `docker compose build`, `docker compose up -d --wait`, `docker compose ps`, `docker stats` do phiên hiện tại không có quyền Administrator để bật `com.docker.service`.
- **Quyết định:** Giữ `main` chưa merge cho đến khi chủ dự án bật Docker Desktop với quyền Administrator và xác nhận container stack.
