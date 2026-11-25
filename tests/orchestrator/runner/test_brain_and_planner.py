import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.orchestrator.contracts import BrainPolicyConfig, BrainRequest  # noqa: E402
from services.orchestrator.planner.brain_adapters import (  # noqa: E402
    OptInDeepSeekBrainAdapter,
    RuleBrainAdapter,
    discover_host_cli_capabilities,
    resolve_role_availability,
)
from services.orchestrator.planner.dag_planner import (  # noqa: E402
    InvalidDAGError,
    build_plan_from_brain,
    compute_revision_hash,
    validate_dag,
)

FAKE_AGY_MODELS = """
gemini-3.8-flash-high    Gemini 3.8 Flash (High)
gemini-3.8-flash-medium  Gemini 3.8 Flash (Medium)
gemini-3.8-flash-low     Gemini 3.8 Flash (Low)
claude-opus-5-5-medium   Claude Opus 5.5 (Medium)
claude-sonnet-5-5-medium Claude Sonnet 5.5 (Medium)
gpt-oss-120b-medium      GPT-OSS 120B (Medium)
"""


class TestBrainAndPlanner(unittest.TestCase):
    def test_neon_unavailable_when_missing_from_agy_models(self) -> None:
        def fake_which(name: str) -> str | None:
            return f"C:\\fake\\{name}.exe" if name in {"agy", "claude", "codex"} else None

        def fake_run(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
            if args[1] == "--version":
                return 0, "1.1.27", ""
            if args[1] == "models":
                return 0, FAKE_AGY_MODELS, ""
            return 1, "", ""

        report = discover_host_cli_capabilities(runner_fn=fake_run, which_fn=fake_which)
        neon_role = resolve_role_availability("Gemini 4 Neon Medium", "gemini-4-neon-medium", report)
        self.assertEqual(neon_role.availability_status, "unavailable")
        self.assertIsNone(neon_role.effective_model_id)

        sonnet_role = resolve_role_availability("Claude Sonnet 5.5 Medium", "claude-sonnet-5-5-medium", report)
        self.assertEqual(sonnet_role.availability_status, "verified")
        self.assertEqual(sonnet_role.effective_model_id, "claude-sonnet-5-5-medium")

    def test_deepseek_blocked_when_paid_fallback_disabled(self) -> None:
        policy = BrainPolicyConfig(project_id=uuid4(), allow_paid_api_fallback=False)
        adapter = OptInDeepSeekBrainAdapter(policy=policy, api_key="sk-fake-never-called")
        resp = adapter.invoke(
            BrainRequest(
                project_id=policy.project_id,
                worktree_id=uuid4(),
                role="secondary_fallback",
                objective="Plan task",
            )
        )
        self.assertEqual(resp.finish_status, "unavailable")
        self.assertEqual(resp.error_code, "paid_api_fallback_disabled")

    def test_dag_validation_and_revision_hash(self) -> None:
        pid, wid = uuid4(), uuid4()
        policy = BrainPolicyConfig(project_id=pid, daily_planner_budget_usd=0.0)
        plan, rev_hash, resp = build_plan_from_brain(
            project_id=pid,
            worktree_id=wid,
            objective="Write unit tests for runner",
            policy=policy,
            adapter=RuleBrainAdapter(),
            role="tier0_rules",
        )
        self.assertEqual(resp.usage.cost_usd, 0.0)
        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(validate_dag(plan.tasks), ["task-1"])

        # Material change alters revision hash
        plan.tasks[0].assigned_model_id = "other-model"
        self.assertNotEqual(compute_revision_hash(plan), rev_hash)

        # Cycle detection
        t1 = plan.tasks[0].model_copy(update={"task_key": "a", "dependencies": ["b"]})
        t2 = plan.tasks[0].model_copy(update={"task_key": "b", "dependencies": ["a"]})
        with self.assertRaises(InvalidDAGError):
            validate_dag([t1, t2])

    def test_planner_budget_blocks_llm_when_exhausted(self) -> None:
        pid, wid = uuid4(), uuid4()
        policy = BrainPolicyConfig(project_id=pid, daily_planner_budget_usd=1.0)
        with self.assertRaises(PermissionError):
            build_plan_from_brain(
                project_id=pid,
                worktree_id=wid,
                objective="Complex architecture",
                policy=policy,
                adapter=RuleBrainAdapter(),
                role="tier2_primary",
                daily_planner_spent_usd=1.0,
            )


if __name__ == "__main__":
    unittest.main()
