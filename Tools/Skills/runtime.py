"""Runtime checks for the Skills framework."""

from __future__ import annotations

import importlib.util
import os
from typing import Any, Dict, List


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
SKILLS_ROOT = os.path.join(PROJECT_ROOT, "Tools", "Skills")

REQUIRED_SKILL_FILES = (
    "__init__.py",
    "skill_base.py",
    "server.py",
    "skills/__init__.py",
    "skills/system_skill.py",
    "skills/config_skill.py",
    "skills/translate_skill.py",
    "skills/queue_skill.py",
    "skills/profile_skill.py",
    "skills/file_skill.py",
)


def _module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def inspect_skills_runtime(project_root: str | None = None) -> Dict[str, Any]:
    """Check if the Skills framework is ready to run."""
    resolved_root = os.path.abspath(project_root or PROJECT_ROOT)
    component_root = os.path.join(resolved_root, "Tools", "Skills")

    missing_files = [
        os.path.join(component_root, filename)
        for filename in REQUIRED_SKILL_FILES
        if not os.path.exists(os.path.join(component_root, filename))
    ]

    # Skills framework has zero extra dependencies — everything is stdlib.
    required_modules = {"json", "http.server", "urllib.parse"}
    missing_modules = [
        name for name in required_modules if not _module_exists(name)
    ]

    available = not missing_files and not missing_modules

    return {
        "available": available,
        "project_root": resolved_root,
        "component_root": component_root,
        "missing_files": missing_files,
        "missing_modules": missing_modules,
        "note": "The Skills framework has no Python package dependencies beyond the standard library.",
    }


def format_runtime_status_lines(status: Dict[str, Any]) -> List[str]:
    """Format runtime status into human-readable lines."""
    lines: List[str] = []
    missing_files = status.get("missing_files", [])
    missing_modules = status.get("missing_modules", [])

    if missing_files:
        lines.append("Missing Skills component files:")
        lines.extend(f"  - {path}" for path in missing_files)

    if missing_modules:
        lines.append("Missing standard library modules (unexpected):")
        lines.extend(f"  - {name}" for name in missing_modules)

    if not lines:
        lines.append("AiNiee Skills runtime is ready (no extra dependencies required).")

    return lines
