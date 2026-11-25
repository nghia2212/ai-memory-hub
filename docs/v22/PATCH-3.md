# V2.2.3 — Stability & Security Patch (M0.5)

Ngày phát hành: 2026-11-25
Nhánh: `fix/v223-stability-security` (base: `lead/v21-development-workbench`)

## Các thay đổi chính

1. **Khắc phục Codebase MCP 503 cho repository AI Memory Hub:**
   - Bổ sung mount chỉ đọc (`:ro`) riêng cho worktree `63537dc5-494a-4f03-8c36-65da83f81aae` trong `docker-compose.override.yml` cục bộ (không commit vào Git), giữ nguyên mount của các dự án khác.
   - Recreate container `codebase`, reindex toàn bộ repository (`nodes`, `edges`, `head_commit`) và xác minh `search_code` qua cả `ai-memory-codebase` và `ai-memory-bridge` MCP.
2. **Chuẩn hóa xác thực toàn bộ Dashboard `/api/*`:**
   - Bắt buộc session cookie hợp lệ (`_require_session`) cho toàn bộ API đọc dữ liệu dự án/worktree/memory/plans/sessions/quotas/catalog/explorer.
   - Bắt buộc xác thực + CSRF + Origin (`require_authenticated_csrf`) cho toàn bộ API ghi và tìm kiếm POST (`/api/search/memory`, `/api/search/code`).
   - Giữ public đúng các endpoint tối thiểu: `/livez`, `/healthz`, `/api/status`, `/api/auth/session`, `/api/auth/login`, giao diện tĩnh.
   - Cập nhật UI để hiển thị trạng thái yêu cầu đăng nhập rõ ràng và tự động nạp dữ liệu ngay sau khi đăng nhập.
3. **Cập nhật Brain Config & Pre-approval Quota Guard:**
   - Tự động resolve cấu hình Brain mặc định theo catalog CLI thực tế (`agy models` hỗ trợ Gemini 4 Hydrogen / Neon / Carbon), loại bỏ chuỗi lỗi thời `v1.1.27`.
   - Không giả định catalog `verified` đồng nghĩa còn quota.
   - Bổ sung kiểm tra `quota_status` ngay tại bước `approve_plan` (trả `409 quota_exhausted`, không tự đổi model đã phê duyệt âm thầm).
4. **Truyền Prompt dài (tới 32.000 ký tự) qua Windows CLI bằng `stdin`:**
   - Bổ sung `build_cli_invocation()` hỗ trợ `stdin` cho `codex` (`-`) và `claude` (`-p`) khi vượt ngưỡng dòng lệnh Windows (đặc biệt `.ps1`/`.cmd` wrapper), không cắt âm thầm và không ghi file tạm chứa secrets.
   - Các adapter chưa xác minh `stdin` (`agy`, `gemini`) vẫn báo lỗi `UnsafeCLIRequestError` tường minh khi vượt giới hạn dòng lệnh Windows.
5. **Script kiểm thử `scripts/Test-V223.ps1`:**
   - Hỗ trợ tiến độ từng bước, timeout rõ ràng, backup SQLite an toàn trước khi nâng cấp, không xóa volume, kiểm tra auth 401/200 và chạy bộ regression test trên disposable Docker container.
