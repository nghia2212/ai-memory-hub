"""Synthetic regression tests: no provider calls."""
import json
import unittest
from services.orchestrator.dispatch.cli_runner import parse_cli_telemetry

class TelemetryRegression(unittest.TestCase):
    def test_zero_cache_is_measured(self):
        counters, _, _ = parse_cli_telemetry(cli_family="claude", stdout=json.dumps({"result":"OK","usage":{"input_tokens":5,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}))
        self.assertEqual(counters.cache_read_tokens, 0)
        self.assertEqual(counters.cache_write_tokens, 0)
    def test_codex_message_not_raw_transcript(self):
        output = "\n".join(json.dumps(x) for x in [{"type":"item.completed","item":{"type":"agent_message","text":"OK"}},{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":2}}])
        counters, _, summary = parse_cli_telemetry(cli_family="codex", stdout=output)
        self.assertEqual(counters.cache_read_tokens, 0)
        self.assertEqual(summary, "OK")
