# Chuẩn bị triển khai Agent B — V2 Orchestrator & Session Analytics

- **Trạng thái V2 theo `docs/V2-AGENT-BOARD.md`:** `QUEUED` (chờ Lead tích hợp PR V1 #4/#3/#2, khởi động Docker daemon để qua release gate V1, tạo nhánh `lead/v2-orchestrator` và đóng băng schema `/v2/*`).
- **Tài liệu đã đọc:** `docs/ORCHESTRATOR-V2.md`, `docs/SESSION-ANALYTICS-V2.md`, `docs/V2-AGENT-BOARD.md`, `docs/tasks/V2-AGENT-B.md`.

## 1. Blocker V1 / V1.1 đã xử lý trước trên nhánh `agent/v1-memory-bridge`

1. **Tương thích payload V1.1 từ Agent C (`POST /v1/events`):**
   - Trước đây `EventCreateRequest` (`extra="forbid"`) chỉ nhận `task_id` bắt buộc và chưa có `source_agent`, `timestamp`, nên hook/CLI từ Agent C (`claude`, `gemini`, `codex`) sẽ bị `422`.
   - Đã cập nhật `EventCreateRequest` hỗ trợ `source_agent`, `timestamp`, và `task_id` mặc định `"general"`, kèm migration SQLite v2 (`add_events_source_agent`).
2. **Đề xuất V1.1 Agent Status (`AGENT-STATUS-PROPOSAL.md`):**
   - Đã triển khai `GET /v1/agents/status?project_id=...&worktree_id=...` trên `memory-bridge` (scope `read`, cô lập theo `project_id`/`worktree_id`).
   - Đã thêm proxy `/api/agents/status` và bảng **AI Agents kết nối** trên Dashboard tiếng Việt.

## 2. Blocker mở cần Lead xử lý để kích hoạt V2

- **Docker daemon chưa chạy trên host Windows:** `docker info` báo lỗi kết nối `//./pipe/dockerDesktopLinuxEngine`, nên chưa chạy được `docker compose build` / V1 smoke gates.
- **Nhánh `lead/v2-orchestrator` và schema `/v2/*` chưa được tạo/đóng băng:** Tuân thủ `V2-AGENT-BOARD.md`, Agent B chưa tạo nhánh `agent/v2-observability` để tránh lệch schema với Lead, Agent A và Agent C.

## 3. Kế hoạch sẵn sàng ngay khi `lead/v2-orchestrator` mở

- Bổ sung migration SQLite cho `catalog_snapshot`, `model_evaluation`, `plans/revisions/approvals`, `tasks/dependencies/attempts`, `leases`, `usage_events`, `quota_snapshots`, `policy/audit`, `sessions`, `task_features`, `session_outcomes`, `forecast_snapshots`, `forecast_actuals`.
- Triển khai atomic approval/claim/budget reservation, dedupe cumulative counters, retention/delete/consent và các endpoint `/v2/*` cùng Dashboard V2 tiếng Việt (hiển thị rõ `Không có dữ liệu` khi thiếu denominator/quota).
