# Codex CLI Integration

Hướng dẫn tích hợp **AI Memory Hub** với **Codex CLI** (đã xác minh trên `codex-cli 0.166.0`).

> Lưu ý: Tài liệu này chỉ cung cấp lệnh cấu hình thủ công; installer không tự động sửa `~/.codex/config.toml`.

## 1. Thiết lập biến môi trường token (PowerShell)

Lấy giá trị `CODEBASE_TOKEN` và `BRIDGE_READ_TOKEN` từ file `.env` ở thư mục gốc repository:

```powershell
$env:AI_MEMORY_CODEBASE_TOKEN = "<CODEBASE_TOKEN>"
$env:AI_MEMORY_BRIDGE_TOKEN   = "<BRIDGE_READ_TOKEN>"
```

## 2. Đăng ký MCP Streamable HTTP qua `codex mcp add`

```powershell
codex mcp add ai-memory-codebase --url http://127.0.0.1:8081/mcp --bearer-token-env-var AI_MEMORY_CODEBASE_TOKEN
codex mcp add ai-memory-bridge   --url http://127.0.0.1:8082/mcp --bearer-token-env-var AI_MEMORY_BRIDGE_TOKEN
```

Kiểm tra lại:

```powershell
codex mcp list
codex mcp get ai-memory-codebase
codex mcp get ai-memory-bridge
```

## 3. Ví dụ `~/.codex/config.toml`

```toml
[mcp_servers.ai-memory-codebase]
url = "http://127.0.0.1:8081/mcp"
bearer_token_env_var = "AI_MEMORY_CODEBASE_TOKEN"

[mcp_servers.ai-memory-bridge]
url = "http://127.0.0.1:8082/mcp"
bearer_token_env_var = "AI_MEMORY_BRIDGE_TOKEN"
```
