import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "hermes"))

from app.config import HermesConfig  # noqa: E402
from app.providers import OpenAICompatibleProvider, SummaryResult  # noqa: E402
from app.worker import HermesWorker  # noqa: E402


class FakeBridge:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.fetch_calls = 0
        self.posted: list[dict[str, Any]] = []

    def fetch_approved_events(self, limit: int = 10) -> list[dict[str, Any]]:
        self.fetch_calls += 1
        return self.events[:limit]

    def post_candidate_result(self, result_payload: dict[str, Any]) -> dict[str, Any]:
        self.posted.append(result_payload)
        return {"ok": True}


class CountingMockProvider:
    def __init__(self, tokens_per_call: int = 100) -> None:
        self.calls = 0
        self.tokens_per_call = tokens_per_call

    def summarize(self, event_kind: str, sanitized_payload: dict[str, Any]) -> SummaryResult:
        self.calls += 1
        return SummaryResult(
            title=f"Summary {event_kind}",
            summary="Concise candidate summary",
            memory_type="note",
            tags=[event_kind],
            provider="mock",
            model="mock-v1",
            input_tokens=self.tokens_per_call // 2,
            output_tokens=self.tokens_per_call // 2,
        )


class TestHermesWorker(unittest.TestCase):
    def test_disabled_by_default_no_calls(self) -> None:
        cfg = HermesConfig.from_env({})
        self.assertFalse(cfg.enabled)
        bridge = FakeBridge(
            [{"id": "e1", "project_id": "p1", "kind": "session_end", "approved": True, "external_llm_approved": True}]
        )
        provider = CountingMockProvider()
        stats = HermesWorker(cfg, bridge, provider).run_once()
        self.assertFalse(stats.enabled)
        self.assertEqual(bridge.fetch_calls, 0)
        self.assertEqual(provider.calls, 0)

    def test_missing_deepseek_key_fails_validation(self) -> None:
        cfg = HermesConfig.from_env({"HERMES_ENABLED": "true", "HERMES_PROVIDER": "deepseek"})
        ready, reason = cfg.validate_for_run()
        self.assertFalse(ready)
        self.assertEqual(reason, "missing_deepseek_api_key")
        bridge = FakeBridge([])
        stats = HermesWorker(cfg, bridge).run_once()
        self.assertEqual(stats.errors, 1)
        self.assertEqual(bridge.fetch_calls, 0)

    def test_approval_gate_and_candidate_posting(self) -> None:
        cfg = HermesConfig.from_env(
            {
                "HERMES_ENABLED": "true",
                "HERMES_PROVIDER": "mock",
                "HERMES_APPROVED_PROJECTS": "proj-1",
            }
        )
        events = [
            {"id": "e1", "project_id": "proj-1", "kind": "session_end", "approved": False},
            {"id": "e2", "project_id": "proj-unapproved", "kind": "session_end", "approved": True},
            {"id": "e3", "project_id": "proj-1", "kind": "raw_dump", "approved": True},
            {
                "id": "e4",
                "project_id": "proj-1",
                "worktree_id": "wt-1",
                "kind": "session_end",
                "approved": True,
                "payload": {
                    "branch": "main",
                    "raw_code": "SECRET_CODE",
                    "transcript": "FULL_TRANSCRIPT",
                    "note": "Completed task with token sk-abc1234567890123",
                },
            },
        ]
        bridge = FakeBridge(events)
        stats = HermesWorker(cfg, bridge).run_once()
        self.assertEqual(stats.fetched, 4)
        self.assertEqual(stats.skipped_unapproved, 3)
        self.assertEqual(stats.processed, 1)
        self.assertEqual(len(bridge.posted), 1)
        posted = bridge.posted[0]
        self.assertEqual(posted["state"], "candidate")
        self.assertTrue(posted["idempotency_key"].startswith("hermes:proj-1:e4:"))

    def test_budget_cap_stops_excess_events(self) -> None:
        cfg = HermesConfig.from_env(
            {
                "HERMES_ENABLED": "true",
                "HERMES_PROVIDER": "mock",
                "HERMES_APPROVED_PROJECTS": "proj-1",
                "HERMES_MAX_TOTAL_TOKENS_PER_RUN": "500",
            }
        )
        events = [
            {"id": f"e{i}", "project_id": "proj-1", "kind": "session_stop", "approved": True}
            for i in range(3)
        ]
        bridge = FakeBridge(events)
        provider = CountingMockProvider(tokens_per_call=300)
        stats = HermesWorker(cfg, bridge, provider).run_once()
        self.assertEqual(stats.processed, 2)  # 300 + 300 = 600 >= 500, third skipped
        self.assertEqual(stats.skipped_budget, 1)

    def test_deepseek_provider_with_local_mock_http_and_retry(self) -> None:
        attempts = {"count": 0}
        captured: dict[str, Any] = {}

        class MockDeepSeekHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                attempts["count"] += 1
                length = int(self.headers.get("Content-Length", "0"))
                captured["auth"] = self.headers.get("Authorization")
                captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                if attempts["count"] == 1:
                    self.send_response(503)
                    self.end_headers()
                    return
                resp = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "title": "Hook fix",
                                            "summary": "Verified fail-open behavior.",
                                            "memory_type": "bugfix",
                                            "tags": ["hooks"],
                                        }
                                    )
                                }
                            }
                        ],
                        "usage": {"prompt_tokens": 40, "completion_tokens": 20},
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), MockDeepSeekHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            port = srv.server_address[1]
            provider = OpenAICompatibleProvider(
                provider_name="deepseek",
                endpoint_url=f"http://127.0.0.1:{port}/chat/completions",
                api_key="sk-test-mock-only",
                model="deepseek-flash",
                timeout_seconds=5.0,
                max_retries=1,
                max_output_tokens=200,
            )
            res = provider.summarize("session_end", {"branch": "main"})
            self.assertEqual(attempts["count"], 2)
            self.assertEqual(captured["auth"], "Bearer sk-test-mock-only")
            self.assertEqual(captured["body"]["model"], "deepseek-flash")
            self.assertEqual(res.title, "Hook fix")
            self.assertEqual(res.input_tokens, 40)
            self.assertEqual(res.output_tokens, 20)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
