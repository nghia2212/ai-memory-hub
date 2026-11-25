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
5. **Kiểm thử & Script `Test-V224.ps1` (P6 & P8):**
   - Bảo toàn 34 test V2.2.3 + bổ sung test V2.2.4 backend, usage collector, và headless Playwright browser QA (100/500/1000 traces trên 1366x768 & 1920x1080).
