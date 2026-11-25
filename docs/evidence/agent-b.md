# Bằng chứng triển khai Agent B — AI Memory Hub V1

- **Nhánh:** `agent/v1-memory-bridge`
- **Base commit:** `60f0324e04a0536a5c74fd080db32e7d9f4c4e53` (`lead/v1-integration`)
- **Phạm vi sở hữu:** `services/memory-bridge/**`, `services/dashboard/**`, `tests/bridge/**`, `tests/dashboard/**`, `docs/evidence/agent-b.md`

## 1. Phiên bản phụ thuộc đã ghim (Pinned Dependencies)

Kiểm thử trên Python `3.13.17` (Container `Dockerfile` dùng `python:3.12-slim`):

- `fastapi==0.165.0`
- `fastmcp==4.2.5`
- `mcp==2.3.0`
- `pydantic==2.14.4`
- `uvicorn==0.54.0`
- `httpx==0.28.1`
- `pytest==9.2.0` (môi trường test)

## 2. Thành phần đã triển khai (Thay thế hoàn toàn scaffold)

### Memory Bridge (`services/memory-bridge/`, cổng `8082`)
- **FastAPI + FastMCP ASGI:** Gắn `mcp.http_app(path="/")` tại `/mcp` bằng `combine_lifespans(bridge_lifespan, mcp_app.lifespan)` (tránh lỗi `/mcp/mcp` và khởi tạo `StreamableHTTPSessionManager` đúng chuẩn).
- **SQLite FTS5 (`app/db.py`):** Sole DB writer với `RLock`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `busy_timeout=5000`, bảng `schema_migrations` có kiểm soát phiên bản, FTS5 `unicode61`.
- **Backup & Restore (`app/db.py`):** Sử dụng `sqlite3.Connection.backup()` trực tuyến, ghi manifest + SHA-256 checksum, khôi phục bằng khóa bảo trì (`503` khi đang restore), kiểm tra checksum + `PRAGMA integrity_check`, và tự động rollback về bản sao an toàn nếu lỗi.
- **Bảo mật & Phạm vi (`app/security.py`, `app/schemas.py`, `app/service.py`):**
  - Bearer token tách biệt 4 vai trò: `read`, `write`, `admin`, `worker`.
  - `extra="forbid"` trên mọi schema đầu vào (`422` nếu dư trường hoặc vượt giới hạn).
  - Kiểm tra đường dẫn worktree an toàn (chặn `..`, đường dẫn tương đối, symlink escape) và gán `mount_status = "pending_host_setup"`.
  - Tokenize truy vấn FTS5 thành chuỗi literal an toàn (chống SQL injection và FTS5 injection), hỗ trợ đầy đủ tiếng Việt có dấu.
  - Cô lập chặt chẽ theo `project_id` và `worktree_id`; ẩn `candidate` mặc định; giới hạn nghiêm ngặt kích thước JSON phản hồi (`<= max_chars`, tối đa 8000 ký tự).
  - Idempotency theo `(scope_key, idempotency_key)` + `payload_hash` (`409` nếu tái sử dụng key với payload khác).
  - Hàng đợi worker (`/v1/worker/events`, `/v1/worker/results`) chỉ trả về sự kiện đã duyệt (`approved=1`) và đã che bí mật khi dự án bật `hermes_approved=1`.
  - Thống kê `/v1/metrics` chỉ báo cáo token/chi phí khi có dữ liệu `measured=1` thực tế (`measured_tokens_available: false` khi chưa có).

### Dashboard quản trị tiếng Việt (`services/dashboard/`, cổng `8080`)
- Giao diện tiếng Việt tự chứa (`app/ui.py`), không dùng CDN ngoài, không nhúng token/mật khẩu vào HTML/JS.
- Chế độ mặc định chỉ đọc; thao tác ghi yêu cầu đăng nhập cookie phiên (`HttpOnly`, `SameSite=Strict`), kiểm tra `Origin`/`Referer` và `X-CSRF-Token`.
- Đầy đủ 9 khu vực chức năng: Tổng quan trạng thái, Dự án & Worktree, Tìm mã nguồn (proxy), Duyệt bộ nhớ FTS5, Lịch sử & Handoff, Sự kiện & Kinh nghiệm, Cấu hình Hermes, Sao lưu & Khôi phục, Thống kê & Chi phí thực tế.

## 3. Kết quả kiểm thử

Lệnh đã chạy:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/bridge tests/dashboard -v
```
Kết quả thực tế: **9 passed in 5.26s**
- `tests/bridge/test_bridge_core.py`: 6/6 PASSED (RBAC, validation/unsafe paths, cross-project/worktree isolation + Vietnamese FTS5 + SQL/FTS injection + candidate visibility, strict serialized budget, idempotency + worker redaction + real-only metrics, backup/corrupt-rejection/restore + restart persistence).
- `tests/bridge/test_mcp_handshake.py`: 1/1 PASSED (401 unauthenticated `/mcp`, Streamable HTTP handshake, `list_tools`, `save_decision`, `save_task_result`, `get_handoff`, `get_project_context`).
- `tests/dashboard/test_dashboard.py`: 2/2 PASSED (Vietnamese UI + no leaked secrets, auth/Origin/CSRF checks + server-side proxy).

## 4. Hướng dẫn tích hợp cho Lead

Để chạy `memory-bridge` và `dashboard` trong `docker-compose.yml` hoặc môi trường local, cần cấu hình các biến môi trường sau trong `.env` (không commit):
- `BRIDGE_READ_TOKENS`, `BRIDGE_WRITE_TOKENS`, `BRIDGE_ADMIN_TOKENS`, `BRIDGE_WORKER_TOKENS`
- `BRIDGE_READ_TOKEN`, `BRIDGE_ADMIN_TOKEN` (cho container `dashboard` gọi proxy phía server)
- `DASHBOARD_PASSWORD`, `DASHBOARD_SESSION_SECRET`, `DASHBOARD_ALLOWED_ORIGINS`
- `CODEBASE_URL` (tùy chọn, mặc định `http://codebase:8081`)

## 5. Hạn chế / Blocker đã ghi nhận
- Chưa chạy `docker compose build` và smoke test container trong phạm vi nhánh Agent B (Lead sẽ chạy kiểm thử tích hợp toàn stack cùng Agent A và Agent C).
- Chưa có `codebase` và `hermes` service thật trong worktree Agent B; các endpoint `/v1/search/code` và `/api/status` đã kiểm thử với trạng thái không khả dụng (`503` / `unreachable`) và proxy REST.
