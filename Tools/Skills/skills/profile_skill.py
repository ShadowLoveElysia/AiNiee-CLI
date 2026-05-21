from __future__ import annotations

import json
import os
from typing import Any, Dict, List

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


def _save_root_config(cfg: Dict[str, Any]) -> bool:
    try:
        with open(ROOT_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


class ProfileSkill(Skill):
    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="profile",
            description="Manage AiNiee configuration profiles.",
            category="config",
            parameters=[
                SkillParameter(
                    name="action",
                    description="Operation: list, switch, create, delete, current.",
                    type="string",
                    required=True,
                    enum=["list", "switch", "create", "delete", "current"],
                ),
                SkillParameter(
                    name="name",
                    description="Profile name (for switch/create/delete).",
                    type="string",
                    required=False,
                ),
                SkillParameter(
                    name="base",
                    description="Base profile to copy from (for create).",
                    type="string",
                    required=False,
                ),
            ],
            examples=[
                {"action": "list"},
                {"action": "current"},
                {"action": "switch", "name": "my-profile"},
                {"action": "create", "name": "new-profile", "base": "default"},
                {"action": "delete", "name": "old-profile"},
            ],
        )

    def _list_profiles(self) -> List[str]:
        if not os.path.isdir(PROFILES_PATH):
            return []
        return sorted([
            n[:-5] for n in os.listdir(PROFILES_PATH)
            if n.endswith(".json")
        ])

    def execute(self, args: Dict[str, Any]) -> SkillResult:
        action = (args.get("action") or "").strip().lower()

        if action == "list":
            profiles = self._list_profiles()
            root = _safe_load_json(ROOT_CONFIG)
            active = root.get("active_profile", "default")
            return SkillResult.ok({
                "profiles": profiles,
                "active": active,
                "count": len(profiles),
            })

        if action == "current":
            root = _safe_load_json(ROOT_CONFIG)
            active = root.get("active_profile", "default")
            rules_active = root.get("active_rules_profile", "default")
            return SkillResult.ok({
                "active_profile": active,
                "active_rules_profile": rules_active,
            })

        if action == "switch":
            name = (args.get("name") or "").strip()
            if not name:
                return SkillResult.fail("Missing required parameter: name", "MISSING_PARAM")
            profiles = self._list_profiles()
            if name not in profiles:
                return SkillResult.fail(
                    f"Profile '{name}' not found. Available: {', '.join(profiles)}",
                    "NOT_FOUND",
                )
            root = _safe_load_json(ROOT_CONFIG)
            root["active_profile"] = name
            if _save_root_config(root):
                return SkillResult.ok({
                    "switched": True,
                    "profile": name,
                    "previous": root.get("active_profile"),
                })
            return SkillResult.fail("Failed to save config.", "WRITE_ERROR")

        if action == "create":
            name = (args.get("name") or "").strip()
            if not name:
                return SkillResult.fail("Missing required parameter: name", "MISSING_PARAM")
            profile_path = os.path.join(PROFILES_PATH, f"{name}.json")
            if os.path.exists(profile_path):
                return SkillResult.fail(f"Profile '{name}' already exists.", "ALREADY_EXISTS")

            base = (args.get("base") or "default").strip()
            base_path = os.path.join(PROFILES_PATH, f"{base}.json")
            if os.path.isfile(base_path):
                try:
                    with open(base_path, "r", encoding="utf-8") as f:
                        base_data = json.load(f)
                    with open(profile_path, "w", encoding="utf-8") as f:
                        json.dump(base_data or {}, f, ensure_ascii=False, indent=2)
                except (OSError, json.JSONDecodeError) as e:
                    return SkillResult.fail(f"Failed to create profile: {e}", "CREATE_ERROR")
            else:
                # Create empty profile
                try:
                    with open(profile_path, "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)
                except OSError as e:
                    return SkillResult.fail(f"Failed to create profile: {e}", "CREATE_ERROR")

            return SkillResult.ok({
                "created": True,
                "profile": name,
                "based_on": base if os.path.isfile(base_path) else None,
            })

        if action == "delete":
            name = (args.get("name") or "").strip()
            if not name:
                return SkillResult.fail("Missing required parameter: name", "MISSING_PARAM")
            if name == "default":
                return SkillResult.fail("Cannot delete the default profile.", "PROTECTED")

            profile_path = os.path.join(PROFILES_PATH, f"{name}.json")
            if not os.path.isfile(profile_path):
                return SkillResult.fail(f"Profile '{name}' not found.", "NOT_FOUND")

            root = _safe_load_json(ROOT_CONFIG)
            if root.get("active_profile") == name:
                root["active_profile"] = "default"
                _save_root_config(root)

            try:
                os.remove(profile_path)
                return SkillResult.ok({"deleted": True, "profile": name})
            except OSError as e:
                return SkillResult.fail(f"Failed to delete profile: {e}", "DELETE_ERROR")

        return SkillResult.fail(f"Unknown profile action: {action}", "INVALID_ACTION")
