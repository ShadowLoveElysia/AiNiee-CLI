"""Shared policy for WebServer and MCP network binding."""

from __future__ import annotations


REMOTE_ACCESS_CONFIG_KEY = "enable_remote_access"
LOOPBACK_HOST = "127.0.0.1"
REMOTE_BIND_HOST = "0.0.0.0"


def is_loopback_bind_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    if normalized in {"localhost", "::1"}:
        return True
    try:
        parts = normalized.split(".")
        return (
            len(parts) == 4
            and parts[0] == "127"
            and all(0 <= int(part) <= 255 for part in parts)
        )
    except ValueError:
        return False


def remote_access_enabled(config: dict | None) -> bool:
    return (config or {}).get(REMOTE_ACCESS_CONFIG_KEY) is True


def resolve_bind_host(config: dict | None) -> str:
    return REMOTE_BIND_HOST if remote_access_enabled(config) else LOOPBACK_HOST


def ensure_bind_allowed(host: str, allow_remote_access: bool, service_name: str) -> None:
    if is_loopback_bind_host(host) or allow_remote_access is True:
        return
    raise ValueError(
        f"Remote {service_name} binding is disabled. Enable {REMOTE_ACCESS_CONFIG_KEY} "
        "in TUI settings or use the documented one-run headless override."
    )
