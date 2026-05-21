from __future__ import annotations

import os
from typing import Any, Dict

from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)


class FileSkill(Skill):
    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="file",
            description="File discovery and staging for translation tasks.",
            category="files",
            parameters=[
                SkillParameter(
                    name="action",
                    description="Operation: list, info, upload_path.",
                    type="string",
                    required=True,
                    enum=["list", "info", "upload_path"],
                ),
                SkillParameter(
                    name="path",
                    description="Directory path (for list) or file path (for info).",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="pattern",
                    description="Glob pattern for file filtering (e.g., '*.txt', '*.epub').",
                    type="string",
                    required=False,
                    default="*",
                ),
            ],
            examples=[
                {"action": "list", "path": "/path/to/input", "pattern": "*.txt"},
                {"action": "info", "path": "/path/to/file.txt"},
                {"action": "upload_path", "path": "/path/to/file.txt"},
            ],
        )

    def execute(self, args: Dict[str, Any]) -> SkillResult:
        action = (args.get("action") or "").strip().lower()

        if action == "list":
            import glob as glob_module

            path = args.get("path") or "."
            pattern = args.get("pattern") or "*"
            search = os.path.join(path, pattern) if os.path.isdir(path) else path
            files = sorted(glob_module.glob(search))
            result = []
            for f in files:
                stat = os.stat(f)
                result.append({
                    "name": os.path.basename(f),
                    "path": os.path.abspath(f),
                    "size": stat.st_size,
                    "is_dir": os.path.isdir(f),
                    "ext": os.path.splitext(f)[1].lower(),
                })
            return SkillResult.ok({
                "count": len(result),
                "files": result,
                "search_path": search,
            })

        if action == "info":
            path = args.get("path", "")
            if not path:
                return SkillResult.fail("Missing required parameter: path", "MISSING_PARAM")
            if not os.path.exists(path):
                return SkillResult.fail(f"Path not found: {path}", "NOT_FOUND")
            stat = os.stat(path)
            supported_exts = {
                ".txt", ".epub", ".docx", ".srt", ".ass", ".vtt", ".lrc",
                ".json", ".po", ".xlsx", ".csv", ".mobi", ".azw3", ".fb2",
                ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".cbz", ".cbr",
                ".html", ".htm", ".md", ".xml", ".yaml", ".yml", ".ts", ".srt",
            }
            ext = os.path.splitext(path)[1].lower()
            return SkillResult.ok({
                "name": os.path.basename(path),
                "path": os.path.abspath(path),
                "size": stat.st_size,
                "is_dir": os.path.isdir(path),
                "ext": ext,
                "supported": ext in supported_exts,
            })

        if action == "upload_path":
            """Return the project staging path suggestion for a file."""
            path = args.get("path", "")
            if not path:
                return SkillResult.fail("Missing required parameter: path", "MISSING_PARAM")
            if not os.path.exists(path):
                return SkillResult.fail(f"Path not found: {path}", "NOT_FOUND")

            return SkillResult.ok({
                "local_path": os.path.abspath(path),
                "note": "Use this path as input_path for translate skill or queue skill.",
            })

        return SkillResult.fail(f"Unknown file action: {action}", "INVALID_ACTION")
