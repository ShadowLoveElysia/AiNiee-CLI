from __future__ import annotations

import os
from typing import Any, Dict, List

from Tools.MCPServer.security import MCP_SECURITY_NOTICE_FIELD, MCP_SECRET_PLACEHOLDER

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GUIDE_PATH = os.path.join(PROJECT_ROOT, "Tools", "MCPServer", "MCP_CLIENT_GUIDE.md")

DEFAULT_GUIDE = """# AiNiee CLI MCP Client Guide

## Overview

AiNiee CLI MCP exposes most WebServer `/api/*` capabilities as MCP tools.

Recommended first steps for any LLM client:
1. Call `get_mcp_usage_manual`
2. Call `get_mcp_security_policy`
3. Call `get_mcp_tool_categories`
4. Call `get_mcp_tool_catalog(category="<needed-category>")`
5. Then use `call_web_api` or `upload_file`

## Security Policy

- Use MCP tools only for LLM-driven AiNiee operations. Do not mix MCP tool calls with direct Web UI, localhost, LAN WebServer, or MCP HTTP requests.
- Sensitive routes are protected by a Web UI session cookie or the MCP bridge token; bare HTTP requests are rejected by the server.
- Sensitive fields such as `api_key`, `access_key`, and `secret_key` are intentionally redacted for MCP/LLM access.
- MCP config reads may also include a security notice field that explains the channel/auth restriction and redaction behavior.
- If a redacted placeholder is returned, do not treat it as a real secret.
- When changing advanced MCP settings, ask the user for a second confirmation.

## Validation Checklist

1. Read config through MCP and confirm secrets are redacted.
2. Read queue and queue raw content through MCP and confirm secrets are redacted.
3. Change a non-secret setting through MCP and confirm existing secrets are preserved.
4. Attempt to save a redacted placeholder as a new queue secret and confirm the server rejects it.
"""

SECTION_ALIASES = {
    "overview": "Overview",
    "first_steps": "First Steps",
    "security": "Security Policy",
    "security_policy": "Security Policy",
    "core_tools": "Core Tools",
    "calling_patterns": "Calling Patterns",
    "validation": "Validation Checklist",
    "validation_checklist": "Validation Checklist",
}

CATEGORY_DESCRIPTIONS = {
    "system": "System and runtime metadata.",
    "version": "Version information.",
    "config": "Current profile configuration and settings persistence.",
    "profiles": "Profile create/switch/rename/delete operations.",
    "rules_profiles": "Rules profile selection and management.",
    "glossary": "Glossary and terminology data.",
    "prompts": "Prompt template listing, reading, and saving.",
    "plugins": "Plugin status and enable toggles.",
    "platforms": "Translation platform profile creation and provider settings.",
    "task": "Task run / stop / monitor operations.",
    "queue": "Queue list, edit, run, and raw queue JSON operations.",
    "files": "File upload and temporary file management.",
    "proofread": "Proofread flows and proofread status data.",
    "analysis": "Glossary analysis and analysis status data.",
    "term": "Term extraction retry and terminology helper operations.",
    "exclusion": "Exclusion list data.",
    "characterization": "Character notes and persona data.",
    "world_building": "World-building context data.",
    "writing_style": "Writing style guide data.",
    "translation_example": "Translation example data.",
    "draft": "Draft editor data for glossary, exclusion, character, world, style, and examples.",
    "cache": "Cache status, load, item update, and search operations.",
    "manga": "Manga project, page, model, pipeline, editor, and export operations.",
}

EXACT_ROUTE_PURPOSES = {
    "/api/config": "Read or save the active profile configuration.",
    "/api/version": "Read the current application version.",
    "/api/system/mode": "Read the current runtime mode.",
    "/api/profiles": "List available profiles.",
    "/api/profiles/switch": "Switch the active profile.",
    "/api/rules_profiles": "List available rules profiles.",
    "/api/rules_profiles/switch": "Switch the active rules profile.",
    "/api/queue": "Read or modify queue tasks.",
    "/api/queue/raw": "Read or replace the raw queue JSON document.",
    "/api/task/run": "Start a translation / polish / export task.",
    "/api/task/stop": "Stop the current running task.",
    "/api/task/status": "Read live task status, logs, and metrics.",
    "/api/files/upload": "Upload a local file to the project staging area.",
}

PREFIX_ROUTE_PURPOSES = {
    "/api/profiles/": "Manage profiles and profile files.",
    "/api/rules_profiles/": "Manage rules profiles.",
    "/api/prompts/": "List, read, or save prompt files.",
    "/api/glossary": "Read or update glossary content.",
    "/api/plugins": "Read or update plugin configuration.",
    "/api/queue/": "Operate on queue state or queue files.",
    "/api/task/": "Operate on task runtime state.",
    "/api/files/": "Operate on uploaded files or temporary files.",
    "/api/proofread": "Run or inspect proofread operations.",
    "/api/analysis": "Run or inspect analysis operations.",
}


def load_mcp_manual(section: str = "all") -> str:
    """Load the MCP client guide, optionally returning one section."""
    content = _read_guide_text()
    normalized = _normalize_section(section)

    if normalized in ("all", "*"):
        return content

    parsed = _parse_markdown_sections(content)
    target_heading = SECTION_ALIASES.get(normalized, section)

    for heading, body in parsed:
        if heading.lower() == target_heading.lower():
            return f"# AiNiee CLI MCP Client Guide\n\n## {heading}\n\n{body}".strip() + "\n"

    available = ", ".join(sorted(SECTION_ALIASES))
    return (
        f"Section '{section}' was not found.\n"
        f"Available sections: {available}\n\n"
        f"{content}"
    )


def build_security_policy() -> Dict[str, Any]:
    """Return the MCP-side security policy that LLM clients must follow."""
    return {
        "must_do": [
            "Use MCP tools only for LLM-driven AiNiee operations.",
            "Call get_mcp_usage_manual, get_mcp_tool_categories, and then get_mcp_tool_catalog(category=...) before large edits when the client has no file-reading ability.",
            "Ask for a second confirmation before changing advanced MCP settings.",
            "Treat redacted secret placeholders as non-readable and non-usable values.",
            "Treat MCP security notice fields as policy text, not user data to be written back.",
            "Treat the Web UI session cookie and MCP bridge token as the real channel gates for sensitive Web API routes.",
        ],
        "forbidden": [
            "Do not mix MCP tool calls with direct HTTP requests to the Web UI, localhost, LAN WebServer ports, or MCP HTTP endpoints.",
            "Do not try to recover, reconstruct, or infer redacted secrets from placeholders.",
            "Do not save a redacted placeholder as if it were a real API key or cloud secret.",
            "Do not use internal-only routes such as /api/internal/*.",
        ],
        "secret_behavior": {
            "redacted_fields": ["api_key", "access_key", "secret_key"],
            "placeholder": MCP_SECRET_PLACEHOLDER,
            "notice_field": MCP_SECURITY_NOTICE_FIELD,
            "read_rule": "Sensitive secrets are readable only by the user in the Web UI. MCP/LLM reads return redacted values plus a security notice on config payloads.",
            "writeback_rule": "Existing stored secrets are preserved when an MCP write payload still contains the placeholder.",
        },
        "channel_gate": {
            "web_ui": "Sensitive routes require a valid Web UI session cookie.",
            "mcp": "Sensitive MCP proxy calls require the MCP bridge token header.",
            "goal": "Keep browser, MCP, and bare HTTP channels separated by explicit authentication.",
        },
    }


def build_validation_checklist() -> Dict[str, Any]:
    """Return the four security validation scenarios for MCP clients."""
    return {
        "items": [
            {
                "id": 1,
                "title": "Config Redaction",
                "goal": "Read current config through MCP and verify api_key/access_key/secret_key are redacted.",
                "recommended_tools": ["call_web_api"],
                "recommended_calls": [
                    {"tool_name": "call_web_api", "arguments": {"method": "GET", "path": "/api/config"}}
                ],
            },
            {
                "id": 2,
                "title": "Queue Redaction",
                "goal": "Read queue data and queue raw JSON through MCP and verify secrets are redacted.",
                "recommended_tools": ["call_web_api"],
                "recommended_calls": [
                    {"tool_name": "call_web_api", "arguments": {"method": "GET", "path": "/api/queue"}},
                    {"tool_name": "call_web_api", "arguments": {"method": "GET", "path": "/api/queue/raw"}},
                ],
            },
            {
                "id": 3,
                "title": "Non-Secret Save",
                "goal": "Change a non-secret setting through MCP and verify existing secrets remain intact after save.",
                "recommended_tools": ["call_web_api"],
                "recommended_calls": [
                    {"tool_name": "call_web_api", "arguments": {"method": "GET", "path": "/api/config"}},
                    {
                        "tool_name": "call_web_api",
                        "arguments": {
                            "method": "POST",
                            "path": "/api/config",
                            "body": {"model": "gpt-4.1-mini"},
                        },
                    },
                ],
            },
            {
                "id": 4,
                "title": "Placeholder Rejection",
                "goal": "Attempt to save a redacted placeholder as a new queue API key and verify the request is rejected.",
                "recommended_tools": ["call_web_api"],
                "recommended_calls": [
                    {
                        "tool_name": "call_web_api",
                        "arguments": {
                            "method": "POST",
                            "path": "/api/queue",
                            "body": {"api_key": MCP_SECRET_PLACEHOLDER},
                        },
                    }
                ],
            },
        ]
    }


def build_tool_category_index(
    routes: List[Dict[str, str]],
    *,
    route_tools_exposed: bool = False,
) -> Dict[str, Any]:
    """Build a lightweight category index without enumerating every endpoint."""
    route_groups = _group_routes(routes)
    categories = [
        _build_category_index_item(group_name, group_routes)
        for group_name, group_routes in route_groups.items()
    ]

    result = _build_catalog_header(
        catalog_mode="category_index",
        route_tools_exposed=route_tools_exposed,
    )
    result.update(
        {
            "usage": (
                "Choose one category, then call "
                "get_mcp_tool_catalog(category='<category>'). "
                "Avoid category='all' unless the user explicitly needs the full endpoint catalog."
            ),
            "category_count": len(categories),
            "endpoint_count": sum(item["endpoint_count"] for item in categories),
            "route_tool_count": sum(item["endpoint_count"] for item in categories)
            if route_tools_exposed
            else 0,
            "categories": categories,
        }
    )
    return result


def build_tool_catalog(
    routes: List[Dict[str, str]],
    *,
    category: str = "index",
    include_examples: bool = True,
    route_tools_exposed: bool = False,
) -> Dict[str, Any]:
    """Build a structured tool catalog for clients that cannot inspect source files."""
    normalized_category = _normalize_category(category)
    route_groups = _group_routes(routes)

    if _is_category_index_request(normalized_category):
        return build_tool_category_index(
            routes,
            route_tools_exposed=route_tools_exposed,
        )

    if normalized_category not in ("all", "*") and normalized_category not in route_groups:
        result = build_tool_category_index(
            routes,
            route_tools_exposed=route_tools_exposed,
        )
        result["catalog_mode"] = "category_not_found"
        result["error"] = (
            f"Category '{category}' was not found. "
            "Use one of the categories listed below."
        )
        return result

    categories: List[Dict[str, Any]] = []
    for group_name, group_routes in route_groups.items():
        if normalized_category not in ("all", "*") and group_name != normalized_category:
            continue

        endpoints = []
        for route in group_routes:
            endpoints.append(
                _build_endpoint_entry(
                    route,
                    include_examples=include_examples,
                    route_tools_exposed=route_tools_exposed,
                )
            )

        categories.append(
            {
                "category": group_name,
                "description": CATEGORY_DESCRIPTIONS.get(group_name, "Route group."),
                "endpoint_count": len(endpoints),
                "endpoints": endpoints,
            }
        )

    result = _build_catalog_header(
        catalog_mode="category_detail" if normalized_category not in ("all", "*") else "full_catalog",
        route_tools_exposed=route_tools_exposed,
    )
    result.update(
        {
            "usage": (
                "Use the listed route with call_web_api. For templated paths, "
                "fill path_params or pass a fully rendered path."
            ),
            "warning": (
                "This is the full endpoint catalog and can consume a large context window. "
                "Prefer category-specific calls."
            )
            if normalized_category in ("all", "*")
            else "",
            "category_count": len(categories),
            "endpoint_count": sum(category_item["endpoint_count"] for category_item in categories),
            "route_tool_count": sum(category_item["endpoint_count"] for category_item in categories)
            if route_tools_exposed
            else 0,
            "categories": categories,
        }
    )
    return result


def _build_catalog_header(*, catalog_mode: str, route_tools_exposed: bool) -> Dict[str, Any]:
    return {
        "catalog_mode": catalog_mode,
        "route_tools_exposed": route_tools_exposed,
        "recommended_first_calls": [
            "get_mcp_usage_manual",
            "get_mcp_security_policy",
            "get_mcp_tool_categories",
            "get_mcp_tool_catalog(category='<needed-category>')",
        ],
        "core_tools": _build_core_tool_descriptions(route_tools_exposed),
        "security_policy": build_security_policy(),
    }


def get_startup_hint_text() -> str:
    """Short startup hint shown to operators for self-describing MCP clients."""
    return (
        "Guide tools: get_mcp_usage_manual / get_mcp_security_policy / "
        "get_mcp_tool_categories / get_mcp_tool_catalog(category=...) / "
        "get_mcp_validation_checklist"
    )


def get_server_instructions_text() -> str:
    """Instructions returned to MCP clients during server initialization."""
    return (
        "You are connected to AiNiee CLI through MCP. Work through the MCP tools "
        "for LLM-driven AiNiee operations instead of mixing MCP calls with direct "
        "HTTP requests to the Web UI, localhost, LAN WebServer ports, or MCP HTTP "
        "endpoints. Sensitive routes are protected by a Web UI session cookie or "
        "the MCP bridge token; bare HTTP requests are not a supported MCP client "
        "path and are rejected by the server.\n\n"
        "For API work, start small: call get_mcp_tool_categories() to see the "
        "lightweight category index, then open only the relevant group with "
        "get_mcp_tool_catalog(category=\"<needed-category>\"). After choosing a "
        "public /api/* route, call it with call_web_api(method, path, "
        "path_params, query, body). Avoid get_mcp_tool_catalog(category=\"all\") "
        "unless the user explicitly needs the full catalog.\n\n"
        "If you need more guidance, read get_mcp_usage_manual(). Treat "
        f"{MCP_SECRET_PLACEHOLDER} as a redacted placeholder, not a usable "
        "secret, and ask the user for a second confirmation before changing MCP "
        "host or port settings."
    )


def _read_guide_text() -> str:
    try:
        with open(GUIDE_PATH, "r", encoding="utf-8") as handle:
            return handle.read().strip() + "\n"
    except Exception:
        return DEFAULT_GUIDE


def _normalize_section(section: str) -> str:
    return (section or "all").strip().lower().replace(" ", "_")


def _normalize_category(category: str) -> str:
    return (category or "index").strip().lower().replace(" ", "_")


def _is_category_index_request(category: str) -> bool:
    return category in {"", "index", "categories", "category_index", "summary"}


def _parse_markdown_sections(content: str) -> List[tuple[str, str]]:
    sections: List[tuple[str, str]] = []
    current_heading = ""
    current_lines: List[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            if current_heading:
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = line[3:].strip()
            current_lines = []
            continue

        if current_heading:
            current_lines.append(line)

    if current_heading:
        sections.append((current_heading, "\n".join(current_lines).strip()))

    return sections


def _group_routes(routes: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    groups: Dict[str, List[Dict[str, str]]] = {}
    for route in routes:
        category = _route_category(route["path"])
        groups.setdefault(category, []).append(route)

    return dict(sorted(groups.items(), key=lambda item: item[0]))


def _route_category(path: str) -> str:
    stripped = path.strip("/")
    parts = stripped.split("/")
    if len(parts) < 2:
        return "misc"
    return parts[1]


def _describe_route(path: str, method: str) -> str:
    if path in EXACT_ROUTE_PURPOSES:
        return EXACT_ROUTE_PURPOSES[path]

    for prefix, description in PREFIX_ROUTE_PURPOSES.items():
        if path.startswith(prefix):
            return description

    return f"Public MCP proxy for {method.upper()} {path}."


def _build_core_tool_descriptions(route_tools_exposed: bool) -> List[Dict[str, str]]:
    tools = [
        {
            "tool_name": "get_mcp_usage_manual",
            "purpose": "Read the built-in MCP usage manual. Call this first if the client cannot inspect repo files.",
        },
        {
            "tool_name": "get_mcp_security_policy",
            "purpose": "Read the channel-auth and secret-handling policy.",
        },
        {
            "tool_name": "get_mcp_tool_categories",
            "purpose": "Read the lightweight category index before requesting endpoint details.",
        },
        {
            "tool_name": "get_mcp_tool_catalog",
            "purpose": "Read endpoint details for one category. Defaults to the lightweight category index.",
        },
        {
            "tool_name": "get_mcp_validation_checklist",
            "purpose": "Read the four MCP security validation scenarios.",
        },
        {
            "tool_name": "list_web_api_routes",
            "purpose": "Read a compact route index. Pass category='<needed-category>' for one group.",
        },
        {
            "tool_name": "call_web_api",
            "purpose": "Call a public /api/* route through MCP after choosing it from the category catalog.",
        },
        {
            "tool_name": "upload_file",
            "purpose": "Upload a local file through the multipart file endpoint.",
        },
    ]

    if route_tools_exposed:
        tools.append(
            {
                "tool_name": "api_*",
                "purpose": "Compatibility named route tools are enabled for every public WebServer API route.",
            }
        )

    return tools


def _build_category_index_item(group_name: str, routes: List[Dict[str, str]]) -> Dict[str, Any]:
    methods = sorted({route["method"].upper() for route in routes})
    sample_routes = [
        f'{route["method"].upper()} {route["path"]}'
        for route in routes[:3]
    ]

    return {
        "category": group_name,
        "description": CATEGORY_DESCRIPTIONS.get(group_name, "Route group."),
        "endpoint_count": len(routes),
        "methods": methods,
        "sample_routes": sample_routes,
        "detail_call": f"get_mcp_tool_catalog(category='{group_name}')",
    }


def _build_endpoint_entry(
    route: Dict[str, str],
    *,
    include_examples: bool,
    route_tools_exposed: bool,
) -> Dict[str, Any]:
    method = route["method"].upper()
    path = route["path"]
    entry: Dict[str, Any] = {
        "route": f"{method} {path}",
        "method": method,
        "path": path,
        "purpose": _describe_route(path, method),
        "recommended_tool": "call_web_api",
        "how_to_call": _build_call_pattern(route),
        "notes": _build_route_notes(path),
    }
    if route_tools_exposed:
        entry["route_tool_name"] = route["tool_name"]
    if include_examples:
        entry["example_arguments"] = _build_example_args(route)
        entry["call_web_api_example"] = _build_call_web_api_example(route)

    return entry


def _build_call_pattern(route: Dict[str, str]) -> Dict[str, Any]:
    path = route["path"]
    method = route["method"].upper()
    has_path_params = "{" in path and "}" in path
    uses_body = method in {"POST", "PUT", "DELETE"}

    pattern: Dict[str, Any] = {
        "path_params": "required when the route path contains {...}" if has_path_params else "not required",
        "query": "optional URL query parameters",
        "body": "JSON body object" if uses_body else "usually omitted",
    }

    if path == "/api/config":
        pattern["confirm_advanced_change"] = (
            "set to true only after the user explicitly confirms MCP host/port changes"
        )

    return pattern


def _build_call_web_api_example(route: Dict[str, str]) -> Dict[str, Any]:
    method = route["method"].upper()
    path = route["path"]
    example: Dict[str, Any] = {
        "method": method,
        "path": path,
    }

    path_params = _extract_path_params(path)
    if path_params:
        example["path_params"] = {name: "<value>" for name in path_params}

    if method in {"POST", "PUT", "DELETE"}:
        example["body"] = _build_example_body(path)

    return example


def _extract_path_params(path: str) -> List[str]:
    return [
        part.strip("{}")
        for part in path.split("/")
        if part.startswith("{") and part.endswith("}")
    ]


def _build_route_notes(path: str) -> List[str]:
    notes = [
        "Use MCP tools only for LLM-driven operations; do not mix MCP calls with direct Web UI or localhost HTTP requests.",
    ]

    if path == "/api/config":
        notes.append("Secrets are redacted for MCP reads. Saving a non-secret change preserves existing stored secrets.")
    if path.startswith("/api/queue"):
        notes.append("Queue API keys are redacted for MCP reads.")
    if path == "/api/queue/raw":
        notes.append("This route returns serialized JSON text; secret fields inside it are still redacted for MCP.")

    return notes


def _build_example_args(route: Dict[str, str]) -> Dict[str, Any]:
    path = route["path"]
    method = route["method"].upper()
    example: Dict[str, Any] = {}

    if "{" in path and "}" in path:
        example["path_params"] = {name: "<value>" for name in _extract_path_params(path)}

    if method in {"POST", "PUT", "DELETE"}:
        example["body"] = _build_example_body(path)

    return example


def _build_example_body(path: str) -> Any:
    if path == "/api/config":
        return {"target_platform": "openai", "model": "gpt-4o-mini"}
    if path == "/api/profiles/switch":
        return {"profile": "default"}
    if path == "/api/rules_profiles/switch":
        return {"profile": "default"}
    if path == "/api/queue":
        return {
            "task_type": "translate",
            "input_path": "/abs/path/input.txt",
            "output_path": "/abs/path/output",
            "platform": "openai",
            "model": "gpt-4o-mini",
        }
    if path == "/api/task/run":
        return {
            "task": "translate",
            "input_path": "/abs/path/input.txt",
            "output_path": "/abs/path/output",
        }
    return {"example": "fill in the JSON body required by this route"}
