import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "hermes"))

from app.redactor import redact_text, sanitize_event_payload  # noqa: E402


class TestRedactor(unittest.TestCase):
    def test_redact_secrets_and_paths(self) -> None:
        raw = (
            "Bearer abcdef123456 sk-secretkey1234567890 "
            "ghp_1234567890abcdefghij1234567890 "
            "postgres://admin:supersecret@db.local:5432/app "
            "user@example.com "
            r"C:\Users\nghia\projects\secret\file.py "
            "/home/nghia/work/repo"
        )
        cleaned, truncated = redact_text(raw, max_chars=2000)
        self.assertFalse(truncated)
        self.assertNotIn("abcdef123456", cleaned)
        self.assertNotIn("sk-secretkey1234567890", cleaned)
        self.assertNotIn("ghp_1234567890abcdefghij1234567890", cleaned)
        self.assertNotIn("supersecret", cleaned)
        self.assertNotIn("user@example.com", cleaned)
        self.assertNotIn(r"C:\Users\nghia", cleaned)
        self.assertNotIn("/home/nghia", cleaned)
        self.assertIn("<USER_HOME>", cleaned)

    def test_sanitize_drops_forbidden_fields_and_bounds(self) -> None:
        payload = {
            "branch": "agent/v1-hermes-integrations",
            "transcript": "FULL TRANSCRIPT MUST BE DROPPED",
            "transcript_path": r"C:\Users\alice\.claude\transcript.jsonl",
            "raw_code": "def secret(): pass",
            "source_code": "print('secret')",
            "diff_full": "+secret",
            "note": "Fixed hook timeout " + ("x" * 600),
        }
        sanitized = sanitize_event_payload(payload, max_chars=400)
        for forbidden in ("transcript", "transcript_path", "raw_code", "source_code", "diff_full"):
            self.assertNotIn(forbidden, sanitized)
        self.assertEqual(sanitized["branch"], "agent/v1-hermes-integrations")
        self.assertLessEqual(len(sanitized["note"]), 400)


if __name__ == "__main__":
    unittest.main()
