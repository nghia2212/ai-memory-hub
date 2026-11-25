"""Deterministic metrics over a bounded, filtered trace selection."""
from datetime import datetime

COUNTERS = ("input_tokens","output_tokens","cache_read_tokens","cache_write_tokens","reasoning_tokens","cost_usd")

# P11 versioned pricing table (USD per 1M tokens) — nullable when model unknown or subscription/local.
PRICING_TABLE_VERSION = "v321-2026-11"
_MODEL_PRICING_PER_1M = {
    "deepseek-chat": {"input": 0.27, "output": 1.10, "cache_read": 0.07},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_read": 0.14},
    "gpt-5": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    "claude-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.30},
    "claude-opus": {"input": 15.0, "output": 75.0, "cache_read": 1.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cache_read": 0.31},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cache_read": 0.0375},
}


def _match_pricing(model_id):
    if not model_id:
        return None
    m = str(model_id).lower()
    if any(k in m for k in ("qwen", "ollama", "local", "deterministic")):
        return None
    for prefix, rates in _MODEL_PRICING_PER_1M.items():
        if prefix in m:
            return rates
    return None


def summarize(rows):
    rows = list(rows)
    result = {"steps":len(rows)}
    for key in ("used_mcp","used_memory","used_llm"):
        result[key] = sum(r.get(key) is True or r.get(key)==1 for r in rows)
        result[key+"_unknown"] = sum(r.get(key) is None for r in rows)
    result["errors"] = sum(r["status"] in ("failed","blocked") for r in rows)
    result["retries"] = sum(bool(r.get("metadata_json",{}).get("retry_of")) for r in rows)
    result["llm_calls"] = sum(r["step_type"]=="llm_call" and r.get("used_llm") is True for r in rows)
    # Canonical session aggregates take precedence over linked per-call records.
    sessions = {r["session_id"] for r in rows if r["step_type"]=="session_usage" and r.get("session_id")}
    usage = [r for r in rows if r["step_type"]=="session_usage" or
             (r["step_type"]=="llm_call" and not r.get("session_id"))]
    # Linked model-call counters never enter the aggregate; their session
    # report may be outside the filter and coverage then remains incomplete.
    result["usage_records"] = len(usage)
    result["excluded_linked_calls"] = sum(r["step_type"]=="llm_call" and bool(r.get("session_id")) for r in rows)
    result["usage_coverage"] = {}
    for k in COUNTERS:
        values = [r[k] for r in usage if r.get(k) is not None and r.get("metadata_json",{}).get("measurement_kind")=="measured"]
        result[k] = round(sum(values),8) if values else None
        result["usage_coverage"][k] = len(values)

    # P11: Known token subtotal (NEVER double-count reasoning_tokens or MCP/Memory/Skill subsets!)
    known_parts = [result[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens") if result.get(k) is not None]
    result["known_token_subtotal"] = sum(known_parts) if known_parts else None
    result["pricing_table_version"] = PRICING_TABLE_VERSION

    # P11: Estimated cost (nullable; never fabricate 0 when unknown or subscription)
    if result.get("cost_usd") is not None:
        result["estimated_cost_usd"] = result["cost_usd"]
        result["cost_kind"] = "measured"
    else:
        est_total = 0.0
        est_count = 0
        for r in usage:
            if r.get("metadata_json", {}).get("measurement_kind") != "measured":
                continue
            rates = _match_pricing(r.get("model_id"))
            if not rates:
                continue
            inp = r.get("input_tokens")
            out = r.get("output_tokens")
            if inp is None and out is None:
                continue
            cr = r.get("cache_read_tokens") or 0
            est_total += ((inp or 0) * rates["input"] + (out or 0) * rates["output"] + cr * rates["cache_read"]) / 1_000_000.0
            est_count += 1
        result["estimated_cost_usd"] = round(est_total, 6) if est_count > 0 else None
        result["cost_kind"] = "estimated" if est_count > 0 else "unavailable"

    intervals = []
    for r in rows:
        if r.get("ended_at") and r.get("started_at"):
            try:
                a,b = datetime.fromisoformat(r["started_at"]),datetime.fromisoformat(r["ended_at"])
                if a.tzinfo and b.tzinfo and b>=a:
                    intervals.append((a,b))
            except (ValueError,TypeError):
                pass
    intervals.sort()
    merged=[]
    for a,b in intervals:
        if merged and a<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    result["observed_union_ms"] = round(sum((b-a).total_seconds()*1000 for a,b in merged)) if merged else None
    result["wall_span_ms"] = round((max(b for a,b in intervals)-min(a for a,b in intervals)).total_seconds()*1000) if intervals else None
    durations = [r for r in rows if r.get("duration_ms") is not None]
    result["summed_step_ms"] = sum(r["duration_ms"] for r in durations) if durations else None
    result["slowest_trace_id"] = max(durations,key=lambda r:r["duration_ms"])["trace_id"] if durations else None
    result["duration_unknown"] = len(rows)-len(durations)
    return result
