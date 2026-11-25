# Vietnamese dashboard design
Navigation: Tổng quan | Công việc | Bộ não | MCP | Nhóm công cụ | Workflow | Nhật ký | Tài nguyên | Cài đặt.
## MCP Control Center
Summary installed/registered/verified/enabled/actually running separately, measured RAM/CPU with timestamp, unknown not zero; per-group expandable rows with tri-state toggles, dependency warnings, owner-pinned vs lease-held states, error/health, last use, version, trust, runtime/transport, project grants. Enable sets eligibility not necessarily start; Start acquires manual lease; Stop drains managed instance; Disable denies new calls; emergency revoke distinct confirmation. Remote entries show Connected/Disconnected not process stopped. Core cannot be toggled off via dashboard (maintenance separate). Group action preview lists shared dependencies, affected active jobs, budget, permissions; no automatic elevation.
## Add MCP wizard
1 Marketplace / paste standard mcpServers JSON / manual manifest. Parse inert; normalize command args/url/env placeholders into manifest, reject unsupported config explicitly.
2 Source/version/license/checksum/dependency/runtime review, no automatic downloads.
3 Permission diff, project/worktree and agent role grants, egress and credentials via secret reference.
4 Owner approves installation exact hash, background operation status in application, progress/errors/cancel; no real secret in UI/log.
5 Probe initialize/tools/list, show schema/action classes as unverified until review; activate selected tools/profile only. 
Update: compare version and permission/schema diff, install side-by-side, verify, drain and switch, rollback prior verified revision; no core rebuild. Uninstall preserve or delete data explicit, active leases block unless safe drain. Export manifests/profiles without credentials; restore quarantine.
## Profiles / plan review
Create/edit/copy/import groups and project profiles, rule-based tool recommendations; requested MCPs, permissions, measured or estimated memory and call budgets shown with V2 model and token forecast. Approve exact revision. Manual/profile/auto scoped controls; auto mode expiry and kill switch.
## Marketplace
Official/approved sources only, cached candidate metadata with fetched_at, freshness and status. Search/filter runtime/license/risk/category. No auto-install or automatic upgrade. Missing dependency instructions. 
## Workflow and analytics
Versioned scripts, typed inputs/outputs, approved tool revisions, dry-run (only when safe supported), execution timeline, call counts and result sizes, error/cancel/uncertain status, reusable workflow approval. Baseline vs Code Mode token/cost and latency, planner/executor metrics separate; unknown quota remains unknown, no fabricated savings. Audit filter by project/actor/time/tool, redacted export. 
## UI acceptance
Vietnamese labels, keyboard navigation, responsive desktop/mobile, accessible contrast, explicit destructive action confirmation, no secret echoed, 401/403/409 state errors, realtime polling/SSE with reconnect and stale indicators, no optimistic success before backend operation verified.
