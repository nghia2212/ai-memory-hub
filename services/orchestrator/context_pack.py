"""V3.5 / V3.1 — Deterministic Budgeted Context Pack builder & honest token benchmark comparator (0 LLM).

V3.1 P0.2 Fix:
- NEVER silently truncate the user's original objective (supports full 32,000+ char prompts from V2.2.2).
- Context budget (`max_chars`) applies strictly to supplemental context (memories, handoffs, code symbols, conversations).
- If objective exceeds `max_objective_chars` (default 32,000) or optional `model_max_chars`, raise explicit `ContextBudgetError`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

MAX_OBJECTIVE_CHARS_DEFAULT = 32_000


class ContextBudgetError(ValueError):
    """Raised when the user's objective exceeds the supported prompt/model/CLI character limit."""


@dataclass
class ContextPackResult:
    text: str
    used_chars: int
    budget_chars: int
    truncated: bool
    deduplicated_count: int
    sections: dict[str, int] = field(default_factory=dict)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    objective_chars: int = 0
    context_used_chars: int = 0
    context_budget_chars: int = 0


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
    max_objective_chars: int = MAX_OBJECTIVE_CHARS_DEFAULT,
    model_max_chars: int | None = None,
) -> ContextPackResult:
    """Assemble a strictly bounded, deduplicated context pack while preserving the full objective verbatim.

    - `objective` is NEVER silently truncated.
    - `max_chars` budgets only the supplemental context sections (memories, handoffs, code, conversations).
    - If `len(objective)` exceeds `max_objective_chars` (default 32,000) or `model_max_chars`, raises `ContextBudgetError`.
    """
    clean_obj = objective.strip()
    obj_len = len(clean_obj)
    if obj_len > max_objective_chars:
        raise ContextBudgetError(
            f"Objective length ({obj_len} chars) exceeds maximum allowed limit ({max_objective_chars} chars). "
            "Prompt was NOT silently truncated; please shorten or split the task."
        )
    if model_max_chars is not None and obj_len + len("# Objective\n") > model_max_chars:
        raise ContextBudgetError(
            f"Objective length ({obj_len} chars) exceeds target model/CLI limit ({model_max_chars} chars). "
            "Prompt was NOT silently truncated."
        )

    context_budget = max(500, int(max_chars))
    if model_max_chars is not None:
        remaining_for_ctx = max(0, int(model_max_chars) - (obj_len + len("# Objective\n") + 2))
        context_budget = min(context_budget, remaining_for_ctx)

    seen: set[str] = set()
    deduped = 0
    truncated = False
    objective_block = f"# Objective\n{clean_obj}"
    parts: list[str] = [objective_block]
    ctx_used = 0
    sections = {"memories": 0, "handoffs": 0, "code_symbols": 0, "conversations": 0}
    provenance: list[dict[str, Any]] = []

    def _try_append(section_key: str, line: str, prov: dict[str, Any], dedup_text: str | None = None) -> bool:
        nonlocal ctx_used, deduped, truncated
        h = _norm_hash(dedup_text or line)
        if h in seen:
            deduped += 1
            return False
        if ctx_used + len(line) + 2 > context_budget:
            truncated = True
            return False
        seen.add(h)
        parts.append(line)
        ctx_used += len(line) + 1
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
        budget_chars=len(objective_block) + context_budget,
        truncated=truncated,
        deduplicated_count=deduped,
        sections=sections,
        provenance=provenance,
        objective_chars=obj_len,
        context_used_chars=ctx_used,
        context_budget_chars=context_budget,
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


def compare_three_way_benchmark(
    *,
    no_hub: dict[str, Any],
    all_tools_hub: dict[str, Any],
    dynamic_mcp_hub: dict[str, Any],
) -> dict[str, Any]:
    """V3.1 P9.5 honest 3-way benchmark: (A) No Hub vs (B) Hub all-tools vs (C) Hub Dynamic MCP."""
    return {
        "methodology": "Three-way comparison (A: No Hub, B: Hub All Tools, C: Hub Dynamic MCP); savings computed only when measurement_kind == 'measured'.",
        "runs": {
            "A_no_hub": no_hub,
            "B_all_tools_hub": all_tools_hub,
            "C_dynamic_mcp_hub": dynamic_mcp_hub,
        },
        "C_vs_A": compare_token_benchmark(no_hub, dynamic_mcp_hub),
        "C_vs_B": compare_token_benchmark(all_tools_hub, dynamic_mcp_hub),
    }
