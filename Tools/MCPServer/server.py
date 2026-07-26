from __future__ import annotations

import argparse
import atexit
import inspect
import json
import os
import re
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

RESOURCE_ROOT = os.path.join(PROJECT_ROOT, "Resource")
ROOT_CONFIG_FILE = os.path.join(RESOURCE_ROOT, "config.json")
PROFILES_PATH = os.path.join(RESOURCE_ROOT, "profiles")

from Tools.MCPServer.runtime import inspect_mcp_runtime
from Tools.MCPServer.docs import (
    build_security_policy,
    build_tool_category_index,
    build_tool_catalog,
    build_validation_checklist,
    get_server_instructions_text,
    load_mcp_manual,
)
from Tools.MCPServer.security import (
    MCP_AUTH_HEADER,
    MCP_CALLER_HEADER,
    MCP_CALLER_VALUE,
    sanitize_data_for_mcp,
)
from ModuleFolders.Infrastructure.RemoteAccessPolicy import (
    ensure_bind_allowed,
    is_loopback_bind_host,
    remote_access_enabled,
    resolve_bind_host,
)


def _safe_load_json(path: str) -> Dict[str, Any]:
    """Load a JSON file when it exists, otherwise return an empty dict."""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_project_mcp_defaults() -> Dict[str, Any]:
    """
    Resolve MCP defaults from the active project profile when available.

    This matters for stdio launchers: if the user changed `mcp_server_port` in
    project settings, the launcher should probe that same running MCP service
    instead of assuming the hard-coded default port.
    """
    root_config = _safe_load_json(ROOT_CONFIG_FILE)
    active_profile = str(root_config.get("active_profile", "default") or "default")
    profile_path = os.path.join(PROFILES_PATH, f"{active_profile}.json")
    profile_config = _safe_load_json(profile_path)

    merged = {}
    merged.update(root_config)
    merged.update(profile_config)
    return merged


def _resolve_int_setting(config: Dict[str, Any], key: str, fallback: int) -> int:
    try:
        value = config.get(key, fallback)
        return int(value if value not in (None, "") else fallback)
    except Exception:
        return fallback


PROJECT_MCP_DEFAULTS = _load_project_mcp_defaults()

DEFAULT_MCP_HOST = os.environ.get(
    "AINIEE_MCP_HOST",
    resolve_bind_host(PROJECT_MCP_DEFAULTS),
)
DEFAULT_MCP_PORT = int(
    os.environ.get(
        "AINIEE_MCP_PORT",
        str(_resolve_int_setting(PROJECT_MCP_DEFAULTS, "mcp_server_port", 8765)),
    )
)
DEFAULT_MCP_PATH = os.environ.get(
    "AINIEE_MCP_PATH",
    str(PROJECT_MCP_DEFAULTS.get("mcp_server_path", "/mcp") or "/mcp"),
)
DEFAULT_BACKEND_HOST = os.environ.get(
    "AINIEE_MCP_BACKEND_HOST",
    str(PROJECT_MCP_DEFAULTS.get("mcp_backend_host", "127.0.0.1") or "127.0.0.1"),
)
DEFAULT_BACKEND_PORT = int(
    os.environ.get(
        "AINIEE_MCP_BACKEND_PORT",
        str(_resolve_int_setting(PROJECT_MCP_DEFAULTS, "mcp_backend_port", 18000)),
    )
)
DEFAULT_MCP_AUTH_TOKEN = os.environ.get("AINIEE_MCP_AUTH_TOKEN", "")
DEFAULT_REGISTER_ROUTE_TOOLS = (
    os.environ.get("AINIEE_MCP_REGISTER_ROUTE_TOOLS", "").strip().lower()
    in {"1", "true", "yes", "on"}
)


def _is_loopback_bind_host(host: str) -> bool:
    return is_loopback_bind_host(host)


def _mcp_tool_doc(summary: str, details: str = "") -> str:
    parts = [summary.strip(), get_server_instructions_text().strip()]
    if details.strip():
        parts.append(details.strip())
    return "\n\n".join(parts)


def _mcp_tool(mcp, summary: str, details: str = ""):
    def decorator(func):
        func.__doc__ = _mcp_tool_doc(summary, details)
        return mcp.tool()(func)

    return decorator


def _t_from_host(host_cli: Any, key: str, default: str) -> str:
    """Read an i18n string from the host CLI when available."""
    i18n = getattr(host_cli, "i18n", None)
    if i18n is None:
        return default

    try:
        value = i18n.get(key)
    except Exception:
        return default

    return default if not value or value == key else value


def _tf_from_host(host_cli: Any, key: str, default: str, **kwargs: Any) -> str:
    """Format a translated string with named placeholders."""
    template = _t_from_host(host_cli, key, default)
    try:
        return template.format(**kwargs)
    except Exception:
        return default.format(**kwargs)


class EmbeddedWebServerController:
    def __init__(
        self,
        host: str,
        port: int,
        host_cli: Any = None,
        startup_timeout: float = 8.0,
        log_level: str = "info",
        mcp_auth_token: str = "",
        allow_remote_access: bool = False,
    ):
        self.host = host
        self.port = port
        self.host_cli = host_cli
        self.startup_timeout = startup_timeout
        self.log_level = log_level
        self.mcp_auth_token = mcp_auth_token
        self.allow_remote_access = allow_remote_access
        self.started_by_self = False
        self.thread = None
        self.ws_module = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self.mcp_auth_token:
            os.environ["AINIEE_MCP_AUTH_TOKEN"] = self.mcp_auth_token

        import Tools.WebServer.web_server as ws_module

        self.ws_module = ws_module
        if self.mcp_auth_token:
            # WebServer may have been imported before MCP generated its bridge token.
            ws_module.MCP_AUTH_TOKEN = self.mcp_auth_token
        if self.host_cli is not None:
            try:
                self.host_cli.web_runtime_bridge._configure_web_handlers(ws_module)
            except Exception:
                pass

        if _is_port_open(self.host, self.port):
            self._verify_reused_backend_access()
            return

        self.thread = ws_module.run_server(
            host=self.host,
            port=self.port,
            monitor_mode=False,
            log_level=self.log_level,
            allow_remote_access=self.allow_remote_access,
        )
        self.started_by_self = self.thread is not None

        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if _is_port_open(self.host, self.port):
                return
            current_server = getattr(self.ws_module, "_current_server", None)
            if current_server is not None and getattr(current_server, "is_running", False):
                return
            if self.thread is not None and not self.thread.is_alive():
                break
            time.sleep(0.2)

        raise RuntimeError(
            _tf_from_host(
                self.host_cli,
                "msg_mcp_embedded_web_start_failed",
                "Embedded WebServer failed to start on {host}:{port}.",
                host=self.host,
                port=self.port,
            )
        )

    def _verify_reused_backend_access(self) -> None:
        """Verify that an already-running backend accepts this MCP bridge token."""
        import requests

        try:
            response = requests.get(
                f"{self.base_url}/api/config",
                headers={
                    MCP_CALLER_HEADER: MCP_CALLER_VALUE,
                    MCP_AUTH_HEADER: self.mcp_auth_token,
                },
                timeout=min(max(self.startup_timeout, 0.5), 5.0),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to verify the existing WebServer MCP bridge token at {self.base_url}."
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                "The existing WebServer rejected the MCP bridge token. "
                "Start both processes with the same AINIEE_MCP_AUTH_TOKEN or use a free backend port."
            )

    def stop(self) -> None:
        if not self.started_by_self or self.ws_module is None:
            return
        try:
            self.ws_module.stop_server()
        except Exception:
            pass


class AiNieeAPIClient:
    def __init__(self, base_url: str, timeout: float = 20.0, mcp_auth_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.mcp_auth_token = mcp_auth_token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Any] = None,
    ) -> Any:
        import requests

        response = requests.request(
            method=method.upper(),
            url=f"{self.base_url}{path}",
            params=params,
            json=payload,
            headers={
                MCP_CALLER_HEADER: MCP_CALLER_VALUE,
                MCP_AUTH_HEADER: self.mcp_auth_token,
            },
            timeout=self.timeout,
        )

        try:
            data = response.json()
        except Exception:
            data = response.text

        if response.status_code >= 400:
            raise RuntimeError(f"{response.status_code} {path}: {data}")

        # 再做一层兜底脱敏，避免未来新增接口忘记在 WebServer 里声明 MCP 侧限制。
        return sanitize_data_for_mcp(data, path=path)


def _patch_streamable_http_shutdown_for_windows() -> None:
    """
    Reduce noisy ASGI shutdown errors for active MCP SSE streams on Windows.

    When the operator stops the streamable-http MCP server while a client still
    has an active GET/POST stream pair, uvicorn can log
    "ASGI callable returned without completing response" during teardown. We
    patch the session manager at runtime so active transports are explicitly
    terminated before the upstream task group is cancelled.
    """
    if os.name != "nt":
        return

    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    if getattr(StreamableHTTPSessionManager.run, "__ainiee_shutdown_patch__", False):
        return

    original_run = StreamableHTTPSessionManager.run

    @contextlib.asynccontextmanager
    async def patched_run(self):
        async with original_run(self):
            try:
                yield
            finally:
                for transport in list(getattr(self, "_server_instances", {}).values()):
                    with contextlib.suppress(Exception):
                        await transport.terminate()

    patched_run.__ainiee_shutdown_patch__ = True
    StreamableHTTPSessionManager.run = patched_run


def _normalize_transport(transport: str) -> str:
    value = (transport or "stdio").strip().lower()
    aliases = {
        "http": "streamable-http",
        "streamable_http": "streamable-http",
        "streamable-http": "streamable-http",
        "sse": "sse",
        "stdio": "stdio",
    }
    return aliases.get(value, value)


def _normalize_client_probe_host(host: str) -> str:
    """Convert wildcard listen hosts into a concrete loopback address for client probes."""
    value = (host or "").strip()
    if value in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return value


def _normalize_http_path(path: str) -> str:
    value = (path or "/mcp").strip()
    return value if value.startswith("/") else f"/{value}"


def _build_mcp_service_url(host: str, port: int, path: str) -> str:
    probe_host = _normalize_client_probe_host(host)
    return f"http://{probe_host}:{port}{_normalize_http_path(path)}"


def _write_startup_notice(message: str) -> None:
    """Emit lightweight startup diagnostics to stderr without polluting MCP stdout."""
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


def _extract_probe_response_payload(response_text: str, content_type: str) -> Any:
    """Decode either JSON or single-message SSE probe responses into a Python object."""
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type == "application/json":
        return json.loads(response_text)

    if normalized_type == "text/event-stream":
        data_lines = []
        for line in response_text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            return json.loads("\n".join(data_lines))

    raise ValueError(f"Unsupported MCP probe response content type: {content_type}")


async def _probe_streamable_http_mcp(url: str) -> bool:
    """
    Verify that an existing HTTP endpoint is actually an MCP server.

    We do a lightweight initialize round-trip instead of trusting only "port is
    open", so unrelated services on the same port do not get treated as MCP.
    This probe intentionally stays at raw HTTP level and skips the follow-up
    `notifications/initialized` exchange, which avoids noisy SSE teardown logs
    on the already-running MCP service.
    """
    import contextlib
    import httpx
    from mcp.types import LATEST_PROTOCOL_VERSION

    client = httpx.AsyncClient(timeout=httpx.Timeout(2.0, read=2.0))
    session_id = ""
    try:
        response = await client.post(
            url,
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": "ainiee-cli-reuse-probe",
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "ainiee-cli-reuse-probe",
                        "version": "1.0.0",
                    },
                },
            },
        )
        session_id = response.headers.get("mcp-session-id", "")
        if response.status_code >= 400:
            return False

        data = _extract_probe_response_payload(
            response.text,
            response.headers.get("content-type", ""),
        )
        if not isinstance(data, dict):
            return False

        result = data.get("result")
        if not isinstance(result, dict):
            return False

        protocol_version = result.get("protocolVersion")
        server_info = result.get("serverInfo")
        return bool(protocol_version and isinstance(server_info, dict) and server_info.get("name"))
    except Exception:
        return False
    finally:
        if session_id:
            with contextlib.suppress(Exception):
                await client.delete(url, headers={"mcp-session-id": session_id})
        await client.aclose()


def _is_expected_proxy_disconnect(exc: BaseException) -> bool:
    """
    Treat common transport teardown errors as a normal client disconnect.

    On Windows, short-lived MCP clients may close their stdio pipe or HTTP
    stream before every async task has fully unwound. Those disconnects are
    expected during tool discovery and should not become noisy tracebacks.
    """
    if exc.__class__.__name__ in {"ClosedResourceError", "BrokenResourceError", "EndOfStream"}:
        return True

    if isinstance(exc, (BrokenPipeError, ConnectionResetError, EOFError)):
        return True

    cause = getattr(exc, "__cause__", None)
    return isinstance(cause, BaseException) and _is_expected_proxy_disconnect(cause)


async def _close_stream_safely(stream) -> None:
    """Close an anyio stream without surfacing teardown noise during proxy shutdown."""
    try:
        await stream.aclose()
    except Exception:
        pass


async def _pipe_session_messages(source, sink, direction: str) -> None:
    """Forward MCP SessionMessage objects between stdio and streamable-http transports."""
    async for item in source:
        if isinstance(item, Exception):
            if _is_expected_proxy_disconnect(item):
                return
            raise RuntimeError(f"MCP proxy stream error ({direction}): {item}") from item
        await sink.send(item)


async def _run_stdio_proxy_to_existing_mcp(url: str) -> None:
    """
    Bridge a stdio MCP client to an already running streamable-http MCP service.

    Some LLM clients eagerly spawn the configured MCP process on startup. When an
    AiNiee MCP HTTP service is already running, reusing it avoids duplicate
    backend startup and keeps all clients attached to the same MCP runtime.
    """
    import anyio
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (local_read, local_write):
        async with streamable_http_client(url, terminate_on_close=True) as (
            remote_read,
            remote_write,
            _,
        ):
            async def bridge_local_to_remote() -> None:
                try:
                    await _pipe_session_messages(local_read, remote_write, "stdio -> http")
                except Exception as exc:
                    if not _is_expected_proxy_disconnect(exc):
                        raise
                finally:
                    # Let the HTTP transport unwind naturally so terminate_on_close
                    # can send DELETE /mcp instead of forcing a TCP reset.
                    await _close_stream_safely(remote_write)

            async def bridge_remote_to_local(cancel_scope) -> None:
                try:
                    await _pipe_session_messages(remote_read, local_write, "http -> stdio")
                except Exception as exc:
                    if not _is_expected_proxy_disconnect(exc):
                        raise
                finally:
                    await _close_stream_safely(local_write)
                    await _close_stream_safely(remote_write)
                    cancel_scope.cancel()

            async with anyio.create_task_group() as tg:
                tg.start_soon(bridge_local_to_remote)
                tg.start_soon(bridge_remote_to_local, tg.cancel_scope)


def is_reusable_mcp_service_running(host: str, port: int, path: str) -> bool:
    """
    Check whether a reusable streamable-http MCP service is already serving this route.

    This is shared by the stdio launcher and the menu runtime bridge so both code
    paths make the same decision about "already running" state.
    """
    reuse_url = _build_mcp_service_url(host, port, path)
    probe_host = _normalize_client_probe_host(host)
    if not _is_port_open(probe_host, port):
        return False

    import anyio

    return bool(anyio.run(_probe_streamable_http_mcp, reuse_url))


def _try_get_reusable_mcp_service_url(transport: str, host: str, port: int, path: str) -> str | None:
    """
    Return a reusable MCP HTTP endpoint for stdio launchers when one is already running.

    Only stdio launchers reuse an existing MCP service. HTTP/SSE launches are
    the service itself and should continue following their normal startup path.
    """
    if _normalize_transport(transport) != "stdio":
        return None

    if os.environ.get("AINIEE_MCP_DISABLE_RUNNING_REUSE", "").strip().lower() in {"1", "true", "yes"}:
        return None

    return _build_mcp_service_url(host, port, path)


def _render_path_template(path_template: str, path_params: Optional[Dict[str, Any]] = None) -> str:
    rendered_path = path_template
    path_params = path_params or {}

    required_params = re.findall(r"{([^}]+)}", path_template)
    missing = [name for name in required_params if name not in path_params]
    if missing:
        raise ValueError(
            f"Missing path parameter(s) for {path_template}: {', '.join(missing)}"
        )

    for key, value in path_params.items():
        rendered_path = rendered_path.replace(f"{{{key}}}", str(value))

    return rendered_path


def _sanitize_tool_name(method: str, path: str) -> str:
    normalized = path.strip("/")
    normalized = re.sub(r"{([^}]+)}", r"by_\1", normalized)
    normalized = normalized.replace("/", "_")
    normalized = normalized.replace("-", "_")
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        normalized = "root"
    return f"api_{method.lower()}_{normalized}"


def _is_public_api_route(path: str) -> bool:
    return path.startswith("/api/") and not (
        path == "/api/internal" or path.startswith("/api/internal/")
    )


def _extract_api_routes(ws_module) -> List[Dict[str, str]]:
    routes: List[Dict[str, str]] = []
    seen = set()

    for route in ws_module.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)

        if not _is_public_api_route(path) or not methods:
            continue

        for method in sorted(methods):
            if method not in {"GET", "POST", "PUT", "DELETE"}:
                continue

            route_key = (method, path)
            if route_key in seen:
                continue
            seen.add(route_key)

            routes.append(
                {
                    "method": method,
                    "path": path,
                    "tool_name": _sanitize_tool_name(method, path),
                }
            )

    routes.sort(key=lambda item: (item["path"], item["method"]))
    return routes


def _route_category(path: str) -> str:
    stripped = path.strip("/")
    parts = stripped.split("/")
    if len(parts) < 2:
        return "misc"
    return parts[1]


def _filter_routes_by_category(
    routes: List[Dict[str, str]],
    category: str = "all",
) -> List[Dict[str, str]]:
    normalized = (category or "all").strip().lower().replace(" ", "_")
    if normalized in {"all", "*"}:
        return routes
    return [route for route in routes if _route_category(route["path"]) == normalized]


def _build_route_index(
    routes: List[Dict[str, str]],
    *,
    route_tools_exposed: bool,
) -> List[Dict[str, str]]:
    route_index = []
    for route in routes:
        item = {
            "method": route["method"],
            "path": route["path"],
            "category": _route_category(route["path"]),
        }
        if route_tools_exposed:
            item["route_tool_name"] = route["tool_name"]
        route_index.append(item)
    return route_index


def _normalize_public_api_path(path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    if not _is_public_api_route(normalized):
        raise ValueError(
            "Only public /api/* routes are available through MCP. "
            "Internal routes and non-MCP Web UI paths are not available through this tool."
        )
    return normalized


def _register_route_proxy_tools(mcp, api: AiNieeAPIClient, routes: List[Dict[str, str]]) -> None:
    # 自动把 WebServer 的 JSON API 映射成 MCP tools，尽量保持 Web 与 MCP 能力面对齐。
    for route_meta in routes:
        if route_meta["path"] == "/api/files/upload":
            continue

        method = route_meta["method"]
        path = route_meta["path"]
        tool_name = route_meta["tool_name"]
        route_category = _route_category(path)

        def make_route_tool(route_method: str, route_path: str, route_tool_name: str):
            def route_tool(
                path_params: Optional[Dict[str, Any]] = None,
                query: Optional[Dict[str, Any]] = None,
                body: Optional[Any] = None,
                confirm_advanced_change: bool = False,
            ) -> Any:
                """
                Proxy one WebServer API route through MCP.

                path_params fills placeholders in the original FastAPI path.
                query maps to URL query params.
                body maps to the JSON request body.
                confirm_advanced_change must be true before changing MCP advanced settings.
                """
                _ensure_advanced_change_confirmed(route_path, body, confirm_advanced_change)
                rendered_path = _render_path_template(route_path, path_params)
                return api.request(route_method, rendered_path, params=query, payload=body)

            route_tool.__name__ = route_tool_name
            route_tool.__doc__ = _mcp_tool_doc(
                f"Proxy WebServer route {_method_display(route_method)} {route_path}. Category: {route_category}.",
                (
                    "Use path_params for templated segments, query for URL params, body for JSON payload. "
                    "Call get_mcp_tool_categories first, then get_mcp_tool_catalog(category='<needed-category>') "
                    "for structured examples. Do not guess API paths."
                ),
            )
            return route_tool

        route_tool = make_route_tool(method, path, tool_name)
        mcp.tool()(route_tool)


def _method_display(method: str) -> str:
    return method.upper()


def _needs_advanced_change_confirmation(path: str, body: Any) -> bool:
    if path != "/api/config" or not isinstance(body, dict):
        return False
    return any(key in body for key in ("mcp_server_port", "mcp_server_host"))


def _ensure_advanced_change_confirmed(path: str, body: Any, confirmed: bool) -> None:
    if _needs_advanced_change_confirmation(path, body) and not confirmed:
        raise RuntimeError(
            "Changing MCP advanced settings requires a second confirmation. "
            "Ask the user again, then retry with confirm_advanced_change=true. "
            "The MCP client route may also need to be updated after the change."
        )


def _build_mcp_app(
    api: AiNieeAPIClient,
    ws_module,
    host: str,
    port: int,
    path: str,
    host_cli: Any = None,
    register_route_tools: bool = DEFAULT_REGISTER_ROUTE_TOOLS,
):
    try:
        _patch_streamable_http_shutdown_for_windows()
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        status = inspect_mcp_runtime(PROJECT_ROOT)
        primary_install = status.get("primary_install_command") or "uv add mcp"
        raise RuntimeError(
            _tf_from_host(
                host_cli,
                "msg_mcp_missing_python_module_install",
                "Missing Python module '{module_name}'. Suggested install: {command}",
                module_name="mcp",
                command=primary_install,
            )
        ) from exc

    mcp = FastMCP(
        "AiNiee CLI MCP",
        instructions=get_server_instructions_text(),
        host=host,
        port=port,
        streamable_http_path=path,
    )

    routes = _extract_api_routes(ws_module)

    @_mcp_tool(
        mcp,
        "Read the built-in MCP usage manual.",
        (
            "Call this first when the MCP client cannot inspect repository files. "
            "It also states that LLM-driven operations should use MCP tools instead of direct Web UI HTTP requests."
        ),
    )
    def get_mcp_usage_manual(section: str = "all") -> str:
        return load_mcp_manual(section)

    @_mcp_tool(
        mcp,
        "Read the MCP security policy.",
        (
            "This explains Web UI session cookie / MCP bridge token channel gates "
            "and how secret redaction behaves."
        ),
    )
    def get_mcp_security_policy() -> Dict[str, Any]:
        return build_security_policy()

    @_mcp_tool(
        mcp,
        "Read the lightweight MCP category index.",
        (
            "Use this before get_mcp_tool_catalog(category=...) so the client only loads "
            "the endpoint category it actually needs."
        ),
    )
    def get_mcp_tool_categories() -> Dict[str, Any]:
        return build_tool_category_index(
            routes,
            route_tools_exposed=register_route_tools,
        )

    @_mcp_tool(
        mcp,
        "Read the structured MCP endpoint catalog for one category.",
        (
            "Defaults to a lightweight category index. Pass category='config', category='queue', "
            "or another category from get_mcp_tool_categories to avoid loading the full catalog."
        ),
    )
    def get_mcp_tool_catalog(category: str = "index", include_examples: bool = True) -> Dict[str, Any]:
        return build_tool_catalog(
            routes,
            category=category,
            include_examples=include_examples,
            route_tools_exposed=register_route_tools,
        )

    @_mcp_tool(
        mcp,
        "Read the four MCP security validation scenarios.",
        "Use this to validate config/queue redaction and placeholder writeback protection.",
    )
    def get_mcp_validation_checklist() -> Dict[str, Any]:
        return build_validation_checklist()

    @_mcp_tool(
        mcp,
        "List public WebServer API routes exposed through MCP. Pass category for a compact group.",
        (
            "Prefer get_mcp_tool_categories() and "
            "get_mcp_tool_catalog(category='<needed-category>') before calling API routes, "
            "so you do not guess endpoint paths."
        ),
    )
    def list_web_api_routes(category: str = "all") -> List[Dict[str, str]]:
        filtered_routes = _filter_routes_by_category(routes, category)
        return _build_route_index(
            filtered_routes,
            route_tools_exposed=register_route_tools,
        )

    @_mcp_tool(
        mcp,
        "Call one public /api/* route through MCP.",
        (
            "Use get_mcp_tool_categories and get_mcp_tool_catalog(category=...) to choose "
            "the route. Do not guess endpoint paths or mix this MCP proxy with direct Web UI HTTP calls. "
            "Internal routes are blocked."
        ),
    )
    def call_web_api(
        method: str,
        path: str,
        path_params: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Any] = None,
        confirm_advanced_change: bool = False,
    ) -> Any:
        normalized_path = _normalize_public_api_path(path)
        _ensure_advanced_change_confirmed(normalized_path, body, confirm_advanced_change)
        rendered_path = _render_path_template(normalized_path, path_params)
        return api.request(method.upper(), rendered_path, params=query, payload=body)

    @_mcp_tool(
        mcp,
        "Upload a local file through the WebServer multipart endpoint.",
    )
    def upload_file(file_path: str, policy: str = "default") -> Dict[str, Any]:
        import requests

        source = Path(file_path).expanduser()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"File not found: {source}")

        # 这个接口在 WebServer 里是 multipart/form-data，不能走统一 JSON 代理。
        with source.open("rb") as handle:
            response = requests.post(
                f"{api.base_url}/api/files/upload",
                params={"policy": policy},
                files={"file": (source.name, handle)},
                headers={
                    MCP_CALLER_HEADER: MCP_CALLER_VALUE,
                    MCP_AUTH_HEADER: api.mcp_auth_token,
                },
                timeout=api.timeout,
            )

        try:
            data = response.json()
        except Exception:
            data = response.text

        if response.status_code >= 400:
            raise RuntimeError(f"{response.status_code} /api/files/upload: {data}")

        return sanitize_data_for_mcp(data, path="/api/files/upload")

    if register_route_tools:
        _register_route_proxy_tools(mcp, api, routes)

    return mcp


def _invoke_fastmcp_run(app: Any, transport: str, host: str, port: int, path: str) -> Any:
    os.environ.setdefault("FASTMCP_HOST", host)
    os.environ.setdefault("FASTMCP_PORT", str(port))
    os.environ.setdefault("FASTMCP_PATH", path)

    candidate_kwargs = [
        {"transport": transport, "host": host, "port": port, "mount_path": path, "path": path},
        {"transport": transport, "mount_path": path},
        {"transport": transport, "host": host, "port": port},
        {"transport": transport},
        {},
    ]

    signature = inspect.signature(app.run)
    last_error: Optional[Exception] = None

    for kwargs in candidate_kwargs:
        filtered = {key: value for key, value in kwargs.items() if key in signature.parameters}
        try:
            return app.run(**filtered)
        except TypeError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error

    return app.run()


def run_mcp_server(
    *,
    host_cli: Any = None,
    transport: str = "stdio",
    host: str = DEFAULT_MCP_HOST,
    port: int = DEFAULT_MCP_PORT,
    path: str = DEFAULT_MCP_PATH,
    backend_host: str = DEFAULT_BACKEND_HOST,
    backend_port: int = DEFAULT_BACKEND_PORT,
    register_route_tools: bool = DEFAULT_REGISTER_ROUTE_TOOLS,
    allow_remote_access: bool | None = None,
) -> Any:
    status = inspect_mcp_runtime(PROJECT_ROOT)
    if not status.get("available"):
        primary_install = status.get("primary_install_command") or "uv add mcp"
        raise RuntimeError(
            _tf_from_host(
                host_cli,
                "msg_mcp_runtime_not_ready_install",
                "MCP runtime is not ready. Suggested install: {command}",
                command=primary_install,
            )
        )

    transport = _normalize_transport(transport)
    if allow_remote_access is None:
        allow_remote_access = remote_access_enabled(PROJECT_MCP_DEFAULTS)
    if transport != "stdio":
        ensure_bind_allowed(host, allow_remote_access, "MCP")
    reusable_url = _try_get_reusable_mcp_service_url(transport, host, port, path)
    if reusable_url is not None:
        if is_reusable_mcp_service_running(host, port, path):
            import anyio

            _write_startup_notice(f"AiNiee MCP reusing running service: {reusable_url}")
            return anyio.run(_run_stdio_proxy_to_existing_mcp, reusable_url)

    mcp_auth_token = DEFAULT_MCP_AUTH_TOKEN or secrets.token_urlsafe(32)
    backend = EmbeddedWebServerController(
        host=backend_host,
        port=backend_port,
        host_cli=host_cli,
        log_level="critical" if transport == "stdio" else "info",
        mcp_auth_token=mcp_auth_token,
        allow_remote_access=allow_remote_access,
    )
    # MCP 复用现有 WebServer 作为后端宿主，避免再维护一套平行业务层。
    backend.start()
    atexit.register(backend.stop)

    api = AiNieeAPIClient(backend.base_url, mcp_auth_token=mcp_auth_token)
    mcp_app = _build_mcp_app(
        api,
        backend.ws_module,
        host,
        port,
        path,
        host_cli=host_cli,
        register_route_tools=register_route_tools,
    )

    try:
        return _invoke_fastmcp_run(mcp_app, transport, host, port, path)
    finally:
        backend.stop()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AiNiee MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http", "streamable_http", "http", "sse"],
        help="MCP transport mode.",
    )
    parser.add_argument("--host", default=DEFAULT_MCP_HOST, help="MCP host address.")
    parser.add_argument("--port", type=int, default=DEFAULT_MCP_PORT, help="MCP port.")
    parser.add_argument("--path", default=DEFAULT_MCP_PATH, help="HTTP MCP path.")
    parser.add_argument(
        "--backend-host",
        default=DEFAULT_BACKEND_HOST,
        help="Embedded AiNiee WebServer host.",
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=DEFAULT_BACKEND_PORT,
        help="Embedded AiNiee WebServer port.",
    )
    parser.add_argument(
        "--register-route-tools",
        action="store_true",
        default=DEFAULT_REGISTER_ROUTE_TOOLS,
        help=(
            "Compatibility mode: register one named api_* MCP tool per public WebServer route. "
            "Disabled by default to keep MCP tool discovery small."
        ),
    )
    return parser


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    run_mcp_server(
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
        backend_host=args.backend_host,
        backend_port=args.backend_port,
        register_route_tools=args.register_route_tools,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
