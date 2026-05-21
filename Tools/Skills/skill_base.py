from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class SkillError(Exception):
    """Raised when a skill execution fails for a known reason."""

    def __init__(self, message: str, code: str = "SKILL_ERROR") -> None:
        self.code = code
        super().__init__(message)


class SkillResult:
    """Wrapper for skill execution results."""

    def __init__(
        self,
        success: bool,
        data: Any = None,
        error: Optional[str] = None,
        error_code: str = "",
    ) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.error_code = error_code

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"success": self.success}
        if self.data is not None:
            d["data"] = self.data
        if self.error:
            d["error"] = self.error
            d["error_code"] = self.error_code
        return d

    @classmethod
    def ok(cls, data: Any = None) -> SkillResult:
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, message: str, code: str = "SKILL_ERROR") -> SkillResult:
        return cls(success=False, error=message, error_code=code)


@dataclass
class SkillParameter:
    """Describes a single input parameter for a skill."""

    name: str
    description: str = ""
    type: str = "string"  # string / integer / boolean / object / array
    required: bool = False
    default: Any = None
    enum: Optional[List[str]] = None


@dataclass
class SkillMeta:
    """Metadata describing a skill."""

    name: str
    description: str
    category: str = "general"
    parameters: List[SkillParameter] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": [
                {
                    "name": p.name,
                    "description": p.description,
                    "type": p.type,
                    "required": p.required,
                    "default": p.default,
                    "enum": p.enum,
                }
                for p in self.parameters
            ],
            "examples": self.examples,
        }


class Skill(abc.ABC):
    """Base class for all skills."""

    def __init__(self) -> None:
        self._meta: Optional[SkillMeta] = None

    @property
    @abc.abstractmethod
    def meta(self) -> SkillMeta:
        ...

    @abc.abstractmethod
    def execute(self, args: Dict[str, Any]) -> SkillResult:
        ...


class SkillRegistry:
    """Registry of all available skills."""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        name = skill.meta.name
        if name in self._skills:
            raise SkillError(f"Duplicate skill registration: {name}", "DUPLICATE_SKILL")
        self._skills[name] = skill

    def get(self, name: str) -> Skill:
        skill = self._skills.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill: {name}", "UNKNOWN_SKILL")
        return skill

    def list_skills(self) -> List[Dict[str, Any]]:
        return [s.meta.to_dict() for s in self._skills.values()]

    def get_skill_meta(self, name: str) -> Dict[str, Any]:
        return self.get(name).meta.to_dict()

    def execute(self, name: str, args: Dict[str, Any]) -> SkillResult:
        skill = self.get(name)
        return skill.execute(args)

    @property
    def count(self) -> int:
        return len(self._skills)
