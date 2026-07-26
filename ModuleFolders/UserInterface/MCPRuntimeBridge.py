"""
MCP 运行桥接模块
从 ainiee_cli.py 分离
"""
import os
import socket
import time
from typing import Any, Dict

from rich.console import Console
from rich.panel import Panel

from Tools.MCPServer.docs import get_startup_hint_text
from Tools.MCPServer.runtime import format_runtime_status_lines, inspect_mcp_runtime
from ModuleFolders.Infrastructure.RemoteAccessPolicy import (
    remote_access_enabled,
    resolve_bind_host,
)


console = Console()


class MCPRuntimeBridge:
    """MCP server startup and runtime checks."""

    def __init__(self, host):
        self.host = host

    @property
    def i18n(self):
        return getattr(self.host, "i18n", None)

    @property
    def project_root(self):
        return getattr(self.host, "PROJECT_ROOT", os.getcwd())

    def inspect_runtime(self) -> Dict[str, object]:
        return inspect_mcp_runtime(self.project_root)

    def start_mcp_server(self) -> bool:
        """Menu entry: run MCP in foreground and return to the menu on Ctrl+C."""
        status = self.inspect_runtime()
        if not status.get("available"):
            self._show_missing_runtime(status, auto_exit=False)
            time.sleep(3)
            return False

        if self.is_mcp_server_running():
            self._show_status_panel(
                self._t("msg_mcp_server_already_running", "MCP服务已在后台运行。"),
                (
                    f"{self._label_line('label_transport', 'Transport', 'streamable-http')}\n"
                    f"{self._label_line('label_local', 'Local', self.get_mcp_local_endpoint())}\n"
                    "\n"
                    f"{self._get_route_update_notice()}\n\n"
                    f"{self._get_llm_guide_notice()}\n\n"
                    f"{self._t('msg_mcp_menu_returning', '3 秒后返回菜单界面...')}"
                ),
                border_style="green",
            )
            time.sleep(3)
            return True

        try:
            from Tools.MCPServer.server import run_mcp_server
        except Exception as exc:
            self._show_runtime_error(
                self._t("msg_mcp_import_failed", "MCP组件导入失败。"),
                exc,
                auto_exit=False,
            )
            time.sleep(3)
            return False

        self._show_status_panel(
            self._t("msg_mcp_server_started", "MCP服务已启动。"),
            (
                f"{self._label_line('label_transport', 'Transport', 'streamable-http')}\n"
                f"{self._label_line('label_local', 'Local', self.get_mcp_local_endpoint())}\n"
                f"{self._label_line('label_backend', 'Backend', f'http://{self._get_backend_host()}:{self._get_backend_port()}')}\n\n"
                f"{self._get_route_update_notice()}\n\n"
                f"{self._get_llm_guide_notice()}\n\n"
                f"{self._t('msg_mcp_logs_below', '运行日志和请求日志会显示在下方。')}\n"
                f"{self._t('msg_mcp_press_ctrl_c_return', '按 Ctrl+C 停止 MCP 服务并返回菜单。')}"
            ),
            border_style="green",
        )

        setattr(self.host, "mcp_server_active", True)
        try:
            run_mcp_server(
                host_cli=self.host,
                transport="streamable-http",
                host=self._get_mcp_host(),
                port=self._get_mcp_port(),
                path=self._get_mcp_path(),
                backend_host=self._get_backend_host(),
                backend_port=self._get_backend_port(),
                allow_remote_access=self._remote_access_enabled(),
            )
            return True
        except KeyboardInterrupt:
            console.print(
                f"\n[yellow]{self._t('msg_mcp_stopping_returning', '正在停止 MCP 服务并返回菜单...')}[/yellow]"
            )
            # Let uvicorn finish flushing shutdown logs before the TUI redraws the menu.
            time.sleep(3)
            return True
        except Exception as exc:
            self._show_runtime_error(
                self._t("msg_mcp_server_start_failed", "MCP服务启动失败。"),
                exc,
                auto_exit=False,
            )
            time.sleep(3)
            return False
        finally:
            setattr(self.host, "mcp_server_active", False)

    def run_mcp_server_from_command(self, transport: str = "stdio") -> int:
        """
        Command entry: run MCP in-process.

        Missing dependencies are rendered with Rich and delayed for 3 seconds
        before returning a non-zero exit code.
        """
        status = self.inspect_runtime()
        if not status.get("available"):
            self._show_missing_runtime(status, auto_exit=True)
            time.sleep(3)
            return 1

        try:
            from Tools.MCPServer.server import run_mcp_server
        except Exception as exc:
            self._show_runtime_error(
                self._t("msg_mcp_import_failed", "MCP组件导入失败。"),
                exc,
                auto_exit=True,
            )
            time.sleep(3)
            return 1

        setattr(self.host, "mcp_server_active", True)
        try:
            run_mcp_server(
                host_cli=self.host,
                transport=transport,
                host=self._get_mcp_host(),
                port=self._get_mcp_port(),
                path=self._get_mcp_path(),
                backend_host=self._get_backend_host(),
                backend_port=self._get_backend_port(),
                allow_remote_access=self._remote_access_enabled(),
            )
        except KeyboardInterrupt:
            console.print(self._t("msg_mcp_stopping_exit", "Stopping MCP......."))
            return 130
        except Exception as exc:
            self._show_runtime_error(
                self._t("msg_mcp_server_start_failed", "MCP服务启动失败。"),
                exc,
                auto_exit=True,
            )
            time.sleep(3)
            return 1
        finally:
            setattr(self.host, "mcp_server_active", False)

        return 0

    def is_mcp_server_running(self) -> bool:
        try:
            from Tools.MCPServer.server import is_reusable_mcp_service_running

            return is_reusable_mcp_service_running(
                self._get_mcp_host(),
                self._get_mcp_port(),
                self._get_mcp_path(),
            )
        except Exception:
            return self._is_port_open(self._get_probe_host(), self._get_mcp_port())

    def get_mcp_http_endpoint(self) -> str:
        return self.get_mcp_local_endpoint()

    def get_mcp_local_endpoint(self) -> str:
        return f"http://127.0.0.1:{self._get_mcp_port()}{self._get_mcp_path()}"

    def get_mcp_network_endpoint(self) -> str:
        return f"http://{self._detect_local_ip()}:{self._get_mcp_port()}{self._get_mcp_path()}"

    def _get_config_value(self, key: str, default: Any) -> Any:
        config = getattr(self.host, "config", None)
        if isinstance(config, dict):
            value = config.get(key)
            if value not in (None, ""):
                return value
        return default

    def _get_mcp_host(self) -> str:
        return resolve_bind_host(getattr(self.host, "config", {}))

    def _remote_access_enabled(self) -> bool:
        return remote_access_enabled(getattr(self.host, "config", {}))

    def _get_mcp_port(self) -> int:
        try:
            return int(self._get_config_value("mcp_server_port", 8765) or 8765)
        except Exception:
            return 8765

    def _get_mcp_path(self) -> str:
        path = str(self._get_config_value("mcp_server_path", "/mcp"))
        return path if path.startswith("/") else f"/{path}"

    def _get_backend_host(self) -> str:
        return str(self._get_config_value("mcp_backend_host", "127.0.0.1"))

    def _get_backend_port(self) -> int:
        try:
            return int(self._get_config_value("mcp_backend_port", 18000) or 18000)
        except Exception:
            return 18000

    def _show_missing_runtime(self, status: Dict[str, object], auto_exit: bool):
        lines = format_runtime_status_lines(status, translate=self._t)
        footer = self._t(
            "msg_mcp_auto_exit_3s",
            "3 秒后自动退出当前 MCP 启动流程。",
        ) if auto_exit else self._t(
            "msg_mcp_menu_returning",
            "3 秒后返回菜单界面...",
        )

        body = "\n".join(lines + ["", footer])
        self._show_status_panel(
            self._t("msg_mcp_runtime_missing", "MCP组件或依赖缺失。"),
            body,
            border_style="yellow",
        )

    def _show_runtime_error(self, title: str, exc: Exception, auto_exit: bool):
        footer = self._t(
            "msg_mcp_auto_exit_3s",
            "3 秒后自动退出当前 MCP 启动流程。",
        ) if auto_exit else self._t(
            "msg_mcp_menu_returning",
            "3 秒后返回菜单界面...",
        )
        self._show_status_panel(
            title,
            f"{type(exc).__name__}: {exc}\n\n{footer}",
            border_style="red",
        )

    def _show_status_panel(self, title: str, body: str, border_style: str = "cyan"):
        console.print(
            Panel(
                body,
                title=title,
                border_style=border_style,
                expand=False,
            )
        )

    def _wait_for_port(self, host: str, port: int, timeout: float = 3.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_port_open(host, port):
                return True
            time.sleep(0.2)
        return False

    def _is_port_open(self, host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                return sock.connect_ex((host, port)) == 0
        except Exception:
            return False

    def _get_probe_host(self) -> str:
        host = self._get_mcp_host()
        return "127.0.0.1" if host == "0.0.0.0" else host

    def _detect_local_ip(self) -> str:
        local_ip = "127.0.0.1"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                local_ip = sock.getsockname()[0]
        except Exception:
            pass
        return local_ip

    def _get_route_update_notice(self) -> str:
        """Return the route reminder shown in the menu-facing HTTP startup panels."""
        return self._t(
            "msg_mcp_route_update_notice",
            "注意：如果修改了 MCP 服务端口，请同步更新 Codex 等客户端中的 MCP 路由配置。",
        )

    def _get_llm_guide_notice(self) -> str:
        """Return the startup hint that points operators to the self-describing MCP tools."""
        return self._t(
            "msg_mcp_guide_tools_hint",
            get_startup_hint_text(),
        )

    def _label_line(self, key: str, default: str, value: str) -> str:
        """Render a localized label-value line inside MCP startup panels."""
        return f"{self._t(key, default)}: {value}"

    def _t(self, key: str, default: str) -> str:
        i18n = self.i18n
        if i18n is None:
            return default

        try:
            value = i18n.get(key)
        except Exception:
            return default

        return default if not value or value == key else value
