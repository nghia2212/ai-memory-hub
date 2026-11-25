# AI Memory Hub V3.1 — Kế hoạch triển khai (Business Workspace & Dynamic MCP Platform)

Cập nhật: 2026-11-25  
Nhánh triển khai: `feature/v31-business-mcp-platform`  
Nhánh nền V3: `feature/v3-dynamic-mcp-history` (commit `1b9ce7cfc482ea1a1d635dde06be77ed6e8b1e0e`)  
Schema SQLite: Additive Migration **v6 → v7 → v8** (kế thừa nguyên vẹn migration 7 từ commit `7b31a95d` & `6544e4a8`).

## Mục tiêu kiến trúc

1. **Business Workspace Manager (P1)**: Quản lý đa nghiệp vụ trực tiếp trên Dashboard (không hardcode, 3 template mẫu `Game Development`, `Technical Architect`, `Software Developer` có thể chỉnh sửa/lưu trữ/xóa vĩnh viễn mà không bị tái tạo khi restart).
2. **Dynamic MCP Gateway (P2)**: Kế thừa `v3_mcp_servers`, `v3_mcp_tools`, `v3_mcp_audit` (không tạo Registry thứ hai), bổ sung thực thi thực tế STDIO JSON-RPC 2.0, Streamable HTTP, Native CLI, REST API, Local Deterministic Tool; Health Check thật (bỏ hardcode `ready`), Discovery thật phát hiện thêm/gỡ/đổi schema, Invocation 14 bước enforcement (default-deny).
3. **Business Policy, Capability Routing & Activation (P3)**: Định tuyến 0-LLM theo nghiệp vụ/dự án/quyền tool, Task Override không vượt quyền, Immutable Policy Snapshot, Agent Activation không ghi đè global config.
4. **Tool Packs (P4–P8)**: Shared, Game Browser/QA/Asset (GLB 2.0, Blender 5.2 headless, Image, Audio), Technical Architect (DB read-only metadata, OpenAPI 3.x, HTTP allowlist, Local Diagram, Doc Extract, Infra read-only), Software Developer (Java structural analyzer, TypeScript analyzer, Maven/JUnit, Offline Security scan, Log analyzer).
5. **Observability, Resource Management, QA & Windows Delivery (P9–P11)**: Monitoring KPIs, Execution Map, 54/54 pytest regression + V3.1 suites, `Test-V31.ps1` & `Install-V31McpPack.ps1`.
