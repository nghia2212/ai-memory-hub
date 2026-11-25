# Gemini CLI Integration

Hướng dẫn tích hợp **AI Memory Hub** với **Gemini CLI** theo tài liệu chính thức của Gemini CLI.

> Lưu ý xác minh: Trên máy Windows thực thi tác vụ này chưa cài đặt sẵn binary `gemini` CLI, nên các lệnh dưới đây được đối chiếu theo tài liệu chính thức của Gemini CLI (`gemini mcp add` và `~/.gemini/settings.json`) và chưa chạy kiểm thử trực tiếp với `gemini` local.

## 1. Đăng ký MCP HTTP qua `gemini mcp add`

```powershell
gemini mcp add -s user -t http ai-memory-codebase http://127.0.0.1:8081/mcp
gemini mcp add -s user -t http ai-memory-bridge   http://127.0.0.1:8082/mcp
```

## 2. Cấu hình `~/.gemini/settings.json` (khi dùng Bearer token)

Merge thêm khối `mcpServers` sau vào `~/.gemini/settings.json` (không ghi đè cấu hình hiện có):

```json
{
  "mcpServers": {
    "ai-memory-codebase": {
      "httpUrl": "http://127.0.0.1:8081/mcp",
      "headers": {
        "Authorization": "Bearer <CODEBASE_TOKEN>"
      }
    },
    "ai-memory-bridge": {
      "httpUrl": "http://127.0.0.1:8082/mcp",
      "headers": {
        "Authorization": "Bearer <BRIDGE_READ_TOKEN>"
      }
    }
  }
}
```

## 3. Kiểm tra kết nối

Mở Gemini CLI và chạy:

```text
/mcp list
```
