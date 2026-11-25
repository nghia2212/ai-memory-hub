# V2.2.4 — Execution Map, Multi-level Summary & Telemetry Upgrade

Ngày phát hành: 2026-11-25
Nhánh: `fix/v223-stability-security` (tiếp nối commit `0c4dd7d` của V2.2.3)

## Các thay đổi chính

1. **Unified Execution Workspace (P1):**
   - Thiết kế lại khung Execution Map chung cho cả 4 View (`Graph`, `Swimlane`, `Timeline`, `Table`) với chiều cao thích ứng viewport (`calc(100vh - 220px)`), cuộn ngang/dọc độc lập, thanh cuộn ngang luôn tiếp cận được ngay mà không phải kéo xuống cuối trang.
   - Bổ sung Sticky Toolbar: chuyển View, Zoom In/Out/100%/Fit, pan chuột không xung đột click node, Compact/Detailed Mode, Ẩn lane trống, Thu gọn/mở rộng nhóm, Mở rộng/Fullscreen Workspace, Đóng/mở & Resize Detail Panel, giữ nguyên scroll/zoom/node đang chọn khi auto-refresh.
2. **Execution Order & Relationships (P2):**
   - Tính toán `execution_sequence` ổn định toàn cục theo `(started_at, trace_id)` trong `trace_summary.py` (không đánh số lại theo trang/bộ lọc), đánh dấu trùng timestamp (`timestamp_tie`), trace đến muộn (`late_arrival`), tách riêng `planned_steps` từ DAG và hiển thị liên kết có bằng chứng (`parent-child`, `dependency`, `retry`).
   - Nâng cấp 4 View: Graph View có Legend + highlight node liên quan + mở parent ngoài bộ lọc; Swimlane View 11 lane chuẩn dùng chung trục sequence + sticky header; Timeline View hiển thị rõ Wall-clock span vs Interval union vs Summed duration + elapsed time cho step đang chạy; Table View có sticky sequence/step name, chọn cột hiển thị, export JSON/CSV.
3. **Summary đa cấp & Detail Panel 6 tab (P3):**
   - Bổ sung Task/Session Summary, Group/Lane Summary, Step Summary (deterministic 0 LLM) và Detail Panel 6 tab (`Overview`, `Input/Output`, `Relationships`, `Token & Cost`, `Evidence`, `Error/Retry`).
   - Chỉ tính phần trăm tiến độ khi có mẫu số `planned_steps_count > 0` đáng tin cậy.
4. **Token Telemetry Collector & Inspector (P4 & P5):**
   - Khảo sát thực tế và triển khai `services/orchestrator/usage_collector.py` (opt-in, read-only, incremental byte-offset cursor, xử lý file đang ghi & rotation, dedupe) cho Antigravity IDE (`transcript.jsonl`), Claude Code (`~/.claude/projects/`), Codex CLI (`~/.codex/sessions/`); ghi rõ `unsupported` cho Gemini CLI chưa cài đặt.
   - Phân loại rõ `token_status` (`measured`, `estimated`, `pending`, `unsupported`, `collection_error`, `not_applicable`, `unknown`), tách `requested_model` / `client_declared_model` / `provider_reported_model`, và bổ sung **Token Telemetry Inspector** (`/api/v2/traces/telemetry/inspector`).
5. **Sửa triệt để lỗi Reindex HTTP 503 (Async Reindex + Timeout Distinction):**
   - Nguyên nhân gốc: Dashboard `_proxy_bridge` dùng timeout cứng `5.0s` và gộp `TimeoutException` vào `503 bridge_unavailable`; Codebase Adapter chạy đồng bộ cả `inspect_worktree_git(force=True)` lẫn `index_repository` ngay trong HTTP handler (khiến `doi-cun-sieu-tri-tue` mất ~48.8s bị ngắt ở 5s dù job vẫn chạy ngầm).
   - Khắc phục: Bổ sung `background: bool = False` (`POST /api/index` mặc định `true`) đẩy cả `inspect_worktree_git` và `index_repository` vào tác vụ nền có chống trùng (`deduplicated=True`), trả `202 Accepted` trong `< 100ms`; bổ sung `GET /api/index/{job_id}` + polling tiến độ trên UI; phân biệt rạch ròi `504 Gateway Timeout` (`bridge_timeout`/`codebase_timeout`) với `503 Service Unavailable`.
   - Kiểm thử live thực tế: `AI Memory Hub` (`202` trong `73ms`, dedupe `33ms`, hoàn tất `6.2s` → `1.883 nodes / 7.205 edges`) và `doi-cun-sieu-tri-tue` (`202` trong `81ms`, dedupe `48ms`, hoàn tất `48.8s` → `15.986 nodes / 48.989 edges`).
6. **Kiểm thử & Script `Test-V224.ps1` (P6 & P8):**
   - Bảo toàn 34 test V2.2.3 + bổ sung test V2.2.4 backend, usage collector, và headless Playwright browser QA (100/500/1000 traces trên 1366x768 & 1920x1080).
