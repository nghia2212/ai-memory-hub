"""Approval-gated, redacted, budget-bounded Hermes worker."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from .config import HermesConfig
from .providers import ProviderError, SummarizationProvider, build_provider
from .redactor import sanitize_event_payload

ALLOWED_EVENT_KINDS = frozenset(
    {"session_stop", "session_end", "task_completed", "decision_note", "handoff_created"}
)


class BridgeAPI(Protocol):
    def fetch_approved_events(self, limit: int = 10) -> list[dict[str, Any]]:
        ...

    def post_candidate_result(self, result_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class RunStats:
    enabled: bool
    fetched: int = 0
    approved: int = 0
    processed: int = 0
    skipped_unapproved: int = 0
    skipped_budget: int = 0
    skipped_duplicate: int = 0
    errors: int = 0
    total_tokens: int = 0


def is_event_approved(event: dict[str, Any], config: HermesConfig) -> bool:
    """Deny by default: require explicit event approval and project-level opt-in."""
    if event.get("approved") is not True:
        return False
    kind = str(event.get("kind") or "").lower()
    if kind not in ALLOWED_EVENT_KINDS:
        return False
    project_id = str(event.get("project_id") or "")
    if not project_id:
        return False
    if event.get("external_llm_approved") is True:
        return True
    return project_id in config.approved_projects


def compute_idempotency_key(project_id: str, event_id: str, sanitized_payload: dict[str, Any]) -> str:
    canonical = json.dumps(sanitized_payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"hermes:{project_id}:{event_id}:{digest}"


class HermesWorker:
    def __init__(
        self,
        config: HermesConfig,
        bridge: BridgeAPI,
        provider: SummarizationProvider | None = None,
    ) -> None:
        self.config = config
        self.bridge = bridge
        self.provider = provider
        self._processed_keys: set[str] = self._load_processed_keys()

    def _state_file(self) -> Any:
        if not self.config.state_dir:
            return None
        from pathlib import Path

        return Path(self.config.state_dir) / "processed_keys.json"

    def _load_processed_keys(self) -> set[str]:
        sf = self._state_file()
        if sf is None or not sf.is_file():
            return set()
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(k) for k in data[-5000:]}
        except Exception:
            pass
        return set()

    def _persist_processed_keys(self) -> None:
        sf = self._state_file()
        if sf is None:
            return
        try:
            sf.parent.mkdir(parents=True, exist_ok=True)
            trimmed = sorted(self._processed_keys)[-5000:]
            sf.write_text(json.dumps(trimmed), encoding="utf-8")
        except Exception:
            pass

    def run_once(self) -> RunStats:
        if not self.config.enabled:
            return RunStats(enabled=False)

        ready, _ = self.config.validate_for_run()
        if not ready:
            return RunStats(enabled=True, errors=1)

        provider = self.provider or build_provider(self.config)
        events = self.bridge.fetch_approved_events(limit=self.config.max_events_per_run)
        stats = RunStats(enabled=True, fetched=len(events))

        for event in events:
            if not is_event_approved(event, self.config):
                stats.skipped_unapproved += 1
                continue

            stats.approved += 1
            if stats.total_tokens >= self.config.max_total_tokens_per_run:
                stats.skipped_budget += 1
                continue

            project_id = str(event["project_id"])
            worktree_id = str(event.get("worktree_id") or "")
            task_id = str(event.get("task_id") or "")
            event_source_agent = str(event.get("source_agent") or "")
            event_id = str(event.get("event_id") or event.get("id") or "")
            event_kind = str(event.get("kind") or "session_end").lower()
            raw_payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
            sanitized = sanitize_event_payload(raw_payload, max_chars=self.config.max_input_chars)
            idem_key = compute_idempotency_key(project_id, event_id or "unknown", sanitized)

            # Prevent duplicate LLM calls for already-processed events
            if idem_key in self._processed_keys:
                stats.skipped_duplicate += 1
                continue

            try:
                summary = provider.summarize(event_kind, sanitized)
                used_tokens = (summary.input_tokens or 0) + (summary.output_tokens or 0)
                stats.total_tokens += used_tokens

                ev_parts = [f"provider={summary.provider}", f"model={summary.model}"]
                if task_id:
                    ev_parts.append(f"task_id={task_id}")
                if event_source_agent:
                    ev_parts.append(f"source_agent={event_source_agent}")
                evidence_ref = ";".join(ev_parts)[:512]

                # Strictly matches Memory Bridge WorkerResultRequest schema
                candidate_payload: dict[str, Any] = {
                    "project_id": project_id,
                    "worktree_id": worktree_id,
                    "type": summary.memory_type,
                    "title": summary.title,
                    "body": summary.summary,
                    "tags": summary.tags,
                    "evidence_ref": evidence_ref,
                    "idempotency_key": idem_key,
                }
                if event_id:
                    candidate_payload["event_id"] = event_id
                if summary.input_tokens is not None:
                    candidate_payload["input_tokens"] = summary.input_tokens
                if summary.output_tokens is not None:
                    candidate_payload["output_tokens"] = summary.output_tokens

                self.bridge.post_candidate_result(candidate_payload)
                self._processed_keys.add(idem_key)
                self._persist_processed_keys()
                stats.processed += 1
            except (ProviderError, Exception):
                stats.errors += 1

        return stats
