"""HTTP client for Memory Bridge worker endpoints (/v1/worker/events, /v1/worker/results)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class BridgeClientError(RuntimeError):
    """Raised when Memory Bridge worker API returns an error."""


class BridgeClient:
    def __init__(self, base_url: str, worker_token: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._worker_token = worker_token
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._worker_token:
            headers["Authorization"] = f"Bearer {self._worker_token}"
        return headers

    def fetch_approved_events(self, limit: int = 10) -> list[dict[str, Any]]:
        url = f"{self.base_url}/v1/worker/events?limit={int(limit)}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise BridgeClientError(f"Bridge GET /v1/worker/events HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BridgeClientError(f"Bridge GET /v1/worker/events failed: {type(exc).__name__}") from exc

        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def post_candidate_result(self, result_payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/v1/worker/results"
        raw = json.dumps(result_payload).encode("utf-8")
        req = urllib.request.Request(url, data=raw, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise BridgeClientError(f"Bridge POST /v1/worker/results HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BridgeClientError(f"Bridge POST /v1/worker/results failed: {type(exc).__name__}") from exc
