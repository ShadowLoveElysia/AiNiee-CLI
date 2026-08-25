from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import Any, Dict

from ModuleFolders.Infrastructure.TaskContract import (
    TaskContractError,
    normalize_task_name,
)
from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager, QueueTaskItem
from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult
from Tools.Skills.skills.common import (
    QUEUE_SECURITY_PATH,
    sanitize_payload,
    task_skill_parameters,
    task_spec_from_skill_args,
    task_subprocess_invocation,
)


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)


def _queue_manager() -> QueueManager:
    manager = QueueManager()
    manager.load_tasks()
    return manager


def _queue_manager_for(path: Any = None) -> QueueManager:
    manager = QueueManager()
    manager.load_tasks(str(path)) if path else manager.load_tasks(manager.default_queue_file)
    return manager


def _task_type_label(value: Any) -> str:
    try:
        return normalize_task_name(value)
    except TaskContractError:
        return str(value)


def _task_to_public_dict(index: int, task: QueueTaskItem) -> Dict[str, Any]:
    item = task.to_dict()
    item["index"] = index
    item["task_type"] = _task_type_label(item.get("task_type"))
    return sanitize_payload(item, path=QUEUE_SECURITY_PATH)


def _coerce_index(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("index must be an integer") from exc


class QueueSkill(Skill):
    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="queue",
            description="Manage the translation task queue.",
            category="queue",
            parameters=[
                SkillParameter(
                    name="action",
                    description="Operation: list, add, remove, clear, run.",
                    type="string",
                    required=True,
                    enum=["list", "add", "remove", "clear", "run"],
                ),
                SkillParameter(
                    name="task_type",
                    description="Task type for new queue items (translate/polish/all_in_one).",
                    type="string",
                    required=False,
                ),
                *task_skill_parameters(),
                SkillParameter(
                    name="index",
                    description="Index of the queue item to remove.",
                    type="integer",
                    required=False,
                ),
                SkillParameter(
                    name="queue_file",
                    description="Optional queue JSON path used by all queue actions.",
                    type="string",
                    required=False,
                ),
            ],
            examples=[
                {"action": "list"},
                {
                    "action": "add",
                    "input_path": "/path/to/file.txt",
                    "task_type": "translate",
                    "profile": "default",
                },
                {"action": "remove", "index": 0},
                {"action": "clear"},
            ],
        )

    def execute(self, args: Dict[str, Any]) -> SkillResult:
        action = (args.get("action") or "").strip().lower()

        if action == "list":
            unexpected = sorted(set(args) - {"action", "queue_file"})
            if unexpected:
                return SkillResult.fail(
                    f"Unknown queue list fields: {', '.join(unexpected)}",
                    "INVALID_TASK",
                )
            manager = _queue_manager_for(args.get("queue_file"))
            return SkillResult.ok({
                "queue_file": manager.queue_file,
                "count": len(manager.tasks),
                "items": [
                    _task_to_public_dict(i, task)
                    for i, task in enumerate(manager.tasks)
                ],
            })

        if action == "add":
            try:
                payload = dict(args)
                queue_file = payload.pop("queue_file", None)
                spec = task_spec_from_skill_args(payload)
                if spec.api_key:
                    return SkillResult.fail(
                        "api_key cannot be stored in a queue file. Use a profile or run the task directly.",
                        "INVALID_TASK",
                    )
                task_fields = spec.to_queue_fields()
            except TaskContractError as e:
                return SkillResult.fail(str(e), "INVALID_TASK")

            manager = _queue_manager_for(queue_file)
            item = QueueTaskItem(**task_fields)
            try:
                manager.add_task(item)
            except Exception as e:
                return SkillResult.fail(f"Failed to write queue file: {e}", "WRITE_ERROR")

            index = len(manager.tasks) - 1
            return SkillResult.ok({
                "added": True,
                "queue_file": manager.queue_file,
                "index": index,
                "total": len(manager.tasks),
                "item": _task_to_public_dict(index, item),
            })

        if action == "remove":
            unexpected = sorted(set(args) - {"action", "index", "queue_file"})
            if unexpected:
                return SkillResult.fail(
                    f"Unknown queue remove fields: {', '.join(unexpected)}",
                    "INVALID_TASK",
                )
            if args.get("index") is None:
                return SkillResult.fail("index is required for remove.", "MISSING_PARAM")
            try:
                index = _coerce_index(args.get("index"))
            except ValueError as e:
                return SkillResult.fail(str(e), "INVALID_INDEX")

            manager = _queue_manager_for(args.get("queue_file"))
            if index < 0 or index >= len(manager.tasks):
                return SkillResult.fail(
                    f"Index {index} out of range (0-{len(manager.tasks) - 1}).", "INVALID_INDEX"
                )
            if not manager.can_modify_task(index):
                return SkillResult.fail(
                    f"Queue item {index} is locked and cannot be removed.", "LOCKED"
                )
            removed = _task_to_public_dict(index, manager.tasks[index])
            if manager.remove_task(index):
                return SkillResult.ok({
                    "removed": True,
                    "item": removed,
                    "total": len(manager.tasks),
                })
            return SkillResult.fail("Failed to write queue file.", "WRITE_ERROR")

        if action == "clear":
            unexpected = sorted(set(args) - {"action", "queue_file"})
            if unexpected:
                return SkillResult.fail(
                    f"Unknown queue clear fields: {', '.join(unexpected)}",
                    "INVALID_TASK",
                )
            manager = _queue_manager_for(args.get("queue_file"))
            locked = [
                index
                for index, _task in enumerate(manager.tasks)
                if not manager.can_modify_task(index)
            ]
            if locked:
                return SkillResult.fail(
                    f"Cannot clear queue while locked items exist: {locked}",
                    "LOCKED",
                )
            try:
                manager.clear_tasks()
                return SkillResult.ok({"cleared": True, "queue_file": manager.queue_file})
            except Exception as e:
                return SkillResult.fail(f"Failed to clear queue: {e}", "WRITE_ERROR")

        if action == "run":
            unexpected = sorted(set(args) - {"action", "queue_file"})
            if unexpected:
                return SkillResult.fail(
                    f"Unknown queue run fields: {', '.join(unexpected)}",
                    "INVALID_TASK",
                )
            try:
                spec = task_spec_from_skill_args(
                    {
                        "task_type": "queue",
                        "queue_file": args.get("queue_file"),
                    }
                )
                cli_args, env = task_subprocess_invocation(spec)
                command = [sys.executable, *cli_args]
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                    cwd=PROJECT_ROOT,
                    env=env,
                )
            except TaskContractError as exc:
                return SkillResult.fail(str(exc), "INVALID_TASK")
            except subprocess.TimeoutExpired:
                return SkillResult.fail("Queue execution timed out.", "TIMEOUT")
            except OSError as exc:
                return SkillResult.fail(f"Failed to start queue: {exc}", "RUNTIME_ERROR")

            data = {
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "command": shlex.join(command),
            }
            if result.returncode != 0:
                return SkillResult.fail(
                    f"Queue subprocess failed with exit code {result.returncode}.",
                    "SUBPROCESS_FAILED",
                    data=data,
                )
            return SkillResult.ok(data)

        return SkillResult.fail(f"Unknown queue action: {action}", "INVALID_ACTION")
