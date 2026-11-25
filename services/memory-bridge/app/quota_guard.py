"""Quota decisions use exact execution provider/auth scope, never brand aliases."""
from datetime import datetime, timezone

def _date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return dt if dt.tzinfo else None
    except (ValueError, TypeError):
        return None

def quota_status(rows, provider, model_id, auth_mode, now=None):
    now = now or datetime.now(timezone.utc)
    applicable = []
    for q in rows:
        if q["provider"] != provider or q["auth_mode"] != auth_mode:
            continue
        if q["quota_kind"] not in ("subscription_quota","api_budget","rate_limit") or q.get("confounded"):
            continue
        bucket = q.get("account_bucket")
        # Only explicitly model-scoped or unscoped observations are applicable.
        if bucket not in (None, "", "model:"+model_id):
            continue
        observed = _date(q.get("observed_at"))
        reset = _date(q.get("reset_at"))
        if not observed or observed > now or (reset and reset <= now):
            continue
        if q.get("reset_at") and not reset:
            continue
        if q.get("numerator") is None or q.get("denominator") is None or q["denominator"] <= 0:
            continue
        applicable.append(q)
    exhausted = next((q for q in applicable if q["numerator"] >= q["denominator"]),None)
    if exhausted:
        return {"state":"exhausted","source":exhausted["source"],"reset_at":exhausted.get("reset_at")}
    # Positive evidence may age quickly. Do not advertise unknown as available.
    fresh = next((q for q in applicable if (now-_date(q["observed_at"])).total_seconds() <= 900),None)
    if fresh:
        return {"state":"observed_available","source":fresh["source"],"reset_at":fresh.get("reset_at")}
    return {"state":"unknown","source":None,"reset_at":None}
