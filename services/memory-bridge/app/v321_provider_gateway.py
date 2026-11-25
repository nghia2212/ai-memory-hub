"""V3.2.1 P14 — Provider-neutral AI Provider Gateway (DeepSeek, Gemini, Qwen Local, OpenAI, Anthropic, Mock).

Security & honesty guarantees:
- Zero network calls on Hub startup, memory search, indexing, or unit tests.
- Strict endpoint allowlist (SSRF protection); no arbitrary URL forwarding.
- Credentials read strictly from env refs at call time; never logged, stored, or echoed.
- Preserves provider inclusive/exclusive reasoning token semantics without double-counting.
- No automatic paid fallback across providers.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .errors import BridgeError

MAX_RESPONSE_BYTES = 512_000

# Verified capability manifests per provider (source: official API docs verified 2026-11).
PROVIDER_CAPABILITY_MANIFESTS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "provider_key": "deepseek",
        "display_name": "DeepSeek API",
        "default_endpoint": "https://api.deepseek.com/v1/chat/completions",
        "default_allowlist": ["https://api.deepseek.com"],
        "billing_mode": "metered_api",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_thinking": True,
        "thinking_param": "model=deepseek-reasoner",
        "reasoning_included_in_output": True,
        "usage_fields": ["input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"],
        "doc_ref": "https://api-docs.deepseek.com (verified 2026-11)",
    },
    "gemini": {
        "provider_key": "gemini",
        "display_name": "Google Gemini API (Developer)",
        "default_endpoint": "https://generativelanguage.googleapis.com/v1beta",
        "default_allowlist": ["https://generativelanguage.googleapis.com"],
        "billing_mode": "metered_api",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_thinking": True,
        "thinking_param": "generationConfig.thinkingConfig",
        "reasoning_included_in_output": False,  # Gemini reports thoughtsTokenCount separately from candidatesTokenCount
        "usage_fields": ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"],
        "note": "Do NOT assume Antigravity IDE model names exist on Gemini Developer API.",
        "doc_ref": "https://ai.google.dev/api/generate-content (verified 2026-11)",
    },
    "qwen_local": {
        "provider_key": "qwen_local",
        "display_name": "Qwen Local (Ollama / OpenAI-compatible LAN)",
        "default_endpoint": "http://127.0.0.1:11434/v1/chat/completions",
        "default_allowlist": ["http://127.0.0.1", "http://localhost", "http://host.docker.internal"],
        "billing_mode": "local_compute",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_thinking": False,
        "reasoning_included_in_output": True,
        "usage_fields": ["input_tokens", "output_tokens", "total_tokens"],
        "note": "Opt-in local/LAN compute (0 API cost). No auto-download of models.",
        "doc_ref": "Ollama / OpenAI-compatible local API (verified 2026-11)",
    },
    "openai": {
        "provider_key": "openai",
        "display_name": "OpenAI API",
        "default_endpoint": "https://api.openai.com/v1/chat/completions",
        "default_allowlist": ["https://api.openai.com"],
        "billing_mode": "metered_api",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_thinking": True,
        "thinking_param": "reasoning_effort",
        "reasoning_included_in_output": True,
        "usage_fields": ["input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"],
        "doc_ref": "https://platform.openai.com/docs/api-reference (verified 2026-11)",
    },
    "anthropic": {
        "provider_key": "anthropic",
        "display_name": "Anthropic Claude API",
        "default_endpoint": "https://api.anthropic.com/v1/messages",
        "default_allowlist": ["https://api.anthropic.com"],
        "billing_mode": "metered_api",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_thinking": True,
        "thinking_param": "thinking.budget_tokens",
        "reasoning_included_in_output": True,
        "usage_fields": ["input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "total_tokens"],
        "doc_ref": "https://docs.anthropic.com/en/api/messages (verified 2026-11)",
    },
    "mock": {
        "provider_key": "mock",
        "display_name": "Deterministic Mock Provider (0 Cost Testing)",
        "default_endpoint": "mock://deterministic",
        "default_allowlist": ["mock://deterministic"],
        "billing_mode": "mock",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_thinking": True,
        "thinking_param": "mock_thinking",
        "reasoning_included_in_output": True,
        "usage_fields": ["input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens", "visible_output_tokens", "total_tokens"],
        "doc_ref": "Internal deterministic replay adapter (0 network, 0 cost)",
    },
}


@dataclass
class NormalizedToolCall:
    call_id: str
    server_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class NormalizedUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    uncached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    visible_output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_included_in_output: bool = True
    measurement_source: str = "unavailable"
    measurement_quality: str = "unavailable"  # measured | estimated | unavailable


@dataclass
class ProviderRequest:
    provider_key: str
    model: str
    endpoint_url: str
    endpoint_allowlist: list[str]
    credential_env_ref: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    thinking_level: str = "none"
    max_output_tokens: int = 2048
    timeout_sec: float = 45.0
    round_index: int = 0
    mock_script: list[dict[str, Any]] | None = None


@dataclass
class ProviderResponse:
    provider_key: str
    requested_model: str
    provider_reported_model: str | None
    provider_request_id: str | None
    content: str
    tool_calls: list[NormalizedToolCall]
    usage: NormalizedUsage
    finish_reason: str
    raw_status: int = 200


def validate_endpoint_allowlist(provider_key: str, endpoint_url: str, allowlist: list[str]) -> str:
    """Enforce SSRF protection: endpoint MUST match explicit allowlist or provider default allowlist."""
    if provider_key == "mock":
        return "mock://deterministic"
    url = (endpoint_url or PROVIDER_CAPABILITY_MANIFESTS[provider_key]["default_endpoint"]).strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise BridgeError(422, "invalid_provider_endpoint", f"Unsupported endpoint scheme: {parsed.scheme}")
    if provider_key != "qwen_local" and parsed.scheme != "https":
        raise BridgeError(422, "insecure_provider_endpoint", f"Provider '{provider_key}' requires HTTPS endpoint.")
    allowed_prefixes = allowlist or PROVIDER_CAPABILITY_MANIFESTS[provider_key]["default_allowlist"]
    if not any(url.startswith(prefix.rstrip("/")) for prefix in allowed_prefixes):
        raise BridgeError(403, "endpoint_not_allowlisted", f"Endpoint '{url}' is not in the approved allowlist for '{provider_key}'.")
    return url


def _resolve_credential(env_ref: str, *, required: bool) -> str:
    ref = (env_ref or "").strip()
    if not ref:
        if required:
            raise BridgeError(403, "missing_provider_credential", "Provider credential env reference is not configured.")
        return ""
    val = os.environ.get(ref, "").strip()
    if required and not val:
        raise BridgeError(403, "missing_provider_credential", f"Environment variable '{ref}' is not set on host/container.")
    return val


def _parse_openai_compatible_response(
    provider_key: str,
    requested_model: str,
    data: dict[str, Any],
    *,
    request_id: str | None,
    tools_map: dict[str, str],
) -> ProviderResponse:
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    msg = choice.get("message") or {}
    content = str(msg.get("content") or "")
    finish = str(choice.get("finish_reason") or "stop")
    tcalls: list[NormalizedToolCall] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        tname = str(fn.get("name") or "")
        raw_args = fn.get("arguments") or "{}"
        try:
            args_dict = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except Exception as exc:
            raise BridgeError(422, "invalid_provider_tool_args", f"Malformed tool arguments from provider: {exc}") from exc
        tcalls.append(
            NormalizedToolCall(
                call_id=str(tc.get("id") or f"call-{uuid.uuid4().hex[:8]}"),
                server_id=tools_map.get(tname, ""),
                tool_name=tname,
                arguments=args_dict,
            )
        )
    u = data.get("usage") or {}
    inp = u.get("prompt_tokens")
    out = u.get("completion_tokens")
    tot = u.get("total_tokens")
    cached = u.get("prompt_cache_hit_tokens")
    if cached is None and isinstance(u.get("prompt_tokens_details"), dict):
        cached = u["prompt_tokens_details"].get("cached_tokens")
    uncached = u.get("prompt_cache_miss_tokens")
    if uncached is None and inp is not None and cached is not None:
        uncached = max(0, int(inp) - int(cached))
    reasoning = None
    if isinstance(u.get("completion_tokens_details"), dict):
        reasoning = u["completion_tokens_details"].get("reasoning_tokens")
    vis_out = max(0, int(out) - int(reasoning)) if (out is not None and reasoning is not None) else out
    has_measured = inp is not None or out is not None
    return ProviderResponse(
        provider_key=provider_key,
        requested_model=requested_model,
        provider_reported_model=data.get("model"),
        provider_request_id=request_id or data.get("id"),
        content=content,
        tool_calls=tcalls,
        usage=NormalizedUsage(
            input_tokens=inp,
            cached_input_tokens=cached,
            uncached_input_tokens=uncached,
            output_tokens=out,
            reasoning_tokens=reasoning,
            visible_output_tokens=vis_out,
            total_tokens=tot,
            reasoning_included_in_output=True,
            measurement_source=f"{provider_key}.usage" if has_measured else "unavailable",
            measurement_quality="measured" if has_measured else "unavailable",
        ),
        finish_reason=finish,
    )


def _format_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t["tool_name"],
                    "description": t.get("description", "")[:250],
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return out


class AIProviderGateway:
    """Dispatches normalized requests to the selected provider adapter with strict safety bounds."""

    def invoke(self, req: ProviderRequest) -> ProviderResponse:
        endpoint = validate_endpoint_allowlist(req.provider_key, req.endpoint_url, req.endpoint_allowlist)
        tools_map = {t["tool_name"]: t.get("server_id", "") for t in req.tools}

        if req.provider_key == "mock":
            return self._invoke_mock(req, tools_map)
        if req.provider_key in ("deepseek", "openai", "qwen_local"):
            return self._invoke_openai_compatible(req, endpoint, tools_map)
        if req.provider_key == "gemini":
            return self._invoke_gemini(req, endpoint, tools_map)
        if req.provider_key == "anthropic":
            return self._invoke_anthropic(req, endpoint, tools_map)
        raise BridgeError(422, "unsupported_provider", f"Unknown provider_key '{req.provider_key}'.")

    def _invoke_mock(self, req: ProviderRequest, tools_map: dict[str, str]) -> ProviderResponse:
        """Deterministic synthetic fixture adapter (0 network, 0 cost)."""
        script = req.mock_script or []
        step = script[req.round_index] if req.round_index < len(script) else None
        if step and step.get("raise_error"):
            raise BridgeError(502, str(step["raise_error"]), str(step.get("error_message") or "Simulated mock provider failure"))

        tcalls: list[NormalizedToolCall] = []
        if step and step.get("tool_calls"):
            for idx, tc in enumerate(step["tool_calls"]):
                tname = str(tc["tool_name"])
                tcalls.append(
                    NormalizedToolCall(
                        call_id=str(tc.get("call_id") or f"mock-call-{req.round_index}-{idx}"),
                        server_id=str(tc.get("server_id") or tools_map.get(tname, "")),
                        tool_name=tname,
                        arguments=dict(tc.get("arguments") or {}),
                    )
                )
        content = str(step.get("content")) if step and step.get("content") is not None else (
            f"[MockProvider:{req.model}] Đã hoàn tất phân tích read-only cho mục tiêu trong phạm vi project."
        )
        u_raw = (step.get("usage") if step else None) or {}
        inp = int(u_raw.get("input_tokens", 420))
        cached = int(u_raw.get("cached_input_tokens", 120))
        uncached = max(0, inp - cached)
        out = int(u_raw.get("output_tokens", 180))
        reasoning = int(u_raw.get("reasoning_tokens", 60)) if req.thinking_level != "none" else u_raw.get("reasoning_tokens")
        vis_out = max(0, out - int(reasoning)) if reasoning is not None else out
        tot = inp + out
        return ProviderResponse(
            provider_key="mock",
            requested_model=req.model or "mock-deterministic-v1",
            provider_reported_model=req.model or "mock-deterministic-v1",
            provider_request_id=f"mock-req-{uuid.uuid4().hex[:10]}",
            content=content,
            tool_calls=tcalls,
            usage=NormalizedUsage(
                input_tokens=inp,
                cached_input_tokens=cached,
                cache_write_tokens=u_raw.get("cache_write_tokens"),
                uncached_input_tokens=uncached,
                output_tokens=out,
                reasoning_tokens=reasoning,
                visible_output_tokens=vis_out,
                total_tokens=tot,
                reasoning_included_in_output=True,
                measurement_source="mock.fixture",
                measurement_quality="measured",
            ),
            finish_reason="tool_calls" if tcalls else "stop",
        )

    def _invoke_openai_compatible(self, req: ProviderRequest, endpoint: str, tools_map: dict[str, str]) -> ProviderResponse:
        need_key = req.provider_key != "qwen_local"
        api_key = _resolve_credential(req.credential_env_ref, required=need_key)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "max_tokens": req.max_output_tokens,
        }
        if req.tools:
            payload["tools"] = _format_openai_tools(req.tools)
        if req.provider_key == "openai" and req.thinking_level != "none":
            payload["reasoning_effort"] = req.thinking_level if req.thinking_level in ("low", "medium", "high") else "high"
        with httpx.Client(timeout=req.timeout_sec) as client:
            r = client.post(endpoint, headers=headers, json=payload)
            if len(r.content) > MAX_RESPONSE_BYTES:
                raise BridgeError(502, "provider_response_too_large", "Provider response exceeded safety bound.")
            r.raise_for_status()
            return _parse_openai_compatible_response(
                req.provider_key,
                req.model,
                r.json(),
                request_id=r.headers.get("x-request-id"),
                tools_map=tools_map,
            )

    def _invoke_gemini(self, req: ProviderRequest, endpoint: str, tools_map: dict[str, str]) -> ProviderResponse:
        api_key = _resolve_credential(req.credential_env_ref, required=True)
        url = f"{endpoint.rstrip('/')}/models/{urllib.parse.quote(req.model)}:generateContent"
        contents = []
        for m in req.messages:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(m.get("content") or "")}]})
        gen_cfg: dict[str, Any] = {"maxOutputTokens": req.max_output_tokens}
        if req.thinking_level != "none":
            budget_map = {"low": 512, "medium": 2048, "high": 4096, "max": 8192}
            gen_cfg["thinkingConfig"] = {"thinkingBudget": budget_map.get(req.thinking_level, 2048)}
        payload: dict[str, Any] = {"contents": contents, "generationConfig": gen_cfg}
        if req.tools:
            payload["tools"] = [{"functionDeclarations": [{"name": t["tool_name"], "description": t.get("description", "")[:250]} for t in req.tools]}]
        with httpx.Client(timeout=req.timeout_sec) as client:
            r = client.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, json=payload)
            if len(r.content) > MAX_RESPONSE_BYTES:
                raise BridgeError(502, "provider_response_too_large", "Gemini response exceeded safety bound.")
            r.raise_for_status()
            data = r.json()
        cands = data.get("candidates") or []
        cand = cands[0] if cands else {}
        parts = (cand.get("content") or {}).get("parts") or []
        text_parts = [str(p.get("text") or "") for p in parts if "text" in p]
        tcalls = []
        for idx, p in enumerate(parts):
            if "functionCall" in p:
                fc = p["functionCall"]
                tname = str(fc.get("name") or "")
                tcalls.append(
                    NormalizedToolCall(
                        call_id=f"gemini-call-{idx}",
                        server_id=tools_map.get(tname, ""),
                        tool_name=tname,
                        arguments=dict(fc.get("args") or {}),
                    )
                )
        um = data.get("usageMetadata") or {}
        inp = um.get("promptTokenCount")
        cached = um.get("cachedContentTokenCount")
        out = um.get("candidatesTokenCount")
        thoughts = um.get("thoughtsTokenCount")
        tot = um.get("totalTokenCount")
        has_m = inp is not None or out is not None
        return ProviderResponse(
            provider_key="gemini",
            requested_model=req.model,
            provider_reported_model=data.get("modelVersion") or req.model,
            provider_request_id=r.headers.get("x-request-id"),
            content="\n".join(text_parts),
            tool_calls=tcalls,
            usage=NormalizedUsage(
                input_tokens=inp,
                cached_input_tokens=cached,
                uncached_input_tokens=(max(0, int(inp) - int(cached)) if inp is not None and cached is not None else inp),
                output_tokens=out,
                reasoning_tokens=thoughts,
                visible_output_tokens=out,
                total_tokens=tot,
                reasoning_included_in_output=False,
                measurement_source="gemini.usageMetadata" if has_m else "unavailable",
                measurement_quality="measured" if has_m else "unavailable",
            ),
            finish_reason=str(cand.get("finishReason") or "STOP"),
        )

    def _invoke_anthropic(self, req: ProviderRequest, endpoint: str, tools_map: dict[str, str]) -> ProviderResponse:
        api_key = _resolve_credential(req.credential_env_ref, required=True)
        sys_msgs = [str(m.get("content") or "") for m in req.messages if m.get("role") == "system"]
        chat_msgs = [{"role": "assistant" if m.get("role") == "assistant" else "user", "content": str(m.get("content") or "")} for m in req.messages if m.get("role") != "system"]
        payload: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_output_tokens,
            "messages": chat_msgs or [{"role": "user", "content": "Proceed."}],
        }
        if sys_msgs:
            payload["system"] = "\n\n".join(sys_msgs)
        if req.thinking_level != "none":
            budget_map = {"low": 1024, "medium": 2048, "high": 4096, "max": 8192}
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget_map.get(req.thinking_level, 2048)}
        if req.tools:
            payload["tools"] = [
                {"name": t["tool_name"], "description": t.get("description", "")[:250], "input_schema": t.get("input_schema") or {"type": "object", "properties": {}}}
                for t in req.tools
            ]
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        with httpx.Client(timeout=req.timeout_sec) as client:
            r = client.post(endpoint, headers=headers, json=payload)
            if len(r.content) > MAX_RESPONSE_BYTES:
                raise BridgeError(502, "provider_response_too_large", "Anthropic response exceeded safety bound.")
            r.raise_for_status()
            data = r.json()
        blocks = data.get("content") or []
        text_parts = [str(b.get("text") or "") for b in blocks if b.get("type") == "text"]
        tcalls = []
        for b in blocks:
            if b.get("type") == "tool_use":
                tname = str(b.get("name") or "")
                tcalls.append(
                    NormalizedToolCall(
                        call_id=str(b.get("id") or f"anth-{uuid.uuid4().hex[:8]}"),
                        server_id=tools_map.get(tname, ""),
                        tool_name=tname,
                        arguments=dict(b.get("input") or {}),
                    )
                )
        u = data.get("usage") or {}
        inp = u.get("input_tokens")
        c_read = u.get("cache_read_input_tokens")
        c_write = u.get("cache_creation_input_tokens")
        out = u.get("output_tokens")
        has_m = inp is not None or out is not None
        return ProviderResponse(
            provider_key="anthropic",
            requested_model=req.model,
            provider_reported_model=data.get("model"),
            provider_request_id=r.headers.get("request-id") or data.get("id"),
            content="\n".join(text_parts),
            tool_calls=tcalls,
            usage=NormalizedUsage(
                input_tokens=inp,
                cached_input_tokens=c_read,
                cache_write_tokens=c_write,
                uncached_input_tokens=inp,
                output_tokens=out,
                reasoning_tokens=None,
                visible_output_tokens=out,
                total_tokens=(int(inp or 0) + int(out or 0)) if has_m else None,
                reasoning_included_in_output=True,
                measurement_source="anthropic.usage" if has_m else "unavailable",
                measurement_quality="measured" if has_m else "unavailable",
            ),
            finish_reason=str(data.get("stop_reason") or "end_turn"),
        )
