"""V3.2.1 P15 & P18 — Provider Profile Registry, Project Egress Policy & Atomic Budget Controller."""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .v321_api_agent_schema import (
    ProjectEgressPolicyRequest,
    ProviderConnectionTestRequest,
    ProviderProfilePatchRequest,
    ProviderProfileUpsertRequest,
)
from .v321_provider_gateway import (
    AIProviderGateway,
    NormalizedUsage,
    PROVIDER_CAPABILITY_MANIFESTS,
    ProviderRequest,
    validate_endpoint_allowlist,
)

# Reject raw API keys anywhere in provider config (DB stores env var reference names ONLY!)
_RAW_KEY_PATTERNS = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z\-_]{24,}|ghp_[A-Za-z0-9]{20,}|anthropic-[A-Za-z0-9_-]{16,}|-----BEGIN )",
    re.IGNORECASE,
)
_VALID_ENV_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

DEFAULT_SEED_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "prov-mock-deterministic",
        "provider_key": "mock",
        "display_name": "Mock Provider (Deterministic 0-Cost)",
        "billing_mode": "mock",
        "credential_env_ref": "",
        "endpoint_url": "mock://deterministic",
        "selected_model": "mock-deterministic-v1",
        "thinking_level": "medium",
        "max_output_tokens": 2048,
        "pricing_version": "mock-v1",
        "pricing": {"input_per_1m": 0.50, "output_per_1m": 2.00, "cache_read_per_1m": 0.10},
        "enabled": 1,
    },
    {
        "id": "prov-deepseek-api",
        "provider_key": "deepseek",
        "display_name": "DeepSeek API (Metered)",
        "billing_mode": "metered_api",
        "credential_env_ref": "DEEPSEEK_API_KEY",
        "endpoint_url": "https://api.deepseek.com/v1/chat/completions",
        "selected_model": "deepseek-chat",
        "thinking_level": "none",
        "max_output_tokens": 4096,
        "pricing_version": "v321-2026-11",
        "pricing": {"input_per_1m": 0.27, "output_per_1m": 1.10, "cache_read_per_1m": 0.07},
        "enabled": 0,
    },
    {
        "id": "prov-gemini-api",
        "provider_key": "gemini",
        "display_name": "Gemini API (Developer Metered)",
        "billing_mode": "metered_api",
        "credential_env_ref": "GEMINI_API_KEY",
        "endpoint_url": "https://generativelanguage.googleapis.com/v1beta",
        "selected_model": "gemini-2.5-flash",
        "thinking_level": "low",
        "max_output_tokens": 4096,
        "pricing_version": "v321-2026-11",
        "pricing": {"input_per_1m": 0.15, "output_per_1m": 0.60, "cache_read_per_1m": 0.0375},
        "enabled": 0,
    },
    {
        "id": "prov-qwen-local",
        "provider_key": "qwen_local",
        "display_name": "Qwen Local (Ollama / LAN 0-Cost)",
        "billing_mode": "local_compute",
        "credential_env_ref": "",
        "endpoint_url": "http://127.0.0.1:11434/v1/chat/completions",
        "selected_model": "qwen2.5-coder:7b",
        "thinking_level": "none",
        "max_output_tokens": 2048,
        "pricing_version": "local-free",
        "pricing": {"input_per_1m": 0.0, "output_per_1m": 0.0},
        "enabled": 0,
    },
    {
        "id": "prov-openai-api",
        "provider_key": "openai",
        "display_name": "OpenAI API (Metered)",
        "billing_mode": "metered_api",
        "credential_env_ref": "OPENAI_API_KEY",
        "endpoint_url": "https://api.openai.com/v1/chat/completions",
        "selected_model": "gpt-5-mini",
        "thinking_level": "low",
        "max_output_tokens": 4096,
        "pricing_version": "unverified",
        "pricing": {},
        "enabled": 0,
    },
    {
        "id": "prov-anthropic-api",
        "provider_key": "anthropic",
        "display_name": "Anthropic Claude API (Metered)",
        "billing_mode": "metered_api",
        "credential_env_ref": "ANTHROPIC_API_KEY",
        "endpoint_url": "https://api.anthropic.com/v1/messages",
        "selected_model": "claude-sonnet-4-5",
        "thinking_level": "none",
        "max_output_tokens": 4096,
        "pricing_version": "unverified",
        "pricing": {},
        "enabled": 0,
    },
]


def _validate_credential_env_ref(ref: str) -> str:
    s = (ref or "").strip()
    if not s:
        return ""
    if _RAW_KEY_PATTERNS.search(s):
        raise BridgeError(422, "raw_api_key_forbidden", "Raw API key is strictly forbidden. Provide an environment variable name (e.g. DEEPSEEK_API_KEY) instead.")
    if not _VALID_ENV_REF_RE.match(s):
        raise BridgeError(422, "invalid_credential_env_ref", "credential_env_ref must be a valid uppercase env var name (e.g. DEEPSEEK_API_KEY).")
    return s


def _mask_provider_row(row: Any) -> dict[str, Any]:
    env_ref = row["credential_env_ref"] or ""
    cred_present = bool(env_ref and os.environ.get(env_ref, "").strip()) if env_ref else (row["provider_key"] in ("mock", "qwen_local"))
    manifest = PROVIDER_CAPABILITY_MANIFESTS.get(row["provider_key"], {})
    pricing = json.loads(row["pricing_json"] or "{}")
    return {
        "id": row["id"],
        "provider_key": row["provider_key"],
        "display_name": row["display_name"],
        "billing_mode": row["billing_mode"],
        "credential_env_ref": env_ref,
        "credential_present": cred_present,
        "credential_masked": f"env:{env_ref} ({'CONFIGURED' if cred_present else 'MISSING'})" if env_ref else "Không cần key",
        "endpoint_url": row["endpoint_url"],
        "endpoint_allowlist": json.loads(row["endpoint_allowlist_json"] or "[]"),
        "selected_model": row["selected_model"],
        "thinking_level": row["thinking_level"],
        "max_output_tokens": int(row["max_output_tokens"]),
        "pricing_version": row["pricing_version"],
        "pricing": pricing,
        "pricing_known": bool(pricing.get("input_per_1m") is not None and pricing.get("output_per_1m") is not None),
        "capabilities": manifest,
        "default_business_ids": json.loads(row["default_business_ids_json"] or "[]"),
        "default_project_ids": json.loads(row["default_project_ids_json"] or "[]"),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def compute_usage_cost_usd(usage: NormalizedUsage, pricing: dict[str, Any], billing_mode: str) -> float | None:
    if billing_mode in ("local_compute", "subscription_ide"):
        return 0.0 if billing_mode == "local_compute" else None
    if not pricing or pricing.get("input_per_1m") is None or pricing.get("output_per_1m") is None:
        return None
    if usage.input_tokens is None and usage.output_tokens is None:
        return None
    in_rate = float(pricing["input_per_1m"])
    out_rate = float(pricing["output_per_1m"])
    cache_rate = float(pricing.get("cache_read_per_1m", in_rate))
    uncached = usage.uncached_input_tokens if usage.uncached_input_tokens is not None else (usage.input_tokens or 0)
    cached = usage.cached_input_tokens or 0
    out_tok = usage.output_tokens or 0
    # Preserve inclusive/exclusive reasoning semantics (never double-count!)
    extra_reasoning = (usage.reasoning_tokens or 0) if not usage.reasoning_included_in_output else 0
    cost = (uncached * in_rate + cached * cache_rate + (out_tok + extra_reasoning) * out_rate) / 1_000_000.0
    return round(cost, 6)


class V321ProviderService:
    """Manages AI Provider Profiles (P15), Project Egress Policies, and Atomic Budget Controller (P18)."""

    def __init__(self, db: DatabaseManager, gateway: AIProviderGateway) -> None:
        self.db = db
        self.gateway = gateway
        self._seeded = False

    def ensure_seeded(self) -> None:
        if self._seeded:
            return
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            for p in DEFAULT_SEED_PROVIDERS:
                manifest = PROVIDER_CAPABILITY_MANIFESTS[p["provider_key"]]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO mcp321_provider_profiles (
                        id, provider_key, display_name, billing_mode, credential_env_ref,
                        endpoint_url, endpoint_allowlist_json, selected_model, thinking_level,
                        max_output_tokens, pricing_version, pricing_json, capabilities_json,
                        default_business_ids_json, default_project_ids_json, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, ?)
                    """,
                    (
                        p["id"], p["provider_key"], p["display_name"], p["billing_mode"],
                        p["credential_env_ref"], p["endpoint_url"],
                        json.dumps(manifest["default_allowlist"]), p["selected_model"], p["thinking_level"],
                        p["max_output_tokens"], p["pricing_version"], json.dumps(p["pricing"]),
                        json.dumps(manifest), p["enabled"], now, now,
                    ),
                )
        self._seeded = True

    def list_providers(self) -> dict[str, Any]:
        self.ensure_seeded()
        with self.db.read_conn() as conn:
            rows = conn.execute("SELECT * FROM mcp321_provider_profiles ORDER BY enabled DESC, provider_key ASC").fetchall()
            return {
                "items": [_mask_provider_row(r) for r in rows],
                "budget_disclosure": "Budget Controller là giới hạn ở cấp ứng dụng (application guard), không phải bảo đảm tuyệt đối hóa đơn nhà cung cấp.",
            }

    def get_provider(self, profile_id: str) -> dict[str, Any]:
        self.ensure_seeded()
        with self.db.read_conn() as conn:
            row = conn.execute("SELECT * FROM mcp321_provider_profiles WHERE id = ?", (profile_id,)).fetchone()
            if not row:
                raise BridgeError(404, "provider_not_found", f"Provider profile '{profile_id}' not found.")
            return _mask_provider_row(row)

    def create_provider(self, req: ProviderProfileUpsertRequest) -> dict[str, Any]:
        self.ensure_seeded()
        env_ref = _validate_credential_env_ref(req.credential_env_ref)
        manifest = PROVIDER_CAPABILITY_MANIFESTS[req.provider_key]
        allowlist = req.endpoint_allowlist or manifest["default_allowlist"]
        endpoint = validate_endpoint_allowlist(req.provider_key, req.endpoint_url, allowlist)
        pid = f"prov-{req.provider_key}-{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if conn.execute("SELECT 1 FROM mcp321_provider_profiles WHERE display_name = ?", (req.display_name,)).fetchone():
                raise BridgeError(409, "provider_exists", f"Provider '{req.display_name}' already exists.")
            conn.execute(
                """
                INSERT INTO mcp321_provider_profiles (
                    id, provider_key, display_name, billing_mode, credential_env_ref,
                    endpoint_url, endpoint_allowlist_json, selected_model, thinking_level,
                    max_output_tokens, pricing_version, pricing_json, capabilities_json,
                    default_business_ids_json, default_project_ids_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid, req.provider_key, req.display_name, req.billing_mode, env_ref,
                    endpoint, json.dumps(allowlist), req.selected_model, req.thinking_level,
                    req.max_output_tokens, req.pricing_version, json.dumps(req.pricing),
                    json.dumps(manifest), json.dumps(req.default_business_ids),
                    json.dumps(req.default_project_ids), 1 if req.enabled else 0, now, now,
                ),
            )
        return self.get_provider(pid)

    def update_provider(self, profile_id: str, req: ProviderProfilePatchRequest) -> dict[str, Any]:
        self.ensure_seeded()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT * FROM mcp321_provider_profiles WHERE id = ?", (profile_id,)).fetchone()
            if not row:
                raise BridgeError(404, "provider_not_found", f"Provider profile '{profile_id}' not found.")
            updates: dict[str, Any] = {}
            if req.credential_env_ref is not None:
                updates["credential_env_ref"] = _validate_credential_env_ref(req.credential_env_ref)
            if req.endpoint_url is not None:
                allowlist = json.loads(row["endpoint_allowlist_json"] or "[]")
                updates["endpoint_url"] = validate_endpoint_allowlist(row["provider_key"], req.endpoint_url, allowlist)
            if req.selected_model is not None:
                updates["selected_model"] = req.selected_model.strip()
            if req.thinking_level is not None:
                updates["thinking_level"] = req.thinking_level
            if req.max_output_tokens is not None:
                updates["max_output_tokens"] = req.max_output_tokens
            if req.pricing_version is not None:
                updates["pricing_version"] = req.pricing_version
            if req.pricing is not None:
                updates["pricing_json"] = json.dumps(req.pricing)
            if req.default_business_ids is not None:
                updates["default_business_ids_json"] = json.dumps(req.default_business_ids)
            if req.default_project_ids is not None:
                updates["default_project_ids_json"] = json.dumps(req.default_project_ids)
            if req.enabled is not None:
                updates["enabled"] = 1 if req.enabled else 0
            if updates:
                set_sql = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE mcp321_provider_profiles SET {set_sql}, updated_at = ? WHERE id = ?", [*updates.values(), now, profile_id])
        return self.get_provider(profile_id)

    def test_provider_connection(self, profile_id: str, req: ProviderConnectionTestRequest) -> dict[str, Any]:
        """By default performs ZERO paid network calls (config + env ref + allowlist check only)."""
        prof = self.get_provider(profile_id)
        try:
            validate_endpoint_allowlist(prof["provider_key"], prof["endpoint_url"], prof["endpoint_allowlist"])
            endpoint_ok = True
            endpoint_err = None
        except BridgeError as exc:
            endpoint_ok = False
            endpoint_err = exc.message

        if not req.allow_paid_probe or prof["provider_key"] == "mock":
            ready = endpoint_ok and prof["credential_present"] and bool(prof["selected_model"])
            return {
                "profile_id": profile_id,
                "provider_key": prof["provider_key"],
                "mode": "config_check_no_paid_call",
                "endpoint_valid": endpoint_ok,
                "endpoint_error": endpoint_err,
                "credential_present": prof["credential_present"],
                "selected_model": prof["selected_model"],
                "status": "ready_for_dispatch" if ready else "blocked_missing_config",
            }
        # Explicit user-approved minimal probe
        if not prof["credential_present"]:
            raise BridgeError(403, "missing_provider_credential", f"Missing env var {prof['credential_env_ref']}.")
        resp = self.gateway.invoke(
            ProviderRequest(
                provider_key=prof["provider_key"],
                model=prof["selected_model"],
                endpoint_url=prof["endpoint_url"],
                endpoint_allowlist=prof["endpoint_allowlist"],
                credential_env_ref=prof["credential_env_ref"],
                messages=[{"role": "user", "content": "Reply with OK"}],
                max_output_tokens=16,
                timeout_sec=15.0,
            )
        )
        return {
            "profile_id": profile_id,
            "provider_key": prof["provider_key"],
            "mode": "approved_minimal_live_probe",
            "status": "connected",
            "provider_reported_model": resp.provider_reported_model,
            "provider_request_id": resp.provider_request_id,
            "usage": resp.usage.__dict__,
        }

    # --- Project Egress Policy (fail-closed by default!) ---
    def get_project_egress_policy(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            row = conn.execute("SELECT * FROM mcp321_project_egress_policies WHERE project_id = ?", (project_id,)).fetchone()
            if not row:
                return {
                    "project_id": project_id,
                    "allow_external_paid_api": False,
                    "allowed_provider_keys": ["mock", "qwen_local"],
                    "allow_redacted_only": True,
                    "max_task_budget_usd": 0.0,
                    "max_daily_budget_usd": 0.0,
                    "default_fail_closed": True,
                }
            return {
                "project_id": project_id,
                "allow_external_paid_api": bool(row["allow_external_paid_api"]),
                "allowed_provider_keys": json.loads(row["allowed_provider_keys_json"] or "[]"),
                "allow_redacted_only": bool(row["allow_redacted_only"]),
                "max_task_budget_usd": float(row["max_task_budget_usd"]),
                "max_daily_budget_usd": float(row["max_daily_budget_usd"]),
                "updated_at": row["updated_at"],
                "default_fail_closed": False,
            }

    def set_project_egress_policy(self, project_id: str, req: ProjectEgressPolicyRequest) -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            conn.execute(
                """
                INSERT INTO mcp321_project_egress_policies (
                    project_id, allow_external_paid_api, allowed_provider_keys_json,
                    allow_redacted_only, max_task_budget_usd, max_daily_budget_usd, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    allow_external_paid_api = excluded.allow_external_paid_api,
                    allowed_provider_keys_json = excluded.allowed_provider_keys_json,
                    allow_redacted_only = excluded.allow_redacted_only,
                    max_task_budget_usd = excluded.max_task_budget_usd,
                    max_daily_budget_usd = excluded.max_daily_budget_usd,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id, 1 if req.allow_external_paid_api else 0,
                    json.dumps(req.allowed_provider_keys), 1 if req.allow_redacted_only else 0,
                    req.max_task_budget_usd, req.max_daily_budget_usd, now,
                ),
            )
        return self.get_project_egress_policy(project_id)

    # --- P18 Atomic Budget Reservation & Reconciliation ---
    def estimate_conservative_request_cost(self, prof: dict[str, Any], prompt_chars: int) -> float | None:
        if prof["billing_mode"] == "local_compute":
            return 0.0
        pricing = prof.get("pricing") or {}
        if pricing.get("input_per_1m") is None or pricing.get("output_per_1m") is None:
            return None
        est_input_tokens = max(64, int(prompt_chars / 3) + 128)
        est_output_tokens = int(prof.get("max_output_tokens") or 2048)
        cost = (est_input_tokens * float(pricing["input_per_1m"]) + est_output_tokens * float(pricing["output_per_1m"])) / 1_000_000.0
        return round(cost, 6)

    def atomic_reserve_request(
        self,
        *,
        run_id: str,
        project_id: str,
        session_id: str | None,
        prof: dict[str, Any],
        requested_model: str,
        thinking_level: str,
        prompt_chars: int,
        idempotency_key: str,
    ) -> tuple[str, float, bool]:
        """Atomically check egress + budget ceiling and reserve conservative request cost.
        Returns (provider_request_row_id, reserved_cost_usd, is_replay).
        """
        est_reserve = self.estimate_conservative_request_cost(prof, prompt_chars)
        if prof["billing_mode"] == "metered_api" and est_reserve is None:
            raise BridgeError(403, "unknown_pricing_blocked", "Cannot dispatch paid API request when model pricing is unknown.")

        reserve_val = float(est_reserve or 0.0)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            # Idempotency guard: never double-reserve or double-charge on retry!
            existing = conn.execute(
                "SELECT id, reserved_cost_usd, status FROM mcp321_provider_requests WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing:
                return existing["id"], float(existing["reserved_cost_usd"]), True

            run = conn.execute("SELECT * FROM mcp321_agent_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                raise BridgeError(404, "agent_run_not_found", "Agent run not found.")
            if run["status"] == "cancelled":
                raise BridgeError(409, "agent_run_cancelled", "Agent run was cancelled.")
            if int(run["calls_used"]) >= int(run["max_calls"]):
                conn.execute("UPDATE mcp321_agent_runs SET status = 'stopped_budget', stop_reason = 'max_calls_reached', updated_at = ? WHERE id = ?", (now, run_id))
                raise BridgeError(403, "max_calls_reached", "Maximum AI call limit reached for this run.")

            max_b = float(run["max_budget_usd"])
            used_total = float(run["spent_budget_usd"]) + float(run["reserved_budget_usd"]) + float(run["uncertain_budget_usd"])
            if prof["billing_mode"] in ("metered_api", "mock") and max_b > 0 and (used_total + reserve_val > max_b + 1e-9):
                conn.execute("UPDATE mcp321_agent_runs SET status = 'stopped_budget', stop_reason = 'budget_exceeded', updated_at = ? WHERE id = ?", (now, run_id))
                raise BridgeError(
                    403,
                    "budget_exceeded",
                    f"Budget exhausted: spent+reserved={used_total:.6f} + req={reserve_val:.6f} > max={max_b:.6f} USD.",
                )
            if prof["billing_mode"] == "metered_api" and max_b <= 0:
                raise BridgeError(403, "paid_budget_required", "Positive finite max_budget_usd is required for metered API calls.")

            req_row_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO mcp321_provider_requests (
                    id, run_id, project_id, session_id, idempotency_key, provider_key, requested_model,
                    thinking_level, status, reserved_cost_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (req_row_id, run_id, project_id, session_id, idempotency_key, prof["provider_key"], requested_model, thinking_level, reserve_val, now),
            )
            conn.execute(
                "UPDATE mcp321_agent_runs SET reserved_budget_usd = reserved_budget_usd + ?, calls_used = calls_used + 1, updated_at = ? WHERE id = ?",
                (reserve_val, now, run_id),
            )
            return req_row_id, reserve_val, False

    def reconcile_request(
        self,
        *,
        req_row_id: str,
        run_id: str,
        trace_id: str | None,
        prof: dict[str, Any],
        usage: NormalizedUsage | None,
        provider_reported_model: str | None = None,
        provider_request_id: str | None = None,
        finish_reason: str | None = None,
        error_code: str | None = None,
        uncertain: bool = False,
    ) -> float | None:
        now = utc_now_iso()
        est_cost = compute_usage_cost_usd(usage, prof.get("pricing") or {}, prof["billing_mode"]) if usage else None
        with self.db.write_tx() as conn:
            prow = conn.execute("SELECT * FROM mcp321_provider_requests WHERE id = ?", (req_row_id,)).fetchone()
            if not prow or prow["status"] != "reserved":
                return est_cost
            res_val = float(prow["reserved_cost_usd"])
            if uncertain:
                conn.execute(
                    "UPDATE mcp321_provider_requests SET status = 'uncertain', error_code = ?, completed_at = ? WHERE id = ?",
                    (error_code or "network_uncertain", now, req_row_id),
                )
                conn.execute(
                    "UPDATE mcp321_agent_runs SET reserved_budget_usd = MAX(0, reserved_budget_usd - ?), uncertain_budget_usd = uncertain_budget_usd + ?, updated_at = ? WHERE id = ?",
                    (res_val, res_val, now, run_id),
                )
                return None
            if error_code:
                conn.execute(
                    "UPDATE mcp321_provider_requests SET status = 'failed', error_code = ?, completed_at = ? WHERE id = ?",
                    (error_code, now, req_row_id),
                )
                conn.execute(
                    "UPDATE mcp321_agent_runs SET reserved_budget_usd = MAX(0, reserved_budget_usd - ?), updated_at = ? WHERE id = ?",
                    (res_val, now, run_id),
                )
                return None
            assert usage is not None
            actual_spend = float(est_cost or 0.0)
            conn.execute(
                """
                UPDATE mcp321_provider_requests
                SET status = 'completed', trace_id = ?, provider_reported_model = ?, provider_request_id = ?,
                    estimated_cost_usd = ?, input_tokens = ?, cached_input_tokens = ?, cache_write_tokens = ?,
                    uncached_input_tokens = ?, output_tokens = ?, reasoning_tokens = ?, visible_output_tokens = ?,
                    total_tokens = ?, reasoning_included_in_output = ?, measurement_source = ?,
                    measurement_quality = ?, finish_reason = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    trace_id, provider_reported_model, provider_request_id, est_cost,
                    usage.input_tokens, usage.cached_input_tokens, usage.cache_write_tokens,
                    usage.uncached_input_tokens, usage.output_tokens, usage.reasoning_tokens,
                    usage.visible_output_tokens, usage.total_tokens,
                    1 if usage.reasoning_included_in_output else 0,
                    usage.measurement_source, usage.measurement_quality, finish_reason, now, req_row_id,
                ),
            )
            conn.execute(
                "UPDATE mcp321_agent_runs SET reserved_budget_usd = MAX(0, reserved_budget_usd - ?), spent_budget_usd = spent_budget_usd + ?, updated_at = ? WHERE id = ?",
                (res_val, actual_spend, now, run_id),
            )
            return est_cost
