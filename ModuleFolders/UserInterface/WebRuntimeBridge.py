"""
Web 运行桥接模块
从 ainiee_cli.py 分离
"""
import os
import re
import threading
import time

import rapidjson as json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Confirm
from rich.table import Table

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.RemoteAccessPolicy import (
    remote_access_enabled,
    resolve_bind_host,
)
from ModuleFolders.Infrastructure.TaskConfig.ConfigProfileService import (
    atomic_write_json,
    deep_merge,
    load_json_file,
    load_master_preset,
    resolve_profile_path,
    sanitize_profile_name,
    split_effective_config,
)


console = Console()


class WebRuntimeBridge:
    """Web server、队列监控与桥接逻辑。"""

    def __init__(self, host):
        self.host = host

    @property
    def i18n(self):
        return self.host.i18n

    @property
    def project_root(self):
        return self.host.PROJECT_ROOT

    def handle_monitor_shortcut(self):
        local_ip = self._detect_local_ip() if self._remote_access_enabled() else "127.0.0.1"
        if self.host.web_server_thread is None or not self.host.web_server_thread.is_alive():
            try:
                from Tools.WebServer.web_server import run_server
                import Tools.WebServer.web_server as ws_module

                self._configure_web_handlers(ws_module)
                webserver_port = self._get_webserver_port()
                webserver_host = self._get_webserver_host()
                self._print_remote_access_disabled_warning()
                self.host.web_server_thread = run_server(
                    host=webserver_host,
                    port=webserver_port,
                    monitor_mode=True,
                    allow_remote_access=self._remote_access_enabled(),
                )
                os.environ["AINIEE_INTERNAL_API_URL"] = f"http://127.0.0.1:{webserver_port}"

                Base.print(f"[bold green]{self.i18n.get('msg_web_server_started_bg')}[/bold green]")
                Base.print(f"[cyan]您可以通过 http://{local_ip}:{webserver_port} 访问网页监控面板[/cyan]")
                self._attach_web_ui(ws_module, local_ip, push_existing_logs=True)
            except Exception as exc:
                Base.print(f"[red]Failed to start Web Server: {exc}[/red]")
                return

        import webbrowser

        webbrowser.open(f"{self._get_web_base_url()}/?mode=monitor#/monitor")

    def handle_queue_editor_shortcut(self):
        try:
            from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

            queue_manager = QueueManager()
            if not queue_manager.tasks:
                self.host.ui.log(f"[yellow]{self.i18n.get('msg_queue_empty_cannot_edit')}[/yellow]")
                return

            self.host.ui.log(f"[cyan]{self.i18n.get('msg_queue_status_display')}[/cyan]")
            self.show_queue_status(queue_manager)
            self.host.ui.log(f"[yellow]{self.i18n.get('msg_tui_edit_limitation')}[/yellow]")
            self.host.ui.log(f"[dim]{self.i18n.get('msg_use_h_key_for_web')}[/dim]")
        except Exception as exc:
            self.host.ui.log(f"[red]Failed to handle queue editor: {exc}[/red]")

    def handle_web_queue_shortcut(self):
        try:
            self.host.ui.log(f"[cyan]{self.i18n.get('msg_queue_web_opening')}[/cyan]")
            self.ensure_web_server_running()
            self.open_queue_page()
        except Exception as exc:
            self.host.ui.log(f"[red]Failed to open web queue manager: {exc}[/red]")

    def start_queue_log_monitor(self):
        if self.host._queue_log_monitor_running:
            return

        self.host._queue_log_monitor_running = True
        self.host._queue_log_monitor_thread = threading.Thread(
            target=self._queue_log_monitor_loop,
            daemon=True,
        )
        self.host._queue_log_monitor_thread.start()

    def stop_queue_log_monitor(self):
        self.host._queue_log_monitor_running = False
        if self.host._queue_log_monitor_thread and self.host._queue_log_monitor_thread.is_alive():
            self.host._queue_log_monitor_thread.join(timeout=1.0)

    def ensure_web_server_running(self):
        import socket

        webserver_port = self._get_webserver_port()
        if self._is_port_open("127.0.0.1", webserver_port):
            self.host.ui.log(f"[green]{self.i18n.get('msg_web_server_ready')}[/green]")
            os.environ["AINIEE_INTERNAL_API_URL"] = f"http://127.0.0.1:{webserver_port}"
            self.start_queue_log_monitor()
            return

        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            self.host.ui.log("[red]Missing dependencies: fastapi, uvicorn. Cannot start web server.[/red]")
            raise Exception("Missing web server dependencies")

        self.host.ui.log(f"[cyan]{self.i18n.get('msg_web_server_starting_background')}[/cyan]")

        import Tools.WebServer.web_server as ws_module

        self._configure_web_handlers(ws_module)
        run_server = ws_module.run_server
        webserver_host = self._get_webserver_host()
        allow_remote_access = self._remote_access_enabled()
        self._print_remote_access_disabled_warning()

        def start_server():
            try:
                run_server(
                    host=webserver_host,
                    port=webserver_port,
                    monitor_mode=False,
                    allow_remote_access=allow_remote_access,
                )
            except Exception as exc:
                self.host.ui.log(f"[red]Failed to start web server: {exc}[/red]")

        threading.Thread(target=start_server, daemon=True).start()

        for _ in range(10):
            time.sleep(0.5)
            if self._is_port_open("127.0.0.1", webserver_port):
                self.host.ui.log(f"[green]{self.i18n.get('msg_web_server_ready')}[/green]")
                os.environ["AINIEE_INTERNAL_API_URL"] = f"http://127.0.0.1:{webserver_port}"
                try:
                    if getattr(self.host, "ui", None):
                        self.host.ui.web_task_manager = ws_module.task_manager
                except Exception as exc:
                    self.host.ui.log(f"[yellow]Warning: Could not establish web connection: {exc}[/yellow]")
                self.start_queue_log_monitor()
                return

        self.host.ui.log(f"[yellow]{self.i18n.get('msg_web_server_timeout')}[/yellow]")

    def show_queue_status(self, queue_manager):
        if hasattr(queue_manager, "cleanup_stale_locks"):
            queue_manager.cleanup_stale_locks()

        self.host.ui.log(f"[bold cyan]═══ {self.i18n.get('title_queue_status')} ═══[/bold cyan]")
        for index, task in enumerate(queue_manager.tasks):
            status_color = (
                "green"
                if task.status == "completed"
                else "yellow"
                if task.status in ["translating", "polishing"]
                else "red"
                if task.status == "error"
                else "white"
            )
            type_str = "T+P" if task.task_type == 4000 else "T" if task.task_type == 1000 else "P"
            lock_icon = (
                "🔒"
                if (hasattr(queue_manager, "is_task_actually_processing") and queue_manager.is_task_actually_processing(index))
                or task.locked
                else ""
            )
            file_name = os.path.basename(task.input_path)
            self.host.ui.log(
                f"[{status_color}]{index + 1:2d}. [{type_str}] {file_name} - {task.status} {lock_icon}[/{status_color}]"
            )

        self.host.ui.log(f"[dim]ⓘ {self.i18n.get('msg_queue_tui_help')}[/dim]")

    def open_queue_page(self):
        import webbrowser

        webbrowser.open(f"{self._get_web_base_url()}/#/queue")

    def run_queue_editor(self, queue_manager):
        try:
            editor_console = Console()

            def get_localized_status(status):
                status_map = {
                    "waiting": self.i18n.get("task_status_waiting"),
                    "translating": self.i18n.get("task_status_translating"),
                    "translated": self.i18n.get("task_status_translated"),
                    "polishing": self.i18n.get("task_status_polishing"),
                    "completed": self.i18n.get("task_status_completed"),
                    "partial": self.i18n.get("task_status_partial"),
                    "running": self.i18n.get("task_status_running"),
                    "error": self.i18n.get("task_status_error"),
                    "stopped": self.i18n.get("task_status_stopped"),
                }
                return status_map.get(status.lower(), status.upper())

            while True:
                queue_manager.hot_reload_queue()
                if hasattr(queue_manager, "cleanup_stale_locks"):
                    queue_manager.cleanup_stale_locks()

                editor_console.clear()
                editor_console.print(
                    Panel.fit(
                        f"[bold cyan]{self.i18n.get('title_queue_editor')}[/bold cyan]\n"
                        f"{self.i18n.get('msg_queue_editor_help')}",
                        border_style="cyan",
                    )
                )

                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("#", style="dim", width=3)
                table.add_column(self.i18n.get("field_status"), width=12)
                table.add_column(self.i18n.get("field_type"), width=15)
                table.add_column(self.i18n.get("field_input_path"), width=40)
                table.add_column(self.i18n.get("field_locked"), width=8, style="red")

                for index, task in enumerate(queue_manager.tasks):
                    status_style = (
                        "green"
                        if task.status == "completed"
                        else "yellow"
                        if task.status in ["translating", "polishing"]
                        else "red"
                        if task.status == "error"
                        else ""
                    )
                    is_actually_processing = (
                        queue_manager.is_task_actually_processing(index)
                        if hasattr(queue_manager, "is_task_actually_processing")
                        else task.locked
                    )
                    locked_symbol = "🔒" if is_actually_processing else ""
                    type_str = (
                        "T+P"
                        if task.task_type == 4000
                        else "T"
                        if task.task_type == 1000
                        else "P"
                        if task.task_type == 2000
                        else str(task.task_type)
                    )
                    table.add_row(
                        str(index + 1),
                        f"[{status_style}]{get_localized_status(task.status)}[/{status_style}]",
                        type_str,
                        task.input_path[-35:] + "..." if len(task.input_path) > 35 else task.input_path,
                        locked_symbol,
                    )

                editor_console.print(table)
                editor_console.print(f"\n[bold yellow]{self.i18n.get('menu_queue_operations')}:[/bold yellow]")
                editor_console.print(f"1. {self.i18n.get('option_move_up')}")
                editor_console.print(f"2. {self.i18n.get('option_move_down')}")
                editor_console.print(f"3. {self.i18n.get('option_remove_task')}")
                editor_console.print(f"4. {self.i18n.get('option_refresh_queue')}")
                editor_console.print(f"0. {self.i18n.get('option_return_to_execution')}")

                try:
                    choice = IntPrompt.ask(
                        f"\n{self.i18n.get('prompt_select_operation')}",
                        console=editor_console,
                        default=0,
                    )
                    if choice == 0:
                        break
                    if choice == 1:
                        self._handle_queue_move(editor_console, queue_manager, move_up=True)
                    elif choice == 2:
                        self._handle_queue_move(editor_console, queue_manager, move_up=False)
                    elif choice == 3:
                        self._handle_queue_delete(editor_console, queue_manager)
                    elif choice == 4:
                        editor_console.print(f"[cyan]{self.i18n.get('msg_queue_refreshed')}[/cyan]")
                        continue

                    if choice != 4:
                        editor_console.input(f"\n{self.i18n.get('prompt_press_enter_continue')}")
                except (KeyboardInterrupt, EOFError):
                    break
                except Exception as exc:
                    editor_console.print(f"[red]Error: {exc}[/red]")
                    editor_console.input(f"\n{self.i18n.get('prompt_press_enter_continue')}")

            if getattr(self.host, "ui", None):
                self.host.ui.log(f"[cyan]{self.i18n.get('msg_queue_editor_closed')}[/cyan]")
        except Exception as exc:
            if getattr(self.host, "ui", None):
                self.host.ui.log(f"[red]Queue editor error: {exc}[/red]")

    def host_create_profile(self, new_name, base_name=None):
        new_path, new_name = resolve_profile_path(self.host.profiles_dir, new_name)
        if os.path.exists(new_path):
            raise Exception("Exists")

        if not base_name:
            base_name = self.host.active_profile_name
        base_name = sanitize_profile_name(base_name)
        base_path, _ = resolve_profile_path(self.host.profiles_dir, base_name)
        preset = load_master_preset()
        if os.path.exists(base_path):
            preset = deep_merge(preset, load_json_file(base_path, {}))
        preset, _, _ = split_effective_config(preset)

        atomic_write_json(new_path, preset)

    def host_rename_profile(self, old_name, new_name):
        old_path, old_name = resolve_profile_path(self.host.profiles_dir, old_name)
        new_path, new_name = resolve_profile_path(self.host.profiles_dir, new_name)
        if not os.path.exists(old_path):
            raise Exception("Not found")
        if os.path.exists(new_path):
            raise Exception("Target exists")

        os.rename(old_path, new_path)
        if self.host.active_profile_name == old_name:
            self.host.active_profile_name = new_name
            self.host.root_config["active_profile"] = new_name
            self.host.save_config(save_root=True)

    def host_delete_profile(self, name):
        target, name = resolve_profile_path(self.host.profiles_dir, name)
        if not os.path.exists(target):
            raise Exception("Not found")
        if name == self.host.active_profile_name:
            raise Exception("Cannot delete active profile")

        count = len([file for file in os.listdir(self.host.profiles_dir) if file.endswith(".json")])
        if count <= 1:
            raise Exception("Cannot delete last profile")

        os.remove(target)

    def host_run_queue(self):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()
        if not queue_manager.tasks:
            raise Exception("Task queue is empty")
        if queue_manager.is_running:
            return True

        self.host._is_queue_mode = True
        self.start_queue_log_monitor()
        queue_manager.start_queue(self.host)

        def queue_cleanup():
            try:
                while queue_manager.is_running:
                    time.sleep(0.5)
            finally:
                self.stop_queue_log_monitor()
                self.host._is_queue_mode = False

        threading.Thread(target=queue_cleanup, daemon=True).start()
        return True

    def start_web_server(self):
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            console.print("[red]Missing dependencies: fastapi, uvicorn. Please install them to use Web Server.[/red]")
            console.print("Try: pip install fastapi uvicorn[standard]")
            console.input("\nPress Enter to return...")
            return

        from Tools.WebServer.web_server import run_server
        import Tools.WebServer.web_server as ws_module

        self._configure_web_handlers(ws_module)
        webserver_port = self._get_webserver_port()
        webserver_host = self._get_webserver_host()
        allow_remote_access = self._remote_access_enabled()

        console.print("[green]Starting Web Server...[/green]")
        console.print("[dim]Press Ctrl+C to stop the server and return to menu.[/dim]")
        self._print_remote_access_disabled_warning()

        server_thread = run_server(
            host=webserver_host,
            port=webserver_port,
            allow_remote_access=allow_remote_access,
        )
        if not server_thread:
            return

        time.sleep(1.5)
        if not server_thread.is_alive():
            console.print(
                f"\n[bold red]Web Server failed to start. Please check if port {webserver_port} is already in use.[/bold red]"
            )
            time.sleep(3)
            return

        import webbrowser

        time.sleep(1)
        endpoint_lines = [f"Local: [bold cyan]http://127.0.0.1:{webserver_port}[/bold cyan]"]
        if allow_remote_access:
            endpoint_lines.append(
                f"{self.i18n.get('label_network')}: [bold cyan]http://{self._detect_local_ip()}:{webserver_port}[/bold cyan]"
            )
        console.print(Panel("\n".join(endpoint_lines), title="Web Server Active", border_style="green", expand=False))
        webbrowser.open(f"http://127.0.0.1:{webserver_port}")

        self.host.web_server_active = True
        try:
            while server_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping Web Server and cleaning up...[/yellow]")
        finally:
            ws_module.stop_server()
            time.sleep(3)
            self.host.web_server_active = False

    def _queue_log_monitor_loop(self):
        try:
            from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

            queue_manager = QueueManager()
            log_file = queue_manager.get_queue_log_path()

            while self.host._queue_log_monitor_running:
                try:
                    if os.path.exists(log_file):
                        current_size = os.path.getsize(log_file)
                        if current_size > self.host._last_queue_log_size:
                            self._display_new_queue_logs(log_file)
                            self.host._last_queue_log_size = current_size
                    time.sleep(1)
                except Exception:
                    pass
        except Exception:
            pass

    def _parse_and_push_stats(self, stats_line):
        try:
            stats_data = {}
            rpm_match = re.search(r"RPM:\s*([\d\.]+)", stats_line)
            if rpm_match:
                stats_data["rpm"] = float(rpm_match.group(1))

            tpm_match = re.search(r"TPM:\s*([\d\.]+k?)", stats_line)
            if tpm_match:
                stats_data["tpm"] = float(tpm_match.group(1).replace("k", ""))

            progress_match = re.search(r"Progress:\s*(\d+)/(\d+)", stats_line)
            if progress_match:
                stats_data["completedProgress"] = int(progress_match.group(1))
                stats_data["totalProgress"] = int(progress_match.group(2))

            tokens_match = re.search(r"Tokens:\s*(\d+)", stats_line)
            if tokens_match:
                stats_data["totalTokens"] = int(tokens_match.group(1))

            success_match = re.search(r"S-Rate:\s*([\d\.]+)%", stats_line)
            if success_match:
                stats_data["successRate"] = float(success_match.group(1))

            error_match = re.search(r"E-Rate:\s*([\d\.]+)%", stats_line)
            if error_match:
                stats_data["errorRate"] = float(error_match.group(1))

            if stats_data:
                self._push_stats_to_webserver(stats_data)
        except Exception:
            pass

    def _get_webserver_port(self):
        try:
            return int(self.host.config.get("webserver_port", 8000) or 8000)
        except Exception:
            return 8000

    def _remote_access_enabled(self):
        return remote_access_enabled(getattr(self.host, "config", {}))

    def _get_webserver_host(self):
        return resolve_bind_host(getattr(self.host, "config", {}))

    def _remote_access_disabled_message(self):
        return self.i18n.get("warning_remote_access_disabled")

    def _print_remote_access_disabled_warning(self):
        if not self._remote_access_enabled():
            console.print(f"[yellow]{self._remote_access_disabled_message()}[/yellow]")

    def _get_internal_api_base(self):
        return os.environ.get("AINIEE_INTERNAL_API_URL", f"http://127.0.0.1:{self._get_webserver_port()}")

    def _get_web_base_url(self):
        return f"http://127.0.0.1:{self._get_webserver_port()}"

    def _push_stats_to_webserver(self, stats_data):
        try:
            import requests
            from Tools.MCPServer.security import INTERNAL_AUTH_ENV, INTERNAL_AUTH_HEADER

            response = requests.post(
                f"{self._get_internal_api_base()}/api/internal/update_stats",
                json=stats_data,
                headers={INTERNAL_AUTH_HEADER: os.environ.get(INTERNAL_AUTH_ENV, "")},
                timeout=1.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    def _push_log_to_webserver(self, message, log_type="info"):
        try:
            import requests
            from Tools.MCPServer.security import INTERNAL_AUTH_ENV, INTERNAL_AUTH_HEADER

            response = requests.post(
                f"{self._get_internal_api_base()}/api/internal/push_log",
                json={"message": message, "type": log_type},
                headers={INTERNAL_AUTH_HEADER: os.environ.get(INTERNAL_AUTH_ENV, "")},
                timeout=1.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    def _display_new_queue_logs(self, log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as file:
                file.seek(self.host._last_queue_log_size)
                new_content = file.read()

            if not new_content.strip():
                return

            for line in new_content.strip().split("\n"):
                if not line.strip():
                    continue

                message = line.split("] ", 1)[1] if "] " in line and line.startswith("[") else line
                if "[STATS]" in message:
                    self._parse_and_push_stats(message)

                self._push_log_to_webserver(message)
                if getattr(self.host, "ui", None):
                    self.host.ui.log(f"[cyan][Queue][/cyan] {message}")
        except Exception:
            pass

    def _configure_web_handlers(self, ws_module):
        ws_module.profile_handlers["create"] = self.host_create_profile
        ws_module.profile_handlers["rename"] = self.host_rename_profile
        ws_module.profile_handlers["delete"] = self.host_delete_profile
        ws_module.queue_handlers["run"] = self.host_run_queue

    def _attach_web_ui(self, ws_module, local_ip, push_existing_logs=False):
        if self.host.task_running and self.host._is_task_ui_instance():
            self.host.ui.web_task_manager = ws_module.task_manager
            self.host.ui._server_ip = local_ip

        if not getattr(self.host, "ui", None):
            return

        self.host.ui.web_task_manager = ws_module.task_manager
        self.host.ui._server_ip = local_ip

        if push_existing_logs:
            with self.host.ui._lock:
                for log_item in self.host.ui.logs:
                    clean_hist = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s+", "", log_item.plain)
                    ws_module.task_manager.push_log(clean_hist)

        self.host.ui.taken_over = True
        self.host.ui.update_progress(None, {})

    def _detect_local_ip(self):
        import socket

        local_ip = "127.0.0.1"
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
            sock.close()
        except Exception:
            pass
        return local_ip

    def _is_port_open(self, host, port):
        import socket

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _handle_queue_move(self, editor_console, queue_manager, move_up):
        task_index = IntPrompt.ask(self.i18n.get("prompt_enter_task_index"), console=editor_console) - 1
        if not 0 <= task_index < len(queue_manager.tasks):
            editor_console.print(f"[red]{self.i18n.get('msg_invalid_index')}[/red]")
            return

        is_locked = (
            queue_manager.is_task_actually_processing(task_index)
            if hasattr(queue_manager, "is_task_actually_processing")
            else queue_manager.tasks[task_index].locked
        )
        if is_locked:
            editor_console.print(f"[red]{self.i18n.get('msg_task_locked_cannot_move')}[/red]")
            return

        success = (
            queue_manager.move_task_up(task_index)
            if move_up
            else queue_manager.move_task_down(task_index)
        )
        if success:
            message_key = "msg_task_moved_up" if move_up else "msg_task_moved_down"
            editor_console.print(f"[green]{self.i18n.get(message_key)}[/green]")
        else:
            editor_console.print(f"[red]{self.i18n.get('msg_move_failed')}[/red]")

    def _handle_queue_delete(self, editor_console, queue_manager):
        task_index = IntPrompt.ask(self.i18n.get("prompt_enter_task_index"), console=editor_console) - 1
        if not 0 <= task_index < len(queue_manager.tasks):
            editor_console.print(f"[red]{self.i18n.get('msg_invalid_index')}[/red]")
            return

        task = queue_manager.tasks[task_index]
        is_locked = (
            queue_manager.is_task_actually_processing(task_index)
            if hasattr(queue_manager, "is_task_actually_processing")
            else task.locked
        )
        if is_locked:
            if task.status == "translating":
                status_text = (
                    self.i18n.get("task_status_all_in_one_cn")
                    if getattr(task, "task_type", None) == 4000
                    else self.i18n.get("task_status_translating_cn")
                )
            elif task.status == "polishing":
                status_text = self.i18n.get("task_status_polishing_cn")
            else:
                status_text = task.status
            editor_console.print(f"[red]{self.i18n.get('msg_task_locked').replace('{}', status_text)}[/red]")
            return

        if Confirm.ask(self.i18n.get("confirm_remove_task").format(task.input_path), console=editor_console):
            if queue_manager.remove_task(task_index):
                editor_console.print(f"[green]{self.i18n.get('msg_task_removed')}[/green]")
            else:
                editor_console.print(f"[red]{self.i18n.get('msg_remove_failed')}[/red]")
