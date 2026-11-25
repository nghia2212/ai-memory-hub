# AI Memory Hub V3.2 (SK32) — Trạng thái triển khai

Cập nhật: 2026-11-25  
Nhánh triển khai: `lead/sk32-integration`  
Nhánh nền V3.1: `feature/v31-business-mcp-platform` (`fd1cea6`)  
Kế hoạch nguồn: `origin/design/v32-skill-management:docs/v32/IMPLEMENTATION-PLAN.md`  
Schema SQLite: **v9** (additive migration `v8 → v9`, bảo toàn toàn bộ V1–V3.1)

| Milestone | Phạm vi | Trạng thái | Bằng chứng & Ghi chú |
|---|---|---|---|
| **P0** | Baseline V3.1, AI Memory Telemetry Gate & Interface Freeze | **COMPLETED** | Session `21f127f4`, Root Trace `5f301016`, đọc handoff V3.1 (`80f49970`) & `search_code` trước khi sửa code. |
| **P1** | Skill Registry, CRUD, Immutable Versions, Rollback, Safe Import/Export | **COMPLETED** | `v32_skill_schema.py` (Migration v9), `v32_skill_service.py`, `v32_routes.py`; chặn `<script>`, path traversal, secret leak, optimistic concurrency, archive giữ lịch sử. |
| **P2** | Scoped Bindings (`global→business→project→agent_role`) & Deterministic 0-LLM Resolution | **COMPLETED** | Precedence + `inherit`/`disabled`/`mandatory` protection + kiểm tra MCP permission thật + budget block (không cắt âm thầm) + `sk32_skill_runs` snapshot. |
| **P3** | Vietnamese Dashboard (**Kho Skill & Quy tắc**) | **COMPLETED** | `v32-ui.js`, `ui.py`, `main.py`; toàn bộ route `/api/v32/*` bắt buộc Auth (401) & CSRF, safe `textContent` rendering. |
| **P4** | Orchestration & Scoped MCP Read Tools | **COMPLETED** | Bổ sung `list_skills`, `resolve_skills`, `get_skill_version` (read-only) trên `ai-memory-bridge` MCP server (`mcp_server.py`). |
| **P5** | Provider Skill Adapters | **COMPLETED** | `services/orchestrator/skill_adapters.py`: manifest 6 adapter, `sync_managed_skills` ghi atomic vào `hub-managed/`, tự backup, giữ nguyên skill cá nhân, báo `load_status: unknown` trung thực. |
| **P6** | Skill Execution Map & Telemetry | **COMPLETED** | `sk32_skill_runs` tách rõ `selected`/`delivered`/`loaded`/`executed`/`blocked`/`unknown`, `bytes_count`, nullable `input_tokens`. |
| **P6A** | **Task-Centric Execution Map (Mandatory)** | **COMPLETED** | `trace_routes.py` thêm `attempt_id`, `include_descendants`, `/v2/traces/scope-options`, chặn foreign ID (404/422), gán nhãn `Chưa liên kết nhiệm vụ`; `execution-map.js` + `ui.py` có cascading Project→Plan→Task→Attempt, breadcrumb, bỏ stale response. |
| **P7** | Experience → Skill Candidate Pipeline | **COMPLETED** | `propose_candidate_from_memories` chỉ nhận `verified` memory cùng project (chặn unverified/cross-project), human review approve/reject. |
| **P8** | 30 Generic Reviewed Starter Skills & Honest Paired Benchmark | **COMPLETED** | `v32_starter_skills.py` (10 Core + 10 Game + 10 TA/Dev, 0 dữ liệu ngân hàng/nội bộ) + `compare_skill_benchmark`. |
| **P9** | Integration, Regression, Live Upgrade, Windows Delivery & Push | **COMPLETED** | 56/56 pytest PASS trong Docker cô lập; `scripts/Test-V32.ps1`; nâng cấp live Schema v9 kèm backup; push `lead/sk32-integration` (không merge `main`). |
