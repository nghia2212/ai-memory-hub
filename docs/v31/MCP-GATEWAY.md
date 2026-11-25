# Dynamic MCP Gateway (V3.1 — P2 & P3)

## Kiến trúc thống nhất
Gateway tái sử dụng trực tiếp các bảng V3 (`v3_mcp_servers`, `v3_mcp_tools`, `v3_mcp_audit`) và mở rộng bằng các bảng additive v8 (`v31_mcp_server_ext`, `v31_mcp_tool_ext`, `v31_mcp_invocations`, `v31_task_policy_snapshots`). **Không tạo Registry thứ hai.**

## Transports hỗ trợ
1. `stdio`: Tiến trình con thực tế (`shell=False`, args dạng array, env allowlist) giao tiếp JSON-RPC 2.0 (`initialize` → `notifications/initialized` → `tools/list` có phân trang → `tools/call`).
2. `streamable_http`: HTTP JSON-RPC 2.0 hỗ trợ cả `application/json` và `text/event-stream` cùng `Mcp-Session-Id`.
3. `native_cli`: Adapter CLI có contract thống nhất, chỉ dùng executable hợp lệ.
4. `rest_api`: Adapter HTTP có domain allowlist.
5. `local_tool`: Công cụ Python deterministic chạy nội bộ (GLB Inspector, OpenAPI, Java/TS analyzer...).

## Quy trình Invocation 14 bước
1. Xác thực Bearer token / Dashboard Session + CSRF.
2. Kiểm tra Project tồn tại.
3. Kiểm tra Worktree thuộc Project.
4. Kiểm tra Business Workspace đang `enabled=1` và `archived=0`.
5. Kiểm tra MCP Server `approval_state == 'approved'`, `enabled=1`, `archived=0` và project allowlist (chống cross-project).
6. Kiểm tra MCP Server được gắn vào Business Workspace.
7. Kiểm tra Tool tồn tại, không bị xóa/vô hiệu hóa.
8. Kiểm tra Tool Policy trong `v31_business_tools`: **Default-Deny** nếu chưa cấu hình; `approval` yêu cầu `approval_ref`.
9. Kiểm tra giới hạn đồng thời (global semaphore + per-server semaphore).
10. Kiểm tra schema đối số đầu vào (`required`, `type`).
11. Giải quyết `secret_refs` chỉ từ env được cấp phép (không log secret).
12. Kiểm tra `idempotency_key` chống chạy trùng.
13. Thực thi với timeout + retry có kiểm soát (chỉ retry `read_only`, không retry mù thao tác có side effect).
14. Giới hạn kích thước output (`max_output_chars`), lọc secret, ghi `v31_mcp_invocations` và `v3_mcp_audit`.
