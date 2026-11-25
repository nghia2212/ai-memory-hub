# Bảo mật AI Memory Hub V3.1 (SECURITY)

1. **Default-Deny Tool Permissions**: Mọi tool trong Business Workspace mặc định ở trạng thái `deny` cho tới khi quản trị viên cấu hình `allow` hoặc `approval` (bắt buộc `approval_ref`).
2. **Backend Enforcement**: Mọi kiểm tra quyền Business, Project, Worktree, MCP Approval, Tool Policy và Task Override đều thực thi tại `memory-bridge` backend, không chỉ là cờ giao diện.
3. **Không Command Injection & Path Traversal**:
   - STDIO/CLI chỉ chạy `shell=False` với `args` dạng mảng; chặn toàn bộ metacharacter (`;`, `&&`, `|`, `` ` ``, `$`, `sh -c`, `cmd /c`, `powershell -enc`).
   - `execution_engine.py` sửa triệt để lỗi `_norm_rel` strip `..`, kiểm tra symlink thoát khỏi worktree, chụp hash file pre-dirty trước khi chạy và lọc sạch biến môi trường chứa `TOKEN`/`SECRET`/`PASSWORD`/`API_KEY` khi chạy test.
4. **Không lưu Credentials trong Config/Memory/Git**:
   - Business Workspace từ chối mọi giá trị secret thô (HTTP 422); chỉ cho phép `secret_refs`.
   - Filesystem adapter chặn `.env`, `.ssh`, `id_rsa`, `credentials.json`.
   - Database Architect adapter mặc định read-only/metadata-only, chặn DDL/DML và từ chối host production (`prod`/`bank-core`).
5. **Auth & CSRF**: Toàn bộ endpoint `/api/v31/*` trên Dashboard bắt buộc session cookie (HTTP 401 khi chưa đăng nhập) và CSRF token + Origin check cho mọi thao tác ghi (`POST`/`PUT`/`DELETE`).
6. **Không sửa Global Config âm thầm**: `mcp_activation.py` chỉ xuất file cấu hình phiên riêng biệt, không ghi đè `~/.claude.json`, `~/.codex/config.toml` hay `~/.gemini/antigravity/mcp_config.json`.
