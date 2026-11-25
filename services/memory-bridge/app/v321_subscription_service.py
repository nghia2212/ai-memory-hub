"""V3.2.1 P19A.1 & P19A.2 — Subscription Execution Sources, Agent/Model Controls, Quota & Project Defaults.

Design invariants:
- Separate Execution Source (antigravity, claude_code, codex_cli, gemini_cli, metered APIs, qwen_local, mock)
  from Model selection.
- Never hard-code IDE model lists: read real verified models from V2 Model Catalog (populated via `agy models` /
  Host Runner discovery) and live Host Runner `/host/clis` probe when reachable.
- Separate admin enabled state, runtime/CLI availability, subscription quota state, and active execution.
- Enforce disabled/exhausted eligibility in backend routing & dispatch (not just UI dropdowns).
- Never extract internal OAuth tokens, invent unsupported CLI flags, or silently fall back to paid API.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .quota_guard import quota_status
from .v2_service import V2BridgeService
from .v321_api_agent_schema import (
    AgentControlUpdateRequest,
    ModelControlUpdateRequest,
    ProjectChatDefaultsRequest,
)
from .v321_provider_service import V321ProviderService

SUBSCRIPTION_SOURCE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "source_key": "antigravity",
        "cli_name": "agy",
        "catalog_provider": "agy",
        "display_name": "Antigravity (agy CLI · Thuê bao)",
        "group": "subscription_cli",
        "billing_label": "Thuê bao (Subscription)",
        "auth_mode": "subscription_cli",
        "default_enabled": True,
        "supports_session_resume": True,
        "supports_direct_dispatch": True,
        "supports_manual_handoff": True,
    },
    {
        "source_key": "claude_code",
        "cli_name": "claude",
        "catalog_provider": "claude",
        "display_name": "Claude Code CLI (Thuê bao)",
        "group": "subscription_cli",
        "billing_label": "Thuê bao (Subscription)",
        "auth_mode": "subscription_cli",
        "default_enabled": True,
        "supports_session_resume": False,
        "supports_direct_dispatch": True,
        "supports_manual_handoff": True,
    },
    {
        "source_key": "codex_cli",
        "cli_name": "codex",
        "catalog_provider": "codex",
        "display_name": "Codex CLI (Thuê bao)",
        "group": "subscription_cli",
        "billing_label": "Thuê bao (Subscription)",
        "auth_mode": "subscription_cli",
        "default_enabled": True,
        "supports_session_resume": False,
        "supports_direct_dispatch": True,
        "supports_manual_handoff": True,
    },
    {
        "source_key": "gemini_cli",
        "cli_name": "gemini",
        "catalog_provider": "gemini",
        "display_name": "Gemini CLI (Thuê bao)",
        "group": "subscription_cli",
        "billing_label": "Thuê bao (Subscription)",
        "auth_mode": "subscription_cli",
        "default_enabled": False,
        "supports_session_resume": False,
        "supports_direct_dispatch": False,
        "supports_manual_handoff": True,
    },
)

SOURCE_TO_CLI: dict[str, str] = {s["source_key"]: s["cli_name"] for s in SUBSCRIPTION_SOURCE_SPECS}
CLI_TO_SOURCE: dict[str, str] = {s["cli_name"]: s["source_key"] for s in SUBSCRIPTION_SOURCE_SPECS}
SUBSCRIPTION_SOURCE_KEYS: frozenset[str] = frozenset(SOURCE_TO_CLI.keys())

_GROUP_LABELS = {
    "subscription_cli": "Thuê bao IDE / CLI (Không mua API Key)",
    "metered_api": "API trả phí (Metered API)",
    "local_compute": "Cục bộ (Local 0 phí)",
    "simulation": "Mô phỏng kiểm thử (Mock — Không tính thống kê AI thật)",
}


def probe_host_runner_clis(timeout: float = 2.5) -> dict[str, Any] | None:
    """Read-only probe to Host Brain Agent `/host/clis` (0 tokens, 0 paid calls)."""
    url = (os.environ.get("HOST_BRAIN_URL") or "").strip().rstrip("/")
    token = (os.environ.get("BRIDGE_RUNNER_TOKEN") or "").strip()
    if not url or not token:
        return None
    req = urllib.request.Request(
        f"{url}/host/clis",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else None
    except Exception:
        return None


def invoke_host_subscription_chat(payload: dict[str, Any], timeout: float = 120.0) -> tuple[int, dict[str, Any]]:
    """Call Host Brain Agent `/host/subscription-chat` over loopback with Runner Token."""
    url = (os.environ.get("HOST_BRAIN_URL") or "").strip().rstrip("/")
    token = (os.environ.get("BRIDGE_RUNNER_TOKEN") or "").strip()
    if not url or not token:
        return 503, {"ok": False, "error_code": "host_runner_not_configured", "message": "Host Runner chưa được cấu hình (HOST_BRAIN_URL / BRIDGE_RUNNER_TOKEN)."}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/host/subscription-chat",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"ok": False, "error_code": f"http_{exc.code}", "message": raw[:400]}
        return exc.code, parsed
    except Exception as exc:
        return 503, {"ok": False, "error_code": "host_runner_offline", "message": f"Không kết nối được Host Runner: {exc}"}


class V321SubscriptionService:
    """Unified Execution Source Registry, Agent/Model Controls, Quota Enforcement & Project Defaults."""

    def __init__(
        self,
        db: DatabaseManager,
        v2_service: V2BridgeService,
        provider_service: V321ProviderService,
    ) -> None:
        self.db = db
        self.v2_service = v2_service
        self.provider_service = provider_service

    def _load_controls(self, conn: Any) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
        agent_rows = conn.execute("SELECT * FROM mcp321_agent_controls").fetchall()
        model_rows = conn.execute("SELECT * FROM mcp321_model_controls").fetchall()
        agents = {r["source_key"]: dict(r) for r in agent_rows}
        models = {(r["source_key"], r["model_id"]): dict(r) for r in model_rows}
        return agents, models

    def _audit(self, conn: Any, *, actor: str, action: str, target_type: str, target_id: str, details: dict[str, Any], project_id: str | None = None) -> None:
        conn.execute(
            """
            INSERT INTO v2_audit_log (id, project_id, actor, action, target_type, target_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), project_id, actor, action, target_type, target_id, json.dumps(details, ensure_ascii=False), utc_now_iso()),
        )

    def set_agent_control(self, source_key: str, req: AgentControlUpdateRequest, *, actor: str = "admin") -> dict[str, Any]:
        skey = source_key.strip().lower()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO mcp321_agent_controls (source_key, enabled, disabled_reason, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    enabled = excluded.enabled,
                    disabled_reason = excluded.disabled_reason,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (skey, 1 if req.enabled else 0, req.disabled_reason.strip(), actor, now),
            )
            # Also mirror enable/disable onto matching API/Local/Mock provider profile if present
            conn.execute(
                "UPDATE mcp321_provider_profiles SET enabled = ?, updated_at = ? WHERE id = ? OR provider_key = ?",
                (1 if req.enabled else 0, now, skey, skey),
            )
            self._audit(
                conn,
                actor=actor,
                action="agent_control_updated",
                target_type="execution_source",
                target_id=skey,
                details={"enabled": req.enabled, "disabled_reason": req.disabled_reason.strip()},
            )
        return {
            "source_key": skey,
            "enabled": req.enabled,
            "disabled_reason": req.disabled_reason.strip(),
            "active_tasks_affected": False,
            "note": "Tắt Agent chỉ chặn điều phối mới; các tác vụ đang chạy không bị tự động hủy.",
            "updated_at": now,
        }

    def set_model_control(self, req: ModelControlUpdateRequest, *, actor: str = "admin") -> dict[str, Any]:
        skey = req.source_key.strip().lower()
        mid = req.model_id.strip()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO mcp321_model_controls (source_key, model_id, enabled, disabled_reason, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key, model_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    disabled_reason = excluded.disabled_reason,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (skey, mid, 1 if req.enabled else 0, req.disabled_reason.strip(), actor, now),
            )
            self._audit(
                conn,
                actor=actor,
                action="model_control_updated",
                target_type="model",
                target_id=f"{skey}:{mid}",
                details={"source_key": skey, "model_id": mid, "enabled": req.enabled, "disabled_reason": req.disabled_reason.strip()},
            )
        return {
            "source_key": skey,
            "model_id": mid,
            "enabled": req.enabled,
            "disabled_reason": req.disabled_reason.strip(),
            "updated_at": now,
        }

    def list_execution_sources(
        self,
        *,
        project_id: str | None = None,
        probe_host: bool = True,
    ) -> dict[str, Any]:
        """Return complete Execution Source + live Model Catalog + Admin Enable + Quota matrix."""
        self.provider_service.ensure_seeded()
        host_disc = probe_host_runner_clis() if probe_host else None
        host_online = host_disc is not None
        host_cli_map: dict[str, dict[str, Any]] = {}
        host_models_by_cli: dict[str, list[dict[str, Any]]] = {}
        if host_disc:
            for c in host_disc.get("clis") or []:
                host_cli_map[str(c.get("cli_name") or "")] = c
            for m in host_disc.get("models") or []:
                cf = str(m.get("cli_family") or m.get("provider") or "")
                if m.get("status") == "verified":
                    host_models_by_cli.setdefault(cf, []).append(m)

        with self.db.read_conn() as conn:
            agent_ctrls, model_ctrls = self._load_controls(conn)
            quotas = self.v2_service._latest_quotas(conn)
            egress = self.provider_service.get_project_egress_policy(project_id) if project_id else None
            snap = conn.execute("SELECT id, fetched_at FROM v2_catalog_snapshots ORDER BY fetched_at DESC LIMIT 1").fetchone()
            catalog_models_by_cli: dict[str, list[dict[str, Any]]] = {}
            if snap:
                rows = conn.execute("SELECT * FROM v2_models WHERE snapshot_id = ? AND status = 'verified'", (snap["id"],)).fetchall()
                for r in rows:
                    cf = str(r["cli_family"] or r["provider"] or "")
                    caps = json.loads(r["capabilities_json"] or "{}")
                    catalog_models_by_cli.setdefault(cf, []).append(
                        {
                            "provider": r["provider"],
                            "model_id": r["model_id"],
                            "cli_family": cf,
                            "cli_version": r["cli_version"],
                            "auth_mode": r["auth_mode"],
                            "display_name": caps.get("display_name") or r["model_id"],
                            "verification_stage": caps.get("verification_stage") or "cli_available",
                            "source_url": r["source_url"],
                            "verified_at": r["verified_at"],
                        }
                    )

        sources: list[dict[str, Any]] = []

        # 1. Subscription CLI / IDE Sources
        for spec in SUBSCRIPTION_SOURCE_SPECS:
            skey = spec["source_key"]
            cli_name = spec["cli_name"]
            ctrl = agent_ctrls.get(skey)
            admin_enabled = bool(ctrl["enabled"]) if ctrl is not None else bool(spec["default_enabled"])
            disabled_reason = ctrl["disabled_reason"] if ctrl is not None else ("" if admin_enabled else "Tắt mặc định cho đến khi cài đặt & xác minh CLI.")

            cinfo = host_cli_map.get(cli_name)
            # Combine live host discovery models with persisted V2 catalog models (preferring live, deduplicated)
            merged_models: dict[str, dict[str, Any]] = {}
            for m in catalog_models_by_cli.get(cli_name, []):
                merged_models[m["model_id"]] = m
            for m in host_models_by_cli.get(cli_name, []):
                caps = m.get("capabilities") or {}
                merged_models[m["model_id"]] = {
                    "provider": m.get("provider") or cli_name,
                    "model_id": m["model_id"],
                    "cli_family": cli_name,
                    "cli_version": m.get("cli_version"),
                    "auth_mode": m.get("auth_mode") or "subscription_cli",
                    "display_name": caps.get("display_name") or m["model_id"],
                    "verification_stage": caps.get("verification_stage") or "cli_available",
                    "source_url": m.get("source_url") or f"local://{cli_name}/models",
                    "verified_at": m.get("verified_at"),
                }

            if cinfo:
                installed = bool(cinfo.get("installed"))
                cli_version = cinfo.get("version")
                runtime_status = "ready" if (installed and (merged_models or cli_name in ("claude", "codex"))) else ("installed_no_models" if installed else "not_installed")
                runtime_note = cinfo.get("notes") or (
                    f"CLI `{cli_name}` {cli_version} sẵn sàng trên Host Runner." if installed else f"CLI `{cli_name}` chưa cài đặt trên máy chủ."
                )
            elif merged_models:
                installed = True
                cli_version = next((m.get("cli_version") for m in merged_models.values() if m.get("cli_version")), None)
                runtime_status = "host_offline" if not host_online else "ready"
                runtime_note = "Danh sách model lấy từ Catalog gần nhất; Host Runner hiện offline (có thể dùng Bàn giao sang IDE)." if not host_online else f"Sẵn sàng qua Catalog ({len(merged_models)} model)."
            else:
                installed = False
                cli_version = None
                runtime_status = "host_offline" if not host_online else "not_installed"
                runtime_note = "Host Runner offline hoặc CLI chưa cài đặt."

            direct_ready = bool(spec["supports_direct_dispatch"] and installed and host_online)
            models_out: list[dict[str, Any]] = []
            for mid in sorted(merged_models.keys()):
                m = merged_models[mid]
                mctrl = model_ctrls.get((skey, mid))
                m_enabled = bool(mctrl["enabled"]) if mctrl is not None else True
                m_disabled_reason = mctrl["disabled_reason"] if mctrl is not None else ""
                q = quota_status(quotas, spec["catalog_provider"], mid, "subscription_cli")
                reasons: list[str] = []
                if not admin_enabled:
                    reasons.append(f"Agent đã tắt{': ' + disabled_reason if disabled_reason else ''}")
                if not m_enabled:
                    reasons.append(f"Model đã tắt{': ' + m_disabled_reason if m_disabled_reason else ''}")
                if q["state"] == "exhausted":
                    reasons.append("Đã hết quota" + (f" (reset dự kiến: {q['reset_at']})" if q.get("reset_at") else ""))
                if not installed:
                    reasons.append(f"CLI `{cli_name}` chưa cài đặt")
                eligible = admin_enabled and m_enabled and q["state"] != "exhausted" and installed
                models_out.append(
                    {
                        "source_key": skey,
                        "provider": spec["catalog_provider"],
                        "model_id": mid,
                        "display_name": m.get("display_name") or mid,
                        "auth_mode": "subscription_cli",
                        "verification_stage": m.get("verification_stage") or "cli_available",
                        "source_url": m.get("source_url"),
                        "verified_at": m.get("verified_at"),
                        "admin_enabled": m_enabled,
                        "disabled_reason": m_disabled_reason,
                        "quota": q,
                        "eligible": eligible,
                        "ineligible_reason": "; ".join(reasons) if reasons else None,
                    }
                )

            sources.append(
                {
                    "source_key": skey,
                    "provider_profile_id": None,
                    "provider_key": spec["catalog_provider"],
                    "cli_name": cli_name,
                    "display_name": spec["display_name"],
                    "group": spec["group"],
                    "group_label": _GROUP_LABELS[spec["group"]],
                    "billing_mode": "subscription_cli",
                    "billing_label": spec["billing_label"],
                    "admin_enabled": admin_enabled,
                    "disabled_reason": disabled_reason,
                    "runtime_status": runtime_status,
                    "runtime_note": runtime_note,
                    "cli_version": cli_version,
                    "host_runner_online": host_online,
                    "capabilities": {
                        "chat": bool(spec["supports_direct_dispatch"] and installed),
                        "task": bool(spec["supports_direct_dispatch"] and installed),
                        "plan_dag": True,
                        "direct_dispatch": direct_ready,
                        "manual_handoff": bool(spec["supports_manual_handoff"]),
                        "session_resume": bool(spec["supports_session_resume"] and installed),
                        "mcp_tools": True,
                    },
                    "models": models_out,
                    "eligible_model_count": sum(1 for x in models_out if x["eligible"]),
                }
            )

        # 2. Metered API, Local Compute & Mock Providers from existing V321ProviderService
        provs = self.provider_service.list_providers()["items"]
        for p in provs:
            bmode = p["billing_mode"]
            group = "simulation" if bmode == "mock" else ("local_compute" if bmode == "local_compute" else "metered_api")
            skey = p["id"]
            ctrl = agent_ctrls.get(skey) or agent_ctrls.get(p["provider_key"])
            admin_enabled = bool(ctrl["enabled"]) if ctrl is not None else bool(p["enabled"])
            disabled_reason = ctrl["disabled_reason"] if ctrl is not None else ("" if admin_enabled else "Provider đang tắt trong cấu hình.")
            egress_blocked = False
            if bmode == "metered_api" and egress is not None:
                if not egress["allow_external_paid_api"] or p["provider_key"] not in egress["allowed_provider_keys"]:
                    egress_blocked = True

            mid = p["selected_model"]
            mctrl = model_ctrls.get((skey, mid)) or model_ctrls.get((p["provider_key"], mid))
            m_enabled = bool(mctrl["enabled"]) if mctrl is not None else True
            m_disabled_reason = mctrl["disabled_reason"] if mctrl is not None else ""
            auth_m = "api_key" if bmode == "metered_api" else "unknown"
            q = quota_status(quotas, p["provider_key"], mid, auth_m) if bmode != "mock" else {"state": "unknown", "source": "simulation", "reset_at": None}

            reasons = []
            if not admin_enabled:
                reasons.append(f"Agent đã tắt{': ' + disabled_reason if disabled_reason else ''}")
            if not m_enabled:
                reasons.append(f"Model đã tắt{': ' + m_disabled_reason if m_disabled_reason else ''}")
            if q["state"] == "exhausted":
                reasons.append("Đã hết quota")
            if bmode == "metered_api" and not p["credential_present"]:
                reasons.append(f"Thiếu biến môi trường {p['credential_env_ref']}")
            if egress_blocked:
                reasons.append("Chính sách Egress dự án đang chặn API trả phí")

            eligible = admin_enabled and m_enabled and q["state"] != "exhausted" and (bmode != "metered_api" or (p["credential_present"] and not egress_blocked))
            runtime_status = "ready" if (bmode == "mock" or (bmode == "local_compute" and admin_enabled) or (p["credential_present"] and not egress_blocked)) else (
                "egress_blocked" if egress_blocked else ("missing_key" if bmode == "metered_api" and not p["credential_present"] else "disabled")
            )

            sources.append(
                {
                    "source_key": skey,
                    "provider_profile_id": p["id"],
                    "provider_key": p["provider_key"],
                    "cli_name": None,
                    "display_name": p["display_name"],
                    "group": group,
                    "group_label": _GROUP_LABELS[group],
                    "billing_mode": bmode,
                    "billing_label": "Mô phỏng (Testing only)" if bmode == "mock" else ("Cục bộ (0 phí)" if bmode == "local_compute" else "API trả phí (Metered)"),
                    "admin_enabled": admin_enabled,
                    "disabled_reason": disabled_reason,
                    "runtime_status": runtime_status,
                    "runtime_note": p["credential_masked"],
                    "cli_version": None,
                    "host_runner_online": host_online,
                    "capabilities": {
                        "chat": True,
                        "task": True,
                        "plan_dag": True,
                        "direct_dispatch": eligible,
                        "manual_handoff": False,
                        "session_resume": False,
                        "mcp_tools": True,
                    },
                    "models": [
                        {
                            "source_key": skey,
                            "provider": p["provider_key"],
                            "model_id": mid,
                            "display_name": f"{mid} ({p['display_name']})",
                            "auth_mode": auth_m,
                            "verification_stage": "simulation" if bmode == "mock" else ("configured" if p["credential_present"] else "unconfigured"),
                            "source_url": p["endpoint_url"],
                            "verified_at": p["updated_at"],
                            "admin_enabled": m_enabled,
                            "disabled_reason": m_disabled_reason,
                            "quota": q,
                            "eligible": eligible,
                            "ineligible_reason": "; ".join(reasons) if reasons else None,
                        }
                    ],
                    "eligible_model_count": 1 if eligible else 0,
                }
            )

        return {
            "host_runner_online": host_online,
            "sources": sources,
            "quotas": quotas,
            "defaults": self.get_project_chat_defaults(project_id) if project_id else None,
        }

    def check_dispatch_eligibility(
        self,
        *,
        source_key: str,
        model_id: str | None,
        project_id: str,
        mode: str = "chat",
        require_direct_dispatch: bool = True,
    ) -> dict[str, Any]:
        """Backend enforcement gate: rejects disabled Agent, disabled Model, exhausted Quota, or unsupported mode."""
        skey = source_key.strip().lower()
        with self.db.read_conn() as conn:
            agent_ctrls, model_ctrls = self._load_controls(conn)
            quotas = self.v2_service._latest_quotas(conn)

            # 1. Agent enabled check
            actrl = agent_ctrls.get(skey)
            if actrl is not None and not actrl["enabled"]:
                raise BridgeError(
                    403,
                    "agent_disabled",
                    f"Agent '{skey}' đang bị tắt tại Agent Control Center"
                    + (f" ({actrl['disabled_reason']})" if actrl["disabled_reason"] else "")
                    + ".",
                )

            # 2. Subscription CLI source
            if skey in SUBSCRIPTION_SOURCE_KEYS:
                spec = next(s for s in SUBSCRIPTION_SOURCE_SPECS if s["source_key"] == skey)
                if actrl is None and not spec["default_enabled"]:
                    raise BridgeError(403, "agent_disabled", f"Agent '{skey}' đang tắt mặc định. Hãy bật tại Agent Control Center sau khi cài đặt CLI.")

                # Resolve model (explicit or deterministic auto among verified, enabled, non-exhausted models)
                snap = conn.execute("SELECT id FROM v2_catalog_snapshots ORDER BY fetched_at DESC LIMIT 1").fetchone()
                verified_ids: list[str] = []
                if snap:
                    rows = conn.execute(
                        "SELECT model_id FROM v2_models WHERE snapshot_id = ? AND status = 'verified' AND (cli_family = ? OR provider = ?)",
                        (snap["id"], spec["cli_name"], spec["catalog_provider"]),
                    ).fetchall()
                    verified_ids = sorted({r["model_id"] for r in rows})

                chosen_model = (model_id or "").strip()
                auto_reason = None
                if not chosen_model:
                    # Deterministic auto-selection among verified, enabled, non-exhausted models
                    candidates = []
                    for mid in verified_ids:
                        mc = model_ctrls.get((skey, mid))
                        if mc is not None and not mc["enabled"]:
                            continue
                        qs = quota_status(quotas, spec["catalog_provider"], mid, "subscription_cli")
                        if qs["state"] == "exhausted":
                            continue
                        # Prefer observed_available, then gemini-4-neon-medium, then deterministic alpha
                        candidates.append((qs["state"] != "observed_available", "neon-medium" not in mid, mid))
                    if not candidates:
                        raise BridgeError(
                            409,
                            "no_eligible_subscription_model",
                            f"Không có model khả dụng nào cho '{skey}' (tất cả đều bị tắt, hết quota hoặc chưa đồng bộ catalog).",
                        )
                    candidates.sort()
                    chosen_model = candidates[0][2]
                    auto_reason = f"Tự động chọn model xác minh khả dụng '{chosen_model}' (không dùng API trả phí)."

                # Check model control
                mctrl = model_ctrls.get((skey, chosen_model))
                if mctrl is not None and not mctrl["enabled"]:
                    raise BridgeError(
                        403,
                        "model_disabled",
                        f"Model '{chosen_model}' của '{skey}' đang bị tắt tại Agent Control Center"
                        + (f" ({mctrl['disabled_reason']})" if mctrl["disabled_reason"] else "")
                        + ".",
                    )

                # Check quota (exact provider + auth_mode + model bucket)
                q = quota_status(quotas, spec["catalog_provider"], chosen_model, "subscription_cli")
                if q["state"] == "exhausted":
                    raise BridgeError(
                        409,
                        "quota_exhausted",
                        f"Model '{chosen_model}' ({skey}) đã hết quota"
                        + (f" (dự kiến reset: {q['reset_at']})" if q.get("reset_at") else "")
                        + ". Không tự động chuyển sang API trả phí.",
                    )

                if require_direct_dispatch and not spec["supports_direct_dispatch"]:
                    raise BridgeError(
                        409,
                        "direct_dispatch_unsupported",
                        f"Agent '{skey}' không hỗ trợ direct dispatch trên runtime hiện tại. Vui lòng dùng chế độ Bàn giao sang IDE (Manual Handoff).",
                    )

                return {
                    "source_key": skey,
                    "group": "subscription_cli",
                    "cli_name": spec["cli_name"],
                    "provider": spec["catalog_provider"],
                    "model_id": chosen_model,
                    "quota": q,
                    "auto_selection_reason": auto_reason,
                }

        # 3. API / Local / Mock provider profile
        prof = self.provider_service.get_provider(source_key)
        with self.db.read_conn() as conn:
            agent_ctrls, model_ctrls = self._load_controls(conn)
            quotas = self.v2_service._latest_quotas(conn)
            for k in (prof["id"], prof["provider_key"]):
                ac = agent_ctrls.get(k)
                if ac is not None and not ac["enabled"]:
                    raise BridgeError(403, "agent_disabled", f"Provider '{prof['display_name']}' đang bị tắt.")
            if not prof["enabled"]:
                raise BridgeError(403, "provider_disabled", f"Provider '{prof['display_name']}' đang bị tắt.")
            chosen_model = (model_id or prof["selected_model"] or "").strip()
            for k in (prof["id"], prof["provider_key"]):
                mc = model_ctrls.get((k, chosen_model))
                if mc is not None and not mc["enabled"]:
                    raise BridgeError(403, "model_disabled", f"Model '{chosen_model}' đang bị tắt tại Agent Control Center.")
            if prof["billing_mode"] != "mock":
                auth_m = "api_key" if prof["billing_mode"] == "metered_api" else "unknown"
                q = quota_status(quotas, prof["provider_key"], chosen_model, auth_m)
                if q["state"] == "exhausted":
                    raise BridgeError(409, "quota_exhausted", f"Model '{chosen_model}' ({prof['provider_key']}) đã hết quota.")
            else:
                q = {"state": "unknown", "source": "simulation", "reset_at": None}

        return {
            "source_key": prof["id"],
            "group": "simulation" if prof["billing_mode"] == "mock" else ("local_compute" if prof["billing_mode"] == "local_compute" else "metered_api"),
            "provider_profile": prof,
            "provider": prof["provider_key"],
            "model_id": chosen_model,
            "quota": q,
            "auto_selection_reason": None,
        }

    def get_project_chat_defaults(self, project_id: str) -> dict[str, Any]:
        """Return persisted project defaults, or smart read-only recommendation without overwriting user prefs."""
        with self.db.read_conn() as conn:
            proj = conn.execute("SELECT id, name FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not proj:
                raise BridgeError(404, "project_not_found", "Project not found.")
            saved = conn.execute("SELECT * FROM mcp321_project_chat_defaults WHERE project_id = ?", (project_id,)).fetchone()
            if saved:
                d = dict(saved)
                d["allow_paid_api_fallback"] = bool(d["allow_paid_api_fallback"])
                d["persisted"] = True
                return d

            # Deterministic recommendation (never persisted automatically!)
            pname = str(proj["name"] or "").lower()
            is_game = any(k in pname for k in ("doi-cun", "game", "khung-long", "phaser", "godot", "unity"))
            biz_row = conn.execute(
                "SELECT id FROM v31_business_profiles WHERE LOWER(name) LIKE ? AND archived = 0 LIMIT 1",
                ("%game%" if is_game else "%software%",),
            ).fetchone()
            snap = conn.execute("SELECT id FROM v2_catalog_snapshots ORDER BY fetched_at DESC LIMIT 1").fetchone()
            rec_model = ""
            if snap:
                m = conn.execute(
                    "SELECT model_id FROM v2_models WHERE snapshot_id = ? AND cli_family = 'agy' AND status = 'verified' ORDER BY (model_id LIKE '%neon-medium%') DESC, model_id ASC LIMIT 1",
                    (snap["id"],),
                ).fetchone()
                if m:
                    rec_model = m["model_id"]
            return {
                "project_id": project_id,
                "business_id": biz_row["id"] if biz_row else "",
                "execution_source": "antigravity",
                "model_id": rec_model,
                "mode": "task" if is_game else "chat",
                "allow_paid_api_fallback": False,
                "persisted": False,
                "recommendation_reason": (
                    "Gợi ý cho dự án Game: Game Development + Antigravity (Thuê bao) + Task + tắt fallback API trả phí."
                    if is_game
                    else "Gợi ý mặc định: Antigravity (Thuê bao) + tắt fallback API trả phí."
                ),
            }

    def set_project_chat_defaults(self, project_id: str, req: ProjectChatDefaultsRequest, *, actor: str = "admin") -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            if req.business_id and not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (req.business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            conn.execute(
                """
                INSERT INTO mcp321_project_chat_defaults (
                    project_id, business_id, execution_source, model_id, mode, allow_paid_api_fallback, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    business_id = excluded.business_id,
                    execution_source = excluded.execution_source,
                    model_id = excluded.model_id,
                    mode = excluded.mode,
                    allow_paid_api_fallback = excluded.allow_paid_api_fallback,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    req.business_id.strip(),
                    req.execution_source.strip().lower(),
                    req.model_id.strip(),
                    req.mode,
                    1 if req.allow_paid_api_fallback else 0,
                    actor,
                    now,
                ),
            )
            self._audit(
                conn,
                actor=actor,
                action="project_chat_defaults_updated",
                target_type="project",
                target_id=project_id,
                details={
                    "business_id": req.business_id,
                    "execution_source": req.execution_source,
                    "model_id": req.model_id,
                    "mode": req.mode,
                    "allow_paid_api_fallback": req.allow_paid_api_fallback,
                },
                project_id=project_id,
            )
        return self.get_project_chat_defaults(project_id)
