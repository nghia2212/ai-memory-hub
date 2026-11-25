# Gemini CLI Integration

Hướng dẫn tích hợp **AI Memory Hub** với **Gemini CLI** qua `~/.gemini/settings.json` (MCP `httpUrl` + `SessionStart`/`SessionEnd` hooks).

> Lưu ý xác minh: Trên máy Windows thực thi tác vụ này chưa cài đặt sẵn binary `gemini` CLI, nên cấu hình dưới đây được đối chiếu theo tài liệu chính thức của Gemini CLI và kiểm thử cú pháp/installer bằng unit test.

## 1. Cài đặt tự động (khuyến nghị)

```powershell
# Kiểm tra trước (không ghi file)
powershell -ExecutionPolicy Bypass -File .\scripts\install-gemini.ps1 -DryRun

# Cài đặt MCP + Hooks vào ~/.gemini/settings.json
powershell -ExecutionPolicy Bypass -File .\scripts\install-gemini.ps1

# Gỡ cài đặt an toàn (chỉ xóa entry của ai-memory-hub)
powershell -ExecutionPolicy Bypass -File .\scripts\install-gemini.ps1 -Uninstall
```

## 2. Lifecycle Hooks & Cơ chế Handoff thay thế

- `SessionStart` (`matcher: "startup"`) và `SessionEnd` (`matcher: "*"`) gọi `session_start.py` / `session_event.py` với `--source-agent gemini --format gemini` để xuất JSON đúng chuẩn `hookSpecificOutput.additionalContext` trên `stdout` và fail-open (`exit 0`) khi có lỗi/timeout.
- Nếu phiên bản Gemini CLI không hỗ trợ hook hoặc người dùng tắt hook, sử dụng MCP tools (`get_handoff`, `save_task_result`) hoặc CLI thay thế:
  ```powershell
  python integrations/common/handoff_cli.py get --task-id <TASK_ID>
  ```

## 3. Cấu hình MCP thủ công (`gemini mcp add`)

```powershell
gemini mcp add -s user -t http ai-memory-codebase http://127.0.0.1:8081/mcp
gemini mcp add -s user -t http ai-memory-bridge   http://127.0.0.1:8082/mcp
```

Xem quy tắc search-first và bàn giao đa agent tại [AGENT-WORKFLOW.md](../common/AGENT-WORKFLOW.md).
