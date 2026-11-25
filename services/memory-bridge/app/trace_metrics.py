"""Deterministic metrics over a bounded, filtered trace selection."""
from datetime import datetime

COUNTERS = ("input_tokens","output_tokens","cache_read_tokens","cache_write_tokens","reasoning_tokens","cost_usd")

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
