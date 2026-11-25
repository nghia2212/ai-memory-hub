# AI Memory Hub V3.2.1 (MCP321) — Trạng thái triển khai (P0–P13 + Business Binding)

Cập nhật: 2026-11-25  
Nhánh triển khai: `lead/mcp321-integration`  
Nhánh nền V3.2: `lead/sk32-integration` (`0c25943`)  
Kế hoạch nguồn: `docs/future/V321-MCP321.md` + `docs/mcp321/P9-P13-TELEMETRY-ADDENDUM.md`  
Schema SQLite: **v11** (strictly additive `v9 → v10 → v11`, bảo toàn toàn bộ dữ liệu V1–V3.2)

| Milestone | Phạm vi | Trạng thái | Bằng chứng & Ghi chú |
|---|---|---|---|
| **P0** | Baseline & Diagnosis | **DONE** | Xác định nguyên nhân: (1) `ensure_initial_seed()` chỉ bind `trust_tier='builtin'`; (2) `list_businesses()` chưa tách Local Tool Pack vs MCP Server & chưa phân rã `pending_approval`/`unconfigured`/`disabled`; (3) Execution Map chỉ đọc `v2_plans`. |
| **P1** | MCP Candidate Catalog | **DONE** | `mcp321_catalog_entries` (10 verified candidates: 4 Local Tool Packs + 6 External MCPs), tách biệt hoàn toàn với server đã đăng ký/đã duyệt/đang kết nối. |
| **P2–P4** | Specialized Local Packs & External Candidates | **DONE** | Đóng gói `services/orchestrator/tool_packs` vào `memory-bridge`; đăng ký & discover thật 4 Local Packs (`pack-shared`, `pack-game`, `pack-architect`, `pack-developer` — 23 tools thật). 6 External MCPs (`github-mcp`, `context7-mcp`, `filesystem-mcp`, `playwright-mcp`, `chrome-devtools-mcp`, `blender-mcp`) giữ `pending_approval`, không tự cài đặt/tự duyệt. |
| **P5–P6** | Business Binding Mặc định, Quản lý MCP & Project Overrides | **DONE** | Migration v11 (`activation_mode` trên `v31_business_mcp_bindings` + bảng `mcp321_project_mcp_overrides`). `sync_default_business_bindings()` idempotent (Game Dev 11, Technical Architect 9, Software Dev 9), không ghi đè tùy chỉnh/gỡ bỏ của người dùng, không tự duyệt server `pending_approval`. Phân rã thống kê trung thực (`mcp_online_count` chỉ tính Real MCP Server, `local_packs_ready_count` tính riêng). Project kế thừa MCP nghiệp vụ & override thu hẹp (chặn leo thang quyền `403`). Tối ưu 16GB RAM (`activation_policy='on_demand'` cho Playwright/Chrome DevTools/Blender). |
| **P7** | Dynamic MCP Resolution (0 LLM) | **DONE** | `route_for_business()` hỗ trợ suy luận capability deterministically từ `task_objective`, tuân thủ `activation_mode` (`required`/`on_demand`/`manual`) & project overrides, cấp minimal tool set, trả `proposed_servers` / `selected_servers` / `provided_tool_count` / `actually_invoked_count` và `tool_schema_bytes` (không cộng trùng vào input tokens). |
| **P8** | External Plan & Task Sync (Monitor-Only) | **DONE** | `mcp321_external_plans` + `external_plan_collector.py` (read-only, lọc secret, chặn unauthorized root) + hợp nhất vào P6A `scope-options?source=...` và Execution Map UI (`[Antigravity · Monitor-only]`). |
| **P9** | Client Runtime Adapter Inventory | **DONE** | `GET /v321/runtime-adapters` + Dashboard proxy `/api/v321/runtime-adapters` cho `antigravity`, `claude`, `codex`, `gemini`, `deepseek_api`, `qwen_local` (managed namespace `ai-memory-hub-managed`, rollback, không ghi đè config cá nhân). |
| **P10** | Skill Execution Telemetry | **DONE** | `GET /v2/traces` đính kèm `skill_runs` theo đúng scope filter (`project_id`/`plan_id`/`task_id`/`session_id`) và hiển thị trong Execution Map. |
| **P11–P12** | Token / Thinking / Pricing Telemetry & Dashboard | **DONE** | `trace_metrics.py` bổ sung `known_token_subtotal` (không cộng trùng `reasoning_tokens` hay MCP/Skill subset), bảng giá có phiên bản `v321-2026-11` với `estimated_cost_usd` nullable & `cost_kind` (`measured`/`estimated`/`unavailable`), giữ nguyên `null`/`Unknown` (không gán `0` giả). Dashboard Execution Map & Business Detail UI nâng cấp hoàn chỉnh. |
| **P13** | Acceptance, Live Upgrade, Browser QA & Delivery | **DONE** | `scripts/Test-MCP321.ps1`, full isolated Docker pytest suite, live backup + upgrade Schema v11, Playwright Browser QA 1366×768 & 1920×1080 (0 console/page errors). |
