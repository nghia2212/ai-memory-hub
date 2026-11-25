import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations" / "claude" / "hooks"))
from common import GitScope, record_manual_session

class ManualSessionTest(unittest.TestCase):
    def test_stable_identity_and_no_transcript(self):
        scope = GitScope("root","git","root","main","a"*40,False,"clean")
        with patch("common.http_json") as call, patch.dict("os.environ", {"AI_MEMORY_MANAGED_SESSION":""}):
            for status in ("running","completed"):
                record_manual_session("http://127.0.0.1:8082","write",scope,"p","w","claude",{"session_id":"native-id","transcript_path":"private","prompt":"secret"},status)
            a,b = [x.kwargs["payload"] for x in call.call_args_list]
            self.assertEqual(a["session_id"],b["session_id"])
            self.assertNotIn("private",str(a))
            self.assertNotIn("secret",str(a))
            self.assertEqual(a["model_id"],"unknown")
            self.assertEqual(b["status"],"completed")
    def test_managed_and_missing_identity_skipped(self):
        scope = GitScope("root","git","root","main","a"*40,False,"clean")
        with patch("common.http_json") as call, patch.dict("os.environ", {"AI_MEMORY_MANAGED_SESSION":"1"}):
            record_manual_session("http://127.0.0.1:8082","write",scope,"p","w","codex",{"session_id":"x"},"running")
            call.assert_not_called()
