# Business Workspace Manager (V3.1 — P1)

## Tổng quan
Mỗi **Nghiệp vụ (Business Workspace)** là một hồ sơ cấu hình động lưu trong SQLite (`v31_business_profiles`, `v31_business_projects`, `v31_business_mcp_bindings`, `v31_business_tools`, `v31_business_versions`).

- **Không hardcode**: 3 template ban đầu (`Game Development`, `Technical Architect`, `Software Developer`) chỉ được seed 1 lần duy nhất (`v31_business_meta.initial_businesses_seeded = '1'`). Nếu người dùng chỉnh sửa, lưu trữ hoặc xóa, restart Hub sẽ **không** tự khôi phục.
- **Lưu trữ an toàn (Archive)**: Mặc định xóa là lưu trữ (`archived = 1, enabled = 0`). Không xóa MCP trong Registry, không xóa Dự án, không xóa Bộ nhớ (Memory), không xóa Hội thoại, không xóa Execution Trace và không tự hủy phiên đang chạy.
- **Lịch sử phiên bản**: Mọi thay đổi cấu hình đều tăng `revision` và lưu snapshot vào `v31_business_versions`, cho phép khôi phục phiên bản bất kỳ.
- **Không lưu bí mật**: Hệ thống từ chối mọi trường chứa secret thô (`sk-...`, `ghp_...`, `api_key`, `password`...) bằng HTTP 422; chỉ cho phép `secret_refs`.

## API Endpoints (`/v31/businesses`)
- `GET /v31/businesses` (search, include_archived, enabled_only)
- `POST /v31/businesses`, `GET /v31/businesses/{id}`, `PUT /v31/businesses/{id}`
- `POST /v31/businesses/{id}/archive`, `POST /v31/businesses/{id}/restore`, `POST /v31/businesses/{id}/duplicate`
- `GET /v31/businesses/{id}/export`, `POST /v31/businesses/import`
- `GET /v31/businesses/{id}/versions`, `POST /v31/businesses/{id}/versions/{rev}/restore`
- `POST /v31/businesses/{id}/projects`, `DELETE /v31/businesses/{id}/projects/{project_id}`
- `POST /v31/businesses/{id}/mcps`, `DELETE /v31/businesses/{id}/mcps/{server_id}`
- `POST /v31/businesses/{id}/tools/policy` (`allow` | `approval` | `deny`)
- `GET /v31/businesses/templates`, `POST /v31/businesses/from-template`, `POST /v31/businesses/templates`, `DELETE /v31/businesses/templates/{key}`
