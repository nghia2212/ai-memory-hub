"""V3.5 — Deterministic Budgeted Context Pack builder & honest token benchmark comparator (0 LLM)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextPackResult:
    text: str
    used_chars: int
    budget_chars: int
    truncated: bool
    deduplicated_count: int
    sections: dict[str, int] = field(default_factory=dict)
    provenance: list[dict[str, Any]] = field(default_factory=list)


def _norm_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode("utf-8")).hexdigest()[:16]


def build_context_pack(
    *,
    objective: str,
    verified_memories: list[dict[str, Any]] | None = None,
    handoffs: list[dict[str, Any]] | None = None,
    code_symbols: list[dict[str, Any]] | None = None,
    approved_conversation_excerpts: list[dict[str, Any]] | None = None,
    max_chars: int = 8000,
) -> ContextPackResult:
    """Assemble a strictly bounded, deduplicated context pack using only verified/approved sources."""
    budget = max(500, int(max_chars))
    seen: set[str] = set()
    deduped = 0
    truncated = False
    parts: list[str] = [f"# Objective\n{objective.strip()[:2000]}"]
    used = len(parts[0])
    sections = {"memories": 0, "handoffs": 0, "code_symbols": 0, "conversations": 0}
    provenance: list[dict[str, Any]] = []

    def _try_append(section_key: str, line: str, prov: dict[str, Any], dedup_text: str | None = None) -> bool:
        nonlocal used, deduped, truncated
        h = _norm_hash(dedup_text or line)
        if h in seen:
            deduped += 1
            return False
        if used + len(line) + 2 > budget:
            truncated = True
            return False
        seen.add(h)
        parts.append(line)
        used += len(line) + 1
        sections[section_key] += 1
        provenance.append(prov)
        return True

    # 1. Verified semantic memories only (never auto-include superseded or unverified)
    for m in verified_memories or []:
        if m.get("state") and m.get("state") != "verified":
            continue
        title = str(m.get("title") or "")[:140]
        excerpt = str(m.get("excerpt") or m.get("body") or "")[:400]
        line = f"[Memory:{str(m.get('id') or '')[:8]}] {title}: {excerpt}"
        _try_append("memories", line, {"kind": "memory", "id": m.get("id"), "source_commit": m.get("source_commit")}, dedup_text=f"{title}:{excerpt}")

    # 2. Recent task handoffs
    for h in handoffs or []:
        line = f"[Handoff:{h.get('task_id')}] {str(h.get('summary') or '')[:450]}"
        _try_append("handoffs", line, {"kind": "handoff", "id": h.get("handoff_id") or h.get("id"), "task_id": h.get("task_id")})

    # 3. Codebase symbols (symbol + exact line range, never full repo dumps)
    for c in code_symbols or []:
        line = f"[Code] {c.get('path')}:{c.get('line_start')}-{c.get('line_end')} {c.get('title')}"
        _try_append("code_symbols", line, {"kind": "code", "path": c.get("path"), "line_start": c.get("line_start")})

    # 4. Bounded user-approved conversation excerpts
    for ex in approved_conversation_excerpts or []:
        line = f"[Conv:{str(ex.get('conversation_id') or '')[:8]}#{ex.get('seq_no')}] ({ex.get('role')}): {str(ex.get('excerpt') or '')[:350]}"
        _try_append("conversations", line, {"kind": "conversation", "conversation_id": ex.get("conversation_id"), "seq_no": ex.get("seq_no")})

    final_text = "\n".join(parts)
    return ContextPackResult(
        text=final_text,
        used_chars=len(final_text),
        budget_chars=budget,
        truncated=truncated,
        deduplicated_count=deduped,
        sections=sections,
        provenance=provenance,
    )


def compare_token_benchmark(
    baseline_usage: dict[str, Any],
    hub_usage: dict[str, Any],
) -> dict[str, Any]:
    """Compare measured token usage before/after Hub without ever fabricating savings."""
    b_mk = baseline_usage.get("measurement_kind")
    h_mk = hub_usage.get("measurement_kind")
    b_in = baseline_usage.get("input_tokens")
    h_in = hub_usage.get("input_tokens")

    if b_mk != "measured" or h_mk != "measured" or b_in is None or h_in is None or b_in <= 0:
        return {
            "status": "unavailable",
            "reason": "Measured input_tokens required for both baseline and hub runs; never fabricate token savings.",
            "baseline_input_tokens": b_in,
            "hub_input_tokens": h_in,
            "saved_input_tokens": None,
            "saved_pct": None,
        }

    saved = int(b_in) - int(h_in)
    return {
        "status": "measured",
        "baseline_input_tokens": int(b_in),
        "hub_input_tokens": int(h_in),
        "saved_input_tokens": saved,
        "saved_pct": round(saved * 100.0 / int(b_in), 2),
    }
