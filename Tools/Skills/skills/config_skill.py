from __future__ import annotations

import json
import os
from typing import Any, Dict

from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
RESOURCE_ROOT = os.path.join(PROJECT_ROOT, "Resource")
PROFILES_PATH = os.path.join(RESOURCE_ROOT, "profiles")
ROOT_CONFIG = os.path.join(RESOURCE_ROOT, "config.json")


def _safe_load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f) if isinstance((d := json.load(f)), dict) else {}
    except Exception:
        return {}


def _active_profile_name() -> str:
    root = _safe_load_json(ROOT_CONFIG)
    return str(root.get("active_profile", "default") or "default")


def _active_profile_path() -> str:
    return os.path.join(PROFILES_PATH, f"{_active_profile_name()}.json")


class ConfigSkill(Skill):
    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="config",
            description="Read and write AiNiee profile configuration.",
            category="config",
            parameters=[
                SkillParameter(
                    name="action",
                    description="Operation: get, set, list_keys.",
                    type="string",
                    required=True,
                    enum=["get", "set", "list_keys"],
                ),
                SkillParameter(
                    name="key",
                    description="Configuration key to read or write.",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="value",
                    description="Value to set (for set action).",
                    type="object",
                    required=False,
                ),
                SkillParameter(
                    name="profile",
                    description="Profile name (defaults to active profile).",
                    type="string",
                    required=False,
                ),
            ],
            examples=[
                {"action": "get", "key": "model"},
                {"action": "get", "key": "target_platform"},
                {"action": "list_keys"},
                {"action": "set", "key": "model", "value": "gpt-4o-mini"},
            ],
        )

    def execute(self, args: Dict[str, Any]) -> SkillResult:
        action = (args.get("action") or "").strip().lower()

        if action == "list_keys":
            profile = args.get("profile") or _active_profile_name()
            path = os.path.join(PROFILES_PATH, f"{profile}.json")
            cfg = _safe_load_json(path)
            return SkillResult.ok({
                "profile": profile,
                "keys": list(cfg.keys()),
            })

        if action == "get":
            key = (args.get("key") or "").strip()
            if not key:
                return SkillResult.fail("Missing required parameter: key", "MISSING_PARAM")
            profile = args.get("profile") or _active_profile_name()
            path = os.path.join(PROFILES_PATH, f"{profile}.json")
            cfg = _safe_load_json(path)
            value = cfg.get(key)
            if value is None:
                # Also try root config
                root = _safe_load_json(ROOT_CONFIG)
                value = root.get(key)
            return SkillResult.ok({
                "profile": profile,
                "key": key,
                "value": value,
                "found": value is not None,
            })

        if action == "set":
            key = (args.get("key") or "").strip()
            if not key:
                return SkillResult.fail("Missing required parameter: key", "MISSING_PARAM")
            if "value" not in args:
                return SkillResult.fail("Missing required parameter: value", "MISSING_PARAM")
            value = args["value"]
            profile = args.get("profile") or _active_profile_name()

            path = os.path.join(PROFILES_PATH, f"{profile}.json")
            cfg = _safe_load_json(path)
            cfg[key] = value
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                return SkillResult.ok({"profile": profile, "key": key, "value": value, "saved": True})
            except OSError as e:
                return SkillResult.fail(f"Failed to write config: {e}", "WRITE_ERROR")

        return SkillResult.fail(f"Unknown config action: {action}", "INVALID_ACTION")
