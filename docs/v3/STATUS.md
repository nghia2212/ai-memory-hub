# AI Memory Hub V3 — Trạng thái triển khai

Cập nhật: 2026-11-25
Nhánh cơ sở: `lead/v21-development-workbench`
Nhánh V3: `feature/v3-dynamic-mcp-history` (kế thừa toàn bộ V2.2.3 & V2.2.4 từ `dc410b9`)

| Milestone | Phạm vi | Trạng thái | Ghi chú |
|---|---|---|---|
| **M0** | Coding Execution Readiness Audit | **COMPLETED** | Session `23c9cb7d`, Root Trace `63a3fdfa`. |
| **M0.5 / V2.2.3** | Stability & Security (Codebase MCP 503, Dashboard Auth/CSRF, Brain Config Gemini 4, Quota Guard, stdin) | **COMPLETED** | Commit `0c4dd7d`, 34/34 pytest PASS. |
| **V2.2.4** | Execution Map Workspace, Summary đa cấp, Usage Collector & Async Reindex (`202`/dedupe/`504` vs `503`) | **COMPLETED** | Commit `dc410b9`, 38/38 pytest PASS, Playwright QA PASS. |
| **M1** | Coding Execution Engine (4 chế độ: Analysis, Coding, Testing, Git Delivery) | **COMPLETED** | `services/orchestrator/dispatch/execution_engine.py` + unit tests trên isolated git repo. |
| **M2 / V3.0** | Dynamic MCP Registry & Scoped Capability Routing | **COMPLETED** | Migration v5 (`v3_mcp_servers`, `v3_mcp_tools`, `v3_mcp_audit`), builtin seed, approval workflow, 0-LLM router, Dashboard Tab Dynamic MCP. |
| **M3 / V3.1** | Conversation Collector (Antigravity, Claude Code, Codex CLI) | **COMPLETED** | `services/orchestrator/conversation_collector.py` (opt-in read-only, strip `thinking`/secrets, incremental cursor, dedupe). |
| **M4 / V3.2** | Conversation Storage & Dashboard Lịch sử hội thoại AI | **COMPLETED** | Migration v6 (`v3_conversations`, `v3_conversation_messages`), 2-pane dark mode UI, pagination, export JSON/MD, delete CSRF. |
| **M5 / V3.3** | Conversation FTS5 Search (tiếng Việt, 0 LLM) & Recovery | **COMPLETED** | `v3_conversation_messages_fts` (`unicode61`), bounded excerpts, promote excerpt → `candidate memory`. |
| **M6 / V3.4** | Unified Execution Trace ↔ Conversation Linking | **COMPLETED** | Liên kết tường minh qua ID thực tế (`session_id`/`plan_id`/`task_id`), hiển thị `Unlinked` khi thiếu ID, nút điều hướng trực tiếp sang Bản đồ thực thi. |
| **M7 / V3.5** | Selective Context Optimization & Honest Token Benchmark | **COMPLETED** | `services/orchestrator/context_pack.py` (budgeted dedupe pack chỉ dùng verified/approved sources + benchmark đo thật không bịa %). |
| **M8** | Integration, Security, Migration v4→v6 & Windows QA | **COMPLETED** | Bảo toàn toàn bộ 38 test V2.2.4 + toàn bộ test V3 mới trong Docker cô lập; kiểm thử migration live không xóa volume. |
| **M9** | Delivery (`Test-V3.ps1`, Docs, Push `feature/v3-dynamic-mcp-history`) | **IN_REVIEW** | Chờ người dùng review Draft PR & phê duyệt trước khi merge `main`. |
