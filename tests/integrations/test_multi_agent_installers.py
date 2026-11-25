import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEMINI_INSTALLER = ROOT / "scripts" / "install-gemini.ps1"
CODEX_INSTALLER = ROOT / "scripts" / "install-codex.ps1"
ALL_INSTALLER = ROOT / "scripts" / "install-all.ps1"


class TestMultiAgentInstallers(unittest.TestCase):
    def _run_ps(self, script: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_gemini_installer_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "gemini with spaces"
            base.mkdir(parents=True, exist_ok=True)
            settings_path = base / "settings.json"
            env_path = base / ".env"

            settings_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {"user-gemini-mcp": {"httpUrl": "http://127.0.0.1:9000/mcp"}},
                        "hooks": {
                            "SessionStart": [
                                {"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = ["-SettingsPath", str(settings_path), "-EnvFilePath", str(env_path)]

            dry = self._run_ps(GEMINI_INSTALLER, [*args, "-DryRun"])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertNotIn("ai-memory-bridge", settings_path.read_text(encoding="utf-8"))

            inst1 = self._run_ps(GEMINI_INSTALLER, args)
            self.assertEqual(inst1.returncode, 0, inst1.stderr)
            data1 = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIn("user-gemini-mcp", data1["mcpServers"])
            self.assertIn("ai-memory-codebase", data1["mcpServers"])
            self.assertIn("ai-memory-bridge", data1["mcpServers"])
            self.assertEqual(len(data1["hooks"]["SessionStart"]), 2)
            self.assertEqual(len(data1["hooks"]["SessionEnd"]), 1)

            # Idempotency
            inst2 = self._run_ps(GEMINI_INSTALLER, args)
            self.assertEqual(inst2.returncode, 0, inst2.stderr)
            data2 = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data2["hooks"]["SessionStart"]), 2)
            self.assertEqual(len(data2["hooks"]["SessionEnd"]), 1)

            # Conflict detection
            data2["mcpServers"]["ai-memory-bridge"]["httpUrl"] = "http://127.0.0.1:9999/mcp"
            settings_path.write_text(json.dumps(data2), encoding="utf-8")
            conflict = self._run_ps(GEMINI_INSTALLER, args)
            self.assertNotEqual(conflict.returncode, 0)
            self.assertEqual(self._run_ps(GEMINI_INSTALLER, [*args, "-Force"]).returncode, 0)

            # Uninstall
            uninst = self._run_ps(GEMINI_INSTALLER, [*args, "-Uninstall"])
            self.assertEqual(uninst.returncode, 0, uninst.stderr)
            after = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIn("user-gemini-mcp", after["mcpServers"])
            self.assertNotIn("ai-memory-bridge", after["mcpServers"])
            self.assertEqual(len(after["hooks"]["SessionStart"]), 1)
            self.assertEqual(len(after["hooks"]["SessionEnd"]), 0)

    def test_codex_installer_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "codex with spaces"
            base.mkdir(parents=True, exist_ok=True)
            toml_path = base / "config.toml"
            hooks_path = base / "hooks.json"
            env_path = base / ".env"

            toml_path.write_text(
                'model = "o3"\n\n[mcp_servers.user_existing]\nurl = "http://127.0.0.1:9001/mcp"\n',
                encoding="utf-8",
            )
            hooks_path.write_text(
                json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user"}]}]}}),
                encoding="utf-8",
            )
            args = [
                "-ConfigTomlPath",
                str(toml_path),
                "-HooksPath",
                str(hooks_path),
                "-EnvFilePath",
                str(env_path),
            ]

            dry = self._run_ps(CODEX_INSTALLER, [*args, "-DryRun"])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertNotIn("ai-memory-bridge", toml_path.read_text(encoding="utf-8"))

            inst1 = self._run_ps(CODEX_INSTALLER, args)
            self.assertEqual(inst1.returncode, 0, inst1.stderr)
            toml_text = toml_path.read_text(encoding="utf-8")
            self.assertIn('model = "o3"', toml_text)
            self.assertIn("[mcp_servers.user_existing]", toml_text)
            self.assertIn("[mcp_servers.ai-memory-codebase]", toml_text)
            self.assertIn("[mcp_servers.ai-memory-bridge]", toml_text)
            self.assertIn("bearer_token_env_var", toml_text)

            # Ensure no raw token leaked into config.toml
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("BRIDGE_READ_TOKEN="):
                    self.assertNotIn(line.split("=", 1)[1], toml_text)

            hooks1 = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(len(hooks1["hooks"]["Stop"]), 2)
            self.assertEqual(len(hooks1["hooks"]["SessionStart"]), 1)
            self.assertEqual(len(hooks1["hooks"]["SessionEnd"]), 1)

            # Idempotency
            self.assertEqual(self._run_ps(CODEX_INSTALLER, args).returncode, 0)
            hooks2 = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(len(hooks2["hooks"]["Stop"]), 2)

            # Uninstall
            uninst = self._run_ps(CODEX_INSTALLER, [*args, "-Uninstall"])
            self.assertEqual(uninst.returncode, 0, uninst.stderr)
            toml_after = toml_path.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.user_existing]", toml_after)
            self.assertNotIn("[mcp_servers.ai-memory-bridge]", toml_after)
            hooks_after = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(len(hooks_after["hooks"]["Stop"]), 1)
            self.assertEqual(len(hooks_after["hooks"]["SessionStart"]), 0)

    def test_install_all_cli_detection_dry_run(self) -> None:
        res = self._run_ps(ALL_INSTALLER, ["-Agents", "auto", "-DryRun"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("AI Memory Hub CLI Detection", res.stdout)
        self.assertIn("claude", res.stdout)
        self.assertIn("codex", res.stdout)


if __name__ == "__main__":
    unittest.main()
