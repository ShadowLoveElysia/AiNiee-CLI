from __future__ import annotations

import json
from typing import Any, Optional

from ModuleFolders.Infrastructure.SensitiveData import is_sensitive_field


# MCP 通过内部 Web API 访问项目能力时，会带上这个调用来源标记。
MCP_CALLER_HEADER = "X-AiNiee-Caller"
MCP_CALLER_VALUE = "mcp"
MCP_AUTH_HEADER = "X-AiNiee-Mcp-Auth"
INTERNAL_AUTH_HEADER = "X-AiNiee-Internal-Auth"
INTERNAL_AUTH_ENV = "AINIEE_INTERNAL_API_TOKEN"
AUTH_REQUIRED_HEADER = "X-AiNiee-Auth-Required"
WEB_SESSION_AUTH_REQUIRED = "web-session"
WEB_SESSION_COOKIE_NAME = "ainiee_web_session"

# 对 LLM 暴露时统一使用固定占位符，避免泄漏真实密钥的任何片段。
MCP_SECRET_PLACEHOLDER = "[MCP_SECRET_REDACTED]"
MCP_SECURITY_NOTICE_FIELD = "_mcp_security_notice"
MCP_CONFIG_NOTICE_PATHS = {
    "/api/config",
}
MCP_SECRET_ACCESS_NOTICE = (
    "权限限制：LLM 客户端无权读取用户的 API Key / Access Key / Secret Key。"
    "这些敏感数据仅允许用户本人在带有效 Web 会话的 Web 端查看；MCP 通道会返回脱敏占位符。"
    "敏感 Web API 路由要求有效的 Web UI 会话 cookie 或 MCP bridge token。 "
    "Permission restriction: LLM clients cannot read user secrets. "
    "The user may view them only in the Web UI with a valid Web session; MCP returns redacted placeholders. "
    "Sensitive Web API routes require a valid Web UI session cookie or MCP bridge token."
)
# 这些接口把 JSON 当字符串返回，需要额外尝试解析后再递归脱敏。
JSON_TEXT_PATHS = {
    "/api/queue/raw",
}


def is_mcp_request(request: Any) -> bool:
    """Return whether the current HTTP request originates from the MCP bridge."""
    if request is None:
        return False

    try:
        header_value = str(request.headers.get(MCP_CALLER_HEADER, "")).strip().lower()
    except Exception:
        return False

    return header_value == MCP_CALLER_VALUE


def sanitize_data_for_mcp(
    data: Any,
    *,
    path: str = "",
    field_name: Optional[str] = None,
    inject_notice: bool = True,
) -> Any:
    """Recursively redact secret fields before data is returned to MCP / LLM clients."""
    if is_sensitive_field(field_name):
        return _redact_secret_value(data)

    if isinstance(data, dict):
        sanitized = {
            key: sanitize_data_for_mcp(
                value,
                path=path,
                field_name=key,
                inject_notice=False,
            )
            for key, value in data.items()
        }
        if inject_notice and _should_attach_notice(path, sanitized):
            sanitized[MCP_SECURITY_NOTICE_FIELD] = MCP_SECRET_ACCESS_NOTICE
        return sanitized

    if isinstance(data, list):
        return [
            sanitize_data_for_mcp(
                item,
                path=path,
                field_name=field_name,
                inject_notice=False,
            )
            for item in data
        ]

    if isinstance(data, str) and path in JSON_TEXT_PATHS:
        parsed = _try_parse_json_text(data)
        if parsed is not None:
            sanitized = sanitize_data_for_mcp(parsed, path=path)
            return _dump_json_text(sanitized)

    return data


def restore_redacted_secrets(new_data: Any, current_data: Any = None, *, field_name: Optional[str] = None) -> Any:
    """
    Replace MCP placeholders with the currently stored secrets before saving.

    This prevents a round-trip like "read sanitized config -> edit one field -> save full config"
    from accidentally overwriting real keys with the redacted placeholder.
    """
    if is_sensitive_field(field_name):
        if new_data == MCP_SECRET_PLACEHOLDER:
            # 如果当前不存在旧值，就保留占位符，让上层保存逻辑明确拒绝这次写入。
            return current_data if current_data not in (None, "") else MCP_SECRET_PLACEHOLDER

        if isinstance(new_data, list):
            current_list = current_data if isinstance(current_data, list) else []
            return [
                restore_redacted_secrets(
                    item,
                    current_list[index] if index < len(current_list) else None,
                    field_name=field_name,
                )
                for index, item in enumerate(new_data)
            ]

        return new_data

    if isinstance(new_data, dict):
        current_dict = current_data if isinstance(current_data, dict) else {}
        return {
            key: restore_redacted_secrets(value, current_dict.get(key), field_name=key)
            for key, value in new_data.items()
        }

    if isinstance(new_data, list):
        current_list = current_data if isinstance(current_data, list) else []
        return [
            restore_redacted_secrets(
                item,
                current_list[index] if index < len(current_list) else None,
                field_name=field_name,
            )
            for index, item in enumerate(new_data)
        ]

    return new_data


def contains_redacted_secret(data: Any, *, field_name: Optional[str] = None) -> bool:
    """Detect whether redacted placeholders are still present after restoration."""
    if is_sensitive_field(field_name):
        if data == MCP_SECRET_PLACEHOLDER:
            return True

        if isinstance(data, list):
            return any(
                contains_redacted_secret(item, field_name=field_name)
                for item in data
            )

        return False

    if isinstance(data, dict):
        return any(
            contains_redacted_secret(value, field_name=key)
            for key, value in data.items()
        )

    if isinstance(data, list):
        return any(
            contains_redacted_secret(item, field_name=field_name)
            for item in data
        )

    return False


def sanitize_json_text_for_mcp(content: str) -> str:
    """Sanitize serialized JSON text, preserving plain text when parsing is not possible."""
    parsed = _try_parse_json_text(content)
    if parsed is None:
        return content

    return _dump_json_text(sanitize_data_for_mcp(parsed))


def restore_redacted_json_text(content: str, current_content: str = "") -> str:
    """Restore placeholder secrets inside serialized JSON text from the current stored content."""
    parsed = _try_parse_json_text(content)
    if parsed is None:
        return content

    current_parsed = _try_parse_json_text(current_content)
    restored = restore_redacted_secrets(parsed, current_parsed)
    return _dump_json_text(restored)


def strip_mcp_security_metadata(data: Any) -> Any:
    """Remove MCP-only advisory metadata before persisting user data back to disk."""
    if isinstance(data, dict):
        return {
            key: strip_mcp_security_metadata(value)
            for key, value in data.items()
            if key != MCP_SECURITY_NOTICE_FIELD
        }

    if isinstance(data, list):
        return [
            strip_mcp_security_metadata(item)
            for item in data
        ]

    return data


def _should_attach_notice(path: str, data: Any) -> bool:
    return path in MCP_CONFIG_NOTICE_PATHS and contains_redacted_secret(data)


def _redact_secret_value(value: Any) -> Any:
    if value in (None, "", []):
        return value

    if isinstance(value, list):
        return [
            _redact_secret_value(item)
            for item in value
        ]

    return MCP_SECRET_PLACEHOLDER


def _try_parse_json_text(content: Any) -> Optional[Any]:
    if not isinstance(content, str):
        return None

    stripped = content.lstrip()
    if not stripped or stripped[0] not in "[{":
        return None

    try:
        return json.loads(content)
    except Exception:
        return None


def _dump_json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=4)
