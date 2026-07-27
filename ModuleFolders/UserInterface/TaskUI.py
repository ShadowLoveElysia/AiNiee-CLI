"""
任务界面模块 - TUI模式下的任务进度和日志显示
"""

import re
import time
import threading
import collections

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, TimeElapsedColumn, SpinnerColumn


class TaskUI:
    """TUI任务界面 - 显示进度条、日志和实时对照"""

    def __init__(self, parent_cli=None, i18n=None):
        self._lock = threading.RLock()
        self.parent_cli = parent_cli
        self.i18n = i18n

        # 根据配置决定日志保留数量
        self.show_detailed = parent_cli.config.get("show_detailed_logs", False) if parent_cli else False
        self.logs = collections.deque(maxlen=100)  # 统一保留100条日志，方便回溯

        self.log_filter = "ALL"
        self.taken_over = False
        self.web_task_manager = None
        self.last_error = ""
        self.log_file = None  # 实时的日志文件句柄
        self._progress_paused = False
        self._progress_pause_started_at = None

        # 实时对照内容存储 (仅在详细模式使用)
        self.current_source = Text("Waiting...", style="dim")
        self.current_translation = Text("Waiting...", style="dim")

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[action]}", justify="left"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.0f}%",
            TimeElapsedColumn(),
            TextColumn("/"),
            TimeRemainingColumn(),
            expand=True
        )
        self.task_id = self.progress.add_task("", total=100, action=self._get_i18n('label_initializing'))

        # 初始化布局
        self.layout = Layout()
        if self.show_detailed:
            # 详细模式：三段式 (Header + Body + Footer)
            self.layout.split(
                Layout(name="header", size=3),
                Layout(name="body", ratio=1),
                Layout(name="footer", size=15)
            )
            self.layout["body"].split_row(
                Layout(name="source_pane", ratio=1),
                Layout(name="target_pane", ratio=1)
            )
            self.layout["footer"].split(
                Layout(name="small_logs", ratio=1),
                Layout(name="stats", size=7)
            )
        else:
            # 经典模式：上下两段式
            self.layout.split(
                Layout(name="upper", ratio=4, minimum_size=10),
                Layout(name="lower", size=7)
            )

        self.stats_text = Text("Initializing stats...", style="cyan")
        self.current_status_key = 'label_status_normal'
        self.current_status_color = 'green'
        self.current_border_color = "green"

        self.refresh_layout()

    def _get_i18n(self, key):
        """获取国际化文本"""
        if self.i18n:
            return self.i18n.get(key)
        return key

    def _get_int_field(self, data, key, default=0):
        try:
            return int(data.get(key, default) or 0)
        except (TypeError, ValueError):
            return default

    def _format_filter_progress_hint(self, data):
        raw_total = self._get_int_field(data, "raw_total_line")
        filtered_total = self._get_int_field(data, "filtered_total_line", self._get_int_field(data, "total_line"))
        excluded_total = self._get_int_field(data, "excluded_total_line")
        file_raw_total = self._get_int_field(data, "file_raw_total_line")
        file_filtered_total = self._get_int_field(data, "file_filtered_total_line", file_raw_total)
        file_count = self._get_int_field(data, "project_file_count", 1)

        if not excluded_total and raw_total <= filtered_total and file_raw_total <= file_filtered_total:
            return ""

        lang = getattr(self.i18n, "lang", "") if self.i18n else ""
        use_file_total = file_raw_total > 0 and file_raw_total > file_filtered_total
        if not use_file_total and file_count <= 1 and raw_total > filtered_total:
            use_file_total = True
            file_raw_total = raw_total

        if lang == "ja":
            if use_file_total:
                if file_count > 1 and raw_total > 0:
                    return f"注意: 現在の進捗は言語フィルター対象を除外しています。現在のファイルは全{file_raw_total}行、今回のタスクは全{raw_total}行です。"
                return f"注意: 現在の進捗は言語フィルター対象を除外しています。このファイルは全{file_raw_total}行です。"
            return f"注意: 現在の進捗は言語フィルター対象を除外しています。今回のタスクは全{raw_total}行です。"
        if lang == "en":
            if use_file_total:
                if file_count > 1 and raw_total > 0:
                    return f"Note: Progress excludes language-filtered rows. Current file has {file_raw_total} rows total; this task has {raw_total} rows total."
                return f"Note: Progress excludes language-filtered rows. This file has {file_raw_total} rows total."
            return f"Note: Progress excludes language-filtered rows. This task has {raw_total} rows total."

        if use_file_total:
            if file_count > 1 and raw_total > 0:
                return f"提示: 当前进度不包含语言过滤内容，当前文件全 {file_raw_total} 行，此次任务全 {raw_total} 行。"
            return f"提示: 当前进度不包含语言过滤内容，此文件全 {file_raw_total} 行。"
        return f"提示: 当前进度不包含语言过滤内容，此次任务全 {raw_total} 行。"

    def _pause_progress_timer(self):
        if self._progress_paused:
            return

        self._progress_paused = True
        self._progress_pause_started_at = self.progress.get_time()
        self.progress.stop_task(self.task_id)

    def _resume_progress_timer(self):
        if not self._progress_paused:
            return

        paused_started_at = self._progress_pause_started_at
        current_time = self.progress.get_time()
        paused_duration = 0
        if paused_started_at is not None:
            paused_duration = max(0, current_time - paused_started_at)

        with self.progress._lock:
            task = self.progress._tasks[self.task_id]
            if task.start_time is None:
                task.start_time = current_time
            else:
                task.start_time += paused_duration
            task.stop_time = None

        self._progress_paused = False
        self._progress_pause_started_at = None

    def refresh_layout(self):
        """刷新 TUI 渲染内容"""
        with self._lock:
            if self.show_detailed:
                # 统计行数
                s_lines = len(self.current_source.plain.split('\n')) if self.current_source.plain else 0
                t_lines = len(self.current_translation.plain.split('\n')) if self.current_translation.plain else 0

                # 渲染详细对照模式
                self.layout["source_pane"].update(Panel(
                    self.current_source,
                    title=f"[bold magenta]SOURCE ({s_lines} lines)[/]",
                    border_style="magenta",
                    padding=(0, 1)
                ))
                self.layout["target_pane"].update(Panel(
                    self.current_translation,
                    title=f"[bold green]TRANSLATION ({t_lines} lines)[/]",
                    border_style="green",
                    padding=(0, 1)
                ))
                # 底部小日志窗格
                log_group = Group(*list(self.logs)[-8:])
                self.layout["small_logs"].update(Panel(log_group, title="System Logs", border_style="blue"))
                self.panel_group = Group(self.progress, self.stats_text)
                self.layout["stats"].update(Panel(self.panel_group, title="Progress & Metrics", border_style=self.current_border_color))
            else:
                # 渲染经典滚动模式
                log_group = Group(*list(self.logs)[-35:])
                self.layout["upper"].update(Panel(log_group, title=f"Logs ({self.log_filter})", border_style="blue", padding=(0, 1)))
                self.panel_group = Group(self.progress, self.stats_text)
                self.layout["lower"].update(Panel(self.panel_group, title="Progress & Stats", border_style=self.current_border_color))

    def update_status(self, event, data):
        with self._lock:
            status = data.get("status", "normal") if isinstance(data, dict) else "normal"
            color_map = {"normal": "green", "fixing": "yellow", "warning": "yellow", "error": "red", "paused": "yellow", "critical_error": "red"}
            status_key_map = {
                "normal": "label_status_normal",
                "fixing": "label_status_fixing",
                "warning": "label_status_warning",
                "error": "label_status_error",
                "paused": "label_status_paused",
                "critical_error": "label_status_critical_error"
            }

            self.current_status_key = status_key_map.get(status, "label_status_normal")
            self.current_status_color = color_map.get(status, "green")
            self.current_border_color = self.current_status_color
            if status == "paused":
                self._pause_progress_timer()
            elif status == "normal":
                self._resume_progress_timer()
            self.update_progress(None, {})

    def _is_error_log(self, log_item: Text):
        """Heuristically determines if a log entry is an error."""
        text = log_item.plain.lower()
        err_words = ['error', 'fail', 'failed', 'exception', 'traceback', 'critical', 'panic', '✗']

        has_red_style = False
        for span in log_item.spans:
            s = span.style
            if isinstance(s, str):
                if "red" in s: has_red_style = True; break
            elif hasattr(s, "color") and s.color and (s.color.name == "red" or s.color.number == 1):
                has_red_style = True; break

        return has_red_style or any(word in text for word in err_words)

    @staticmethod
    def _is_explicit_critical_log(log_item: Text) -> bool:
        """Return True only for structured crash output, not translated content."""
        text = log_item.plain.lower()
        marker = re.compile(
            r"(?:^|\]\s*)(?:traceback \(most recent call last\):|panic(?:\s|:))"
        )
        return marker.search(text) is not None

    def refresh_logs(self):
        """Renders the log panel according to the current filter."""
        with self._lock:
            if self.log_filter == "ALL":
                display_logs = list(self.logs)[-35:]
            else:  # ERROR
                display_logs = [log for log in self.logs if self._is_error_log(log)][-35:]

            log_group = Group(*display_logs)
            self.layout["upper"].update(Panel(log_group, title=f"Logs ({self.log_filter})", border_style="blue", padding=(0, 1)))

    def toggle_log_filter(self):
        self.log_filter = "ERROR" if self.log_filter == "ALL" else "ALL"
        self.log(f"[dim]Log view set to: {self.log_filter}[/dim]")
        self.refresh_logs()

    def on_source_data(self, event, data):
        """接收原文数据的事件回调"""
        if not self.show_detailed: return
        pass

    def on_result_data(self, event, data):
        """接收译文数据的事件回调"""
        if not self.show_detailed: return
        if not isinstance(data, dict): return
        raw_content = str(data.get("data", ""))
        source_content = data.get("source")
        if not raw_content and not source_content: return

        with self._lock:
            if source_content:
                clean_source = "".join([c for c in str(source_content) if c == '\n' or c >= ' '])
                self.current_source = Text(clean_source, style="magenta")

            if raw_content:
                clean_content = "".join([c for c in raw_content if c == '\n' or c >= ' '])
                self.current_translation = Text(clean_content, style="green")

            if self.web_task_manager:
                self.web_task_manager.push_comparison(
                    str(self.current_source.plain),
                    str(self.current_translation.plain)
                )

            self._last_result_time = time.time()
            self.refresh_layout()

    def log(self, msg):
        # 1. 预处理：将对象转为字符串
        if not isinstance(msg, str):
            from io import StringIO
            with StringIO() as buf:
                temp_console = Console(file=buf, force_terminal=True, width=120)
                temp_console.print(msg)
                msg_str = buf.getvalue()
        else:
            msg_str = msg

        # 2. 拦截实时对照信号 (双通道补丁)
        if "<<<RAW_RESULT>>>" in msg_str:
            if time.time() - getattr(self, "_last_result_time", 0) < 0.5:
                return

            try:
                data = msg_str.split("<<<RAW_RESULT>>>")[1].strip()
                if data:
                    with self._lock:
                        clean = "".join([c for c in data if c == '\n' or c >= ' '])
                        self.current_translation = Text(clean, style="green")
                        if self.web_task_manager:
                            self.web_task_manager.push_comparison(
                                str(self.current_source.plain),
                                str(self.current_translation.plain)
                            )
                        self.refresh_layout()
            except: pass
            return

        # 3. 过滤私有标签和状态
        if "<<<" in msg_str and ">>>" in msg_str: return
        if "[STATUS]" in msg_str: return

        clean_msg = msg_str.strip()
        if not clean_msg: return

        # Push to WebServer
        if self.web_task_manager:
            plain_msg = re.sub(r'\[/?[a-zA-Z\s]+\]', '', clean_msg)
            self.web_task_manager.push_log(plain_msg)

        current_time = time.time()
        if hasattr(self, "_last_msg") and self._last_msg == clean_msg and (current_time - getattr(self, "_last_msg_time", 0)) < 0.3:
            return
        self._last_msg, self._last_msg_time = clean_msg, current_time

        # Real-time File Logging
        timestamp = f"[{time.strftime('%H:%M:%S')}] "
        if self.log_file:
            try:
                plain_log = re.sub(r'\[/?[a-zA-Z\s]+\]', '', clean_msg)
                self.log_file.write(timestamp + plain_log + "\n")
                self.log_file.flush()
            except: pass

        if self.taken_over: return

        # 4. 构造日志内容并刷新
        try:
            new_log = Text.from_markup(timestamp + clean_msg)
        except:
            new_log = Text(timestamp + clean_msg)

        with self._lock:
            self.logs.append(new_log)

            # 自动错误监测补丁
            if self._is_error_log(new_log) and self.parent_cli:
                lower_msg = clean_msg.lower()
                if any(w in lower_msg for w in ['traceback', 'panic', 'exception', 'fatal']):
                    if self.current_status_color != 'red':
                        self.current_status_color = 'red'
                        self.current_border_color = 'red'

                if self._is_explicit_critical_log(new_log):
                    self.parent_cli._is_critical_failure = True
                    if not getattr(self.parent_cli, "_last_crash_msg", None):
                        self.parent_cli._last_crash_msg = clean_msg

                api_error_keywords = ['401', '403', '429', '500', '502', '503', 'timeout', 'connection', 'ssl', 'rate_limit']
                if any(k in lower_msg for k in api_error_keywords):
                    self.parent_cli._api_error_count += 1
                    if len(self.parent_cli._api_error_messages) < 10:
                        self.parent_cli._api_error_messages.append(clean_msg)
                    if self.parent_cli._api_error_count >= 3 and not self.parent_cli._show_diagnostic_hint:
                        self.parent_cli._show_diagnostic_hint = True

            self.refresh_layout()

    def update_progress(self, event, data):
        with self._lock:
            if not hasattr(self, "_last_progress_data"):
                self._last_progress_data = {
                    "line": 0,
                    "total_line": 1,
                    "raw_total_line": 1,
                    "filtered_total_line": 1,
                    "excluded_total_line": 0,
                    "token": 0,
                    "time": 0,
                    "file_name": "...",
                    "total_requests": 0,
                    "error_requests": 0,
                }

            if data and isinstance(data, dict):
                self._last_progress_data.update(data)
            d = self._last_progress_data
            completed, total = d["line"], d["total_line"]
            tokens, elapsed = d["token"], d["time"]

            calc_tokens = d.get("session_token", tokens)
            calc_requests = d.get("session_requests", d.get("total_requests", 0))

            if elapsed > 0:
                rpm = (calc_requests / (elapsed / 60))
                tpm_k = (calc_tokens / (elapsed / 60) / 1000)
            else: rpm, tpm_k = 0, 0

            total_req = d.get("total_requests", 0)
            success_req = d.get("success_requests", 0)
            error_req = d.get("error_requests", 0)

            if total_req > 0:
                s_rate = (success_req / total_req) * 100
                e_rate = (error_req / total_req) * 100
            else:
                s_rate, e_rate = 0, 0

            # 更新 Header (详细模式专用)
            if self.show_detailed and self.parent_cli:
                cfg = self.parent_cli.config
                src = cfg.get("source_language", "Unknown")
                tgt = cfg.get("target_language", "Unknown")
                tp = cfg.get("target_platform", "Unknown")
                status_line = f"[bold cyan]AiNiee-Next[/bold cyan] | {src} -> {tgt} | API: {tp} | Progress: {completed}/{total}"
                self.layout["header"].update(Panel(status_line, title="Status", border_style="cyan"))

            if self.taken_over:
                target_pane = "body" if self.show_detailed else "upper"

            # 检查是否为队列模式
            is_queue_mode = False
            if self.parent_cli and hasattr(self.parent_cli, '_is_queue_mode'):
                is_queue_mode = self.parent_cli._is_queue_mode

            current_file = d.get("file_name", "...")

            if is_queue_mode and self.parent_cli:
                try:
                    import os
                    from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager
                    qm = QueueManager()
                    if qm.current_task_index >= 0 and qm.current_task_index < len(qm.tasks):
                        current_task = qm.tasks[qm.current_task_index]
                        if current_task and hasattr(current_task, 'input_path'):
                            current_file = os.path.basename(current_task.input_path)
                except:
                    pass

            if self.web_task_manager:
                web_status = "paused" if self.current_status_key == "label_status_paused" else "running"
                self.web_task_manager.push_stats({
                    "rpm": rpm,
                    "tpm": tpm_k,
                    "totalProgress": total,
                    "completedProgress": completed,
                    "totalTokens": tokens,
                    "currentFile": current_file,
                    "status": web_status,
                    "successRate": s_rate,
                    "errorRate": e_rate
                })

            rpm_str = f"{rpm:.2f}"
            tpm_str = f"{tpm_k:.2f}k"
            status_text = self._get_i18n(self.current_status_key)
            filter_progress_hint = self._format_filter_progress_hint(d)

            if is_queue_mode:
                hotkeys = self._get_i18n("label_shortcuts_queue")
            else:
                if self.parent_cli and self.parent_cli.config.get("translation_consistency_enhancement", False):
                    hotkeys = self._get_i18n("label_shortcuts_consistency")
                else:
                    hotkeys = self._get_i18n("label_shortcuts")

            diagnostic_hint = ""
            if self.parent_cli and getattr(self.parent_cli, '_show_diagnostic_hint', False):
                diagnostic_hint = f"\n[bold yellow]{self._get_i18n('msg_api_error_hint')}[/bold yellow]"

            current_threads = "Auto"
            if self.parent_cli and hasattr(self.parent_cli, 'task_executor'):
                current_threads = self.parent_cli.task_executor.config.actual_thread_counts

            stats_markup = (
                f"File: [bold]{current_file}[/] | Progress: [bold]{completed}/{total}[/] | Threads: [bold]{current_threads}[/] | RPM: [bold]{rpm_str}[/] | TPM: [bold]{tpm_str}[/]\n"
                f"S-Rate: [bold green]{s_rate:.1f}%[/] | E-Rate: [bold red]{e_rate:.1f}%[/] | Tokens: [bold]{tokens}[/] | Status: [{self.current_status_color}]{status_text}[/{self.current_status_color}] | {hotkeys}"
            )
            if filter_progress_hint:
                stats_markup += f"\n[yellow]{filter_progress_hint}[/yellow]"
            if diagnostic_hint:
                stats_markup += diagnostic_hint
            self.stats_text = Text.from_markup(stats_markup, style="cyan")

            is_start = data.get('is_start') if isinstance(data, dict) else False
            if is_start:
                self._progress_paused = False
                self._progress_pause_started_at = None
                self.progress.reset(self.task_id, total=total, completed=completed, action=self._get_i18n('label_processing'))
            else:
                self.progress.update(self.task_id, total=total, completed=completed, action=self._get_i18n('label_processing'))

            self.refresh_layout()
