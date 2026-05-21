from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
QUEUE_FILE = os.path.join(PROJECT_ROOT, "queue.json")


def _load_queue() -> List[Dict[str, Any]]:
    if not os.path.isfile(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_queue(queue: List[Dict[str, Any]]) -> bool:
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


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
                SkillParameter(
                    name="input_path",
                    description="Input file path for new queue item.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="output_path",
                    description="Output path for new queue item.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="profile",
                    description="Profile name for queue item.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="index",
                    description="Index of the queue item to remove.",
                    type="integer",
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
            queue = _load_queue()
            return SkillResult.ok({
                "count": len(queue),
                "items": [
                    {
                        "index": i,
                        "task_type": item.get("task_type", "unknown"),
                        "input_path": item.get("input_path", ""),
                        "output_path": item.get("output_path", ""),
                        "profile": item.get("profile", ""),
                    }
                    for i, item in enumerate(queue)
                ],
            })

        if action == "add":
            item: Dict[str, Any] = {}
            if args.get("task_type"):
                item["task_type"] = args["task_type"]
            if args.get("input_path"):
                item["input_path"] = args["input_path"]
            if args.get("output_path"):
                item["output_path"] = args["output_path"]
            if args.get("profile"):
                item["profile"] = args["profile"]

            if not item.get("input_path"):
                return SkillResult.fail(
                    "input_path is required for queue items.", "MISSING_PARAM"
                )

            queue = _load_queue()
            queue.append(item)
            if _save_queue(queue):
                return SkillResult.ok({
                    "added": True,
                    "index": len(queue) - 1,
                    "total": len(queue),
                })
            return SkillResult.fail("Failed to write queue file.", "WRITE_ERROR")

        if action == "remove":
            index = args.get("index")
            if index is None:
                return SkillResult.fail("index is required for remove.", "MISSING_PARAM")
            queue = _load_queue()
            if index < 0 or index >= len(queue):
                return SkillResult.fail(
                    f"Index {index} out of range (0-{len(queue) - 1}).", "INVALID_INDEX"
                )
            removed = queue.pop(index)
            if _save_queue(queue):
                return SkillResult.ok({
                    "removed": True,
                    "item": removed,
                    "total": len(queue),
                })
            return SkillResult.fail("Failed to write queue file.", "WRITE_ERROR")

        if action == "clear":
            if _save_queue([]):
                return SkillResult.ok({"cleared": True})
            return SkillResult.fail("Failed to clear queue.", "WRITE_ERROR")

        if action == "run":
            return SkillResult.ok({
                "note": "Queue execution is available via CLI: uv run ainiee_cli.py queue --yes",
                "command": f"cd {PROJECT_ROOT} && uv run ainiee_cli.py queue --yes",
            })

        return SkillResult.fail(f"Unknown queue action: {action}", "INVALID_ACTION")
