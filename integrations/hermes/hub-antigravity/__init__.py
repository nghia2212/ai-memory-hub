"""Hermes native plugin; no scheduler or direct DB access."""
import json
import os
import urllib.request
import urllib.error


def request_host(path, body=None):
    token = os.environ.get("HUB_AGY_TOKEN")
    if not token:
        return {"ok": False, "error_code": "missing_host_token"}
    url = os.environ.get("HUB_HOST_URL", "http://host.docker.internal:8084")
    try:
        req = urllib.request.Request(url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=195 if body is not None else 20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error_code": "host_http_" + str(exc.code)}
    except Exception:
        return {"ok": False, "error_code": "outcome_uncertain" if body is not None else "host_unreachable"}


def status(args, **kwargs):
    return json.dumps(request_host("/host/hermes/agy/status"), ensure_ascii=False)


def analyze(args, **kwargs):
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        return json.dumps({"ok": False, "error_code": "invalid_prompt"})
    return json.dumps(request_host("/host/hermes/agy/analyze", {"prompt": prompt}), ensure_ascii=False)


def register(ctx):
    ctx.register_tool(name="hub_antigravity_status", toolset="hub-antigravity",
        schema={"name": "hub_antigravity_status", "description": "Check Windows Antigravity CLI installation and opt-in configuration, no model call.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}, handler=status)
    ctx.register_tool(name="hub_antigravity_analyze", toolset="hub-antigravity",
        schema={"name": "hub_antigravity_analyze", "description": "Ask the explicitly enabled Windows Antigravity CLI to analyze an approved disposable worktree in plan mode only. Use after user authorization. Do not retry uncertain results; return to Hermes for review.", "parameters": {"type": "object", "properties": {"prompt": {"type": "string", "maxLength": 4000}}, "required": ["prompt"], "additionalProperties": False}}, handler=analyze)
