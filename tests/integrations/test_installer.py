import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-claude.ps1"


class TestInstallClaudePs1(unittest.TestCase):
    def _run_ps(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_dry_run_install_idempotent_conflict_and_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "dir with spaces"
            base.mkdir(parents=True, exist_ok=True)
            settings_path = base / "settings.json"
            mcp_path = base / ".claude.json"
            env_path = base / ".env"

            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {"matcher": "", "hooks": [{"type": "command", "command": "echo user-hook"}]}
                            ]
                        },
                        "theme": "dark",
                    }
                ),
                encoding="utf-8",
            )
            mcp_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "user-existing": {"type": "stdio", "command": "npx", "args": ["foo"]}
                        }
                    }
                ),
                encoding="utf-8",
            )
            env_path.write_text("CUSTOM_VAR=preserve_me\n", encoding="utf-8")

            common_args = [
                "-SettingsPath",
                str(settings_path),
                "-McpConfigPath",
                str(mcp_path),
                "-EnvFilePath",
                str(env_path),
            ]

            # 1. Dry-run must not modify files
            dry = self._run_ps([*common_args, "-DryRun"])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertNotIn("ai-memory-bridge", mcp_path.read_text(encoding="utf-8"))

            # 2. First install
            inst1 = self._run_ps(common_args)
            self.assertEqual(inst1.returncode, 0, inst1.stderr)
            self.assertEqual(len(list(base.glob("settings.json.bak.*"))), 1)
            self.assertEqual(len(list(base.glob(".claude.json.bak.*"))), 1)

            env_text_1 = env_path.read_text(encoding="utf-8")
            self.assertIn("CUSTOM_VAR=preserve_me", env_text_1)
            self.assertIn("BRIDGE_READ_TOKEN=", env_text_1)
            for line in env_text_1.splitlines():
                if line.startswith("BRIDGE_READ_TOKEN="):
                    token = line.split("=", 1)[1]
                    self.assertNotIn(token, inst1.stdout)

            mcp1 = json.loads(mcp_path.read_text(encoding="utf-8"))
            self.assertIn("user-existing", mcp1["mcpServers"])
            self.assertIn("ai-memory-codebase", mcp1["mcpServers"])
            self.assertIn("ai-memory-bridge", mcp1["mcpServers"])

            settings1 = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings1["theme"], "dark")
            self.assertEqual(len(settings1["hooks"]["SessionStart"]), 2)

            # 3. Second install (idempotent: no duplicates, tokens preserved)
            inst2 = self._run_ps(common_args)
            self.assertEqual(inst2.returncode, 0, inst2.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), env_text_1)
            settings2 = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(len(settings2["hooks"]["SessionStart"]), 2)
            self.assertEqual(len(settings2["hooks"]["Stop"]), 1)
            self.assertEqual(len(settings2["hooks"]["SessionEnd"]), 1)

            # 4. Conflict detection without -Force
            mcp1["mcpServers"]["ai-memory-bridge"]["url"] = "http://127.0.0.1:9999/mcp"
            mcp_path.write_text(json.dumps(mcp1), encoding="utf-8")
            conflict = self._run_ps(common_args)
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("Conflict detected", conflict.stdout + conflict.stderr)

            # 5. Resolve conflict with -Force
            forced = self._run_ps([*common_args, "-Force"])
            self.assertEqual(forced.returncode, 0, forced.stderr)

            # 6. Uninstall removes only AI Memory Hub entries
            uninst = self._run_ps([*common_args, "-Uninstall"])
            self.assertEqual(uninst.returncode, 0, uninst.stderr)
            mcp_after = json.loads(mcp_path.read_text(encoding="utf-8"))
            self.assertIn("user-existing", mcp_after["mcpServers"])
            self.assertNotIn("ai-memory-codebase", mcp_after["mcpServers"])
            self.assertNotIn("ai-memory-bridge", mcp_after["mcpServers"])
            settings_after = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(len(settings_after["hooks"]["SessionStart"]), 1)
            self.assertEqual(len(settings_after["hooks"]["Stop"]), 0)
            self.assertEqual(len(settings_after["hooks"]["SessionEnd"]), 0)


if __name__ == "__main__":
    unittest.main()
