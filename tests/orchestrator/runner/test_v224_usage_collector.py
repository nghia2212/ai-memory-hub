"""V2.2.4 unit tests for opt-in read-only local Usage Collector (synthetic fixtures only)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.orchestrator.usage_collector import (
    collect_antigravity_transcript,
    collect_claude_jsonl,
    collect_codex_jsonl,
)


class TestUsageCollector(unittest.TestCase):
    def test_antigravity_incremental_partial_line_and_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "transcript.jsonl"
            line1 = json.dumps({
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-25T07:00:00Z",
                "model": "gemini-4-neon-medium",
                "input_tokens": 12000,
                "output_tokens": 350,
                "cache_read_tokens": 4000,
                "thinking": "PRIVATE_SHOULD_NEVER_BE_STORED",
            })
            line_user = json.dumps({"step_index": 1, "type": "USER_INPUT", "content": "SECRET_PROMPT"})
            # Write two complete lines plus one incomplete active line (no trailing newline)
            p.write_text(line_user + "\n" + line1 + "\n{\"step_index\": 3, \"type\": \"PLANNER_RESPONSE\"", encoding="utf-8")

            cursors: dict = {}
            st1 = collect_antigravity_transcript(p, "conv-1234", cursors)
            self.assertEqual(st1.records_processed, 1)
            self.assertEqual(st1.records_skipped, 1)
            self.assertEqual(st1.samples[0].input_tokens, 12000)
            self.assertEqual(st1.samples[0].output_tokens, 350)
            self.assertEqual(st1.samples[0].cache_read_tokens, 4000)
            self.assertEqual(st1.samples[0].counter_mode, "delta")

            # Complete the partial line and re-sync incrementally
            with p.open("a", encoding="utf-8") as fh:
                fh.write(", \"input_tokens\": 500, \"output_tokens\": 40, \"created_at\": \"2026-11-25T07:01:00Z\"}\n")
            st2 = collect_antigravity_transcript(p, "conv-1234", cursors)
            self.assertEqual(st2.records_processed, 1)
            self.assertEqual(st2.samples[0].input_tokens, 500)

            # Simulate file rotation/truncation
            p.write_text(line1 + "\n", encoding="utf-8")
            st3 = collect_antigravity_transcript(p, "conv-1234", cursors)
            self.assertEqual(st3.records_processed, 1)
            self.assertEqual(st3.samples[0].source_event_id, st1.samples[0].source_event_id)

    def test_claude_and_codex_usage_collectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "claude-session.jsonl"
            cp.write_text(
                json.dumps({
                    "type": "assistant",
                    "sessionId": "cl-1",
                    "timestamp": "2026-11-25T07:00:00Z",
                    "message": {
                        "id": "msg_1",
                        "model": "claude-sonnet-5-5-medium",
                        "usage": {
                            "input_tokens": 1000,
                            "output_tokens": 200,
                            "cache_read_input_tokens": 300,
                            "cache_creation_input_tokens": 50,
                            "output_tokens_details": {"thinking_tokens": 80},
                        },
                    },
                }) + "\n",
                encoding="utf-8",
            )
            st_c = collect_claude_jsonl(cp, {})
            self.assertEqual(st_c.records_processed, 1)
            self.assertEqual(st_c.samples[0].cache_write_tokens, 50)
            self.assertEqual(st_c.samples[0].reasoning_tokens, 80)

            xp = Path(tmp) / "codex-rollout.jsonl"
            xp.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "cx-1", "model": "gpt-oss-120b-medium"}}) + "\n"
                + json.dumps({
                    "type": "token_usage_record",
                    "ordinal": 10,
                    "timestamp": "2026-11-25T07:02:00Z",
                    "payload": {
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 8000,
                                "cached_input_tokens": 2000,
                                "cache_write_input_tokens": 100,
                                "output_tokens": 600,
                                "reasoning_output_tokens": 150,
                            }
                        }
                    },
                }) + "\n",
                encoding="utf-8",
            )
            st_x = collect_codex_jsonl(xp, {})
            self.assertEqual(st_x.records_processed, 1)
            self.assertEqual(st_x.samples[0].counter_mode, "cumulative")
            self.assertEqual(st_x.samples[0].upstream_session_id, "cx-1")
            self.assertEqual(st_x.samples[0].reasoning_tokens, 150)


if __name__ == "__main__":
    unittest.main()
