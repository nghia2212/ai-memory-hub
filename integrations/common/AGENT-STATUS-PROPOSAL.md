# Proposal for Lead & Agent B: Multi-Agent Status on Dashboard

Theo ranh giới sở hữu file trong `AGENT-BOARD.md`, Agent C không chỉnh sửa `services/memory-bridge/**` hay `services/dashboard/**`. Tài liệu này đề xuất contract để Lead và Agent B bổ sung trạng thái các AI Agent trên Dashboard.

## 1. Chuẩn hóa Event từ Agent C (đã triển khai)

Các hook (`session_start.py`, `session_event.py`) và `handoff_cli.py` gửi event tới `POST /v1/events` với:
- `project_id`, `worktree_id`, `task_id` (tùy chọn)
- `source_agent`: `"claude" | "gemini" | "codex" | "hermes"`
- `kind`: `"session_start" | "session_stop" | "session_end" | "handoff_created" | "task_completed"`
- `idempotency_key`, `timestamp`, `payload` (giới hạn, đã loại bỏ transcript/secrets)

## 2. Đề xuất Endpoint trên Memory Bridge (Agent B)

- `GET /v1/agents/status?project_id=...&worktree_id=...`
  - Yêu cầu scope `read`.
  - Tổng hợp từ bảng `events` theo `(project_id, worktree_id, source_agent)`:
    ```json
    {
      "items": [
        {
          "source_agent": "claude",
          "last_kind": "session_end",
          "task_id": "TASK-123",
          "branch": "agent/v1-hermes-integrations",
          "head_commit": "1ca9e8ade5",
          "last_seen_at": 1764038400
        }
      ]
    }
    ```

## 3. Đề xuất hiển thị trên Dashboard (Agent B / Lead)

- Thêm thẻ **Connected AI Agents** (read-only) trên Dashboard proxy server-side:
  - Hiển thị từng agent (`claude`, `gemini`, `codex`, `hermes`), `last_seen_at`, `task_id` gần nhất, `branch`/`head_commit`, và trạng thái `hermes` worker (`/v1/status` trên port `8083`).
  - Không hiển thị token hoặc đường dẫn cục bộ nhạy cảm trên giao diện.
