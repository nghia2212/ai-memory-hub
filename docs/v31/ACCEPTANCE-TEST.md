# Báo cáo Nghiệm thu AI Memory Hub V3.1 (ACCEPTANCE-TEST)

Ngày kiểm thử: 2026-11-25  
Nhánh: `feature/v31-business-mcp-platform`  
Môi trường: Windows 11 (Intel Core i9-13900H, 16GB RAM), Docker Desktop, Python 3.12 (Docker test container) & Python 3.13 (host).

## 1. Pytest Regression & V3.1 Suites (Isolated Docker)
- **Tổng số test**: **54**
  - V2.2.4 + V3 Regression cũ: **43 PASS** (0 FAIL, 0 SKIP)
  - V3.1 mới (`test_v31_p0_foundation.py`, `test_v31_business_workspace.py`, `test_v31_mcp_gateway_and_packs.py`): **11 PASS** (0 FAIL, 0 SKIP)
- **Tổng kết**: **54 PASSED / 0 FAILED / 0 SKIPPED** trong `23.38s`.

## 2. Kiểm thử thực tế trên Host Windows
- **P10.4 Game Development (Đội Cún Siêu Trí Tuệ)**:
  - Blender headless: Phát hiện thật `Blender 5.2.3 LTS` (`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`).
  - GLB 2.0 Inspector: Quét thấy 212 file `.glb` trong `D:\workspace1\doi-cun-sieu-tri-tue`, parse thật `art-src/blender/_test/test.glb` (`Khronos glTF Blender I/O v5.2.41`, 4 meshes, 1671 vertices, 1784 triangles, 3 materials, bounding box chính xác).
  - TypeScript Analyzer: Quét 150 file `.ts` trong `doi-cun-sieu-tri-tue/src`, trích xuất 938 exported symbols.
- **P10.5 Technical Architect**:
  - Kiểm thử trên SQLite test DB cô lập (2 bảng có FK), OpenAPI 3.0.3 inventory & breaking change detection, HTTP domain allowlist (chặn domain ngoài), Mermaid sequence diagram local (không upload cloud), chặn DDL/DML & production DB host.
- **P10.6 Software Developer**:
  - Kiểm thử Java/Spring structural analyzer (`@Service OrderService`), Maven `pom.xml` inspector (`mvn` có sẵn trên host), JUnit XML parser, offline security scan, Log analyzer (lọc secret + nhóm exception + correlation ID).
- **P3.5 Agent Activation**:
  - Quét client thực tế trên máy: `agy 1.5.16` (installed), `claude 2.1.269` (installed), `codex-cli 0.166.0` (installed), `gemini` (not installed), `hermes` (not installed). Tạo file cấu hình phiên riêng biệt không đụng global config.

## 3. Giới hạn & Thành phần Optional / Not Configured
- Oracle / PostgreSQL / SQL Server live DB: `not_configured` (chưa cấp test DSN; không kết nối DB production/ngân hàng).
- GitHub MCP, Cloudflare API, Jira/Confluence, Android ADB, Trivy/Semgrep: `optional` / `not_configured` khi chưa cấu hình token hoặc phần mềm trên máy.
- Java JDT Language Server: chưa gắn LSP process; `java_semantic_analyze` ghi rõ `analysis_level: "structural_source_parser"`, không gọi text search là semantic analysis.
- Token benchmark: Antigravity IDE không xuất provider-verified token counter; hệ thống hiển thị `unavailable` trung thực, không bịa % tiết kiệm.
