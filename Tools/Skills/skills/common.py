from __future__ import annotations

import copy
import os
from typing import Any, Dict, Tuple

from ModuleFolders.Infrastructure.TaskContract import (
    QUEUE_TASK_OVERRIDE_FIELDS,
    TASK_API_KEY_ENV,
    TaskSpec,
    build_cli_args,
)
from Tools.Skills.skill_base import SkillParameter

from ModuleFolders.Infrastructure.TaskConfig.ConfigProfileService import (
    PROFILES_PATH,
    atomic_write_json,
    get_active_profile_name,
    list_profile_names,
    load_json_file,
    load_root_config,
    resolve_profile_path,
    save_root_config,
)
from Tools.MCPServer.security import (
    contains_redacted_secret,
    restore_redacted_secrets,
    sanitize_data_for_mcp,
    strip_mcp_security_metadata,
)


CONFIG_SECURITY_PATH = "/api/config"
QUEUE_SECURITY_PATH = "/api/queue/raw"


_PARAMETER_DESCRIPTIONS = {
    "input_path": "Path to the input file or directory.",
    "output_path": "Output directory path.",
    "profile": "Configuration profile name.",
    "rules_profile": "Rules profile name.",
    "source_lang": "Source language.",
    "target_lang": "Target language.",
    "project_type": "Project type (Txt, Epub, MTool, RenPy, etc.).",
    "resume": "Resume from cache if available.",
    "platform": "API platform override.",
    "model": "Model name override.",
    "api_url": "API base URL override.",
    "api_key": "Temporary API key override; never added to process argv.",
    "failover": "Enable or disable API failover for this task.",
    "threads": "Concurrent thread count.",
    "retry": "Maximum retry count.",
    "timeout": "Request timeout in seconds.",
    "rounds": "Maximum execution rounds.",
    "pre_lines": "Context lines included before each segment.",
    "lines_limit": "Lines per request; mutually exclusive with tokens_limit.",
    "tokens_limit": "Tokens per request; mutually exclusive with lines_limit.",
    "think_depth": "Reasoning depth name or integer from 0 to 10000.",
    "thinking_budget": "Thinking token budget.",
    "polish_mode": "Polishing mode for polish or all-in-one tasks.",
}
_INTEGER_PARAMETERS = {
    "threads",
    "retry",
    "timeout",
    "rounds",
    "pre_lines",
    "lines_limit",
    "tokens_limit",
    "thinking_budget",
}
_BOOLEAN_PARAMETERS = {"resume", "failover"}
_TASK_FIELD_ORDER = tuple(
    field for field in QUEUE_TASK_OVERRIDE_FIELDS if field != "input_path"
)


def task_skill_parameters(
    *,
    include_manga: bool = False,
    require_input: bool = False,
) -> list[SkillParameter]:
    """由共享协议生成 Translate/Queue Skills 的公共字段目录。"""
    parameters = []
    for name in ("input_path", *_TASK_FIELD_ORDER):
        param_type = "integer" if name in _INTEGER_PARAMETERS else "boolean" if name in _BOOLEAN_PARAMETERS else "string"
        parameters.append(
            SkillParameter(
                name=name,
                description=_PARAMETER_DESCRIPTIONS[name],
                type=param_type,
                required=name == "input_path" and require_input,
            )
        )
    parameters.extend(
        [
            SkillParameter(
                name="lines",
                description="Legacy alias of lines_limit.",
                type="integer",
                required=False,
            ),
            SkillParameter(
                name="tokens",
                description="Legacy alias of tokens_limit.",
                type="integer",
                required=False,
            ),
        ]
    )
    if include_manga:
        parameters.append(
            SkillParameter(
                name="manga",
                description="Run the translate task through MangaCore.",
                type="boolean",
                required=False,
            )
        )
    return parameters


def task_spec_from_skill_args(args: Dict[str, Any]) -> TaskSpec:
    payload = dict(args)
    payload.pop("action", None)
    payload.pop("index", None)
    if "task" not in payload and "task_type" not in payload:
        payload["task_type"] = "translate"
    return TaskSpec.from_mapping(payload)


def task_subprocess_invocation(spec: TaskSpec) -> tuple[list[str], Dict[str, str]]:
    """生成 Skill 子进程 argv/env，确保临时密钥不出现在命令行。"""
    command = ["-m", "ainiee_cli", *build_cli_args(spec, non_interactive=True)]
    env = os.environ.copy()
    env.pop(TASK_API_KEY_ENV, None)
    if spec.api_key:
        env[TASK_API_KEY_ENV] = spec.api_key
    return command, env


def load_dict_json(path: str) -> Dict[str, Any]:
    """Load a JSON object from disk and return an empty dict on invalid data."""
    try:
        data = load_json_file(path, {})
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def active_profile_name() -> str:
    return get_active_profile_name(load_root_config())


def resolve_config_profile_path(profile: Any = None) -> Tuple[str, str]:
    """Resolve a profile file path without allowing path traversal."""
    profile_name = profile or active_profile_name()
    return resolve_profile_path(PROFILES_PATH, profile_name)


def sanitize_config_value(value: Any, key: str) -> Any:
    """Redact a config value when exposing it to Skills clients."""
    return sanitize_data_for_mcp(
        value,
        path=CONFIG_SECURITY_PATH,
        field_name=key,
    )


def sanitize_payload(data: Any, *, path: str = CONFIG_SECURITY_PATH) -> Any:
    return sanitize_data_for_mcp(data, path=path)


def prepare_config_value_for_save(value: Any, current_value: Any, key: str) -> Any:
    """
    Restore redacted secret placeholders before saving.

    This lets a client round-trip sanitized data without overwriting an existing
    API key with "[MCP_SECRET_REDACTED]".
    """
    clean_value = strip_mcp_security_metadata(copy.deepcopy(value))
    restored = restore_redacted_secrets(clean_value, current_value, field_name=key)
    if contains_redacted_secret(restored, field_name=key):
        raise ValueError(
            f"Refusing to save redacted placeholder for sensitive config key: {key}"
        )
    return restored
