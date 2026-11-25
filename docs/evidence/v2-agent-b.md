# Bằng chứng triển khai Agent B — AI Memory Hub V2 (`agent/v2-observability`)

- **Nhánh:** `agent/v2-observability`
- **Base:** `origin/lead/v2-orchestrator` (`3bea6cc`)
- **Phạm vi sở hữu:** `services/memory-bridge/**`, `services/dashboard/**`, `tests/bridge/**`, `tests/dashboard/**`, `docs/evidence/v2-agent-b.md`

## 1. Tuân thủ Contract & Schema đóng băng

- **Import trực tiếp từ `services/orchestrator/contracts.py`:** `services/memory-bridge/app/v2_schemas.py` import trực tiếp các kiểu Pydantic và Literal (`ModelRecord`, `RoutingRecommendation`, `PreExecutionFeatures`, `TokenUsageCounters`, `QuotaSnapshot`, `TaskForecast`, `PlanTaskSpec`, `PlanCreateRequest`, `PlanApproveRequest`, `BrainPolicyConfig`, `BrainRoleConfig`, `BrainTokenAccounting`, `StrictV2Model`, ...).
- **SQLite Migration v3 (`services/memory-bridge/app/db.py`):** Triển khai toàn bộ 17 bảng `v2_*` theo đúng DDL đóng băng trong `docs/SCHEMA-V2.sql` (bao gồm `v2_catalog_snapshots`, `v2_models`, `v2_model_evaluations`, `v2_policies`, `v2_brain_roles`, `v2_plans`, `v2_plan_revisions`, `v2_plan_approvals`, `v2_tasks`, `v2_task_dependencies`, `v2_task_attempts`, `v2_job_leases`, `v2_sessions`, `v2_task_features`, `v2_session_outcomes`, `v2_usage_events`, `v2_quota_snapshots`, `v2_budget_reservations`, `v2_forecast_snapshots`, `v2_forecast_actuals`, `v2_audit_log`).
- **Đối chiếu `docs/evidence/host-brain-discovery-v2.md`:**
  - `tier0_rules`: Quy tắc cục bộ + SQLite (0 LLM).
  - `tier1_light` (`Gemini 4 Hydrogen`) & `tier2_primary` (`Gemini 4 Neon Medium`): gắn `availability_status = "unavailable"` vì chưa có trong `agy models` v1.1.27; không hardcode selector chưa xác minh và không tự fallback sang DeepSeek API trả phí.
  - `tier3_advanced` (`claude-opus-5-5-medium`) & `secondary_fallback` (`claude-sonnet-5-5-medium`): gắn `verified` qua `agy`.

## 2. Chức năng V2 đã triển khai

### Memory Bridge V2 (`services/memory-bridge/app/v2_service.py`, `v2_routes.py`)
- **Phân quyền RBAC V2:** Bổ sung scope `planner` (`BRIDGE_PLANNER_TOKENS`) và `runner` (`BRIDGE_RUNNER_TOKENS`) tách biệt với `read`, `write`, `admin`, `worker`.
- **Catalog, Evaluation & Deterministic Routing:** `/v2/catalog`, `/v2/catalog/refresh`, `/v2/evaluations`, `/v2/routing/recommend` (không dùng LLM, không tự fallback API trả phí).
- **Policies & Adaptive Brain:** `/v2/policies`, `/v2/brain/config` kèm `v2_audit_log`.
- **Plans, DAG & Atomic Approval:** `/v2/plans`, `/v2/plans/{id}`, `/v2/plans/{id}/revisions` (sửa revision vô hiệu hóa duyệt cũ), `/v2/plans/{id}/approve` (kiểm tra `revision_id` + `revision_hash` nguyên tử, `409` nếu lệch, tạo `v2_budget_reservations`), `reject`/`pause`/`resume`/`cancel`.
- **Narrow Runner Jobs:** `/v2/jobs/claim` (chỉ claim task thuộc plan `APPROVED`/`RUNNING` khớp `approved_revision_id` và mọi phụ thuộc DAG đã `succeeded`, kiểm soát `max_concurrency`), `heartbeat`, `events`, `checkpoint`, `complete` (tất toán ngân sách & đối soát `v2_forecast_actuals`), `cancel`.
- **Restart Reconciliation (`db.py`):** Khi khởi động lại, reset `AUTO_SCOPED` về `REVIEW_REQUIRED` nếu `auto_persist_across_restart = 0`, giải phóng lease đang mở, đánh dấu attempt đang chạy là `orphaned`, đưa task về `queued` và phiên đang chạy về `partial`.
- **Session Analytics, Dedup Usage & Quotas:** `/v2/sessions`, `DELETE /v2/sessions/{id}` (retention + audit), `/v2/usage` (dedupe theo `(session_id, source_event_id)`, không cộng dồn snapshot `cumulative`, tách riêng `planner` vs `executor`, counter thiếu là `null`), `/v2/quotas` (`'Không có dữ liệu'` khi thiếu denominator), `/v2/analytics/cohorts`, `/v2/forecasts`, `/v2/forecasts/{id}/actual`, `/v2/analytics/calibration`.

### Dashboard Tiếng Việt V2 (`services/dashboard/app/main.py`, `ui.py`)
- Bổ sung đầy đủ proxy `/api/v2/*` (CSRF + Origin + session cookie cho thao tác ghi).
- Bổ sung các tab tiếng Việt: **Bộ não điều phối**, **Kế hoạch & Duyệt DAG**, **Phiên & Dự báo**, **Danh mục Model**; hiển thị rõ `'Không có dữ liệu'` cho quota/counter chưa có.

## 3. Kết quả kiểm thử (Mock/Synthetic, không gọi API trả phí)

Lệnh đã chạy:
```powershell
D:\workspace1\ai-memory-hub-agent-b\.venv\Scripts\python.exe -m pytest tests/bridge tests/dashboard -v
```
Kết quả thực tế: **14 passed in 8.62s**
- V1/V1.1 (`test_bridge_core.py`, `test_mcp_handshake.py`, `test_dashboard.py`): 10/10 PASSED.
- V2 Bridge (`tests/bridge/test_v2_bridge.py`): 3/3 PASSED (catalog/routing/adaptive brain defaults, DAG cycle/atomic approval/runner dependency order, dedup cumulative+delta usage, unknown quota `'Không có dữ liệu'`, cross-project isolation, restart reconciliation, retention delete).
- V2 Dashboard (`tests/dashboard/test_v2_dashboard.py`): 1/1 PASSED (Vietnamese V2 tabs, no leaked secrets, CSRF/Origin on `/api/v2/policies`, proxy `/api/v2/brain/config`, `/api/v2/usage`, `/api/v2/analytics/*`, `/api/v2/catalog`, `/api/v2/quotas`).

## 4. Lưu ý tích hợp & Blocker cho Lead

- **Tích hợp Dockerfile (`memory-bridge`):** `v2_schemas.py` import `services.orchestrator.contracts`; khi Lead build container `memory-bridge` qua `docker-compose.yml`, cần điều chỉnh build context hoặc copy `services/orchestrator/contracts.py` vào image `memory-bridge`.
- **Docker daemon:** Hiện chưa chạy trên host Windows nên chưa chạy `docker compose build` / container smoke test.
