# Hướng dẫn triển khai & vận hành V3.1 trên Windows (ASUS Vivobook / Docker Desktop)

## 1. Kiểm tra môi trường (DryRun, không thay đổi hệ thống)
```powershell
cd D:\workspace1\ai-memory-hub
.\scripts\Test-V31.ps1 -CheckEnvironment -DryRun
```

## 2. Xem danh mục MCP Pack theo nghiệp vụ (chưa tự cài)
```powershell
.\scripts\Install-V31McpPack.ps1 -SelectPack Shared -DryRun
.\scripts\Install-V31McpPack.ps1 -SelectPack Game -DryRun
.\scripts\Install-V31McpPack.ps1 -SelectPack Architect -DryRun
.\scripts\Install-V31McpPack.ps1 -SelectPack Developer -DryRun
```

## 3. Nâng cấp an toàn từ V3 (Schema 6 → 8, tự động Backup trước khi rebuild)
```powershell
.\scripts\Test-V31.ps1 -Upgrade -VerifyBusinessWorkspace -McpHealth
```
- **Không xóa Docker volume**.
- **Không gọi API trả phí**.
- Tự động tạo online SQLite backup qua `POST /v1/backup` kèm SHA-256 trước khi chạy `docker compose up -d --build`.

## 4. Chạy toàn bộ Regression + V3.1 Tests trong Docker cô lập
```powershell
.\scripts\Test-V31.ps1 -RunTests
```

## 5. Hướng dẫn Rollback nếu cần
1. Lấy `backup_id` đã in ra ở bước 2 của `Test-V31.ps1` (hoặc qua `GET /v1/backups`).
2. Gọi `POST /v1/restore` với `{"backup_id": "<id>"}` (hoặc bấm **Khôi phục** trong tab **Sao lưu & Khôi phục** trên Dashboard).
3. Nếu muốn quay về nhánh V3: `git checkout feature/v3-dynamic-mcp-history && docker compose up -d --build`.
