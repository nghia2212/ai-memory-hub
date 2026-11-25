# Codex CLI Integration

Hướng dẫn tích hợp **AI Memory Hub** với **Codex CLI** (đã xác minh trực tiếp trên `codex-cli 0.166.0` với `hooks` feature `stable = true`).

## 1. Cài đặt tự động (khuyến nghị)

```powershell
# Kiểm tra trước (không ghi file)
powershell -ExecutionPolicy Bypass -File .\scripts\install-codex.ps1 -DryRun

# Cài đặt MCP (~/.codex/config.toml) + Hooks (~/.codex/hooks.json)
powershell -ExecutionPolicy Bypass -File .\scripts\install-codex.ps1

# Hoặc cài đặt đồng bộ qua install-all.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install-all.ps1 -Agents codex
```

Script không ghi đè các mục TOML/hook hiện có, tạo backup `.bak.<timestamp>`, không ghi token trực tiếp vào `config.toml` (dùng `bearer_token_env_var`), và hỗ trợ `-Uninstall`.

## 2. Kích hoạt Hooks & Lưu ý Trust trên Codex CLI 0.166.0

- `install-codex.ps1` cấu hình `SessionStart`, `Stop`, `SessionEnd` trong `~/.codex/hooks.json` (gắn `source_agent="codex"`).
- **Yêu cầu bảo mật của Codex CLI:** Lần đầu mở phiên Codex tương tác, chạy lệnh `/hooks` để xác nhận tin cậy các hook mới; nếu chưa trust, Codex sẽ bỏ qua hook mà không chặn phiên.
- **Cơ chế handoff thay thế khi chưa bật hook:** Dùng trực tiếp MCP tools (`get_handoff`, `save_task_result`) hoặc chạy:
  ```powershell
  python integrations/common/handoff_cli.py get --task-id <TASK_ID>
  ```

## 3. Cấu hình MCP thủ công (`codex mcp add`)

```powershell
$env:AI_MEMORY_CODEBASE_TOKEN = "<CODEBASE_TOKEN>"
$env:AI_MEMORY_BRIDGE_TOKEN   = "<BRIDGE_READ_TOKEN>"

codex mcp add ai-memory-codebase --url http://127.0.0.1:8081/mcp --bearer-token-env-var AI_MEMORY_CODEBASE_TOKEN
codex mcp add ai-memory-bridge   --url http://127.0.0.1:8082/mcp --bearer-token-env-var AI_MEMORY_BRIDGE_TOKEN
codex mcp list
```

Xem quy tắc search-first và bàn giao đa agent tại [AGENT-WORKFLOW.md](../common/AGENT-WORKFLOW.md).
