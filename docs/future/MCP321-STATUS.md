# AI Memory Hub V3.2.1 (MCP321) — Trạng thái triển khai

Cập nhật: 2026-11-25  
Nhánh triển khai: `lead/mcp321-integration`  
Nhánh nền V3.2: `lead/sk32-integration` (`0c25943`)  
Kế hoạch nguồn: `docs/future/V321-MCP321.md`  
Schema SQLite: **v10** (additive migration `v9 → v10`, bảo toàn toàn bộ dữ liệu V1–V3.2)

| Milestone | Phạm vi | Trạng thái | Bằng chứng & Ghi chú |
|---|---|---|---|
| **P0** Baseline & Diagnosis | **DONE** | Session `46c89a85`, Root Trace `9d75c9ab`; xác nhận rõ nguyên nhân 2 khoảng trống (Kho MCP mới chỉ seed 2 built-in server; Execution Map chỉ đọc `v2_plans`). |
| **P1** MCP Candidate Catalog (tách biệt với server đã đăng ký) | **DONE** | `v321_schema.py` (`mcp321_catalog_entries`), `v321_service.py`, `v321_routes.py`, UI **Danh mục MCP đề xuất** trong Kho MCP & Công cụ (10 verified candidates: 4 built-in local packs + 6 external candidates). |
| **P2–P4** Specialized Local Packs (Shared, Game, Architect, Developer) + External Candidates | **DONE** | Đóng gói `services/orchestrator/tool_packs` vào image `memory-bridge` + tự động `register_all_local_packs(v31_gw)`; hỗ trợ đăng ký & discover thật ngay trên Dashboard (`pack-shared`, `pack-game`, `pack-architect`, `pack-developer` — 23+ tools thật). External candidates (`github-mcp`, `context7-mcp`, `filesystem-mcp`, `playwright-mcp`, `chrome-devtools-mcp`, `blender-mcp`) giữ trạng thái `candidate_not_installed` / `pending_approval`, không tự cài đặt trái phép. |
| **P5–P6** Business / Project / Skill Linkage & Lifecycle | **DONE** | Cho phép đăng ký từ Catalog → Kho MCP → Gắn vào Nghiệp vụ → Cấp quyền từng Tool (`allow`/`approval`/`deny`) với backend enforcement & default-deny. |
| **P7** MCP Acceptance & Windows Script | **DONE** | `test_v321_mcp_catalog_and_external_plans.py` + `scripts/Test-V321.ps1` (mặc định read-only, có `-Upgrade` kèm backup và `-RunTests` chạy 58 pytest cô lập). |
| **P8 (P8.0–P8.7)** **External Plan & Task Synchronization (Monitor-Only)** | **DONE** | `mcp321_external_plans`, `mcp321_external_tasks`, `mcp321_external_plan_revisions`, `mcp321_trace_associations`; `external_plan_collector.py` (read-only, lọc secret, chặn unauthorized root, ma trận tương thích Antigravity/Claude/Codex/Gemini); `POST /v321/external-plans/from-session`; hợp nhất vào P6A `GET /v2/traces/scope-options?source=...`, `GET /v2/traces?external_plan_id=...` và bộ lọc **Nguồn Plan** trên Dashboard Execution Map (nhãn `[Antigravity · Monitor-only]`, không đưa vào hàng đợi dispatch). |
