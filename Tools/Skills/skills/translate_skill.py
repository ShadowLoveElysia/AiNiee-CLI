from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)


def _run_ainiee_cli(args: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a CLI subcommand and return the result."""
    cmd = [sys.executable, "-m", "ainiee_cli"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=PROJECT_ROOT,
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
                SkillParameter(
                    name="input_path",
                    description="Path to the input file or directory.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="output_path",
                    description="Output directory path.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="profile",
                    description="Configuration profile name.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="source_lang",
                    description="Source language.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="target_lang",
                    description="Target language.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="project_type",
                    description="Project type (Txt, Epub, MTool, RenPy, etc.).",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="threads",
                    description="Concurrent thread count.",
                    type="integer",
                    required=False,
                ),
                SkillParameter(
                    name="model",
                    description="Model name override.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="platform",
                    description="API platform override.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="resume",
                    description="Resume from cache if available.",
                    type="boolean",
                    required=False,
                    default=False,
                ),
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
        cli_args = [args.get("task_type", "translate")]

        if args.get("input_path"):
            cli_args.append(args["input_path"])
        if args.get("output_path"):
            cli_args.extend(["-o", args["output_path"]])
        if args.get("profile"):
            cli_args.extend(["-p", args["profile"]])
        if args.get("source_lang"):
            cli_args.extend(["-s", args["source_lang"]])
        if args.get("target_lang"):
            cli_args.extend(["-t", args["target_lang"]])
        if args.get("project_type"):
            cli_args.extend(["--type", args["project_type"]])
        if args.get("threads"):
            cli_args.extend(["--threads", str(args["threads"])])
        if args.get("model"):
            cli_args.extend(["--model", args["model"]])
        if args.get("platform"):
            cli_args.extend(["--platform", args["platform"]])
        if args.get("resume"):
            cli_args.append("--resume")

        # Non-interactive mode for automation
        cli_args.append("--yes")

        try:
            result = _run_ainiee_cli(cli_args, timeout=3600)
            return SkillResult.ok({
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "command": "uv run ainiee_cli.py " + " ".join(cli_args),
            })
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
