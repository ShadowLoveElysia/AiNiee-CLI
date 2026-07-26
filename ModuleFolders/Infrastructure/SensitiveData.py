"""Shared helpers for keeping credentials out of persisted and returned payloads."""

from __future__ import annotations

import copy
import re
from typing import Any


_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "client_secret",
    "private_key",
    "authorization",
    "auth_token",
    "access_token",
    "api_token",
    "password",
    "secret",
}

_SENSITIVE_FIELD_SUFFIXES = tuple(
    f"_{field_name}"
    for field_name in _SENSITIVE_FIELD_NAMES
    if "_" in field_name
)


def _normalize_field_name(name: Any) -> str:
    raw_name = str(name or "").strip()
    # Insert separators before lower-to-upper transitions so APIKey and
    # clientSecret normalize like api_key and client_secret.
    raw_name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw_name)
    raw_name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", raw_name)
    return re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")


def is_sensitive_field(name: Any) -> bool:
    """Return whether a mapping key represents credential material."""
    normalized_name = _normalize_field_name(name)
    return normalized_name in _SENSITIVE_FIELD_NAMES or normalized_name.endswith(
        _SENSITIVE_FIELD_SUFFIXES
    )


def contains_sensitive_data(value: Any) -> bool:
    """Detect credential fields recursively without inspecting ordinary scalar values."""
    if isinstance(value, dict):
        return any(
            is_sensitive_field(key) or contains_sensitive_data(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_data(item) for item in value)
    return False


def split_sensitive_data(value: Any) -> tuple[Any, Any]:
    """
    Split a JSON-like value into a safe payload and a matching credential overlay.

    The safe payload can be persisted. The overlay is intended only for a scoped
    runtime channel such as a child process environment.
    """
    if isinstance(value, dict):
        safe: dict[Any, Any] = {}
        secrets: dict[Any, Any] = {}
        for key, item in value.items():
            if is_sensitive_field(key):
                if item not in (None, "", [], {}):
                    secrets[key] = copy.deepcopy(item)
                continue
            safe_item, secret_item = split_sensitive_data(item)
            safe[key] = safe_item
            if secret_item not in (None, {}, []):
                secrets[key] = secret_item
        return safe, secrets

    if isinstance(value, list):
        safe_items = []
        secret_items: dict[str, Any] = {}
        for index, item in enumerate(value):
            safe_item, secret_item = split_sensitive_data(item)
            safe_items.append(safe_item)
            if secret_item not in (None, {}, []):
                secret_items[str(index)] = secret_item
        return safe_items, secret_items

    if isinstance(value, tuple):
        safe_items, secret_items = split_sensitive_data(list(value))
        return safe_items, secret_items

    return copy.deepcopy(value), None


def sanitize_sensitive_data(value: Any) -> Any:
    """Return a deep-copied payload with all credential fields removed."""
    safe, _ = split_sensitive_data(value)
    return safe


def restore_sensitive_data(safe_value: Any, secret_overlay: Any) -> Any:
    """Reapply an in-memory credential overlay to its sanitized JSON payload."""
    restored = copy.deepcopy(safe_value)
    if not isinstance(secret_overlay, dict):
        return restored

    if isinstance(restored, dict):
        for key, secret_item in secret_overlay.items():
            if is_sensitive_field(key):
                restored[key] = copy.deepcopy(secret_item)
            else:
                restored[key] = restore_sensitive_data(restored.get(key), secret_item)
        return restored

    if isinstance(restored, list):
        for index_text, secret_item in secret_overlay.items():
            try:
                index = int(index_text)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(restored):
                restored[index] = restore_sensitive_data(restored[index], secret_item)
        return restored

    return restored
