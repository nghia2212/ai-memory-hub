# AI Memory Hub V3 — Trạng thái triển khai

Cập nhật: 2026-11-25
Nhánh cơ sở: `lead/v21-development-workbench`
Nhánh hiện tại: `fix/v223-stability-security`

| Milestone | Phạm vi | Trạng thái | Ghi chú |
|---|---|---|---|
| **M0** | Coding Execution Readiness Audit | **COMPLETED** | Session `23c9cb7d-2834-4699-a2f0-9a3d2e30ffa1`, Root Trace `63a3fdfa-32ef-4846-a5b3-4f6ddcb5f4e3`, Candidate Memory `01531082-aae6-4f4e-9122-d6d623e16491`, Handoff `V3-M1` `1831d0ca-917d-4df6-8fc3-ea55d9c29aa0`. |
| **M0.5 / V2.2.3** | Stability & Security (Codebase MCP 503, Dashboard Auth/CSRF, Brain Config Gemini 4, Pre-approval Quota Guard, Long Prompt stdin) | **IN_REVIEW** | Triển khai trên `fix/v223-stability-security`; chờ người dùng nghiệm thu trước khi bắt đầu M1. |
| **M1** | Coding Execution Engine (4 chế độ: Analysis, Coding, Testing, Git Delivery) | **PLANNED** | Chờ duyệt sau M0.5. |
| **M2 / V3.0** | Dynamic MCP Registry & Scoped Routing | **PLANNED** | Chờ duyệt sau M1. |
| **M3 / V3.1** | Conversation Collector (Claude, Codex, Antigravity) | **PLANNED** | Đã khảo sát định dạng JSONL thực tế ở M0. |
| **M4 / V3.2** | Conversation Storage & Dashboard UI | **PLANNED** | Migration v6 additive. |
| **M5 / V3.3** | Conversation Search (FTS5) & Recovery | **PLANNED** | 0 LLM cho tìm kiếm. |
| **M6 / V3.4** | Unified Execution Trace | **PLANNED** | Tái sử dụng 4 views Bản đồ thực thi. |
| **M7 / V3.5** | Context Optimization & Token Benchmark | **PLANNED** | Đo đối chứng thực tế trước khi công bố. |
| **M8–M9** | Integration, Security, Windows QA & Delivery | **PLANNED** | Không merge `main` khi chưa duyệt. |
