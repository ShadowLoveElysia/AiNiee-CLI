from __future__ import annotations

import os
import sys
from typing import Any, Dict

from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)


class SystemSkill(Skill):
    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="system",
            description="System information and health checks for the AiNiee runtime.",
            category="system",
            parameters=[
                SkillParameter(
                    name="action",
                    description="The system action to perform: info, health, version, or ping.",
                    type="string",
                    required=True,
                    enum=["info", "health", "version", "ping"],
                ),
            ],
            examples=[
                {"action": "info"},
                {"action": "health"},
                {"action": "version"},
                {"action": "ping"},
            ],
        )

    def _load_project_meta(self) -> Dict[str, Any]:
        """Read version/name from pyproject.toml without pulling in a TOML lib at runtime."""
        meta: Dict[str, Any] = {"name": "ainiee-cli", "version": "0.1.0"}
        pyproject = os.path.join(PROJECT_ROOT, "pyproject.toml")
        if not os.path.isfile(pyproject):
            return meta
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                return meta
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            project = data.get("project", {})
            meta["name"] = project.get("name", meta["name"])
            meta["version"] = project.get("version", meta["version"])
        except Exception:
            pass
        return meta

    def execute(self, args: Dict[str, Any]) -> SkillResult:
        action = (args.get("action") or "info").strip().lower()

        if action == "ping":
            return SkillResult.ok({"pong": True})

        if action == "version":
            meta = self._load_project_meta()
            return SkillResult.ok({"version": meta["version"], "name": meta["name"]})

        if action == "health":
            meta = self._load_project_meta()
            resource_root = os.path.join(PROJECT_ROOT, "Resource")
            config_ok = os.path.isfile(os.path.join(resource_root, "config.json"))
            return SkillResult.ok({
                "status": "ok" if config_ok else "degraded",
                "version": meta["version"],
                "python": sys.version.split()[0],
                "platform": sys.platform,
                "config_found": config_ok,
                "project_root": PROJECT_ROOT,
            })

        if action == "info":
            meta = self._load_project_meta()
            resource_root = os.path.join(PROJECT_ROOT, "Resource")
            profiles_dir = os.path.join(resource_root, "profiles")
            profile_count = 0
            if os.path.isdir(profiles_dir):
                profile_count = len([
                    n for n in os.listdir(profiles_dir)
                    if n.endswith(".json")
                ])
            return SkillResult.ok({
                "name": meta["name"],
                "version": meta["version"],
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "profile_count": profile_count,
                "project_root": PROJECT_ROOT,
            })

        return SkillResult.fail(
            f"Unknown system action: {action}",
            code="INVALID_ACTION",
        )
