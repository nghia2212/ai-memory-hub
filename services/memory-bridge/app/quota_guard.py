"""Quota decisions use exact execution provider/auth scope, never brand aliases.

P19A.4 enhancements:
- Supports shared multi-window subscription quotas (e.g. Codex 5h + 7d windows via `window:*` buckets)
  alongside model-scoped (`model:<id>`) and unscoped buckets.
- Preserves manual confirmation precedence (`manual_user_confirmation`) and never declares recovered
  merely because `reset_at` passed without a newer observation.
- Computes P19A.4 advisory tiers (>=50% normal, 20-49% caution, 5-19% limit_large_tasks, <5% suggest_alternatives, 0% blocked)
  only when fresh numeric percentages exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _date(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def _advisory_for_remaining(rem_pct: float | None) -> str | None:
    if rem_pct is None:
        return None
    if rem_pct <= 0.0:
        return "blocked"
    if rem_pct < 5.0:
        return "suggest_alternatives"
    if rem_pct < 20.0:
        return "limit_large_tasks"
    if rem_pct < 50.0:
        return "caution"
    return "normal"


def quota_status(rows: list[dict[str, Any]], provider: str, model_id: str, auth_mode: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    applicable: list[dict[str, Any]] = []
    for q in rows:
        if q["provider"] != provider or q["auth_mode"] != auth_mode:
            continue
        if q["quota_kind"] not in ("subscription_quota", "api_budget", "rate_limit") or q.get("confounded"):
            continue
        bucket = q.get("account_bucket")
        is_window_bucket = isinstance(bucket, str) and bucket.startswith("window:")
        if bucket not in (None, "", "model:" + model_id) and not is_window_bucket:
            continue
        observed = _date(q.get("observed_at"))
        reset = _date(q.get("reset_at"))
        # Never declare recovered merely because reset_at passed; expired reset windows become unknown/stale
        if not observed or observed > now or (reset and reset <= now):
            continue
        if q.get("reset_at") and not reset:
            continue
        if q.get("numerator") is None or q.get("denominator") is None or q["denominator"] <= 0:
            continue
        applicable.append(q)

    # Sort newest first so manual override or latest observation takes clear precedence
    applicable.sort(key=lambda x: _date(x.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Build per-window observed summaries (only for percentage-based snapshots)
    windows: list[dict[str, Any]] = []
    seen_win: set[str] = set()
    min_rem_pct: float | None = None
    for q in applicable:
        if q.get("unit") == "percent_used" or (q.get("window_label") and q["denominator"] == 100):
            wlabel = str(q.get("window_label") or q.get("account_bucket") or "default")
            if wlabel in seen_win:
                continue
            seen_win.add(wlabel)
            used_pct = round((float(q["numerator"]) / float(q["denominator"])) * 100.0, 1)
            rem_pct = round(max(0.0, 100.0 - used_pct), 1)
            if min_rem_pct is None or rem_pct < min_rem_pct:
                min_rem_pct = rem_pct
            windows.append(
                {
                    "window_label": wlabel,
                    "used_pct": used_pct,
                    "remaining_pct": rem_pct,
                    "reset_at": q.get("reset_at"),
                    "source": q.get("source"),
                    "observed_at": q.get("observed_at"),
                    "advisory": _advisory_for_remaining(rem_pct),
                }
            )

    # Manual model-scoped confirmation takes precedence over older provider observations
    manual_model = next((q for q in applicable if q.get("account_bucket") == "model:" + model_id and q.get("source") == "manual_user_confirmation"), None)
    if manual_model:
        if manual_model["numerator"] >= manual_model["denominator"]:
            return {
                "state": "exhausted",
                "source": manual_model["source"],
                "reset_at": manual_model.get("reset_at"),
                "advisory": "blocked",
                "remaining_pct": 0.0,
                "windows": windows,
            }

    exhausted = next((q for q in applicable if q["numerator"] >= q["denominator"]), None)
    if exhausted:
        return {
            "state": "exhausted",
            "source": exhausted["source"],
            "reset_at": exhausted.get("reset_at"),
            "advisory": "blocked",
            "remaining_pct": 0.0,
            "windows": windows,
        }

    # Positive evidence ages (15 min for availability flags, 60 min for official rate_limits windows)
    fresh = next(
        (
            q
            for q in applicable
            if (now - (_date(q.get("observed_at")) or now)).total_seconds()
            <= (3600 if q.get("unit") == "percent_used" else 900)
        ),
        None,
    )
    if fresh:
        return {
            "state": "observed_available",
            "source": fresh["source"],
            "reset_at": fresh.get("reset_at"),
            "advisory": _advisory_for_remaining(min_rem_pct),
            "remaining_pct": min_rem_pct,
            "windows": windows,
        }
    return {
        "state": "unknown",
        "source": None,
        "reset_at": None,
        "advisory": None,
        "remaining_pct": None,
        "windows": windows,
    }
