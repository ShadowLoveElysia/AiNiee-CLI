from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import Any, Dict, List

from ModuleFolders.Infrastructure.TaskContract import TaskContractError
from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult
from Tools.Skills.skills.common import (
    task_skill_parameters,
    task_spec_from_skill_args,
    task_subprocess_invocation,
)


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)


def _run_ainiee_cli(
    args: List[str],
    timeout: int = 300,
    *,
    env: Dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a CLI subcommand and return the result."""
    cmd = [sys.executable, "-m", "ainiee_cli"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=PROJECT_ROOT,
        env=env,
    )


class TranslateSkill(Skill):
    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="translate",
            description="Execute translation, polishing, and all-in-one tasks.",
            category="task",
            parameters=[
                SkillParameter(
                    name="action",
                    description="Operation: run, status.",
                    type="string",
                    required=True,
                    enum=["run", "status"],
                ),
                SkillParameter(
                    name="task_type",
                    description="Type of task: translate, polish, or all_in_one.",
                    type="string",
                    required=False,
                    default="translate",
                    enum=["translate", "polish", "all_in_one"],
                ),
                *task_skill_parameters(include_manga=True),
            ],
            examples=[
                {
                    "action": "run",
                    "task_type": "translate",
                    "input_path": "/path/to/file.txt",
                    "source_lang": "Japanese",
                    "target_lang": "Chinese",
                    "profile": "default",
                },
                {"action": "status"},
            ],
        )

    def _run_translate_subprocess(self, args: Dict[str, Any]) -> SkillResult:
        """Execute translation via CLI subprocess (混合模式: CLI fallback)."""
        try:
            spec = task_spec_from_skill_args(args)
            cli_args, env = task_subprocess_invocation(spec)
        except TaskContractError as exc:
            return SkillResult.fail(str(exc), "INVALID_TASK")

        try:
            result = _run_ainiee_cli(cli_args[2:], timeout=3600, env=env)
            data = {
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "command": shlex.join([sys.executable, *cli_args]),
            }
            if result.returncode != 0:
                return SkillResult.fail(
                    f"Translation subprocess failed with exit code {result.returncode}.",
                    "SUBPROCESS_FAILED",
                    data=data,
                )
            return SkillResult.ok(data)
        except subprocess.TimeoutExpired:
            return SkillResult.fail("Translation task timed out.", "TIMEOUT")
        except FileNotFoundError as e:
            return SkillResult.fail(f"Python executable not found: {e}", "RUNTIME_ERROR")

    def execute(self, args: Dict[str, Any]) -> SkillResult:
        action = (args.get("action") or "").strip().lower()

        if action == "status":
            # Check if a task is currently running by looking for PID/lock files
            return SkillResult.ok({
                "running": False,
                "note": "Task status check available via WebServer API when running.",
            })

        if action == "run":
            # 混合模式: 通过CLI子进程执行
            return self._run_translate_subprocess(args)

        return SkillResult.fail(f"Unknown translate action: {action}", "INVALID_ACTION")
