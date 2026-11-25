"""V3 M3 & M7 unit tests: Conversation Collector privacy/redaction/cursor & Budgeted Context Pack + honest benchmark."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.orchestrator.context_pack import build_context_pack, compare_token_benchmark
from services.orchestrator.conversation_collector import (
    collect_antigravity_conversation,
    collect_claude_conversation,
    collect_codex_conversation,
)


class TestV3CollectorsAndContextPack(unittest.TestCase):
    def test_conversation_collectors_strip_thinking_and_redact_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "transcript.jsonl"
            p.write_text(
                json.dumps({"step_index": 1, "type": "USER_INPUT", "content": "Dùng key sk-testsecret1234567890 để test", "created_at": "2026-11-25T08:00:00Z"}) + "\n"
                + json.dumps({"step_index": 2, "type": "PLANNER_RESPONSE", "thinking": "PRIVATE_CHAIN_OF_THOUGHT", "content": "Đã xử lý an toàn.", "model": "gemini-4-neon-medium", "input_tokens": 100, "output_tokens": 20, "created_at": "2026-11-25T08:01:00Z"}) + "\n"
                + json.dumps({"step_index": 3, "type": "SYSTEM_MESSAGE", "content": "HIDDEN_SYSTEM_PROMPT"}) + "\n",
                encoding="utf-8",
            )
            cursors: dict = {}
            c = collect_antigravity_conversation(p, "conv-ag-1", cursors)
            self.assertEqual(len(c.messages), 2)
            self.assertNotIn("sk-testsecret1234567890", c.messages[0].visible_text)
            self.assertIn("[REDACTED_API_KEY]", c.messages[0].visible_text)
            self.assertNotIn("PRIVATE_CHAIN_OF_THOUGHT", c.messages[1].visible_text)
            self.assertNotIn("HIDDEN_SYSTEM_PROMPT", " ".join(m.visible_text for m in c.messages))

            # Claude & Codex adapters
            cp = Path(tmp) / "claude.jsonl"
            cp.write_text(
                json.dumps({"type": "assistant", "sessionId": "cl-1", "timestamp": "2026-11-25T08:00:00Z", "message": {"model": "claude-sonnet-5-5-medium", "content": [{"type": "thinking", "thinking": "SECRET_THOUGHT"}, {"type": "text", "text": "Visible answer"}]}}) + "\n",
                encoding="utf-8",
            )
            cc = collect_claude_conversation(cp, {})
            self.assertEqual(len(cc.messages), 1)
            self.assertEqual(cc.messages[0].visible_text, "Visible answer")

            xp = Path(tmp) / "codex.jsonl"
            xp.write_text(
                json.dumps({"type": "response_item", "ordinal": 1, "timestamp": "2026-11-25T08:00:00Z", "payload": {"role": "assistant", "content": [{"type": "output_text", "text": "Codex reply"}]}}) + "\n",
                encoding="utf-8",
            )
            cx = collect_codex_conversation(xp, {})
            self.assertEqual(len(cx.messages), 1)
            self.assertEqual(cx.messages[0].visible_text, "Codex reply")

    def test_context_pack_dedup_verified_only_and_honest_benchmark(self) -> None:
        pack = build_context_pack(
            objective="Triển khai V3 Dynamic MCP",
            verified_memories=[
                {"id": "m1", "state": "verified", "title": "Rule 1", "excerpt": "Single writer SQLite"},
                {"id": "m2", "state": "verified", "title": "Rule 1", "excerpt": "Single writer SQLite"},  # dup
                {"id": "m3", "state": "superseded", "title": "Old Rule", "excerpt": "Should be ignored"},
            ],
            handoffs=[{"id": "h1", "task_id": "V3-M1", "summary": "Ready for M1"}],
            code_symbols=[{"path": "services/memory-bridge/app/db.py", "line_start": 1, "line_end": 50, "title": "DatabaseManager"}],
            approved_conversation_excerpts=[{"conversation_id": "c1", "seq_no": 2, "role": "assistant", "excerpt": "Approved insight"}],
            max_chars=4000,
        )
        self.assertEqual(pack.sections["memories"], 1)
        self.assertNotIn("Old Rule", pack.text)
        self.assertEqual(pack.sections["code_symbols"], 1)
        self.assertEqual(pack.sections["conversations"], 1)

        # Honest benchmark: refuses to fabricate savings if unavailable
        unavail = compare_token_benchmark({"measurement_kind": "unavailable", "input_tokens": None}, {"measurement_kind": "measured", "input_tokens": 1000})
        self.assertEqual(unavail["status"], "unavailable")
        self.assertIsNone(unavail["saved_pct"])

        measured = compare_token_benchmark({"measurement_kind": "measured", "input_tokens": 10000}, {"measurement_kind": "measured", "input_tokens": 4000})
        self.assertEqual(measured["status"], "measured")
        self.assertEqual(measured["saved_input_tokens"], 6000)
        self.assertEqual(measured["saved_pct"], 60.0)


if __name__ == "__main__":
    unittest.main()
