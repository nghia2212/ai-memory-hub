"""V2.2.3 regression tests: Windows CLI long prompt stdin delivery (up to 32,000 chars, no silent truncation)."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from services.orchestrator.dispatch import cli_runner
from services.orchestrator.dispatch.cli_runner import UnsafeCLIRequestError, build_cli_invocation


class TestLongPromptStdinDelivery(unittest.TestCase):
    def test_codex_ps1_wrapper_uses_stdin_for_32k_prompt(self) -> None:
        prompt = ("Phân tích yêu cầu V3.\n" * 1200) + "END_SENTINEL"
        self.assertGreater(len(prompt), 20000)
        self.assertLessEqual(len(prompt), 32000)
        with tempfile.TemporaryDirectory() as tmp, patch.object(cli_runner, "_is_windows", return_value=True):
            cmd, cwd, stdin_text = build_cli_invocation(
                cli_family="codex",
                model_id="gpt-oss-120b-medium",
                worktree_path=tmp,
                prompt=prompt,
                executable=r"C:\tools\codex.ps1",
            )
        self.assertEqual(cmd[-1], "-")
        self.assertEqual(stdin_text, prompt.strip())
        self.assertNotIn(prompt.strip(), cmd)

    def test_claude_uses_stdin_for_long_prompt_on_windows(self) -> None:
        prompt = "A" * 31500
        with tempfile.TemporaryDirectory() as tmp, patch.object(cli_runner, "_is_windows", return_value=True):
            cmd, _cwd, stdin_text = build_cli_invocation(
                cli_family="claude",
                model_id="claude-sonnet-5-5-medium",
                worktree_path=tmp,
                prompt=prompt,
                executable=r"C:\tools\claude.exe",
            )
        self.assertEqual(stdin_text, prompt)
        self.assertNotIn(prompt, cmd)

    def test_agy_rejects_oversized_windows_argv_explicitly_without_truncation(self) -> None:
        prompt = "B" * 31500
        with tempfile.TemporaryDirectory() as tmp, patch.object(cli_runner, "_is_windows", return_value=True):
            with self.assertRaises(UnsafeCLIRequestError) as ctx:
                build_cli_invocation(
                    cli_family="agy",
                    model_id="gemini-4-neon-medium",
                    worktree_path=tmp,
                    prompt=prompt,
                    executable=r"C:\tools\agy.exe",
                )
        self.assertIn("stdin", str(ctx.exception).lower())

    def test_prompt_over_32000_chars_always_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UnsafeCLIRequestError):
                build_cli_invocation(
                    cli_family="codex",
                    model_id="gpt-oss-120b-medium",
                    worktree_path=tmp,
                    prompt="X" * 32001,
                    executable=r"C:\tools\codex.ps1",
                )


if __name__ == "__main__":
    unittest.main()
