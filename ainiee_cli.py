import os
import sys

# Silence TF and other C++ logs that break TUI
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['GLOG_minloglevel'] = '3'

import re
import time
import signal
import threading
import warnings
import glob
import rapidjson as json
import shutil
import subprocess
import argparse
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table
from rich.live import Live

warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from ModuleFolders.Base.Base import Base, TUIHandler
from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.CLI.OperationLogger import OperationLogger, log_operation
from ModuleFolders.UserInterface.AppI18N import (
    detect_system_language,
    initialize_i18n,
    switch_runtime_language,
)
from ModuleFolders.UserInterface.BannerRenderer import build_status_banner
from ModuleFolders.UserInterface.UIHelpers import (
    ensure_calibre_available,
    get_calibre_lang_code,
)
from ModuleFolders.UserInterface.WebLogger import WebLogger
from ModuleFolders.UserInterface.RuntimeBootstrap import ensure_runtime_bootstrap, start_background_prewarm
from ModuleFolders.UserInterface.ConsoleInputGuard import suppress_console_mouse_input
from ModuleFolders.UserInterface.ConfigExperience import calculate_output_path
from ModuleFolders.Infrastructure.TaskConfig.ConfigProfileService import (
    list_profile_names,
    load_effective_config,
    load_root_config,
    sanitize_profile_name,
    save_effective_config,
    save_root_config,
)
from ModuleFolders.Infrastructure.TaskConfig.PolishingMode import (
    POLISH_SOURCE_TEXT,
    POLISH_TRANSLATED_TEXT,
    normalize_polishing_mode,
    polishing_mode_i18n_key,
)



console = Console()
current_lang, i18n = initialize_i18n(PROJECT_ROOT)
SPONSOR_IDS = ("往生", "Kingxiao")
WEB_TASK_API_KEY_ENV = "AINIEE_WEB_TASK_API_KEY"


def _consume_web_task_api_key(args, environ=None):
    """Apply a Web worker credential override and remove it from the environment."""
    environment = os.environ if environ is None else environ
    api_key = environment.pop(WEB_TASK_API_KEY_ENV, None)
    if api_key and not args.api_key:
        args.api_key = api_key


class CLIMenu:
    def __init__(self):
        self.root_config_path = os.path.join(PROJECT_ROOT, "Resource", "config.json")
        self.profiles_dir = os.path.join(PROJECT_ROOT, "Resource", "profiles")
        self.rules_profiles_dir = os.path.join(PROJECT_ROOT, "Resource", "rules_profiles")
        os.makedirs(self.rules_profiles_dir, exist_ok=True)

        self._plugin_manager = None
        self._file_reader = None
        self._file_outputer = None
        self._cache_manager = None
        self._task_executor = None
        self._file_selector = None
        self._update_manager = None
        self._input_listener = None
        self._smart_diagnostic = None
        self._api_manager = None
        self._glossary_menu = None
        self._ai_proofread_menu = None
        self._automation_menu = None
        self._editor_menu_handler = None
        self._diagnostic_menu_handler = None
        self._settings_menu_handler = None
        self._plugin_settings_menu_handler = None
        self._crash_handler = None
        self._web_runtime_bridge = None
        self._mcp_runtime_bridge = None
        self.runtime_config_overrides = {}
        self._command_mode_runner = None
        self._export_flow = None
        self._profile_menu_handler = None
        self._task_queue_menu_handler = None
        self._manga_runtime_menu_handler = None
        self._prompt_selection_guard = None
        self._terminal_compatibility = None
        self._config_experience = None
        self._recent_projects_menu = None
        self._rule_preview_menu = None

        self.config = {}
        self.root_config = {}
        self.active_profile_name = "default"
        self.active_rules_profile_name = "default"
        self.load_config()

        # 全局属性供子模块使用
        self.PROJECT_ROOT = PROJECT_ROOT

        # 加载 Base 翻译库以供子模块 (Dry Run等) 使用
        self._sync_base_interface_language()

        signal.signal(signal.SIGINT, self.signal_handler)
        self.task_running, self.original_print = False, Base.print
        self.web_server_thread = None
        self.mcp_server_process = None
        self.mcp_server_active = False

        # 操作记录器 (必须在 _check_web_server_dist 之前初始化，因为 display_banner 会使用它)
        self.operation_logger = OperationLogger()
        if self.config.get("enable_operation_logging", False):
            self.operation_logger.enable()

        self._api_error_count = 0  # API错误计数
        self._api_error_messages = []  # 存储最近的API错误信息
        self._show_diagnostic_hint = False  # 是否显示诊断提示

        # --- WebServer 独立检测 (必须在 operation_logger 之后) ---
        self._check_web_server_dist()

    @property
    def i18n(self):
        return i18n

    def _sync_base_interface_language(self):
        Base.i18n = i18n

    def apply_interface_language(self, lang):
        global current_lang, i18n

        if lang not in ("zh_CN", "zh_CNTW", "ja", "en", "ko", "ru", "es"):
            lang = detect_system_language()

        current_lang = lang
        self.config["interface_language"] = current_lang
        i18n = switch_runtime_language(PROJECT_ROOT, current_lang)
        self._file_selector = None
        self._update_manager = None
        self._smart_diagnostic = None
        self._sync_base_interface_language()

    @property
    def plugin_manager(self):
        if self._plugin_manager is None:
            from ModuleFolders.Base.PluginManager import PluginManager

            self._plugin_manager = PluginManager()
            self._plugin_manager.load_plugins_from_directory(os.path.join(PROJECT_ROOT, "PluginScripts"))
            if "plugin_enables" in self.root_config:
                self._plugin_manager.update_plugins_enable(self.root_config["plugin_enables"])
        return self._plugin_manager

    @property
    def file_reader(self):
        if self._file_reader is None:
            ensure_runtime_bootstrap()
            from ModuleFolders.Domain.FileReader.FileReader import FileReader

            self._file_reader = FileReader()
        return self._file_reader

    @property
    def file_outputer(self):
        if self._file_outputer is None:
            from ModuleFolders.Domain.FileOutputer.FileOutputer import FileOutputer

            self._file_outputer = FileOutputer()
        return self._file_outputer

    @property
    def cache_manager(self):
        if self._cache_manager is None:
            ensure_runtime_bootstrap()
            from ModuleFolders.Infrastructure.Cache.CacheManager import CacheManager

            self._cache_manager = CacheManager()
        return self._cache_manager

    @property
    def task_executor(self):
        if self._task_executor is None:
            ensure_runtime_bootstrap()
            from ModuleFolders.Service.TaskExecutor.TaskExecutor import TaskExecutor

            self._task_executor = TaskExecutor(
                self.plugin_manager,
                self.cache_manager,
                self.file_reader,
                self.file_outputer,
            )
        return self._task_executor

    @property
    def file_selector(self):
        if (
            self._file_selector is None
            or getattr(getattr(self._file_selector, "i18n", None), "lang", None) != current_lang
        ):
            from ModuleFolders.UserInterface.FileSelector import FileSelector

            self._file_selector = FileSelector(self.i18n)
        return self._file_selector

    @property
    def update_manager(self):
        if (
            self._update_manager is None
            or getattr(getattr(self._update_manager, "i18n", None), "lang", None) != current_lang
        ):
            from ModuleFolders.Infrastructure.Update.UpdateManager import UpdateManager

            self._update_manager = UpdateManager(self.i18n)
        return self._update_manager

    @property
    def input_listener(self):
        if self._input_listener is None:
            from ModuleFolders.UserInterface.InputListener import InputListener

            self._input_listener = InputListener()
        return self._input_listener

    @property
    def smart_diagnostic(self):
        if self._smart_diagnostic is None or getattr(self._smart_diagnostic, "lang", None) != current_lang:
            from ModuleFolders.Diagnostic import SmartDiagnostic

            self._smart_diagnostic = SmartDiagnostic(lang=current_lang)
        return self._smart_diagnostic

    @property
    def api_manager(self):
        if self._api_manager is None:
            from ModuleFolders.UserInterface.APIManager import APIManager

            self._api_manager = APIManager(self)
        return self._api_manager

    @property
    def glossary_menu(self):
        if self._glossary_menu is None:
            from ModuleFolders.UserInterface.GlossaryMenu import GlossaryMenu

            self._glossary_menu = GlossaryMenu(self)
        return self._glossary_menu

    @property
    def ai_proofread_menu(self):
        if self._ai_proofread_menu is None:
            from ModuleFolders.UserInterface.AIProofreadMenu import AIProofreadMenu

            self._ai_proofread_menu = AIProofreadMenu(self)
        return self._ai_proofread_menu

    @property
    def automation_menu(self):
        if self._automation_menu is None:
            from ModuleFolders.UserInterface.AutomationMenu import AutomationMenu

            self._automation_menu = AutomationMenu(self)
        return self._automation_menu

    @property
    def editor_menu_handler(self):
        if self._editor_menu_handler is None:
            from ModuleFolders.UserInterface.EditorMenu import EditorMenu

            self._editor_menu_handler = EditorMenu(self)
        return self._editor_menu_handler

    @property
    def diagnostic_menu_handler(self):
        if self._diagnostic_menu_handler is None:
            from ModuleFolders.UserInterface.DiagnosticMenu import DiagnosticMenu

            self._diagnostic_menu_handler = DiagnosticMenu(self)
        return self._diagnostic_menu_handler

    @property
    def settings_menu_handler(self):
        if self._settings_menu_handler is None:
            from ModuleFolders.UserInterface.SettingsMenu import SettingsMenu

            self._settings_menu_handler = SettingsMenu(self)
        return self._settings_menu_handler

    @property
    def plugin_settings_menu_handler(self):
        if self._plugin_settings_menu_handler is None:
            from ModuleFolders.UserInterface.PluginSettingsMenu import PluginSettingsMenu

            self._plugin_settings_menu_handler = PluginSettingsMenu(self)
        return self._plugin_settings_menu_handler

    @property
    def crash_handler(self):
        if self._crash_handler is None:
            from ModuleFolders.UserInterface.CrashHandler import CrashHandler

            self._crash_handler = CrashHandler(self)
        return self._crash_handler

    @property
    def web_runtime_bridge(self):
        if self._web_runtime_bridge is None:
            from ModuleFolders.UserInterface.WebRuntimeBridge import WebRuntimeBridge

            self._web_runtime_bridge = WebRuntimeBridge(self)
        return self._web_runtime_bridge

    @property
    def mcp_runtime_bridge(self):
        if self._mcp_runtime_bridge is None:
            from ModuleFolders.UserInterface.MCPRuntimeBridge import MCPRuntimeBridge

            self._mcp_runtime_bridge = MCPRuntimeBridge(self)
        return self._mcp_runtime_bridge

    @property
    def command_mode_runner(self):
        if self._command_mode_runner is None:
            from ModuleFolders.UserInterface.CommandModeRunner import CommandModeRunner

            self._command_mode_runner = CommandModeRunner(self)
        return self._command_mode_runner

    @property
    def export_flow(self):
        if self._export_flow is None:
            from ModuleFolders.UserInterface.ExportFlow import ExportFlow

            self._export_flow = ExportFlow(self)
        return self._export_flow

    @property
    def profile_menu_handler(self):
        if self._profile_menu_handler is None:
            from ModuleFolders.UserInterface.ProfileMenu import ProfileMenu

            self._profile_menu_handler = ProfileMenu(self)
        return self._profile_menu_handler

    @property
    def task_queue_menu_handler(self):
        if self._task_queue_menu_handler is None:
            from ModuleFolders.UserInterface.TaskQueueMenu import TaskQueueMenu

            self._task_queue_menu_handler = TaskQueueMenu(self)
        return self._task_queue_menu_handler

    @property
    def manga_runtime_menu_handler(self):
        if self._manga_runtime_menu_handler is None:
            from ModuleFolders.UserInterface.MangaRuntimeMenu import MangaRuntimeMenu

            self._manga_runtime_menu_handler = MangaRuntimeMenu(self)
        return self._manga_runtime_menu_handler

    @property
    def prompt_selection_guard(self):
        if self._prompt_selection_guard is None:
            from ModuleFolders.UserInterface.PromptSelectionGuard import PromptSelectionGuard

            self._prompt_selection_guard = PromptSelectionGuard(self)
        return self._prompt_selection_guard

    @property
    def terminal_compatibility(self):
        if self._terminal_compatibility is None:
            from ModuleFolders.UserInterface.TerminalCompatibility import TerminalCompatibilityHelper

            self._terminal_compatibility = TerminalCompatibilityHelper(self)
        return self._terminal_compatibility

    @property
    def config_experience(self):
        if self._config_experience is None:
            from ModuleFolders.UserInterface.ConfigExperience import ConfigExperience

            self._config_experience = ConfigExperience(self)
        return self._config_experience

    @property
    def recent_projects_menu(self):
        if self._recent_projects_menu is None:
            from ModuleFolders.UserInterface.RecentProjectsMenu import RecentProjectsMenu

            self._recent_projects_menu = RecentProjectsMenu(self)
        return self._recent_projects_menu

    @property
    def rule_preview_menu(self):
        if self._rule_preview_menu is None:
            from ModuleFolders.UserInterface.RulePreview import RulePreviewMenu

            self._rule_preview_menu = RulePreviewMenu(self)
        return self._rule_preview_menu

    def _is_task_ui_instance(self):
        ui = getattr(self, "ui", None)
        if ui is None:
            return False
        try:
            from ModuleFolders.UserInterface.TaskUI import TaskUI

            return isinstance(ui, TaskUI)
        except Exception:
            return False

    def _format_diagnostic_result(self, result):
        from ModuleFolders.Diagnostic import DiagnosticFormatter

        return DiagnosticFormatter.format_result(result, current_lang)

    def _check_web_server_dist(self):
        """检查 WebServer 编译产物是否存在"""
        dist_path = os.path.join(PROJECT_ROOT, "Tools", "WebServer", "dist", "index.html")
        if not os.path.exists(dist_path):
            self.display_banner()
            self.update_manager.setup_web_server()

        # 队列日志监控相关
        self._last_queue_log_size = 0
        self._queue_log_monitor_thread = None
        self._queue_log_monitor_running = False

    def handle_monitor_shortcut(self):
        self.web_runtime_bridge.handle_monitor_shortcut()

    def handle_queue_editor_shortcut(self):
        self.web_runtime_bridge.handle_queue_editor_shortcut()

    def handle_web_queue_shortcut(self):
        self.web_runtime_bridge.handle_web_queue_shortcut()

    def start_queue_log_monitor(self):
        self.web_runtime_bridge.start_queue_log_monitor()

    def stop_queue_log_monitor(self):
        self.web_runtime_bridge.stop_queue_log_monitor()

    def _queue_log_monitor_loop(self):
        self.web_runtime_bridge._queue_log_monitor_loop()

    def _parse_and_push_stats(self, stats_line):
        self.web_runtime_bridge._parse_and_push_stats(stats_line)

    def _get_webserver_port(self):
        return self.web_runtime_bridge._get_webserver_port()

    def _get_internal_api_base(self):
        return self.web_runtime_bridge._get_internal_api_base()

    def _get_web_base_url(self):
        return self.web_runtime_bridge._get_web_base_url()

    def _push_stats_to_webserver(self, stats_data):
        return self.web_runtime_bridge._push_stats_to_webserver(stats_data)

    def _push_log_to_webserver(self, message, log_type="info"):
        return self.web_runtime_bridge._push_log_to_webserver(message, log_type)

    def _display_new_queue_logs(self, log_file):
        self.web_runtime_bridge._display_new_queue_logs(log_file)

    def ensure_web_server_running(self):
        self.web_runtime_bridge.ensure_web_server_running()

    def show_queue_status(self, qm):
        self.web_runtime_bridge.show_queue_status(qm)

    def open_queue_page(self):
        self.web_runtime_bridge.open_queue_page()

    def _run_queue_editor(self, queue_manager):
        self.web_runtime_bridge.run_queue_editor(queue_manager)

    def _host_create_profile(self, new_name, base_name=None):
        self.web_runtime_bridge.host_create_profile(new_name, base_name)

    def _host_rename_profile(self, old_name, new_name):
        self.web_runtime_bridge.host_rename_profile(old_name, new_name)

    def _host_delete_profile(self, name):
        self.web_runtime_bridge.host_delete_profile(name)

    def _host_run_queue(self):
        return self.web_runtime_bridge.host_run_queue()

    def run_non_interactive(self, args):
        return self.command_mode_runner.run(args)


    def _migrate_and_load_profiles(self):
        requested_profile = self.active_profile_name
        self.config = load_effective_config(
            root_config=self.root_config,
            active_profile_name=self.active_profile_name,
            active_rules_profile_name=self.active_rules_profile_name,
            create_missing=False,
            interface_language=current_lang,
        )
        self.active_profile_name = self.config.get("active_profile", "default")
        self.active_rules_profile_name = self.config.get("active_rules_profile", "default")
        self.root_config["active_profile"] = self.active_profile_name
        self.root_config["active_rules_profile"] = self.active_rules_profile_name

        if requested_profile and requested_profile != self.active_profile_name:
            console.print(f"[bold yellow]Warning: Active profile '{requested_profile}' not found or invalid; using '{self.active_profile_name}'.[/bold yellow]")

    def load_config(self, active_profile_name=None, active_rules_profile_name=None):
        self.root_config = load_root_config()
        self.active_profile_name = active_profile_name or self.root_config.get("active_profile", "default")
        self.active_rules_profile_name = active_rules_profile_name or self.root_config.get("active_rules_profile", "default")

        self._migrate_and_load_profiles()
        self._after_config_loaded()

    def _after_config_loaded(self):
        if self.config.get("interface_language") and self.config.get("interface_language") != current_lang:
            self.apply_interface_language(self.config.get("interface_language"))
        if getattr(self, "_plugin_manager", None) is not None and "plugin_enables" in self.root_config:
            self._plugin_manager.update_plugins_enable(self.root_config["plugin_enables"])

    def save_config(self, save_root=False):
        self.root_config = save_effective_config(
            self.config,
            root_config=self.root_config,
            active_profile_name=self.active_profile_name,
            active_rules_profile_name=self.active_rules_profile_name,
            write_root=save_root,
            prefer_sdk_request_mode="sdk_request_mode" in self.config,
        )
        self.active_profile_name = self.root_config.get("active_profile", self.active_profile_name)
        self.active_rules_profile_name = self.root_config.get("active_rules_profile", self.active_rules_profile_name)

    def switch_active_profiles(self, profile_name=None, rules_profile_name=None):
        if profile_name is not None:
            profile_name = sanitize_profile_name(profile_name)
        if rules_profile_name is not None:
            rules_profile_name = sanitize_profile_name(rules_profile_name, allow_none=True)

        self.root_config = load_root_config()
        if profile_name is not None:
            self.root_config["active_profile"] = profile_name
            self.active_profile_name = profile_name
        if rules_profile_name is not None:
            self.root_config["active_rules_profile"] = rules_profile_name
            self.active_rules_profile_name = rules_profile_name
        self._migrate_and_load_profiles()
        self._after_config_loaded()
        save_root_config(self.root_config)

    def switch_active_profile(self, profile_name):
        self.switch_active_profiles(profile_name=profile_name)

    def switch_active_rules_profile(self, rules_profile_name):
        self.switch_active_profiles(rules_profile_name=rules_profile_name)

    def _update_recent_projects(self, project_path):
        recent = self.root_config.get("recent_projects", [])

        # --- Migration & Cleanup ---
        # Convert any old string-only entries to new object format
        new_recent = []
        current_pinned = False
        for item in recent:
            if isinstance(item, str):
                new_recent.append({"path": item, "profile": "default", "rules_profile": "default"})
            elif isinstance(item, dict) and "path" in item:
                new_recent.append(item)

        # Remove current project if it exists in list (compare by path)
        kept_recent = []
        for item in new_recent:
            if item["path"] == project_path:
                current_pinned = bool(item.get("pinned", False))
            else:
                kept_recent.append(item)

        # Add current project at start
        kept_recent.insert(0, {
            "path": project_path,
            "profile": self.active_profile_name,
            "rules_profile": self.active_rules_profile_name,
            "pinned": current_pinned,
        })

        pinned = [item for item in kept_recent if item.get("pinned")]
        normal = [item for item in kept_recent if not item.get("pinned")]
        self.root_config["recent_projects"] = pinned + normal[: max(0, 10 - len(pinned))]
        save_root_config(self.root_config)

    def _auto_merge_batch_ebooks(self, merge_input_dir, merge_output_dir, merge_name, allow_non_series_prompt=True):
        """批量目录任务完成后，自动调用批量电子书整合脚本进行合并。"""
        import collections

        conv_script = os.path.join(PROJECT_ROOT, "批量电子书整合.py")
        if not os.path.isfile(conv_script):
            self.ui.log(f"[dim]{i18n.get('msg_batch_merge_script_missing')}[/dim]")
            return False

        supported_extensions = (
            '.pdf', '.cbz', '.cbr', '.epub', '.mobi', '.azw3', '.docx', '.txt',
            '.kepub', '.fb2', '.lit', '.lrf', '.pdb', '.pmlz', '.rb', '.rtf',
            '.tcr', '.txtz', '.htmlz'
        )
        try:
            merge_candidates = [
                f for f in os.listdir(merge_input_dir)
                if os.path.isfile(os.path.join(merge_input_dir, f)) and f.lower().endswith(supported_extensions)
            ]
        except Exception as e:
            self.ui.log(i18n.get("msg_batch_merge_failed").format(str(e)))
            return False

        if len(merge_candidates) < 2:
            self.ui.log(f"[dim]{i18n.get('msg_batch_merge_not_enough_files')}[/dim]")
            return False

        keyword_counter = collections.Counter()
        for file_name in merge_candidates:
            stem = os.path.splitext(file_name)[0]
            stem = re.sub(r"(?i)(?:_translated|\.translated)$", "", stem).strip()

            while True:
                old_stem = stem
                # 只按“同名 + 末尾数字序号”思路去掉尾巴，如：作品名 01 / 作品名-02 / 作品名(003)
                stem = re.sub(r"[\s._\-]*[（(【\[]?\d{1,4}[】\])）]?$", "", stem).strip()
                stem = re.sub(r"[\s._\-]+$", "", stem).strip()
                if stem == old_stem:
                    break

            keyword = re.sub(r"[\s._\-]+", " ", stem).strip()
            if len(keyword) >= 2:
                keyword_counter[keyword] += 1

        detected_keywords = keyword_counter.most_common(3)
        top_count = detected_keywords[0][1] if detected_keywords else 0
        threshold = max(2, int(len(merge_candidates) * 0.6 + 0.5))
        is_series_like = top_count >= threshold

        if not is_series_like and allow_non_series_prompt:
            keyword_text = ", ".join([f"{k} x{v}" for k, v in detected_keywords]) if detected_keywords else i18n.get("label_none")
            self.ui.log(f"[yellow]{i18n.get('msg_batch_merge_non_series_detected').format(keyword_text)}[/yellow]")
            if not Confirm.ask(i18n.get("prompt_batch_merge_disable_for_non_series"), default=False):
                self.ui.log(f"[yellow]{i18n.get('msg_batch_merge_auto_disabled')}[/yellow]")
                return False

        self.ui.log(i18n.get("msg_batch_merge_start").format(merge_name))
        cmd = [
            "uv", "run", conv_script,
            "-p", merge_input_dir,
            "-f", "epub",
            "-m", "novel",
            "-op", merge_output_dir,
            "-o", merge_name,
            "-t", merge_name,
            "-l", get_calibre_lang_code(current_lang),
            "--auto-merge",
            "--AiNiee",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                merged_name = f"{merge_name}.epub"
                merged_path = os.path.join(merge_output_dir, merged_name)
                if os.path.exists(merged_path):
                    self.ui.log(i18n.get("msg_batch_merge_success").format(os.path.basename(merged_path)))
                else:
                    self.ui.log(i18n.get("msg_batch_merge_success").format(merged_name))
                return True

            err_detail = (result.stderr or result.stdout or "").strip()
            if err_detail:
                err_detail = err_detail.splitlines()[-1][:240]
            else:
                err_detail = "Unknown error"
            self.ui.log(i18n.get("msg_batch_merge_failed").format(err_detail))
        except FileNotFoundError as e:
            missing_cmd = e.filename or str(e)
            self.ui.log(i18n.get("msg_batch_merge_failed").format(f"Command not found: {missing_cmd}"))
        except Exception as e:
            self.ui.log(i18n.get("msg_batch_merge_failed").format(str(e)))

        return False

    def signal_handler(self, sig, frame):
        if self.task_running:
            if getattr(self, "stop_requested", False):
                console.print("\n[bold red]Force quitting immediately...[/bold red]")
                os._exit(1)

            console.print("\n[yellow]Stopping task... (Press Ctrl+C again to force quit)[/yellow]")
            self.stop_requested = True

            from ModuleFolders.Base.EventManager import EventManager
            EventManager.get_singleton().emit(Base.EVENT.TASK_STOP, {"reason": "stop"})
        elif getattr(self, "web_server_active", False) or getattr(self, "mcp_server_active", False):
            # WebServer / MCP 运行时，抛出 KeyboardInterrupt 让对应流程自行清理并返回菜单或退出
            raise KeyboardInterrupt
        else:
            sys.exit(0)

    def _fetch_github_status_async(self):
        """后台异步获取 GitHub 状态信息"""
        self._github_fetch_event = threading.Event()
        self._github_fetch_failed = False

        def fetch():
            try:
                lang = getattr(i18n, 'lang', 'en')
                info = self.update_manager.get_status_bar_info(lang)
                # 检查是否真的获取到了数据
                if info and (info.get("commit_text") or info.get("release_text")):
                    self._cached_github_info = info
                    self._github_fetch_failed = False
                else:
                    self._cached_github_info = None
                    self._github_fetch_failed = True
            except:
                self._cached_github_info = None
                self._github_fetch_failed = True
            finally:
                self._github_fetch_event.set()

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _maybe_start_background_prewarm(self):
        if not self.config.get("enable_background_prewarm", True):
            return

        start_background_prewarm(
            enabled=True,
            should_continue=lambda: (
                not getattr(self, "task_running", False)
                and bool(self.config.get("enable_background_prewarm", True))
            ),
        )

    def display_banner(self):
        console.clear()
        console.print(build_status_banner(self, PROJECT_ROOT))

    def run_wizard(self):
        self.display_banner()
        console.print(Panel("[bold cyan]Welcome to AiNiee-Next! Let's run a quick setup wizard.[/bold cyan]"))
        
        # 1. UI Language
        self.first_time_lang_setup()
        
        # 2. Translation Languages
        console.print(f"\n[bold]1. {i18n.get('setting_src_lang')}/{i18n.get('setting_tgt_lang')}[/bold]")
        self.config["source_language"] = Prompt.ask(i18n.get('prompt_source_lang'), default="auto")
        self.config["target_language"] = Prompt.ask(i18n.get('prompt_target_lang'), default="Chinese")
        
        # 3. API Platform
        console.print(f"\n[bold]2. {i18n.get('menu_api_settings')}[/bold]")
        console.print(f"1. {i18n.get('menu_api_online')}\n2. {i18n.get('menu_api_local')}")
        api_choice = IntPrompt.ask(i18n.get('prompt_select'), choices=["1", "2"], default=1)
        self.api_manager.select_api_menu(online=(api_choice == 1))

        # 4. Validation
        console.print(f"\n[bold]3. {i18n.get('menu_api_validate')}[/bold]")
        self.api_manager.validate_api()
        
        # 5. Save and complete
        self.root_config["wizard_completed"] = True
        self.save_config(save_root=True)
        self.save_config() # Save the profile as well
        
        console.print(f"\n[bold green]✓ {i18n.get('msg_saved')} Wizard complete! Entering the main menu...[/bold green]")
        time.sleep(2)

    def _detect_terminal_capability(self):
        return self.terminal_compatibility.detect_terminal_capability()

    def _check_terminal_compatibility(self):
        return self.terminal_compatibility.check_terminal_compatibility()

    def main_menu(self):
        # 检查终端兼容性
        self._check_terminal_compatibility()

        if not self.root_config.get("wizard_completed"):
            self.run_wizard()

        self._maybe_start_background_prewarm()

        # 启动时自动检查更新
        if self.config.get("enable_auto_update", False):
            self.update_manager.check_update(silent=True)

        # 启动时获取 GitHub 状态信息 (后台异步)
        if self.config.get("enable_github_status_bar", True):
            self._fetch_github_status_async()
            # 等待异步获取完成（最多等待3秒）
            if hasattr(self, '_github_fetch_event'):
                self._github_fetch_event.wait(timeout=3)

        while True:
            self._maybe_start_background_prewarm()
            if self.config.get("main_menu_layout", "flat") == "flat":
                if self._show_flat_main_menu():
                    continue
                return
            if self._show_grouped_main_menu():
                continue
            return

    def _menu_label(self, menu_key: str) -> str:
        label = i18n.get(f"menu_{menu_key}")
        if menu_key == "start_web_server" and label == f"menu_{menu_key}":
            return "Start Web Server"
        if menu_key == "start_mcp_server" and label == f"menu_{menu_key}":
            return "Start MCP Server"
        if menu_key == "task_queue" and label == f"menu_{menu_key}":
            return i18n.get("menu_task_queue")
        if menu_key == "automation" and label == f"menu_{menu_key}":
            return i18n.get("menu_automation")
        if menu_key == "start_all_in_one" and label == f"menu_{menu_key}":
            return i18n.get("menu_start_all_in_one")
        if menu_key == "start_manga_translation" and label == f"menu_{menu_key}":
            return "漫画翻译 (MangaCore)"
        if menu_key == "manga_runtime_manager" and label == f"menu_{menu_key}":
            return "MangaCore Runtime 管理"
        return label

    def _main_menu_actions(self):
        return {
            "start_translation": lambda: self.run_task(TaskType.TRANSLATION),
            "start_manga_translation": self.run_manga_translation,
            "start_polishing": lambda: self.run_task(TaskType.POLISH),
            "start_all_in_one": self.run_all_in_one,
            "export_only": self.run_export_only,
            "editor": self.editor_menu_handler.show,
            "settings": self.settings_menu,
            "api_settings": self.api_manager.api_settings_menu,
            "glossary": self.glossary_menu.prompt_menu,
            "plugin_settings": self.plugin_settings_menu,
            "task_queue": self.task_queue_menu,
            "automation": self.automation_menu.show,
            "profiles": self.profiles_menu,
            "qa": self.qa_menu,
            "acknowledgements": self.show_acknowledgements,
            "update": self.update_manager.start_update,
            "update_web": lambda: self.update_manager.setup_web_server(manual=True),
            "start_web_server": self.start_web_server,
            "start_mcp_server": self.start_mcp_server,
            "manga_runtime_manager": self.manga_runtime_menu_handler.show,
        }

    def _log_main_menu_action(self, label: str):
        self.operation_logger.log(f"主菜单 -> {label}", "MENU")

    def _show_flat_main_menu(self) -> bool:
        self.display_banner()
        table = Table(show_header=False, box=None)
        menus = ["start_translation", "start_manga_translation", "start_polishing", "start_all_in_one", "export_only", "editor", "settings", "api_settings", "glossary", "plugin_settings", "task_queue", "automation", "profiles", "qa", "update", "update_web", "start_web_server", "start_mcp_server", "manga_runtime_manager", "acknowledgements"]
        colors = ["green", "cyan", "green", "bold green", "magenta", "bold cyan", "blue", "blue", "yellow", "cyan", "bold blue", "bold yellow", "cyan", "yellow", "dim", "bold magenta", "magenta", "bold magenta", "cyan", "bold yellow"]
        actions = self._main_menu_actions()

        for i, (menu_key, color) in enumerate(zip(menus, colors), 1):
            table.add_row(f"[{color}]{i}.[/]", self._menu_label(menu_key))

        table.add_row("[red]0.[/]", i18n.get("menu_exit"))
        console.print(table)
        choice = IntPrompt.ask(f"\n{i18n.get('prompt_select')}", choices=[str(i) for i in range(len(menus) + 1)], show_choices=False)
        console.print("\n")
        if choice == 0:
            sys.exit()
        menu_key = menus[choice - 1]
        self._log_main_menu_action(self._menu_label(menu_key))
        actions[menu_key]()
        return True

    def _show_grouped_main_menu(self) -> bool:
        self.display_banner()
        groups = [
            ("main_menu_group_tasks", "green", [
                ("start_translation", "green"),
                ("start_manga_translation", "cyan"),
                ("start_polishing", "green"),
                ("start_all_in_one", "bold green"),
            ]),
            ("main_menu_group_automation", "bold yellow", [
                ("automation", "bold yellow"),
                ("task_queue", "bold blue"),
            ]),
            ("main_menu_group_edit_export", "magenta", [
                ("editor", "bold cyan"),
                ("export_only", "magenta"),
            ]),
            ("main_menu_group_config", "blue", [
                ("settings", "blue"),
                ("api_settings", "blue"),
                ("profiles", "cyan"),
                ("manga_runtime_manager", "cyan"),
            ]),
            ("main_menu_group_glossary", "yellow", [
                ("glossary", "yellow"),
            ]),
            ("main_menu_group_services", "bold magenta", [
                ("plugin_settings", "cyan"),
                ("start_web_server", "magenta"),
                ("start_mcp_server", "bold magenta"),
            ]),
            ("main_menu_group_maintenance", "dim", [
                ("qa", "yellow"),
                ("update", "dim"),
                ("update_web", "bold magenta"),
                ("acknowledgements", "bold yellow"),
            ]),
        ]
        table = Table(show_header=False, box=None)
        for i, (group_key, color, _) in enumerate(groups, 1):
            table.add_row(f"[{color}]{i}.[/]", i18n.get(group_key))
        table.add_row("[red]0.[/]", i18n.get("menu_exit"))
        console.print(table)
        choice = IntPrompt.ask(f"\n{i18n.get('prompt_select')}", choices=[str(i) for i in range(len(groups) + 1)], show_choices=False)
        console.print("\n")
        if choice == 0:
            sys.exit()
        group_key, _, items = groups[choice - 1]
        return self._show_grouped_main_submenu(group_key, items)

    def _show_grouped_main_submenu(self, group_key: str, items: list[tuple[str, str]]) -> bool:
        actions = self._main_menu_actions()
        while True:
            self.display_banner()
            table = Table(show_header=False, box=None)
            console.print(Panel(f"[bold]{i18n.get(group_key)}[/bold]"))
            for i, (menu_key, color) in enumerate(items, 1):
                table.add_row(f"[{color}]{i}.[/]", self._menu_label(menu_key))
            table.add_row("[dim]0.[/]", i18n.get("menu_back"))
            console.print(table)
            choice = IntPrompt.ask(f"\n{i18n.get('prompt_select')}", choices=[str(i) for i in range(len(items) + 1)], show_choices=False)
            console.print("\n")
            if choice == 0:
                return True
            menu_key = items[choice - 1][0]
            self._log_main_menu_action(f"{i18n.get(group_key)} -> {self._menu_label(menu_key)}")
            actions[menu_key]()
            return True

    def profiles_menu(self):
        self.profile_menu_handler.show()

    def qa_menu(self):
        self.diagnostic_menu_handler.show()

    def show_acknowledgements(self):
        self.display_banner()
        console.print(Panel(
            i18n.get("acknowledgements_intro"),
            title=f"[bold]{i18n.get('acknowledgements_title')}[/bold]",
            expand=False,
        ))

        table = Table(show_header=True, box=None)
        table.add_column(i18n.get("acknowledgements_sponsor_id"), style="bold cyan")
        for sponsor_id in SPONSOR_IDS:
            table.add_row(sponsor_id)
        console.print(table)

        Prompt.ask(f"\n{i18n.get('msg_press_enter')}")

    def handle_crash(self, error_msg, temp_config=None):
        self.crash_handler.handle_crash(error_msg, temp_config)

    def _analyze_error_with_llm(self, error_msg, temp_config=None):
        return self.crash_handler.analyze_error_with_llm(error_msg, temp_config)

    def _prepare_github_issue(self, error_msg, analysis=None):
        self.crash_handler.prepare_github_issue(error_msg, analysis)

    def _save_error_log(self, error_msg):
        return self.crash_handler.save_error_log(error_msg)

    def first_time_lang_setup(self):
        detected = detect_system_language()
        default_idx = {"zh_CN": 1, "zh_CNTW": 2, "ja": 3, "en": 4, "ko": 5, "ru": 6, "es": 7}.get(detected, 4)

        console.print(Panel(f"[bold cyan]Language Setup / 语言设置 / 語言設定 / 言語設定[/bold cyan]"))
        console.print(f"[dim]Detected System Language: {detected}[/dim]\n")

        table = Table(show_header=False, box=None)
        table.add_row("[cyan]1.[/]", "中文 (简体)")
        table.add_row("[cyan]2.[/]", "中文 (繁體)")
        table.add_row("[cyan]3.[/]", "日本語")
        table.add_row("[cyan]4.[/]", "English")
        table.add_row("[cyan]5.[/]", "한국어")
        table.add_row("[cyan]6.[/]", "Русский")
        table.add_row("[cyan]7.[/]", "Español")
        console.print(table)

        c = IntPrompt.ask("\nSelect / 选择 / 選擇 / 選択", choices=["1", "2", "3", "4", "5", "6", "7"], default=default_idx, show_choices=False)

        selected_lang = {"1": "zh_CN", "2": "zh_CNTW", "3": "ja", "4": "en", "5": "ko", "6": "ru", "7": "es"}[str(c)]
        self.apply_interface_language(selected_lang)
        self.save_config()

    def _scan_cache_files(self):
        """扫描系统中的缓存文件"""
        cache_projects = []

        # 扫描常见位置的缓存文件（只搜索浅层目录，避免卡住）
        search_paths = [
            ".",  # 当前目录
            "./output",  # 默认输出目录
        ]

        # 添加最近使用的项目路径（如果有的话）
        recent_projects = self.config.get("recent_projects", [])
        search_paths.extend(recent_projects)

        # 添加配置中的输出路径
        label_output = self.config.get("label_output_path", "")
        if label_output:
            search_paths.append(label_output)

        # 移除重复路径
        search_paths = list(set(search_paths))

        for base_path in search_paths:
            try:
                if not os.path.exists(base_path):
                    continue

                # 只搜索一层子目录，避免递归搜索卡住
                cache_files = []

                # 直接查找当前目录下的cache文件
                direct_cache = os.path.join(base_path, "cache", "AinieeCacheData.json")
                if os.path.exists(direct_cache):
                    cache_files.append(direct_cache)

                # 查找一层子目录
                try:
                    for subdir in os.listdir(base_path):
                        subdir_path = os.path.join(base_path, subdir)
                        if os.path.isdir(subdir_path):
                            cache_file = os.path.join(subdir_path, "cache", "AinieeCacheData.json")
                            if os.path.exists(cache_file):
                                cache_files.append(cache_file)
                except PermissionError:
                    pass

                # 也直接查找当前目录下的cache文件
                direct_cache = os.path.join(base_path, "cache", "AinieeCacheData.json")
                if os.path.exists(direct_cache):
                    cache_files.append(direct_cache)

                for cache_file in cache_files:
                    try:
                        project_info = self._analyze_cache_file(cache_file)
                        if project_info and project_info not in cache_projects:
                            cache_projects.append(project_info)
                    except Exception:
                        continue  # 跳过损坏的缓存文件

            except Exception:
                continue  # 跳过无法访问的路径

        # 按最后修改时间排序
        cache_projects.sort(key=lambda x: x["modified_time"], reverse=True)
        return cache_projects

    def settings_menu(self):
        self.settings_menu_handler.show()

    def plugin_settings_menu(self):
        self.plugin_settings_menu_handler.show()

    def _i18n_text(self, key, fallback):
        value = i18n.get(key)
        if not value or value == key:
            return fallback
        return value

    def _configure_dynamic_glossary_for_task(self, task_mode, interactive=True):
        self.config["dynamic_glossary_switch"] = False
        self.config["dynamic_glossary_series"] = ""
        self.config["dynamic_glossary_volume"] = None
        self.config["dynamic_glossary_volume_map"] = {}

        if task_mode != TaskType.TRANSLATION or not interactive:
            return

        has_history = self._has_dynamic_glossary_history()
        if not has_history:
            return

        if not Confirm.ask(
            self._i18n_text("confirm_dynamic_glossary_series", "本次翻译文件是否属于一个系列作，需要启用动态术语表按卷号过滤?"),
            default=False,
        ):
            return

        series_label = Prompt.ask(
            self._i18n_text("prompt_dynamic_glossary_series", "请输入系列标签（仅用于本次任务记录，可留空）"),
            default="",
        ).strip()
        volume = IntPrompt.ask(
            self._i18n_text("prompt_dynamic_glossary_volume", "请输入当前翻译卷号"),
            default=1,
        )
        volume = max(1, volume)
        self.config["dynamic_glossary_switch"] = True
        self.config["dynamic_glossary_series"] = series_label
        self.config["dynamic_glossary_volume"] = volume
        self.config["dynamic_glossary_volume_map"] = {}
        console.print(
            f"[cyan]{self._i18n_text('msg_dynamic_glossary_enabled', '已启用动态术语表')}: "
            f"{series_label or '-'} Vol_{volume}[/cyan]"
        )

    def _configure_dynamic_glossary_volume_map(self, cache_project, interactive=True):
        self.config["dynamic_glossary_volume_map"] = {}
        if not interactive or not self.config.get("dynamic_glossary_switch"):
            return
        files = sorted(
            path
            for path, cache_file in getattr(cache_project, "files", {}).items()
            if getattr(cache_file, "items", None)
        )
        if len(files) <= 1:
            return

        console.print(f"\n[cyan]{self._i18n_text('prompt_dynamic_glossary_volume_map_title', '检测到多个文件，请为每个系列文件指定卷号:')}[/cyan]")
        volume_map = {}
        default_volume = self.config.get("dynamic_glossary_volume") or 1
        for file_path in files:
            display_name = os.path.basename(str(file_path)) or str(file_path)
            volume = IntPrompt.ask(
                self._i18n_text("prompt_dynamic_glossary_file_volume", "请输入当前文件卷号") + f" [{display_name}]",
                default=default_volume,
            )
            volume_map[file_path] = max(1, volume)
        self.config["dynamic_glossary_volume_map"] = volume_map
        self.task_executor.config.dynamic_glossary_volume_map = dict(volume_map)

    def _has_dynamic_glossary_history(self):
        for item in self.config.get("prompt_dictionary_data", []) or []:
            if isinstance(item, dict) and isinstance(item.get("history"), list) and item.get("history"):
                return True
        for item in self.config.get("characterization_data", []) or []:
            if isinstance(item, dict) and isinstance(item.get("history"), list) and item.get("history"):
                return True
        return bool(self.config.get("world_building_history") or self.config.get("writing_style_history"))

    def _polishing_mode_label(self, mode):
        mode = normalize_polishing_mode(mode)
        key = polishing_mode_i18n_key(mode)
        value = self._i18n_text(key, key)
        return value if value != key else mode

    def _select_polishing_mode(self):
        current_mode = normalize_polishing_mode(self.config.get("polishing_mode_selection"))
        options = [
            (POLISH_TRANSLATED_TEXT, "prompt_polishing_mode_translated_hint"),
            (POLISH_SOURCE_TEXT, "prompt_polishing_mode_source_hint"),
        ]

        table = Table(show_header=False, box=None)
        for index, (mode, hint_key) in enumerate(options, 1):
            marker = "[green]●[/green]" if mode == current_mode else " "
            table.add_row(
                f"[cyan]{index}.[/cyan]",
                self._polishing_mode_label(mode),
                f"[dim]{self._i18n_text(hint_key, '')}[/dim]",
                marker,
            )
        console.print(
            Panel(
                table,
                title=f"[bold]{self._i18n_text('prompt_polishing_mode_select', 'Select polishing mode')}[/bold]",
                expand=False,
            )
        )
        choice = IntPrompt.ask(
            self._i18n_text("prompt_select", "Select"),
            choices=["1", "2"],
            default=1 if current_mode == POLISH_TRANSLATED_TEXT else 2,
            show_choices=False,
        )
        selected_mode = options[choice - 1][0]
        self.config["polishing_mode_selection"] = selected_mode
        self.save_config()
        console.print(
            f"[cyan]{self._i18n_text('label_polishing_mode', 'Polishing Mode')}: "
            f"{self._polishing_mode_label(selected_mode)}[/cyan]"
        )

    def run_task(self, task_mode, target_path=None, continue_status=False, non_interactive=False, web_mode=False, from_queue=False, skip_prompt_validation=False, save_runtime_config=True, skip_preflight=False, automation_progress=False):
        # 如果是非交互模式，直接跳过菜单
        if target_path is None:
            last_path = self.config.get("label_input_path")
            can_resume = False
            
            if last_path and os.path.exists(last_path):
                abs_last = os.path.abspath(last_path)
                last_parent = os.path.dirname(abs_last)
                last_base = os.path.basename(abs_last)
                if os.path.isfile(last_path):
                    last_base = os.path.splitext(last_base)[0]
                last_opath = os.path.join(last_parent, f"{last_base}_AiNiee_Output")
                if os.path.exists(os.path.join(last_opath, "cache", "AinieeCacheData.json")):
                    can_resume = True

            # Input Mode Selection
            console.clear()
            
            menu_text = f"1. {i18n.get('mode_single_file')}\n2. {i18n.get('mode_batch_folder')}"
            menu_text += f"\nE. {i18n.get('menu_recent_project_manager') if i18n.get('menu_recent_project_manager') != 'menu_recent_project_manager' else i18n.get('menu_recent_projects')}"
            choices = ["0", "1", "2", "E", "e"]
            next_option_idx = 3
            
            if can_resume:
                short_path = last_path if len(last_path) < 60 else "..." + last_path[-57:]
                menu_text += f"\n{next_option_idx}. {i18n.get('mode_resume').format(short_path)}"
                choices.append(str(next_option_idx))
                next_option_idx += 1

            recent_projects = self.config.get("recent_projects", [])
            recent_projects_start_idx = next_option_idx
            
            if recent_projects:
                menu_text += f"\n\n[bold cyan]--- {i18n.get('menu_recent_projects')} ---[/bold cyan]"
                for i, item in enumerate(recent_projects):
                    path = item["path"] if isinstance(item, dict) else item
                    short_path = path if len(path) < 60 else "..." + path[-57:]
                    
                    profile_info = ""
                    if isinstance(item, dict):
                        profile_info = f" [dim]({item.get('profile', 'def')}/{item.get('rules_profile', 'def')})[/dim]"
                    
                    menu_text += f"\n{recent_projects_start_idx + i}. {short_path}{profile_info}"
                    choices.append(str(recent_projects_start_idx + i))

            menu_text += f"\n\n[dim]0. {i18n.get('menu_exit')}[/dim]"
            console.print(Panel(menu_text, title=f"[bold]{i18n.get('menu_input_mode')}[/bold]", expand=False))
            
            prompt_text = i18n.get('prompt_select').strip().rstrip(':').rstrip('：')
            choice_raw = Prompt.ask(f"\n{prompt_text}", choices=choices, show_choices=False)
            console.print("\n")
            if str(choice_raw).upper() == "E":
                self.recent_projects_menu.show()
                return False
            choice = int(choice_raw)
            if choice == 0:
                return False
            
            if can_resume and choice == 3:
                target_path = last_path
                continue_status = True
            elif choice >= recent_projects_start_idx:
                recent_idx = choice - recent_projects_start_idx
                if 0 <= recent_idx < len(recent_projects):
                    item = recent_projects[recent_idx]
                    if isinstance(item, dict):
                        target_path = item["path"]
                        # Auto-switch profiles
                        p_name = item.get("profile")
                        r_p_name = item.get("rules_profile")
                        
                        if p_name and p_name != self.active_profile_name:
                            self.active_profile_name = p_name
                            self.root_config["active_profile"] = p_name
                            console.print(f"[dim]Auto-switched Profile to: {p_name}[/dim]")
                        if r_p_name and r_p_name != self.active_rules_profile_name:
                            self.active_rules_profile_name = r_p_name
                            self.root_config["active_rules_profile"] = r_p_name
                            console.print(f"[dim]{i18n.get('msg_auto_switched_rules_profile').format(r_p_name)}[/dim]")

                        if p_name or r_p_name:
                            save_root_config(self.root_config)
                            self.load_config() # Reload to apply merge
                    else:
                        target_path = item
            elif choice == 1: # Single File
                start_path = self.config.get("label_input_path", ".")
                if os.path.isfile(start_path):
                    start_path = os.path.dirname(start_path)
                target_path = self.file_selector.select_path(start_path=start_path, select_file=True, select_dir=False)
            
            elif choice == 2: # Batch Folder
                start_path = self.config.get("label_input_path", ".")
                target_path = self.file_selector.select_path(start_path=start_path, select_file=False, select_dir=True)

            if not target_path:
                return False

            if task_mode == TaskType.POLISH:
                self._select_polishing_mode()

        # Smart suggestion for folders
        if os.path.isdir(target_path):
            candidates = []
            for ext in ("*.txt", "*.epub"):
                candidates.extend(glob.glob(os.path.join(target_path, ext)))
            
            if len(candidates) == 1:
                file_name = os.path.basename(candidates[0])
                if Confirm.ask(f"\n[cyan]Found a single file '{file_name}' in this directory. Process this file instead of the whole folder?[/cyan]", default=True):
                    target_path = candidates[0]
                    console.print(f"[dim]Switched target to file: {target_path}[/dim]")

        # --- 非交互模式的路径处理 ---
        if not os.path.exists(target_path):
            console.print(f"[red]Error: Input path '{target_path}' not found.[/red]")
            return False

        if not skip_prompt_validation:
            can_interact_for_prompt_guard = not non_interactive and not web_mode and not from_queue
            if not self.prompt_selection_guard.ensure_prompts_selected(
                task_mode,
                interactive=can_interact_for_prompt_guard,
            ):
                return False

        opath = calculate_output_path(self.config, target_path)

        if not skip_preflight and not non_interactive and not web_mode and not from_queue:
            if not self.config_experience.confirm_before_task(task_mode, target_path, opath, continue_status):
                return False

        if save_runtime_config:
            self._update_recent_projects(target_path)
        self.config["label_input_path"] = target_path
        self.config["label_output_path"] = opath

        if save_runtime_config:
            self.save_config()

        self._configure_dynamic_glossary_for_task(
            task_mode,
            interactive=not non_interactive and not web_mode and not from_queue,
        )
        
        # --- NEW: Enhanced Output Directory Handling ---
        if not continue_status and os.path.exists(opath) and not non_interactive:
            cache_exists = os.path.exists(os.path.join(opath, "cache", "AinieeCacheData.json"))
            console.print(Panel(i18n.get("menu_output_exists_prompt"), title=f"[yellow]{i18n.get('menu_output_exists_title')}[/yellow]", expand=False))
            
            options, choices_map = [], {}
            
            if cache_exists:
                options.append(f"1. {i18n.get('option_resume')}")
                choices_map["1"] = "resume"
            else:
                options.append(f"[dim]1. {i18n.get('option_resume')} ({i18n.get('err_resume_no_cache')})[/dim]")

            options.append(f"2. {i18n.get('option_archive')}")
            choices_map["2"] = "archive"
            options.append(f"3. {i18n.get('option_overwrite')}")
            choices_map["3"] = "overwrite"
            options.append(f"0. {i18n.get('option_cancel')}")
            choices_map["0"] = "cancel"

            console.print("\n".join(options))
            
            valid_choices = [k for k, v in choices_map.items() if v != "resume" or cache_exists]
            choice_str = Prompt.ask(f"\n{i18n.get('prompt_select')}", choices=valid_choices, show_choices=False)
            action = choices_map.get(choice_str)

            if action == "resume":
                continue_status = True
            elif action == "archive":
                timestamp = time.strftime('%Y%m%d_%H%M%S')
                backup_path = f"{opath}_backup_{timestamp}"
                try:
                    os.rename(opath, backup_path)
                    console.print(i18n.get('msg_archive_success').format(os.path.basename(backup_path)))
                except OSError as e:
                    console.print(f"[red]Error archiving directory: {e}[/red]")
                    return False
                continue_status = False
            elif action == "overwrite":
                if Confirm.ask(i18n.get('msg_overwrite_confirm').format(os.path.basename(opath)), default=False):
                    try:
                        shutil.rmtree(opath)
                        console.print(f"[green]'{os.path.basename(opath)}' deleted.[/green]")
                    except OSError as e:
                        console.print(f"[red]Error deleting directory: {e}[/red]")
                        return False
                else:
                    console.print("[yellow]Overwrite cancelled.[/yellow]")
                    return False
                continue_status = False
            elif action == "cancel":
                return False
        
        # Fallback for non-interactive or simple resume case
        elif not continue_status and os.path.exists(os.path.join(opath, "cache", "AinieeCacheData.json")):
             if non_interactive:
                 continue_status = True
             elif Confirm.ask(f"\n[yellow]Detected existing cache for this file. Resume?[/yellow]", default=True):
                 continue_status = True

        preloaded_resume_cache = False
        if continue_status and not web_mode and not automation_progress:
            cache_file_path = os.path.join(opath, "cache", "AinieeCacheData.json")
            if os.path.exists(cache_file_path):
                console.print(f"[cyan]Checking resume cache before entering TUI: {cache_file_path}[/cyan]")
                try:
                    self.cache_manager.load_from_file(opath, interactive_recovery=not from_queue and not non_interactive)
                    preloaded_resume_cache = True
                except Exception as e:
                    console.print(f"[yellow]{i18n.get('msg_resume_cache_load_failed_rebuild').format(e)}[/yellow]")
                    continue_status = False
            else:
                console.print(f"[yellow]{i18n.get('msg_resume_cache_missing_rebuild')}[/yellow]")
                continue_status = False

        # --- 格式转换询问逻辑 ---
        self.target_output_format = None
        if self.config.get("enable_post_conversion", False) and not non_interactive:
            # 检查是否是电子书格式
            input_ext = os.path.splitext(target_path)[1].lower()
            ebook_exts = [".epub", ".mobi", ".azw3", ".fb2", ".txt", ".docx", ".pdf", ".htmlz", ".kepub"]

            if input_ext in ebook_exts or (os.path.isdir(target_path) and any(
                f.lower().endswith(tuple(ebook_exts)) for f in os.listdir(target_path) if os.path.isfile(os.path.join(target_path, f))
            )):
                if self.config.get("fixed_output_format_switch", False):
                    # 使用固定格式
                    self.target_output_format = self.config.get("fixed_output_format", "epub")
                else:
                    # 询问用户选择格式
                    console.print(f"\n[cyan]{i18n.get('msg_format_conversion_hint')}[/cyan]")
                    format_choices = ["epub", "mobi", "azw3", "fb2", "pdf", "txt", "docx", "htmlz"]

                    table = Table(show_header=False, box=None)
                    for idx, fmt in enumerate(format_choices, 1):
                        table.add_row(f"[cyan]{idx}.[/]", fmt.upper())
                    table.add_row(f"[dim]0.[/dim]", f"[dim]{i18n.get('opt_none')}[/dim]")
                    console.print(table)

                    fmt_choice = IntPrompt.ask(
                        i18n.get('prompt_select_output_format'),
                        choices=[str(i) for i in range(len(format_choices) + 1)],
                        show_choices=False,
                        default=0
                    )
                    if fmt_choice > 0:
                        self.target_output_format = format_choices[fmt_choice - 1]

        console.print(f"[dim]{i18n.get('label_input')}: {target_path}[/dim]")
        console.print(f"[dim]{i18n.get('label_output')}: {opath}[/dim]")

        # 记录任务开始操作
        task_type_name = "翻译" if task_mode == TaskType.TRANSLATION else "润色" if task_mode == TaskType.POLISH else "翻译&润色"
        file_ext = os.path.splitext(target_path)[1].upper() if os.path.isfile(target_path) else "文件夹"
        self.operation_logger.log(f"开始{task_type_name}任务 -> 文件类型:{file_ext}", "TASK")

        # Initialize variables for finally block safety
        current_listener = None
        log_file = None
        task_success = False

        original_stdout, original_stderr = sys.stdout, sys.stderr
        
        # Ensure our UI console uses the REAL stdout to avoid recursion
        self.ui_console = Console(file=original_stdout)

        # Start Logic
        if automation_progress:
            from ModuleFolders.Infrastructure.Automation.AutomationProgress import AutomationProgressUI, reporter_from_env

            reporter = reporter_from_env(
                {
                    "input_path": target_path,
                    "file_name": os.path.basename(os.path.normpath(target_path)),
                    "status": "starting",
                    "phase": "task",
                }
            )
            self.ui = AutomationProgressUI(reporter) if reporter else WebLogger(stream=original_stdout, show_detailed=False)
        elif web_mode:
            self.ui = WebLogger(stream=original_stdout, show_detailed=self.config.get("show_detailed_logs", False))
        else:
            from ModuleFolders.UserInterface.TaskUI import TaskUI

            self.ui = TaskUI(parent_cli=self, i18n=i18n)
            # 设置 TUIHandler 的 UI 实例
            TUIHandler.set_ui(self.ui)

        Base.print = self.ui.log
        self.stop_requested = False
        self.live_state = [True] # 必须在这里初始化，防止 LogStream 报错

        # 确保 TaskExecutor 的配置与 CLIMenu 的配置同步
        self.task_executor.config.load_config_from_dict(self.config)
        runtime_overrides = getattr(self, "runtime_config_overrides", {})
        if isinstance(runtime_overrides, dict) and runtime_overrides:
            self.task_executor.config.load_config_from_dict(runtime_overrides)

        if self.input_listener.disabled and not web_mode and not automation_progress:
            self.ui.log("[bold yellow]Warning: Keyboard listener failed to initialize (no TTY found). Hotkeys will be disabled.[/bold yellow]")

        is_batch_folder_mode = os.path.isdir(target_path)
        batch_folder_name = os.path.basename(os.path.normpath(target_path)) if is_batch_folder_mode else ""
        original_ext = os.path.splitext(target_path)[1].lower()
        is_middleware_converted = False
        is_xlsx_converted = False

        ensure_runtime_bootstrap()

        # Patch tqdm to avoid conflict with Rich Live
        import ModuleFolders.Service.TaskExecutor.TaskExecutor as TaskExecutorModule
        TaskExecutorModule.tqdm = lambda x, **kwargs: x
        
        # --- NEW: Session Logger & Resume Log Recovery ---
        log_file = None
        if self.config.get("enable_session_logging", True):
            try:
                log_dir = os.path.join(opath, "logs")
                os.makedirs(log_dir, exist_ok=True)
                
                # 生成基于路径的稳定 Hash 标识，用于断点续传时的日志识别
                import hashlib
                file_id = hashlib.md5(os.path.abspath(target_path).encode('utf-8')).hexdigest()[:8]
                log_name = f"session_{file_id}_{time.strftime('%Y%m%d')}.log"
                log_path = os.path.join(log_dir, log_name)

                # 如果是断点续传且日志已存在，先读取历史日志到 TUI
                if continue_status and os.path.exists(log_path) and not web_mode and not automation_progress:
                    try:
                        from rich.text import Text

                        with open(log_path, 'r', encoding='utf-8') as f:
                            # 读取最后 50 行
                            history = f.readlines()[-50:]
                            for line in history:
                                if line.strip():
                                    # 剥离历史时间戳后载入 UI
                                    clean_line = re.sub(r'^\[\d{2}:\d{2}:\d{2}\]\s+', '', line.strip())
                                    self.ui.logs.append(Text(f"[RESUME] {clean_line}", style="dim"))
                    except: pass

                log_file = open(log_path, "a", encoding="utf-8") # 使用追加模式
                # 绑定到 UI 实例以实现实时写入
                if hasattr(self.ui, "log_file"):
                    self.ui.log_file = log_file
            except: pass

        # Redirect stdout/stderr to capture errors in UI
        class LogStream:
            _local = threading.local() # For recursion guard

            def __init__(self, ui, f=None, parent=None): 
                self.ui = ui
                self.f = f
                self.parent = parent
                self._local.is_writing = False

            def write(self, msg):
                if hasattr(self._local, 'is_writing') and self._local.is_writing:
                    return

                if not msg or msg == '\n': return
                if Base.should_suppress_task_output():
                    return
                msg_str = str(msg)
                
                # 网页模式下的统计数据行，必须直接通过真正的 stdout 发送
                if "[STATS]" in msg_str:
                    original_stdout.write(msg_str + '\n')
                    original_stdout.flush()
                    return

                # 只有当 UI 没有接管文件日志写入时，才由 LogStream 负责写入
                if self.f and not (hasattr(self.ui, "log_file") and getattr(self.ui, "log_file")):
                    try:
                        self.f.write(f"[{time.strftime('%H:%M:%S')}] {msg_str}\n")
                        self.f.flush()
                    except: pass

                if "[STATUS]" in msg_str:
                    return
                
                self._local.is_writing = True
                try:
                    # Always try to log to UI, which handles takeover logic internally
                    clean_msg = msg_str.strip()
                    if clean_msg:
                        self.ui.log(clean_msg)
                except:
                    pass
                finally:
                    self._local.is_writing = False

            def flush(self): pass
        
        sys.stdout = sys.stderr = LogStream(self.ui, log_file, self)

        # 启动键盘监听
        if not web_mode and not automation_progress:
            self.input_listener.start()
            self.input_listener.clear()

        # 定义完成事件
        self.task_running = True; finished = threading.Event(); success = threading.Event(); pause_stop_completed = threading.Event()

        from ModuleFolders.Base.EventManager import EventManager

        # --- 任务追踪状态 ---
        self._is_critical_failure = False
        self._last_crash_msg = None
        self._api_error_count = 0  # 重置API错误计数
        self._api_error_messages = []  # 重置API错误信息
        self._show_diagnostic_hint = False  # 重置诊断提示
        self._enter_diagnostic_on_exit = False  # 是否在退出后进入诊断菜单

        def _task_completion_counts():
            try:
                line = int(last_task_data.get("line") or 0)
                total_line = int(last_task_data.get("total_line") or 0)
            except (TypeError, ValueError):
                return 0, 0
            return line, total_line

        def _task_has_missing_items():
            line, total_line = _task_completion_counts()
            return total_line > 0 and 0 < line < total_line

        def _task_missing_items_message():
            line, total_line = _task_completion_counts()
            return i18n.get("msg_task_missing_items").format(line, total_line)

        def on_complete(e, d):
            if _task_has_missing_items():
                self.ui.log(f"[bold yellow]⚠ {i18n.get('msg_task_completed_partial')}[/bold yellow]")
                self.ui.log(f"[yellow]{_task_missing_items_message()}[/yellow]")
                if hasattr(self.ui, "finish") and automation_progress:
                    self.ui.finish("partial", _task_missing_items_message())
            else:
                self.ui.log(f"[bold green]✓ {i18n.get('msg_task_completed')}[/bold green]")
            success.set(); finished.set()
        
        def on_stop(e, d):
            # 只有在收到明确的任务停止完成事件时才记录日志
            if e == Base.EVENT.TASK_STOP_DONE:
                stop_reason = d.get("reason") if isinstance(d, dict) else None
                if stop_reason == "pause":
                    pause_stop_completed.set()
                    self.ui.update_status(None, {"status": "paused"})
                    self.ui.log("[bold yellow]Task paused. Press r to resume or q to stop.[/bold yellow]")
                    return

                self.ui.log(f"[bold yellow]{i18n.get('msg_task_stopped')}[/bold yellow]")
                finished.set()  # 任务停止完成，设置finished事件

            # 记录是否为熔断导致的停止
            if d and isinstance(d, dict) and d.get("status") == "critical_error":
                self._is_critical_failure = True
                self.ui.log(f"[bold red]熔断：因连续错误过多任务已暂停。[/bold red]")
        
        # 订阅事件
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_COMPLETED, on_complete)
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_STOP_DONE, on_stop)
        EventManager.get_singleton().subscribe(Base.EVENT.SYSTEM_STATUS_UPDATE, on_stop) # 借用 on_stop 处理状态更新
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_UPDATE, self.ui.update_progress)
        EventManager.get_singleton().subscribe(Base.EVENT.SYSTEM_STATUS_UPDATE, self.ui.update_status)
        EventManager.get_singleton().subscribe(Base.EVENT.TUI_SOURCE_DATA, self.ui.on_source_data)
        EventManager.get_singleton().subscribe(Base.EVENT.TUI_RESULT_DATA, self.ui.on_result_data)
        
        last_task_data = {"line": 0, "token": 0, "time": 0}
        def track_last_data(e, d):
            nonlocal last_task_data
            if d and isinstance(d, dict):
                last_task_data.update(d)
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_UPDATE, track_last_data)

        # Wrapper to run task logic (so we can use it with or without Live)
        def run_task_logic():
                nonlocal is_xlsx_converted
                self.ui.log(f"{i18n.get('msg_task_started')}")

                # --- Middleware Conversion Logic (从配置读取) ---
                calibre_enabled = self.config.get("enable_calibre_middleware", True)
                middleware_exts = self.config.get("calibre_middleware_exts", ['.mobi', '.azw3', '.kepub', '.fb2', '.lit', '.lrf', '.pdb', '.pmlz', '.rb', '.rtf', '.tcr', '.txtz', '.htmlz']) if calibre_enabled else []
                xlsx_middleware_exts = self.config.get("xlsx_middleware_exts", ['.xlsx'])

                # We need to access target_path from outer scope.
                # Since we modify it, we should be careful.
                # In python 3, we can use nonlocal for rebind, but target_path is local variable.
                # Let's use a mutable container or just refer to it.
                # Actually, the previous code structure had this logic inside 'with Live'.
                # We will just copy-paste the logic here.

                current_target_path = target_path
                is_middleware_converted_local = False

                if original_ext in middleware_exts:
                    is_middleware_converted_local = True
                    base_name = os.path.splitext(os.path.basename(current_target_path))[0]
                    os.makedirs(opath, exist_ok=True)
                    temp_conv_dir = os.path.join(opath, "temp_conv")

                    potential_epub = os.path.join(temp_conv_dir, f"{base_name}.epub")
                    if os.path.exists(potential_epub) and os.path.getsize(potential_epub) > 0:
                        self.ui.log(i18n.get("msg_epub_reuse").format(os.path.basename(potential_epub)))
                        current_target_path = potential_epub
                    else:
                        # 先检查Calibre是否可用
                        calibre_path = ensure_calibre_available(current_lang)
                        if not calibre_path:
                            self.ui.log("[red]Calibre is required for this format. Task cancelled.[/red]")
                            time.sleep(2); return

                        self.ui.log(i18n.get("msg_epub_conv_start").format(original_ext))
                        os.makedirs(temp_conv_dir, exist_ok=True)
                        conv_script = os.path.join(PROJECT_ROOT, "批量电子书整合.py")
                        cmd = f'uv run "{conv_script}" -p "{current_target_path}" -f 1 -m novel -op "{temp_conv_dir}" -o "{base_name}" --AiNiee'
                        try:
                            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                            if result.returncode == 0:
                                epubs = [f for f in os.listdir(temp_conv_dir) if f.endswith(".epub")]
                                if epubs:
                                    new_path = os.path.join(temp_conv_dir, epubs[0])
                                    self.ui.log(i18n.get("msg_epub_conv_success").format(os.path.basename(new_path)))
                                    current_target_path = new_path
                                else: raise Exception("No EPUB found")
                            else: raise Exception(f"Conversion failed: {result.stderr}")
                        except Exception as e:
                            self.ui.log(i18n.get("msg_epub_conv_fail").format(e))
                            time.sleep(2); return

                # --- XLSX Middleware Conversion Logic ---
                is_xlsx_converted = False
                if original_ext in xlsx_middleware_exts:
                    is_xlsx_converted = True
                    base_name = os.path.splitext(os.path.basename(current_target_path))[0]
                    # 确保输出目录和临时转换文件夹已创建
                    os.makedirs(opath, exist_ok=True)
                    temp_conv_dir = os.path.join(opath, "temp_xlsx_conv")

                    # 检查是否已存在转换好的CSV文件
                    potential_csv = os.path.join(temp_conv_dir, f"{base_name}.csv")
                    metadata_file = os.path.join(temp_conv_dir, "xlsx_metadata.json")

                    if os.path.exists(potential_csv) and os.path.exists(metadata_file):
                        self.ui.log(i18n.get("msg_xlsx_reuse").format(os.path.basename(potential_csv)))
                        current_target_path = temp_conv_dir  # 指向包含CSV文件的目录
                    else:
                        self.ui.log(i18n.get("msg_xlsx_conv_start").format(original_ext))
                        os.makedirs(temp_conv_dir, exist_ok=True)
                        conv_script = os.path.join(PROJECT_ROOT, "xlsx_converter.py")

                        # 调用XLSX转换器：XLSX -> CSV
                        cmd = f'uv run "{conv_script}" -i "{current_target_path}" -o "{temp_conv_dir}" -m to_csv --ainiee'
                        try:
                            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                            if result.returncode == 0:
                                # 检查转换结果
                                csv_files = [f for f in os.listdir(temp_conv_dir) if f.endswith(".csv")]
                                if csv_files:
                                    self.ui.log(i18n.get("msg_xlsx_conv_success").format(len(csv_files)))
                                    current_target_path = temp_conv_dir  # 指向包含CSV文件的目录
                                else: raise Exception("No CSV files found")
                            else: raise Exception(f"XLSX conversion failed: {result.stderr}")
                        except Exception as e:
                            self.ui.log(i18n.get("msg_xlsx_conv_fail").format(e))
                            time.sleep(2); return

                # --- 1. 文件与缓存加载 ---
                try:
                    resume_mode = continue_status
                    # 如果是继续任务，尝试直接加载缓存
                    cache_loaded = False
                    if resume_mode:
                        cache_file_path = os.path.join(opath, "cache", "AinieeCacheData.json")
                        if preloaded_resume_cache:
                            self.ui.log(f"[cyan]Resuming from cache: {cache_file_path}[/cyan]")
                            cache_loaded = True
                        elif os.path.exists(cache_file_path):
                            self.ui.log(f"[cyan]Resuming from cache: {cache_file_path}[/cyan]")
                            try:
                                self.cache_manager.load_from_file(opath, interactive_recovery=False)
                                cache_loaded = True
                            except Exception as e:
                                self.ui.log(f"[yellow]{i18n.get('msg_resume_cache_load_failed_rebuild').format(e)}[/yellow]")
                        else:
                            self.ui.log(f"[yellow]{i18n.get('msg_resume_cache_missing_rebuild')}[/yellow]")

                    if not cache_loaded:
                        if resume_mode:
                            resume_mode = False
                        cache_project = self.file_reader.read_files(self.config.get("translation_project", "AutoType"), current_target_path, self.config.get("exclude_rule_str", ""))
                        if not cache_project:
                            self.ui.log("[red]No files loaded.[/red]")
                            time.sleep(2); raise Exception("Load failed")
                        self._configure_dynamic_glossary_volume_map(
                            cache_project,
                            interactive=not non_interactive and not web_mode and not from_queue,
                        )
                        self.cache_manager.load_from_project(cache_project)
                        
                    total_items = self.cache_manager.get_item_count()
                    translated = self.cache_manager.get_item_count_by_status(TranslationStatus.TRANSLATED)
                    self.ui.update_progress(None, {"line": translated, "total_line": total_items})
                except Exception as e:
                    self.ui.log(f"[red]Error during initialization: {e}[/red]")
                    time.sleep(3); raise e

                # --- 3. 启动任务 ---
                EventManager.get_singleton().emit(
                    Base.EVENT.TASK_START,
                    {
                        "continue_status": resume_mode,
                        "current_mode": task_mode,
                        "session_input_path": current_target_path,
                        "session_output_path": opath
                    }
                )

                # --- 4. 主循环与输入监听 ---
                is_paused = False
                while not finished.is_set():
                    # 及时介入：如果监测到致命错误（如 Traceback），主动中断循环并进入分析菜单
                    if self._is_critical_failure and not web_mode:
                        self.ui.log(f"[bold red]Detection: Critical error found in logs. Intervening for analysis...[/bold red]")
                        time.sleep(2)
                        break

                    if not web_mode and not automation_progress:
                        key = self.input_listener.get_key()
                        if key:
                            if key == 'q':
                                self.ui.log("[bold red]Stop requested via keyboard...[/bold red]")
                                self.signal_handler(None, None)
                            elif key == 'p':
                                if Base.work_status == Base.STATUS.TASKING:
                                    self.ui.log("[bold yellow]Pausing System (Stopping processes)...[/bold yellow]")
                                    pause_stop_completed.clear()
                                    self.ui.update_status(None, {"status": "paused"})
                                    # 更新状态通知 TaskExecutor 停止
                                    EventManager.get_singleton().emit(Base.EVENT.TASK_STOP, {"reason": "pause"})
                                    is_paused = True
                            elif key == 'r':
                                if is_paused:
                                    if Base.work_status == Base.STATUS.STOPING and not pause_stop_completed.is_set():
                                        self.ui.log("[bold yellow]Still waiting for in-flight requests to stop before resuming...[/bold yellow]")
                                        self.ui.update_status(None, {"status": "paused"})
                                        continue

                                    self.ui.log("[bold green]Resuming System...[/bold green]")
                                    # 使用 continue_status=True 和 silent=True 重新启动
                                    EventManager.get_singleton().emit(
                                        Base.EVENT.TASK_START, 
                                        {
                                            "continue_status": True, 
                                            "current_mode": task_mode,
                                            "session_input_path": current_target_path,
                                            "session_output_path": opath,
                                            "silent": True
                                        }
                                    )
                                    self.ui.update_status(None, {"status": "normal"})
                                    is_paused = False
                            elif key == 'v':
                                self.ui.toggle_log_filter()
                            elif key == '[' or key == ']':
                                cfg = self.task_executor.config
                                if cfg.tokens_limit_switch:
                                    current_val = max(400, int(getattr(cfg, "tokens_limit", 1500) or 1500))
                                    step = 100
                                    new_val = max(400, current_val - step) if key == '[' else min(16000, current_val + step)
                                    cfg.tokens_limit = new_val
                                    self.ui.log(i18n.get('msg_split_limit_changed').format(new_val, "tokens"))
                                else:
                                    current_val = cfg.lines_limit
                                    step = 1
                                    new_val = max(1, current_val - step) if key == '[' else min(100, current_val + step)
                                    cfg.lines_limit = new_val
                                    self.ui.log(i18n.get('msg_split_limit_changed').format(new_val, "lines"))
                            elif key == 'n':
                                current_file_path = self.ui._last_progress_data.get('file_path_full')
                                if current_file_path:
                                    file_name = os.path.basename(current_file_path)
                                    self.ui.log(i18n.get('msg_skipping_file').format(file_name))

                                    # 在队列模式下处理跳过任务
                                    if hasattr(self, '_is_queue_mode') and self._is_queue_mode:
                                        try:
                                            from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager
                                            qm = QueueManager()

                                            # 将当前跳过的任务移动到队列末尾
                                            success, message = qm.skip_task_to_end(current_file_path)
                                            if success:
                                                self.ui.log(i18n.get('msg_queue_task_moved_to_end').format(file_name, message.split()[-1]))
                                            else:
                                                self.ui.log(f"[yellow]{i18n.get('msg_queue_task_move_failed')}: {message}[/yellow]")

                                            # 显示下一个任务信息
                                            next_index, next_task = qm.get_next_unlocked_task()
                                            if next_task:
                                                next_file_name = os.path.basename(next_task.input_path)
                                                task_type_name = i18n.get("task_type_translation") if next_task.task_type == TaskType.TRANSLATION else \
                                                                 i18n.get("task_type_polishing") if next_task.task_type == TaskType.POLISH else \
                                                                 i18n.get("task_type_all_in_one") if next_task.task_type == TaskType.TRANSLATE_AND_POLISH else "Unknown"
                                                self.ui.log(i18n.get('msg_queue_next_task').format(next_index + 1, task_type_name, next_file_name))
                                            else:
                                                self.ui.log(i18n.get('msg_queue_no_more_tasks'))
                                        except Exception as e:
                                            pass  # 静默忽略队列查询错误

                                    EventManager.get_singleton().emit("TASK_SKIP_FILE_REQUEST", {"file_path_full": current_file_path})
                            elif key == '-': # 减少线程
                                old_val = self.task_executor.config.actual_thread_counts
                                new_val = max(1, old_val - 1)
                                self.task_executor.config.actual_thread_counts = new_val
                                self.task_executor.config.user_thread_counts = new_val
                                self.config["user_thread_counts"] = new_val
                                try:
                                    from ModuleFolders.Infrastructure.LLMRequester.AsyncSignalHub import get_signal_hub
                                    get_signal_hub().set_concurrency(new_val)
                                except Exception:
                                    pass
                                self.ui.log(f"[yellow]{i18n.get('msg_thread_changed').format(new_val)}[/yellow]")
                            elif key == '+': # 增加线程
                                old_val = self.task_executor.config.actual_thread_counts
                                new_val = min(100, old_val + 1)
                                self.task_executor.config.actual_thread_counts = new_val
                                self.task_executor.config.user_thread_counts = new_val
                                self.config["user_thread_counts"] = new_val
                                try:
                                    from ModuleFolders.Infrastructure.LLMRequester.AsyncSignalHub import get_signal_hub
                                    get_signal_hub().set_concurrency(new_val)
                                except Exception:
                                    pass
                                self.ui.log(f"[green]{i18n.get('msg_thread_changed').format(new_val)}[/green]")
                            elif key == 'k': # 热切换 API
                                self.ui.log(f"[cyan]{i18n.get('msg_api_switching_manual')}[/cyan]")
                                EventManager.get_singleton().emit(Base.EVENT.TASK_API_STATUS_REPORT, {"force_switch": True})
                            elif key == 'm': # Open Web Monitor
                                self.handle_monitor_shortcut()
                            elif key == 'e': # Open Queue Editor (Queue mode only)
                                if hasattr(self, '_is_queue_mode') and self._is_queue_mode:
                                    self.handle_queue_editor_shortcut()
                                else:
                                    self.ui.log(f"[yellow]{i18n.get('msg_queue_editor_not_available')}[/yellow]")
                            elif key == 'h': # Open Web Queue Manager (Queue mode only)
                                if hasattr(self, '_is_queue_mode') and self._is_queue_mode:
                                    self.handle_web_queue_shortcut()
                                else:
                                    self.ui.log(f"[yellow]{i18n.get('msg_web_queue_not_available')}[/yellow]")
                            elif key == 'y': # 进入诊断模式 (当检测到多次API错误时)
                                if self._show_diagnostic_hint or self._api_error_count >= 3:
                                    self.ui.log(f"[bold cyan]{i18n.get('msg_entering_diagnostic')}[/bold cyan]")
                                    # 强制停止
                                    Base.cancel_active_task_session()
                                    Base.work_status = Base.STATUS.STOPING
                                    finished.set()
                                    # 设置标志，退出后进入诊断菜单
                                    self._enter_diagnostic_on_exit = True
                                    self._is_critical_failure = True
                                    break

                    time.sleep(0.1)
                
                return is_middleware_converted_local

        tui_error_pause_shown = False
        try:
            if automation_progress:
                is_middleware_converted = run_task_logic()
            elif web_mode:
                is_middleware_converted = run_task_logic()
            else:
                # 提前启动 Live，确保加载过程可见
                with suppress_console_mouse_input(), Live(
                    self.ui.layout,
                    console=self.ui_console,
                    refresh_per_second=10,
                    screen=True,
                    transient=False,
                ) as live:
                    try:
                        is_middleware_converted = run_task_logic()
                    except Exception as live_exc:
                        self.ui.log(f"[bold red]Critical Task Error: {str(live_exc)}[/bold red]")
                        tui_error_pause_shown = True
                        time.sleep(3)
                        raise

        except KeyboardInterrupt: self.signal_handler(None, None)
        except Exception as e:
            # Capture and log the error before TUI disappears
            import traceback
            error_full = traceback.format_exc()
            if not tui_error_pause_shown:
                err_msg = f"[bold red]Critical Task Error: {str(e)}[/bold red]"
                if hasattr(self, "ui") and self.ui:
                    self.ui.log(err_msg)
                else:
                    console.print(err_msg)
                time.sleep(3) # Give the user time to read the error before the TUI exits
            
            # 标记为真正的崩溃
            self._last_crash_msg = error_full
            self._is_critical_failure = True

        finally:
            if not web_mode and not automation_progress:
                self.input_listener.stop()
            if log_file: log_file.close()

            # --- Ensure Takeover Mode is disabled before UI cleanup ---
            if self._is_task_ui_instance():
                with self.ui._lock:
                    self.ui.taken_over = False
                # The Live context manager is about to exit, let it do one last clean frame
                time.sleep(0.2)

            sys.stdout, sys.stderr = original_stdout, original_stderr
            self.task_running = False; Base.print = self.original_print
            TUIHandler.clear()  # 清理 TUIHandler 的 UI 引用
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_COMPLETED, on_complete)
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_STOP_DONE, on_stop)
            EventManager.get_singleton().unsubscribe(Base.EVENT.SYSTEM_STATUS_UPDATE, on_stop)
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_UPDATE, self.ui.update_progress)
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_UPDATE, track_last_data)
            
            # --- 报错处理逻辑 (仅在致命失败时触发) ---
            if self._is_critical_failure and not success.is_set():
                # 检查是否是用户主动按Y进入诊断模式
                if getattr(self, '_enter_diagnostic_on_exit', False) and not non_interactive:
                    # 用户按Y主动进入诊断，显示诊断菜单
                    self.qa_menu()
                else:
                    # 只有发生了崩溃异常，或触发了 critical_error 熔断，且任务最终未完成时才弹出
                    crash_msg = self._last_crash_msg or "Task was terminated due to exceeding critical error threshold."
                    if not non_interactive and not automation_progress:
                        self.handle_crash(crash_msg)
                    else:
                        console.print(f"[bold red]Task failed fatally. Check logs.[/bold red]")
            
            if success.is_set():
                if self.config.get("enable_task_notification", True):
                    try:
                        import winsound
                        winsound.MessageBeep()
                    except ImportError:
                        print("提示：winsound模块在此系统上不可用（Linux/Docker环境）")
                        pass
                    except:
                        print("\a")
                
                # Summary Report
                lines = last_task_data.get("line", 0); tokens = last_task_data.get("token", 0); duration = last_task_data.get("time", 1)
                total_lines = last_task_data.get("total_line", lines)
                if not web_mode and not automation_progress:
                    report_table = Table(show_header=False, box=None, padding=(0, 2))
                    report_table.add_row(f"[cyan]{i18n.get('label_report_total_lines')}:[/]", f"[bold]{lines}/{total_lines}[/]")
                    if _task_has_missing_items():
                        report_table.add_row(f"[yellow]{i18n.get('label_report_missing_items')}:[/]", f"[yellow]{_task_missing_items_message()}[/]")
                    report_table.add_row(f"[cyan]{i18n.get('label_report_total_tokens')}:[/]", f"[bold]{tokens}[/]")
                    report_table.add_row(f"[cyan]{i18n.get('label_report_total_time')}:[/]", f"[bold]{duration:.1f}s[/]")
                    console.print("\n"); console.print(Panel(report_table, title=f"[bold green]✓ {i18n.get('msg_task_report_title')}[/bold green]", expand=False))
                    if self.config.get("enable_github_promotion", True):
                        console.print(f"[bold green]{i18n.get('msg_github_promotion')}[/bold green]")
                else:
                    print(f"[STATS] RPM: 0.00 | TPM: 0.00k | Progress: {lines}/{total_lines} | Tokens: {tokens}") # Final Stat
                    if _task_has_missing_items():
                        print(_task_missing_items_message())
                    if self.config.get("enable_github_promotion", True):
                        print(i18n.get("msg_github_promotion"))

            if success.is_set() and is_middleware_converted:
                try:
                    temp_dir = os.path.join(opath, "temp_conv")
                    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
                except: pass

            # XLSX restoration and cleanup
            if success.is_set() and is_xlsx_converted and self.config.get("enable_auto_restore_xlsx", True):
                try:
                    temp_xlsx_dir = os.path.join(opath, "temp_xlsx_conv")

                    # First, restore CSV back to XLSX
                    self.ui.log("[cyan]Restoring XLSX format...[/cyan]")
                    conv_script = os.path.join(PROJECT_ROOT, "xlsx_converter.py")

                    # Call XLSX converter: CSV -> XLSX
                    cmd = f'uv run "{conv_script}" -i "{temp_xlsx_dir}" -o "{opath}" -m to_xlsx --ainiee'
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

                    if result.returncode == 0:
                        self.ui.log(i18n.get("msg_xlsx_restore_success"))

                        # Clean up temporary CSV files
                        if os.path.exists(temp_xlsx_dir):
                            shutil.rmtree(temp_xlsx_dir)

                    else:
                        self.ui.log(i18n.get("msg_xlsx_restore_fail").format(result.stderr))

                except Exception as e:
                    self.ui.log(f"[yellow]XLSX restoration error: {e}[/yellow]")

            if (
                success.is_set()
                and task_mode == TaskType.TRANSLATION
                and is_batch_folder_mode
                and self.config.get("enable_batch_auto_merge_ebook", False)
            ):
                merge_name = f"{batch_folder_name}_AiNiee_Merged" if batch_folder_name else "AiNiee_Merged"
                self._auto_merge_batch_ebooks(
                    opath,
                    opath,
                    merge_name,
                    allow_non_series_prompt=(not non_interactive and not web_mode and not automation_progress),
                )
            
            if not web_mode and not automation_progress and not non_interactive and not from_queue:
                Prompt.ask(f"\n{i18n.get('msg_task_ended')}")
            
            # --- Post-Task Logic (Reverse Conversion) ---
            if task_success and is_middleware_converted and self.config.get("enable_auto_restore_ebook", False):
                 self.ui.log(f"[cyan]Restoring original format...[/cyan]")
                 # ... Reuse existing logic or simplified ...
                 # Since I can't easily reuse the exact block without copying, I'll implement a simple one
                 output_dir = self.config.get("label_output_path")
                 if output_dir:
                     translated_epubs = [f for f in os.listdir(output_dir) if f.endswith(".epub")]
                     if translated_epubs:
                         base_name = os.path.splitext(os.path.basename(target_path))[0] # This is the temp epub name
                         # Wait, target_path was swapped to the temp epub. 
                         # We need to map back to original ext.
                         # Simplified: Just run the restore command
                         conv_script = os.path.join(PROJECT_ROOT, "批量电子书整合.py")
                         cmd = f'uv run "{conv_script}" -p "{target_path}" -f 1 -m novel -op "{temp_conv_dir}" -o "{base_name} --AiNiee"'
                         # Actually the restore logic in original code was complex mapping.
                         # For now, let's skip complex restoration to keep it safe or just log.
                         self.ui.log("[dim]Auto-restore skipped in new architecture (manual restore recommended if needed).[/dim]")

            # --- Post-Task: Format Conversion ---
            if task_success and self.target_output_format:
                output_dir = self.config.get("label_output_path")
                if output_dir:
                    output_files = [f for f in os.listdir(output_dir) if f.endswith(".epub")]
                    if output_files:
                        # 使用新的Calibre检测和下载逻辑
                        calibre_path = ensure_calibre_available(current_lang)
                        if calibre_path:
                            self.ui.log(f"[cyan]Converting to {self.target_output_format.upper()} format...[/cyan]")
                            for epub_file in output_files:
                                src_path = os.path.join(output_dir, epub_file)
                                dst_name = os.path.splitext(epub_file)[0] + f".{self.target_output_format}"
                                dst_path = os.path.join(output_dir, dst_name)
                                try:
                                    result = subprocess.run(
                                        [calibre_path, src_path, dst_path],
                                        capture_output=True, text=True, timeout=300
                                    )
                                    if result.returncode == 0:
                                        self.ui.log(f"[green]✓ Converted: {dst_name}[/green]")
                                    else:
                                        self.ui.log(f"[yellow]Conversion warning: {result.stderr[:200]}[/yellow]")
                                except Exception as e:
                                    self.ui.log(f"[yellow]Conversion error: {e}[/yellow]")
                        else:
                            self.ui.log("[dim]Format conversion skipped.[/dim]")

            # --- Post-Task: Auto AI Proofread ---
            if task_success and task_mode == TaskType.TRANSLATION and self.config.get("enable_auto_proofread", False):
                if not web_mode and not automation_progress:
                    console.print(f"\n[cyan]自动AI校对已开启，正在执行校对...[/cyan]")
                    try:
                        self._execute_proofread(opath)
                    except Exception as e:
                        console.print(f"[yellow]AI校对执行出错: {e}[/yellow]")

            # Summary
            if task_success:
                self.ui.log("[bold green]All Done![/bold green]")
                if self.config.get("enable_task_notification", True):
                    try:
                        import winsound
                        winsound.MessageBeep()
                    except ImportError:
                        print("提示：winsound模块在此系统上不可用（Linux/Docker环境）")
                        pass
                    except:
                        print("\a")

            if not non_interactive and not web_mode and not automation_progress and not from_queue:
                Prompt.ask(f"\n{i18n.get('msg_task_ended')}")

        return success.is_set()


    def run_all_in_one(self):
        """Sequential execution of translation and then polishing."""
        start_path = self.config.get("label_input_path", ".")
        target_path = self.file_selector.select_path(start_path=start_path)
        if not target_path:
            return

        if not self.prompt_selection_guard.ensure_prompts_selected(
            TaskType.TRANSLATE_AND_POLISH,
            interactive=True,
        ):
            return

        opath = calculate_output_path(self.config, target_path)
        if not self.config_experience.confirm_before_task(
            TaskType.TRANSLATE_AND_POLISH,
            target_path,
            opath,
            continue_status=False,
        ):
            return

        # 1. Run Translation
        if not self.run_task(
            TaskType.TRANSLATION,
            target_path=target_path,
            continue_status=False,
            from_queue=True, # Suppress "Press Enter"
            skip_prompt_validation=True,
            skip_preflight=True,
        ):
            return

        # 2. Check stop signal
        if Base.work_status == Base.STATUS.STOPING:
             return

        # 3. Run Polishing
        self.run_task(
            TaskType.POLISH,
            target_path=target_path,
            continue_status=True, # Resume based on translation output
            from_queue=False, # Allow "Press Enter" on final completion
            skip_prompt_validation=True,
            skip_preflight=True,
        )

    def run_export_only(self, target_path=None, non_interactive=False):
        self.export_flow.run_export_only(target_path, non_interactive)

    def run_manga_translation(self):
        start_path = self.config.get("label_input_path", ".")
        if os.path.isfile(start_path):
            start_path = os.path.dirname(start_path)
        target_path = self.file_selector.select_path(
            start_path=start_path,
            select_file=True,
            select_dir=True,
        )
        if not target_path:
            return

        args = argparse.Namespace(
            task="translate",
            input_path=target_path,
            output_path=None,
            profile=None,
            rules_profile=None,
            queue_file=None,
            source_lang=None,
            target_lang=None,
            project_type=None,
            resume=False,
            non_interactive=False,
            threads=None,
            retry=None,
            rounds=None,
            timeout=None,
            platform=None,
            model=None,
            api_url=None,
            api_key=None,
            think_depth=None,
            thinking_budget=None,
            failover=None,
            web_mode=False,
            manga=True,
            manga_strict_models=False,
            manga_allow_fallback=False,
            manga_runtime_check=False,
            manga_ocr_engine=None,
            manga_detect_engine=None,
            manga_segment_engine=None,
            manga_inpaint_engine=None,
            manga_runtime_device=None,
            manga_detect_device=None,
            manga_ocr_device=None,
            manga_inpaint_device=None,
            lines=None,
            tokens=None,
            pre_lines=None,
            mcp=False,
            mcp_stdio=False,
            mcp_http=False,
            mcp_transport="stdio",
        )
        self._run_manga_translation_with_tui(args)
        Prompt.ask(f"\n{i18n.get('msg_press_enter')}")

    def _run_manga_translation_with_tui(self, args):
        from ModuleFolders.UserInterface.TaskUI import TaskUI

        original_stdout, original_stderr = sys.stdout, sys.stderr
        original_base_print = Base.print
        self.ui_console = Console(file=original_stdout)
        self.ui = TaskUI(parent_cli=self, i18n=i18n)
        TUIHandler.set_ui(self.ui)
        Base.print = self.ui.log
        self._manga_tui = self.ui
        self._manga_tui_started_at = time.time()
        self._manga_tui_input_path = args.input_path
        self.task_running = True

        class MangaLogStream:
            _local = threading.local()

            def __init__(self, ui):
                self.ui = ui

            def write(self, msg):
                if getattr(self._local, "is_writing", False):
                    return
                if not msg or msg == "\n":
                    return
                clean_msg = str(msg).strip()
                if not clean_msg:
                    return
                self._local.is_writing = True
                try:
                    self.ui.log(clean_msg)
                finally:
                    self._local.is_writing = False

            def flush(self):
                pass

        sys.stdout = sys.stderr = MangaLogStream(self.ui)
        exit_code = 1
        try:
            self.ui.update_progress(
                None,
                {
                    "line": 0,
                    "total_line": 1,
                    "token": 0,
                    "time": 0,
                    "file_name": os.path.basename(str(args.input_path)) or "MangaCore",
                    "file_path_full": args.input_path,
                    "is_start": True,
                },
            )
            with suppress_console_mouse_input(), Live(
                self.ui.layout,
                console=self.ui_console,
                refresh_per_second=10,
                screen=True,
                transient=False,
            ):
                self.ui.log("[bold cyan]MangaCore task started.[/bold cyan]")
                try:
                    exit_code = self.run_non_interactive(args) or 0
                    if exit_code == 0:
                        self.ui.update_status(None, {"status": "normal"})
                        self.ui.log("[bold green]MangaCore task completed.[/bold green]")
                    else:
                        self.ui.update_status(None, {"status": "error"})
                        self.ui.log(f"[bold red]MangaCore task exited with code {exit_code}.[/bold red]")
                        time.sleep(3)
                except KeyboardInterrupt:
                    exit_code = 130
                    self.ui.update_status(None, {"status": "error"})
                    self.ui.log("[bold red]MangaCore task interrupted by user.[/bold red]")
                    time.sleep(3)
                except Exception as exc:
                    exit_code = 1
                    self.ui.update_status(None, {"status": "error"})
                    self.ui.log(f"[bold red]MangaCore TUI task failed: {exc}[/bold red]")
                    import traceback
                    self.ui.log(traceback.format_exc())
                    time.sleep(3)
        except KeyboardInterrupt:
            exit_code = 130
            self.ui.update_status(None, {"status": "error"})
            self.ui.log("[bold red]MangaCore task interrupted by user.[/bold red]")
            time.sleep(3)
        except Exception as exc:
            exit_code = 1
            self.ui.update_status(None, {"status": "error"})
            self.ui.log(f"[bold red]MangaCore TUI task failed: {exc}[/bold red]")
            import traceback
            self.ui.log(traceback.format_exc())
            time.sleep(3)
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr
            Base.print = original_base_print
            TUIHandler.clear()
            self.task_running = False
            for attr in ("_manga_tui", "_manga_tui_started_at", "_manga_tui_input_path"):
                if hasattr(self, attr):
                    delattr(self, attr)
        return exit_code

    def start_web_server(self):
        self.web_runtime_bridge.start_web_server()

    def start_mcp_server(self):
        self.mcp_runtime_bridge.start_mcp_server()

    def _get_profiles_list(self, profiles_dir):
        return list_profile_names(profiles_dir)

    def task_queue_menu(self):
        self.task_queue_menu_handler.show()

def main():
    parser = argparse.ArgumentParser(description="AiNiee-Next - A powerful tool for AI-driven translation and polishing.", add_help=False)
    
    # 将 --help 参数单独处理，以便自定义帮助信息
    parser.add_argument('-h', '--help', action='help', default=argparse.SUPPRESS, help='Show this help message and exit.')

    # 核心任务参数
    parser.add_argument('task', nargs='?', choices=['translate', 'manga', 'polish', 'export', 'all_in_one', 'queue', 'mcp'], help=i18n.get('help_task'))
    parser.add_argument('input_path', nargs='?', help=i18n.get('help_input'))

    # 路径与环境
    parser.add_argument('-o', '--output', dest='output_path', help=i18n.get('help_output'))
    parser.add_argument('-p', '--profile', dest='profile', help=i18n.get('help_profile'))
    parser.add_argument('--rules-profile', dest='rules_profile', help="Rules profile to use (Glossary, Characterization, etc.)")
    parser.add_argument('--queue-file', dest='queue_file', help="Path to the task queue JSON file")
    parser.add_argument('-s', '--source', dest='source_lang', help=i18n.get('help_source'))
    parser.add_argument('-t', '--target', dest='target_lang', help=i18n.get('help_target'))
    parser.add_argument('--type', dest='project_type', help="Project type (Txt, Epub, MTool, RenPy, etc.)")

    # 运行策略
    parser.add_argument('-r', '--resume', action='store_true', help=i18n.get('help_resume'))
    parser.add_argument('-y', '--yes', action='store_true', dest='non_interactive', help=i18n.get('help_yes'))
    parser.add_argument('--threads', type=int, help="Concurrent thread counts (0 for auto)")
    parser.add_argument('--retry', type=int, help="Max retry counts for failed requests")
    parser.add_argument('--rounds', type=int, help="Max execution rounds")
    parser.add_argument('--timeout', type=int, help="Request timeout in seconds")
    parser.add_argument(
        '--polish-mode',
        choices=['translated', 'source', 'translated_text_polish', 'source_text_polish'],
        help="Polishing mode for this run only: translated polishes existing translations; source polishes source text.",
    )

    # API 与模型配置
    parser.add_argument('--platform', help="Target platform (e.g., Openai, LocalLLM, sakura)")
    parser.add_argument('--model', help="Model name")
    parser.add_argument('--api-url', help="Base URL for the API")
    parser.add_argument('--api-key', help="API Key")
    parser.add_argument('--think-depth', help="Reasoning depth (minimal/low/medium/high/xhigh/max or 0-10000)")
    parser.add_argument('--thinking-budget', type=int, help="Thinking budget limit")
    parser.add_argument('--failover', choices=['on', 'off'], help="Enable or disable API failover")

    parser.add_argument('--web-mode', action='store_true', help="Enable Web Server compatible output mode")
    parser.add_argument('--manga', action='store_true', help="Enable the MangaCore batch bootstrap pipeline for manga/image sources")
    parser.add_argument(
        '--manga-strict-models',
        action='store_true',
        help="Fail MangaCore startup if default visual model packages are missing. This is now the default for automatic manga pipelines.",
    )
    parser.add_argument(
        '--manga-allow-fallback',
        action='store_true',
        help="Allow MangaCore to run fallback visual runtimes for diagnostic first-pass output.",
    )
    parser.add_argument(
        '--manga-runtime-check',
        action='store_true',
        help="Print MangaCore runtime/model readiness diagnostics and exit before running a manga pipeline.",
    )
    parser.add_argument('--manga-ocr-engine', help="MangaCore OCR engine id (default: mit48px-ocr; aliases: 48px, mocr, paddleocr_vl)")
    parser.add_argument('--manga-detect-engine', help="MangaCore bubble/text detector id (default: comic-text-bubble-detector)")
    parser.add_argument('--manga-segment-engine', help="MangaCore text segmenter id (default: comic-text-detector)")
    parser.add_argument('--manga-inpaint-engine', help="MangaCore inpaint engine id (default: aot-inpainting)")
    parser.add_argument('--manga-runtime-device', choices=['auto', 'cpu', 'cuda', 'mps'], help="MangaCore default visual runtime device (auto/cpu/cuda/mps)")
    parser.add_argument('--manga-detect-device', choices=['auto', 'cpu', 'cuda', 'mps'], help="MangaCore detect-stage device override")
    parser.add_argument('--manga-ocr-device', choices=['auto', 'cpu', 'cuda', 'mps'], help="MangaCore OCR-stage device override")
    parser.add_argument('--manga-inpaint-device', choices=['auto', 'cpu', 'cuda', 'mps'], help="MangaCore inpaint-stage device override")
    parser.add_argument(
        '--mcp',
        action='store_true',
        help="Shortcut for launching the MCP task from CLI.",
    )
    parser.add_argument(
        '--mcp-stdio',
        action='store_true',
        help="Shortcut for launching the MCP task with stdio transport.",
    )
    parser.add_argument(
        '--mcp-http',
        action='store_true',
        help="Shortcut for launching the MCP task with streamable-http transport.",
    )
    parser.add_argument(
        '--mcp-transport',
        default='stdio',
        choices=['stdio', 'streamable-http', 'streamable_http', 'http', 'sse'],
        help="MCP transport mode when task is 'mcp'",
    )

    # 文本处理逻辑
    parser.add_argument('--lines', type=int, help="Lines per request (Line Mode)")
    parser.add_argument('--tokens', type=int, help="Tokens per request (Token Mode)")
    parser.add_argument('--pre-lines', type=int, help="Context lines to include")

    args = parser.parse_args()
    _consume_web_task_api_key(args)

    # CLI shortcut layer: allow `ainiee --mcp` to map onto the existing `mcp`
    # task entry without duplicating any MCP runtime logic in the parser.
    mcp_shortcut_flags = [
        flag_name
        for flag_name, enabled in (
            ("--mcp", args.mcp),
            ("--mcp-stdio", args.mcp_stdio),
            ("--mcp-http", args.mcp_http),
        )
        if enabled
    ]
    if len(mcp_shortcut_flags) > 1:
        parser.error(f"Only one MCP shortcut flag can be used at a time: {', '.join(mcp_shortcut_flags)}")

    if mcp_shortcut_flags and args.task and args.task != 'mcp':
        parser.error("MCP shortcut flags cannot be combined with a non-MCP task.")

    if args.mcp_stdio and args.mcp_transport != 'stdio':
        parser.error("--mcp-stdio cannot be combined with a different --mcp-transport value.")

    if args.mcp_http and args.mcp_transport not in {'streamable-http', 'streamable_http', 'http'}:
        parser.error("--mcp-http cannot be combined with a different --mcp-transport value.")

    if args.mcp or args.mcp_stdio or args.mcp_http:
        args.task = 'mcp'

    if args.manga_runtime_check:
        if args.task and args.task not in {'translate', 'manga'}:
            parser.error("--manga-runtime-check can only be used with translate/manga tasks.")
        args.task = args.task or 'translate'
        args.manga = True

    if args.task == 'manga':
        args.task = 'translate'
        args.manga = True

    if args.mcp_stdio:
        args.mcp_transport = 'stdio'
    elif args.mcp_http:
        args.mcp_transport = 'streamable-http'

    cli = CLIMenu()
    exit_code = 0
    try:
        # 命令行任务统一委托给 CommandModeRunner，由它自己决定参数校验。
        if args.task:
            exit_code = cli.run_non_interactive(args) or 0
        else:
            cli.main_menu()
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        cli.handle_crash(error_msg)
        exit_code = 1
    finally:
        # Final cleanup for WebServer and its subtasks
        try:
            import Tools.WebServer.web_server as ws_module
            ws_module.stop_server()
        except:
            pass
        sys.exit(exit_code)

if __name__ == "__main__":
    main()
