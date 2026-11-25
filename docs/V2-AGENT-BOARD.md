# V2 Agent Board — ACTIVE on `lead/v2-orchestrator`

- **Updated:** `2026-11-25T10:15:00+07:00`
- **V1/V1.1 Integration Base SHA:** `1ff4bb27167ecdda02767d1ac61b5965e54c7588` (`lead/v1-integration`, 35/35 automated tests PASSED including real cross-service E2E)
- **V2 Integration Branch:** `lead/v2-orchestrator` (branched from tested V1/V1.1 integration SHA; **DO NOT** merge to `main` until host Docker Desktop container gate + V2 acceptance gates pass)
- **Frozen V2 Contracts (Lead-Owned):**
  - API Contract: `docs/INTERFACE-V2.md`
  - SQLite Schema DDL (Migration v3+): `docs/SCHEMA-V2.sql`
  - Shared Pydantic Types & `BrainAdapter` Protocol: `services/orchestrator/contracts.py`
  - Requirements: `docs/ORCHESTRATOR-V2.md`, `docs/SESSION-ANALYTICS-V2.md`, `docs/ADAPTIVE-BRAIN-V2.md`

## Trạng thái phân công 3 Agent

| Agent | Nhánh làm việc (tạo từ `lead/v2-orchestrator`) | Phạm vi sở hữu | Trạng thái | Tài liệu nhiệm vụ |
| :--- | :--- | :--- | :--- | :--- |
| **Agent A** | `agent/v2-model-registry` | `services/orchestrator/catalog/**`, `services/orchestrator/routing/**`, `services/orchestrator/forecast/**`, `tests/orchestrator/catalog/**`, `tests/orchestrator/forecast/**`, `docs/evidence/v2-agent-a.md` | **ACTIVE / READY** | `docs/tasks/V2-AGENT-A.md` |
| **Agent B** | `agent/v2-observability` | `services/memory-bridge/**`, `services/dashboard/**`, `tests/bridge/**`, `tests/dashboard/**`, `docs/evidence/v2-agent-b.md` | **ACTIVE / READY** | `docs/tasks/V2-AGENT-B.md` |
| **Agent C** | `agent/v2-orchestration` | `services/orchestrator/planner/**`, `services/orchestrator/dispatch/**`, `integrations/**`, `scripts/runner/**`, `tests/orchestrator/runner/**`, `docs/evidence/v2-agent-c.md` | **ACTIVE / READY** | `docs/tasks/V2-AGENT-C.md` |

## Quy tắc bắt buộc cho cả 3 Agent

1. Tạo nhánh từ `origin/lead/v2-orchestrator`, chỉ mở **Draft PR** về `lead/v2-orchestrator`. Tuyệt đối không merge `main`.
2. Import và tuân thủ tuyệt đối `docs/INTERFACE-V2.md`, `docs/SCHEMA-V2.sql`, và `services/orchestrator/contracts.py`. Không tự ý sửa file thuộc phạm vi Lead hoặc agent khác.
3. **Không bịa số liệu:** counter thiếu phải là `null`, quota chưa biết phải hiển thị `'Không có dữ liệu'` (không hiển thị `0%`), không cộng trùng cached input/reasoning, không tự động fallback sang API trả phí khi chưa có phê duyệt.
4. **Không gọi API trả phí thật** trong test tự động (dùng fake/mock adapters và synthetic fixtures).
5. Ghi đầy đủ bằng chứng test, commit SHA và blocker vào `docs/evidence/v2-agent-*.md`.

## Gate còn mở (Lead quản lý trước khi merge `main`)

- Bật `com.docker.service` bằng quyền Administrator trên Windows host và chạy xác nhận `scripts/preflight.ps1` + `docker compose build` + `docker compose up -d --wait` + `docker stats`.
