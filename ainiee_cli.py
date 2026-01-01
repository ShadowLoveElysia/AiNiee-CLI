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
import locale
import collections
import rapidjson as json
import shutil
import winsound
import subprocess
import argparse

from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, TimeElapsedColumn, SpinnerColumn
from rich import print
from rich.text import Text

warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from ModuleFolders.Infrastructure.Tokener.TiktokenLoader import initialize_tiktoken
import ModuleFolders.Infrastructure.Tokener.TiktokenLoader as TiktokenLoaderModule
import ModuleFolders.Domain.FileReader.ReaderUtil as ReaderUtilModule
TiktokenLoaderModule._SUPPRESS_OUTPUT = True
ReaderUtilModule._SUPPRESS_OUTPUT = True
try: initialize_tiktoken()
except Exception: pass

from ModuleFolders.Base.Base import Base
from ModuleFolders.Base.PluginManager import PluginManager
from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Infrastructure.Cache.CacheManager import CacheManager
from ModuleFolders.Domain.FileReader.FileReader import FileReader
from ModuleFolders.Domain.FileOutputer.FileOutputer import FileOutputer
from ModuleFolders.Service.SimpleExecutor.SimpleExecutor import SimpleExecutor
from ModuleFolders.Service.TaskExecutor.TaskExecutor import TaskExecutor
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
from ModuleFolders.Service.HttpService.HttpService import HttpService
from ModuleFolders.UserInterface.FileSelector import FileSelector
from ModuleFolders.UserInterface.InputListener import InputListener

console = Console()

class I18NLoader:
    def __init__(self, lang="en"):
        self.lang, self.data = lang, {}
        self.load_language(lang)
    def load_language(self, lang):
        path = os.path.join(PROJECT_ROOT, "I18N", f"{lang}.json")
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: self.data = json.load(f)
        elif lang != "en": self.load_language("en")
    def get(self, key): return self.data.get(key, key)

config_path = os.path.join(PROJECT_ROOT, "Resource", "config.json")
config = {}
if os.path.exists(config_path):
    with open(config_path, 'r', encoding='utf-8') as f: config = json.load(f)

saved_lang = config.get("interface_language")
config_path = os.path.join(PROJECT_ROOT, "Resource", "config.json")
config = {}
if os.path.exists(config_path):
    with open(config_path, 'r', encoding='utf-8') as f: config = json.load(f)

saved_lang = config.get("interface_language")
current_lang = saved_lang or (lambda: "zh_CN" if (l := locale.getdefaultlocale()[0]) and l.startswith("zh") else "ja" if l and l.startswith("ja") else "en")()
i18n = I18NLoader(current_lang)
Base.i18n = i18n # Make I18N globally accessible

def mask_key(key):
    if not key: return ""
    if len(key) < 8: return "*" * len(key)
    return key[:4] + "****" + key[-4:]

class TaskUI:
    def __init__(self):
        self.logs = collections.deque(maxlen=20)
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[action]}", justify="left"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.0f}%",
            TimeRemainingColumn(),
            expand=True
        )
        self.task_id = self.progress.add_task("", total=100, action=i18n.get('label_initializing'))
        
        # 初始化用于组合的文本组件
        self.stats_text = Text("Waiting for data...", style="cyan")
        self.current_status_key = 'label_status_normal' # 存储当前状态的 i18n 键
        self.current_status_color = 'green' # 存储当前状态的颜色
        self.current_border_color = "green" # 存储当前边框颜色

        self.layout = Layout()
        self.layout.split(Layout(name="upper", ratio=4, minimum_size=10), Layout(name="lower", size=6))
        
        # panel_group 只包含 progress 和 stats_text，因为 stats_text 会组合所有底部文本
        self.panel_group = Group(self.progress, self.stats_text)
        self.layout["lower"].update(Panel(self.panel_group, title="Progress & Stats", border_style=self.current_border_color))

    def update_status(self, event, data):
        status = data.get("status", "normal")
        color_map = {"normal": "green", "fixing": "yellow", "warning": "yellow", "error": "red"}
        status_key_map = {
            "normal": "label_status_normal",
            "fixing": "label_status_fixing",
            "warning": "label_status_warning",
            "error": "label_status_error"
        }
        
        # 只更新内部状态，不直接操作 UI 渲染
        self.current_status_key = status_key_map.get(status, "label_status_normal")
        self.current_status_color = color_map.get(status, "green")
        self.current_border_color = self.current_status_color # 更新当前边框颜色
        # 触发一次进度更新以确保 UI 刷新，但只传递必要参数
        # update_progress 负责从 self.current_status_key 和 self.current_status_color 构建完整文本
        self.update_progress(None, data)

    def log(self, msg):
        # 拦截 [STATUS] 消息，防止其污染日志和刷新 Action
        if isinstance(msg, str) and "[STATUS]" in msg:
            return

        # 简单的消息去重（防止同一时刻 stdout/stderr 重复输出）
        clean_msg_for_dedup = str(msg).strip()
        current_time = time.time()
        if hasattr(self, "_last_msg") and self._last_msg == clean_msg_for_dedup and (current_time - getattr(self, "_last_msg_time", 0)) < 0.5:
            return
        self._last_msg = clean_msg_for_dedup
        self._last_msg_time = current_time

        # 修复颜色丢失：支持 Markup 并带上时间戳
        timestamp = f"[{time.strftime('%H:%M:%S')}] "
        if isinstance(msg, str):
            try:
                # 尝试解析 AiNiee 常用的 Markup 标记
                new_log = Text.from_markup(timestamp + msg.strip())
            except:
                new_log = Text(timestamp + msg.strip())
        else:
            from io import StringIO
            with StringIO() as buf:
                temp_console = Console(file=buf, force_terminal=True, width=120)
                temp_console.print(msg)
                new_log = Text.from_ansi(timestamp + buf.getvalue().strip())

        self.logs.append(new_log)
        log_group = Group(*self.logs)
        self.layout["upper"].update(Panel(log_group, title="Logs", border_style="blue", padding=(0, 1)))

    def update_progress(self, event, data):
        completed, total = data.get("line", 0), data.get("total_line", 1)
        tokens, elapsed = data.get("token", 0), data.get("time", 0)
        target_platform = str(config.get("target_platform", "")).lower()
        is_local = any(k in target_platform for k in ["local", "sakura"])

        if elapsed > 0:
            rpm = (data.get("total_requests", 0) / (elapsed / 60))
            tpm_k = (tokens / (elapsed / 60) / 1000)
        else:
            rpm, tpm_k = 0, 0

        current_file = data.get("file_name", "...")
        rpm_str = f"{rpm:.1f}" if is_local else f"{rpm:.2f}"
        tpm_str = f"{(tpm_k * 1000):.0f}" if is_local else f"{tpm_k:.2f}k"
        token_display = f"{tokens}"

        # 组合所有底部文本：统计信息 + 快捷键 + 系统状态
        stats_markup = (
            f"File: [bold]{current_file}[/]\n"
            f"RPM: [bold]{rpm_str}[/] | TPM: [bold]{tpm_str}[/] | Tokens: [bold]{token_display}[/] | Lines: [bold]{completed}/{total}[/]\n"
            f"{i18n.get('label_shortcuts')} | [{self.current_status_color}]{i18n.get(self.current_status_key)}[/{self.current_status_color}]"
        )
        self.stats_text = Text.from_markup(stats_markup, style="cyan")
        
        # panel_group 现在只包含 progress 和 stats_text
        self.panel_group = Group(self.progress, self.stats_text)
        
        # 使用 self.current_border_color 来保持边框颜色一致性
        self.layout["lower"].update(Panel(self.panel_group, title="Progress & Stats", border_style=self.current_border_color))
        # 使用固定的 Action 文本防止抖动
        self.progress.update(self.task_id, total=total, completed=completed, action=i18n.get('label_processing'))

class CLIMenu:
    def __init__(self):
        self.root_config_path = os.path.join(PROJECT_ROOT, "Resource", "config.json")
        self.profiles_dir = os.path.join(PROJECT_ROOT, "Resource", "profiles")
        self.config = {}
        self.root_config = {}
        self.active_profile_name = "default"
        self.load_config()

        self.plugin_manager = PluginManager()
        self.plugin_manager.load_plugins_from_directory(os.path.join(PROJECT_ROOT, "PluginScripts"))
        self.file_reader, self.file_outputer, self.cache_manager = FileReader(), FileOutputer(), CacheManager()
        self.simple_executor = SimpleExecutor()
        self.task_executor = TaskExecutor(self.plugin_manager, self.cache_manager, self.file_reader, self.file_outputer)
        self.file_selector = FileSelector(i18n)
        
        # 输入监听器
        self.input_listener = InputListener()
        
        # 加载 Base 翻译库以供子模块 (Dry Run等) 使用
        Base.current_interface_language = "简中" if current_lang == "zh_CN" else "日语" if current_lang == "ja" else "英语"
        Base.multilingual_interface_dict = Base.load_translations(Base, os.path.join(PROJECT_ROOT, "Resource", "Localization"))
        
        signal.signal(signal.SIGINT, self.signal_handler)
        self.task_running, self.original_print = False, Base.print

    def run_non_interactive(self, args):
        """处理命令行参数，以非交互模式运行任务"""
        # 切换 Profile
        if args.profile:
            self.root_config["active_profile"] = args.profile
            self.save_config(save_root=True)
            self.load_config() # 重新加载配置
        
        # 覆盖配置
        if args.source_lang: self.config["source_language"] = args.source_lang
        if args.target_lang: self.config["target_language"] = args.target_lang
        if args.output_path: self.config["label_output_path"] = args.output_path
        
        self.save_config()

        task_map = {
            'translate': TaskType.TRANSLATION,
            'polish': TaskType.POLISH
        }

        if args.task in task_map:
            self.run_task(
                task_map[args.task],
                target_path=args.input_path,
                continue_status=args.resume,
                non_interactive=args.non_interactive
            )
        elif args.task == 'export':
            self.run_export_only(
                target_path=args.input_path,
                non_interactive=args.non_interactive
            )


    def _migrate_and_load_profiles(self):
        os.makedirs(self.profiles_dir, exist_ok=True)
        active_profile_path = os.path.join(self.profiles_dir, f"{self.active_profile_name}.json")

        # One-time migration from old config to default profile
        if not os.path.exists(self.profiles_dir) or not os.listdir(self.profiles_dir):
            if os.path.exists(self.root_config_path):
                try:
                    shutil.move(self.root_config_path, active_profile_path)
                    self.root_config = {"active_profile": "default", "recent_projects": []}
                    with open(self.root_config_path, 'w', encoding='utf-8') as f:
                        json.dump(self.root_config, f, indent=4, ensure_ascii=False)
                except Exception as e:
                    console.print(f"[red]Failed to migrate config: {e}[/red]")
                    # Create a blank default profile if migration fails
                    if not os.path.exists(active_profile_path):
                        with open(active_profile_path, 'w', encoding='utf-8') as f: json.dump({}, f)

        # Load the active profile
        if os.path.exists(active_profile_path):
            with open(active_profile_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        else: # If active profile is missing, create a blank one
            self.config = {}
            
        # Load preset platforms if not present in the config
        preset_path = os.path.join(PROJECT_ROOT, "Resource", "platforms", "preset.json")
        if os.path.exists(preset_path):
            try:
                with open(preset_path, 'r', encoding='utf-8') as f:
                    preset_data = json.load(f)
                
                preset_platforms = preset_data.get("platforms", {})
                
                # 如果配置中没有平台配置或平台配置为空，则使用预设平台
                if "platforms" not in self.config or not self.config["platforms"]:
                    self.config["platforms"] = preset_platforms
                else:
                    # 否则，只添加当前配置中不存在的预设平台
                    for platform_key, platform_value in preset_platforms.items():
                        if platform_key not in self.config["platforms"]:
                            self.config["platforms"][platform_key] = platform_value
            except Exception as e:
                console.print(f"[red]Failed to load preset platforms: {e}[/red]")
        
        self.save_config()

    def load_config(self):
        # Load root config
        if os.path.exists(self.root_config_path) and os.path.getsize(self.root_config_path) > 0:
            try:
                with open(self.root_config_path, 'r', encoding='utf-8') as f:
                    self.root_config = json.load(f)
                self.active_profile_name = self.root_config.get("active_profile", "default")
            except (json.JSONDecodeError, UnicodeDecodeError):
                 # This can happen if the root config is the old, large settings file. Trigger migration path.
                 self.active_profile_name = "default"
                 self._migrate_and_load_profiles()
                 return
        else:
            self.active_profile_name = "default"
        
        self._migrate_and_load_profiles()

    def save_config(self, save_root=False):
        # Save the main settings to the active profile file
        active_profile_path = os.path.join(self.profiles_dir, f"{self.active_profile_name}.json")
        os.makedirs(os.path.dirname(active_profile_path), exist_ok=True)
        with open(active_profile_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

        # Optionally save the root config (e.g., when changing active profile or recent projects)
        if save_root:
            with open(self.root_config_path, 'w', encoding='utf-8') as f:
                json.dump(self.root_config, f, indent=4, ensure_ascii=False)
    def _update_recent_projects(self, project_path):
        recent = self.root_config.get("recent_projects", [])
        if project_path in recent:
            recent.remove(project_path)
        recent.insert(0, project_path)
        self.root_config["recent_projects"] = recent[:5] # Keep last 5
        self.save_config(save_root=True)
    def signal_handler(self, sig, frame):
        if self.task_running:
            if getattr(self, "stop_requested", False):
                console.print("\n[bold red]Force quitting immediately...[/bold red]")
                os._exit(1)
            
            console.print("\n[yellow]Stopping task... (Press Ctrl+C again to force quit)[/yellow]")
            self.stop_requested = True
            
            # Immediately set status to stop threads faster
            Base.work_status = Base.STATUS.STOPING
            
            from ModuleFolders.Base.EventManager import EventManager
            EventManager.get_singleton().emit(Base.EVENT.TASK_STOP, {})
        else:
            sys.exit(0)
    def display_banner(self):
        profile_display = f"[bold yellow]({self.active_profile_name})[/bold yellow]"
        console.clear()
        console.print(Panel.fit(f"[bold cyan]AiNiee CLI[/bold cyan] {profile_display}\nGUI Original: By NEKOparapa\nCLI Version: By ShadowLoveElysia\nLang: {current_lang}", title="Welcome"))

    def main_menu(self):
        if "interface_language" not in self.config: self.first_time_lang_setup()
        while True:
            self.display_banner()
            table = Table(show_header=False, box=None)
            menus, colors = ["start_translation", "start_polishing", "export_only", "settings", "api_settings", "glossary", "profiles"], ["green", "green", "magenta", "blue", "blue", "yellow", "cyan"]
            for i, (m, c) in enumerate(zip(menus, colors)): table.add_row(f"[{c}]{i+1}.[/]", i18n.get(f"menu_{m}"))
            table.add_row("[red]0.[/]", i18n.get("menu_exit")); console.print(table)
            choice = IntPrompt.ask(f"\n{i18n.get('prompt_select')}", choices=[str(i) for i in range(len(menus) + 1)], show_choices=False)
            console.print("\n")
            [sys.exit, lambda: self.run_task(TaskType.TRANSLATION), lambda: self.run_task(TaskType.POLISH), self.run_export_only, self.settings_menu, self.api_settings_menu, self.prompt_menu, self.profiles_menu][choice]()

    def profiles_menu(self):
        while True:
            self.display_banner()
            console.print(Panel(f"[bold]{i18n.get('menu_profiles')}[/bold]"))
            
            profiles = [f.replace(".json", "") for f in os.listdir(self.profiles_dir) if f.endswith(".json")]
            
            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", i18n.get("menu_profile_select"))
            table.add_row("[cyan]2.[/]", i18n.get("menu_profile_create"))
            table.add_row("[cyan]3.[/]", i18n.get("menu_profile_rename"))
            table.add_row("[red]4.[/]", i18n.get("menu_profile_delete"))
            console.print(table)
            console.print(f"\n[dim]0. {i18n.get('menu_exit')}[/dim]")

            choice = IntPrompt.ask(f"\n{i18n.get('prompt_select')}", choices=["0", "1", "2", "3", "4"], show_choices=False)
            
            if choice == 0:
                break
            elif choice == 1: # Switch Profile
                console.print(Panel(i18n.get("menu_profile_select")))
                p_table = Table(show_header=False, box=None)
                for i, p in enumerate(profiles):
                    p_table.add_row(f"[cyan]{i+1}.[/]", p + (" [green](Active)[/]" if p == self.active_profile_name else ""))
                console.print(p_table)
                console.print(f"\n[dim]0. {i18n.get('menu_back')}[/dim]")
                
                sel_idx = IntPrompt.ask(i18n.get('prompt_select'), choices=[str(i) for i in range(len(profiles)+1)], show_choices=False)
                if sel_idx == 0: continue
                
                sel = profiles[sel_idx - 1]
                self.root_config["active_profile"] = sel
                self.save_config(save_root=True)
                self.load_config() # Reload everything
                console.print(f"[green]{i18n.get('msg_active_platform').format(sel)}[/green]"); time.sleep(1)
                break # Exit to main menu to reflect change
            elif choice == 2: # Create New Profile
                new_name = Prompt.ask(i18n.get("prompt_profile_name")).strip()
                if new_name and not os.path.exists(os.path.join(self.profiles_dir, f"{new_name}.json")):
                    shutil.copyfile(
                        os.path.join(self.profiles_dir, f"{self.active_profile_name}.json"),
                        os.path.join(self.profiles_dir, f"{new_name}.json")
                    )
                    console.print(f"[green]{i18n.get('msg_profile_created').format(new_name)}[/green]")
                else:
                    console.print(f"[red]{i18n.get('msg_profile_invalid')}[/red]")
                time.sleep(1)
            elif choice == 3: # Rename Current Profile
                new_name = Prompt.ask(i18n.get("prompt_profile_rename")).strip()
                if new_name and not os.path.exists(os.path.join(self.profiles_dir, f"{new_name}.json")):
                    os.rename(
                        os.path.join(self.profiles_dir, f"{self.active_profile_name}.json"),
                        os.path.join(self.profiles_dir, f"{new_name}.json")
                    )
                    self.active_profile_name = new_name
                    self.root_config["active_profile"] = new_name
                    self.save_config(save_root=True)
                    console.print(f"[green]{i18n.get('msg_profile_renamed').format(new_name)}[/green]")
                else:
                    console.print(f"[red]{i18n.get('msg_profile_invalid')}[/red]")
                time.sleep(1)

            elif choice == 4: # Delete Profile
                if len(profiles) <= 1:
                    console.print(f"[red]{i18n.get('msg_cannot_delete_last')}[/red]"); time.sleep(1); continue

                del_candidates = [p for p in profiles if p != self.active_profile_name]
                console.print(Panel(f"{i18n.get('menu_profile_delete')}"))
                p_table = Table(show_header=False, box=None)
                for i, p in enumerate(del_candidates):
                    p_table.add_row(f"[cyan]{i+1}.[/]", p)
                console.print(p_table)
                console.print(f"\n[dim]0. {i18n.get('menu_cancel')}[/dim]")

                sel_idx = IntPrompt.ask(i18n.get('prompt_select'), choices=[str(i) for i in range(len(del_candidates)+1)], show_choices=False)
                if sel_idx == 0: continue
                
                sel = del_candidates[sel_idx - 1]
                if Confirm.ask(f"[bold red]{i18n.get('msg_profile_delete_confirm').format(sel)}[/bold red]"):
                    os.remove(os.path.join(self.profiles_dir, f"{sel}.json"))
                    console.print(f"[green]{i18n.get('msg_profile_deleted').format(sel)}[/green]")
                    profiles = [f.replace(".json", "") for f in os.listdir(self.profiles_dir) if f.endswith(".json")] # Refresh list
                else:
                    console.print(f"[yellow]{i18n.get('msg_delete_cancel')}[/yellow]")
                time.sleep(1)

    def first_time_lang_setup(self):
        global current_lang, i18n
        
        detected = "en"
        if (l := locale.getdefaultlocale()[0]):
            if l.startswith("zh"): detected = "zh_CN"
            elif l.startswith("ja"): detected = "ja"
            
        default_idx = {"zh_CN": 1, "ja": 2, "en": 3}.get(detected, 3)
        
        console.print(Panel(f"[bold cyan]Language Setup / 语言设置 / 言語設定[/bold cyan]"))
        console.print(f"[dim]Detected System Language: {detected}[/dim]\n")
        
        table = Table(show_header=False, box=None)
        table.add_row("[cyan]1.[/]", "中文 (简体)")
        table.add_row("[cyan]2.[/]", "日本語")
        table.add_row("[cyan]3.[/]", "English")
        console.print(table)
        
        c = IntPrompt.ask("\nSelect / 选择 / 選択", choices=["1", "2", "3"], default=default_idx, show_choices=False)
        
        current_lang = {"1": "zh_CN", "2": "ja", "3": "en"}[str(c)]
        self.config["interface_language"] = current_lang
        self.save_config()
        i18n = I18NLoader(current_lang)
    def settings_menu(self):
        while True:
            self.display_banner(); console.print(Panel(f"[bold]{i18n.get('menu_settings')}[/bold]"))
            table = Table(show_header=True); table.add_column("ID", style="dim"); table.add_column("Setting"); table.add_column("Value", style="cyan")
            
            limit_switch = self.config.get("tokens_limit_switch", False)
            limit_mode_str = "Token" if limit_switch else "Line"; limit_val_key = "tokens_limit" if limit_switch else "lines_limit"
            
            # --- 分区 1：数值与路径 (Values & Paths) ---
            table.add_row("1", i18n.get("setting_input_path"), self.config.get("label_input_path", ""))
            table.add_row("2", i18n.get("setting_output_path"), self.config.get("label_output_path", ""))
            table.add_row("3", i18n.get("setting_src_lang"), self.config.get("source_language", ""))
            table.add_row("4", i18n.get("setting_tgt_lang"), self.config.get("target_language", ""))
            table.add_row("5", i18n.get("setting_thread_count"), str(self.config.get("actual_thread_counts", 5)))
            table.add_row("6", i18n.get("setting_cache_backup_limit"), str(self.config.get("cache_backup_limit", 10)))
            
            table.add_section()
            
            # --- 分区 2：功能开关 (Feature Toggles) ---
            table.add_row("7", i18n.get("setting_detailed_logs"), "[green]ON[/]" if self.config.get("show_detailed_logs", False) else "[red]OFF[/]")
            table.add_row("8", i18n.get("setting_cache_backup"), "[green]ON[/]" if self.config.get("enable_cache_backup", False) else "[red]OFF[/]")
            table.add_row("9", i18n.get("setting_auto_restore_ebook"), "[green]ON[/]" if self.config.get("enable_auto_restore_ebook", False) else "[red]OFF[/]")
            table.add_row("10", i18n.get("setting_dry_run"), "[green]ON[/]" if self.config.get("enable_dry_run", True) else "[red]OFF[/]")
            table.add_row("11", i18n.get("setting_auto_heal"), "[green]ON[/]" if self.config.get("enable_auto_heal", True) else "[red]OFF[/]")
            table.add_row("12", i18n.get("setting_retry_backoff"), "[green]ON[/]" if self.config.get("enable_retry_backoff", True) else "[red]OFF[/]")
            table.add_row("13", i18n.get("setting_task_notification"), "[green]ON[/]" if self.config.get("enable_task_notification", True) else "[red]OFF[/]")
            table.add_row("14", i18n.get("setting_session_logging"), "[green]ON[/]" if self.config.get("enable_session_logging", True) else "[red]OFF[/]")
            
            table.add_section()
            
            # --- 分区 3：子菜单/复杂项 (Sub-menus & Advanced) ---
            table.add_row("15", i18n.get("setting_project_type"), self.config.get("translation_project", "AutoType"))
            table.add_row("16", i18n.get("setting_trans_mode"), f"{limit_mode_str} ({self.config.get(limit_val_key, 20)})")
            table.add_row("17", i18n.get("menu_api_pool_settings"), f"[cyan]{len(self.config.get('backup_apis', []))} APIs[/]")

            console.print(table); console.print(f"\n[dim]0. {i18n.get('menu_exit')}[/dim]")
            choice = IntPrompt.ask(f"\n{i18n.get('prompt_select')}", choices=[str(i) for i in range(18)], show_choices=False)
            console.print("\n")
            if choice == 0: break
            
            # --- 处理数值类 ---
            elif choice == 1 or choice == 2:
                console.print(f"\n[yellow]{i18n.get('warn_path_change')}[/yellow]")
                key = "label_input_path" if choice == 1 else "label_output_path"
                prompt_key = "prompt_input_path" if choice == 1 else "prompt_output_path"
                new_val = Prompt.ask(f"\n{i18n.get(prompt_key)}", default=self.config.get(key, ""))
                if new_val == "0": continue
                self.config[key] = new_val
            elif choice == 3: 
                new_val = Prompt.ask(f"\n{i18n.get('prompt_source_lang')}", default=self.config.get('source_language'))
                if new_val == "0": continue
                self.config["source_language"] = new_val
            elif choice == 4: 
                new_val = Prompt.ask(f"\n{i18n.get('prompt_target_lang')}", default=self.config.get('target_language'))
                if new_val == "0": continue
                self.config["target_language"] = new_val
            elif choice == 5:
                console.print(f"\n[cyan]{i18n.get('tip_thread_count')}[/cyan]")
                val = IntPrompt.ask(f"\n{i18n.get('setting_thread_count')}", default=self.config.get("actual_thread_counts"))
                if val == 0: continue
                self.config["actual_thread_counts"] = val
                self.config["user_thread_counts"] = val
            elif choice == 6:
                val = IntPrompt.ask(f"\n{i18n.get('setting_cache_backup_limit')}", default=self.config.get("cache_backup_limit", 10))
                if val >= 0: self.config["cache_backup_limit"] = val
            
            # --- 处理开关类 ---
            elif choice == 7: self.config["show_detailed_logs"] = not self.config.get("show_detailed_logs", False)
            elif choice == 8: self.config["enable_cache_backup"] = not self.config.get("enable_cache_backup", False)
            elif choice == 9: self.config["enable_auto_restore_ebook"] = not self.config.get("enable_auto_restore_ebook", False)
            elif choice == 10: self.config["enable_dry_run"] = not self.config.get("enable_dry_run", True)
            elif choice == 11: self.config["enable_auto_heal"] = not self.config.get("enable_auto_heal", True)
            elif choice == 12: self.config["enable_retry_backoff"] = not self.config.get("enable_retry_backoff", True)
            elif choice == 13: self.config["enable_task_notification"] = not self.config.get("enable_task_notification", True)
            elif choice == 14: self.config["enable_session_logging"] = not self.config.get("enable_session_logging", True)
            
            # --- 处理子菜单类 ---
            elif choice == 15: self.project_type_menu()
            elif choice == 16: self.trans_mode_menu()
            elif choice == 17: self.api_pool_menu()
            
            self.save_config()

    def api_pool_menu(self):
        while True:
            self.display_banner()
            sw = self.config.get("enable_api_failover", False)
            th = self.config.get("api_failover_threshold", 10)
            pool = self.config.get("backup_apis", [])
            
            console.print(Panel(f"[bold]{i18n.get('menu_api_pool_settings')}[/bold]"))
            console.print(f"[dim]{i18n.get('tip_failover_logic')}[/dim]\n")
            
            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"{i18n.get('setting_failover_status')}: [green]{'ON' if sw else 'OFF'}[/green]")
            table.add_row("[cyan]2.[/]", f"{i18n.get('setting_failover_threshold')}: [yellow]{th}[/yellow]")
            table.add_row("[cyan]3.[/]", f"{i18n.get('prompt_add_to_pool')}")
            table.add_row("[cyan]4.[/]", f"{i18n.get('prompt_remove_from_pool')}")
            console.print(table)
            
            if pool:
                console.print(f"\n[bold]{i18n.get('label_current_pool')}:[/bold]")
                p_list = " ➔ ".join([f"[cyan]{p}[/cyan]" for p in pool])
                console.print(f"  {p_list}")
            else:
                console.print(f"\n[dim]{i18n.get('msg_api_pool_empty')}[/dim]")
                
            console.print(f"\n[dim]0. {i18n.get('menu_back')}[/dim]")
            c = IntPrompt.ask(i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4"], show_choices=False)
            
            if c == 0: break
            elif c == 1: self.config["enable_api_failover"] = not sw
            elif c == 2: self.config["api_failover_threshold"] = IntPrompt.ask(i18n.get("setting_failover_threshold"), default=10)
            elif c == 3: # Add
                local_keys = ["localllm", "sakura"]
                online_platforms = [k for k in self.config.get("platforms", {}).keys() if k.lower() not in local_keys]
                candidates = [k for k in online_platforms if k not in pool]
                if not candidates: continue
                
                console.print(Panel(i18n.get("prompt_add_to_pool")))
                c_table = Table(show_header=False, box=None)
                for i, k in enumerate(candidates): c_table.add_row(f"[cyan]{i+1}.[/]", k)
                console.print(c_table)
                sel = IntPrompt.ask(i18n.get("prompt_select"), choices=[str(i) for i in range(len(candidates)+1)], default=0, show_choices=False)
                if sel > 0:
                    pool.append(candidates[sel-1])
                    self.config["backup_apis"] = pool
            elif c == 4: # Remove
                if not pool: continue
                console.print(Panel(i18n.get("prompt_remove_from_pool")))
                r_table = Table(show_header=False, box=None)
                for i, k in enumerate(pool): r_table.add_row(f"[cyan]{i+1}.[/]", k)
                console.print(r_table)
                sel = IntPrompt.ask(i18n.get("prompt_select"), choices=[str(i) for i in range(len(pool)+1)], default=0, show_choices=False)
                if sel > 0:
                    pool.pop(sel-1)
                    self.config["backup_apis"] = pool
            self.save_config()

    def trans_mode_menu(self):
        console.clear(); console.print(Panel(f"[bold]{i18n.get('menu_trans_mode_select')}[/bold]"))
        console.print(f"1. {i18n.get('mode_line')}")
        console.print(f"2. {i18n.get('mode_token')}")
        console.print(f"\n[dim]0. {i18n.get('menu_exit')}[/dim]")
        
        c = IntPrompt.ask(i18n.get('prompt_select'), choices=["0", "1", "2"], show_choices=False)
        console.print()
        if c == 0: return
        elif c == 1: # Line Mode
            console.print(f"[cyan]{i18n.get('tip_line_mode')}[/cyan]")
            val = IntPrompt.ask(i18n.get('prompt_new_value'), default=20)
            self.config["tokens_limit_switch"] = False
            self.config["lines_limit"] = val
        elif c == 2: # Token Mode
            console.print(f"[bold red]{i18n.get('warn_token_mode_severe')}[/bold red]")
            confirm_phrase = i18n.get('phrase_confirm_risk')
            
            user_input = Prompt.ask(i18n.get('prompt_confirm_risk').format(confirm_phrase))

            # Final Validation
            if user_input != confirm_phrase:
                console.print(f"[red]{i18n.get('msg_risk_abort')}[/red]"); time.sleep(2); return
            
            val = IntPrompt.ask(i18n.get('prompt_new_value'), default=1500)
            self.config["tokens_limit_switch"] = True
            self.config["tokens_limit"] = val
            self.config["tokens_limit_switch"] = True
            self.config["tokens_limit"] = val
    def project_type_menu(self):
        while True:
            self.display_banner(); console.print(Panel(f"[bold]{i18n.get('menu_project_type')}[/bold]"))
            table = Table(show_header=False, box=None)
            cats = ["auto", "game", "doc", "sub", "dev"]
            for i, cat in enumerate(cats): table.add_row(f"[cyan]{i+1}.[/]", i18n.get(f"cat_{cat}"))
            console.print(table); console.print(f"\n[dim]0. {i18n.get('menu_back')}[/dim]")
            choice = IntPrompt.ask(i18n.get('prompt_select'), choices=[str(i) for i in range(len(cats)+1)], show_choices=False)
            console.print()
            if choice == 0: return
            elif choice == 1: self.config["translation_project"] = "AutoType"
            else:
                types = [["MTool", "RenPy", "TPP"], ["Txt", "Epub", "Docx"], ["Srt", "Ass"], ["Po", "I18next"]][choice-2]
                
                console.print(Panel(i18n.get("menu_sub_type_select")))
                t_table = Table(show_header=False, box=None)
                for i, t in enumerate(types):
                    t_table.add_row(f"[cyan]{i+1}.[/]", t)
                console.print(t_table)
                console.print(f"\n[dim]0. {i18n.get('menu_cancel')}[/dim]")
                
                sub_choice = IntPrompt.ask(i18n.get('prompt_select'), choices=[str(i) for i in range(len(types)+1)], show_choices=False)
                if sub_choice == 0: continue
                
                self.config["translation_project"] = types[sub_choice - 1]
            self.save_config(); return
    def api_settings_menu(self):
        while True:
            self.display_banner(); current_p, current_m = self.config.get("target_platform", "None"), self.config.get("model", "None")
            console.print(Panel(f"[bold]{i18n.get('menu_api_settings')}[/bold] [dim](Current: {current_p} - {current_m})[/dim]"))
            menus=["online", "local", "validate", "manual"]
            table = Table(show_header=False, box=None)
            for i, m in enumerate(menus): table.add_row(f"[{'cyan' if i<2 else 'green' if i<3 else 'yellow'}]{i+1}.[/]", i18n.get(f"menu_api_{m}"))
            console.print(table); console.print(f"\n[dim]0. {i18n.get('menu_exit')}[/dim]")
            choice = IntPrompt.ask(i18n.get('prompt_select'), choices=list("01234"), show_choices=False)
            console.print()
            if choice == 0: break
            elif choice in [1, 2]: self.select_api_menu(online=choice==1)
            elif choice == 3: self.validate_api()
            elif choice == 4: self.manual_edit_api_menu()
    def select_api_menu(self, online: bool):
        local_keys = ["localllm", "sakura"]; platforms = self.config.get("platforms", {})
        options = {k: v for k, v in platforms.items() if (k.lower() not in local_keys) == online}
        if not options: return
        
        # Refactored to numeric selection
        sorted_keys = sorted(list(options.keys()))
        console.print(Panel(f"[bold]{i18n.get('msg_api_select_' + ('online' if online else 'local'))}[/bold]"))
        table = Table(show_header=False, box=None)
        for i, k in enumerate(sorted_keys):
            table.add_row(f"[cyan]{i+1}.[/]", k)
        console.print(table)
        console.print(f"\n[dim]0. {i18n.get('menu_exit')}[/dim]")
        
        choice = IntPrompt.ask(i18n.get('prompt_select'), choices=[str(i) for i in range(len(sorted_keys)+1)], show_choices=False)
        if choice == 0: return
        
        sel = sorted_keys[choice - 1]
        
        plat_conf = options[sel]
        self.config.update({
            "target_platform": sel, 
            "base_url": plat_conf.get("api_url"), 
            "model": plat_conf.get("models", [""])[0],
            "api_settings": {"translate": sel, "polish": sel}
        })
        if online:
            key = Prompt.ask(i18n.get("msg_api_key_for").format(sel), password=True)
            self.config["platforms"][sel]["api_key"] = key; self.config["api_key"] = key
        self.save_config(); console.print(f"[green]{i18n.get('msg_active_platform').format(sel)}[/green]"); time.sleep(1)
    def validate_api(self):
        # 使用 TaskExecutor 中已有的 TaskConfig 实例，确保配置一致性
        task_config = self.task_executor.config
        task_config.initialize() # 确保加载最新配置
        task_config.prepare_for_translation(TaskType.TRANSLATION) # 确保模型和平台信息已加载

        # 确保 task_config 反映 CLIMenu 的活动配置
        # 显式设置 base_url 和 target_platform 如果它们在 task_config 中缺失
        if not task_config.base_url and self.config.get("base_url"):
            task_config.base_url = self.config["base_url"]
        if not task_config.target_platform and self.config.get("target_platform"):
            task_config.target_platform = self.config["target_platform"]
        # 确保特定平台的配置也可用
        if task_config.target_platform and task_config.target_platform in self.config.get("platforms", {}):
            # 在这里，我们直接更新 task_config.platforms 字典中对应平台项
            # 而不是仅仅将整个 self.config["platforms"] 赋值给 task_config.platforms
            task_config.platforms[task_config.target_platform] = self.config["platforms"][task_config.target_platform]

        target_platform = task_config.target_platform
        
        # 确保 base_url 至少是一个空字符串，避免NoneType错误
        base_url_for_validation = task_config.base_url if task_config.base_url else ""

        with console.status(f"[cyan]{i18n.get('msg_api_validating')}[/cyan]"):
            try:
                if target_platform.lower() in ["localllm", "sakura"]:
                    import httpx
                    # --- DEBUG PRINT ---
                    console.print(f"[dim]Debug: base_url_for_validation = {base_url_for_validation}[/dim]")
                    # --- END DEBUG PRINT ---

                    if not base_url_for_validation.startswith("http://") and not base_url_for_validation.startswith("https://"):
                        api_url = "http://" + base_url_for_validation.rstrip('/') + "/chat/completions"
                    else:
                        api_url = base_url_for_validation.rstrip('/') + "/chat/completions"
                    # --- DEBUG PRINT ---
                    console.print(f"[dim]Debug: constructed api_url = {api_url}[/dim]")
                    # --- END DEBUG PRINT ---

                    api_key = task_config.get_next_apikey()
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    payload = {"messages": [{"role": "user", "content": i18n.get("msg_test_msg")}], "max_tokens": 50}
                    
                    with httpx.Client(timeout=20) as client:
                        response = client.post(api_url, json=payload, headers=headers)
                        response.raise_for_status()
                        content = response.json()["choices"][0]["message"]["content"]
                else:
                    # Use standard LLMRequester for online APIs
                    from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
                    platform_config = task_config.get_platform_configuration("translationReq")
                    requester = LLMRequester()
                    messages = [{"role": "user", "content": i18n.get("msg_test_msg")}]
                    # Ensure base_url is present in platform_config for LLMRequester as well
                    if "base_url" not in platform_config and task_config.base_url:
                        platform_config["base_url"] = task_config.base_url
                    
                    skip, think, content, p_tokens, c_tokens = requester.sent_request(
                        messages=messages, system_prompt=i18n.get("msg_test_sys"), platform_config=platform_config
                    )
                    if skip:
                        raise Exception("Request failed or was skipped by requester.")
                
                console.print(f"[green]✓ {i18n.get('msg_api_ok')}[/green]")
                console.print(f"[dim]Response: {content[:100]}...[/dim]")

            except Exception as e:
                console.print(f"[red]✗ {i18n.get('msg_api_fail')}:[/red] {e}")

        Prompt.ask(f"\n{i18n.get('msg_press_enter')}")
    def manual_edit_api_menu(self):
        while True:
            self.display_banner(); console.print(Panel(f"[bold]{i18n.get('menu_api_manual')}[/bold]"))
            table = Table(show_header=True); table.add_column("ID", style="dim"); table.add_column("Setting"); table.add_column("Value", style="cyan")
            
            # Helper to get current platform dict
            tp = self.config.get("target_platform", "")
            plat_conf = self.config.get("platforms", {}).get(tp, {})
            
            # Values can be in root config (overrides) or platform config
            think_sw = self.config.get("think_switch", plat_conf.get("think_switch", False))
            think_dp = self.config.get("think_depth", plat_conf.get("think_depth", 0))

            table.add_row("1", i18n.get("label_platform"), tp)
            table.add_row("2", i18n.get("label_url"), self.config.get("base_url", ""))
            table.add_row("3", i18n.get("label_key"), "****")
            table.add_row("4", i18n.get("label_model"), self.config.get("model", ""))
            table.add_row("5", i18n.get("menu_api_think_switch"), "[green]ON[/]" if think_sw else "[red]OFF[/]")
            table.add_row("6", i18n.get("menu_api_think_depth"), str(think_dp))
            
            console.print(table); console.print(f"\n[dim]0. {i18n.get('menu_exit')}[/dim]")
            choice = IntPrompt.ask(i18n.get('prompt_select'), choices=list("0123456"), show_choices=False)
            console.print()
            
            if choice == 0: break
            elif choice == 1: self.config["target_platform"] = Prompt.ask(i18n.get("label_platform"), default=self.config.get("target_platform"))
            elif choice == 2: self.config["base_url"] = Prompt.ask(i18n.get("label_url"), default=self.config.get("base_url"))
            elif choice == 3: self.config["api_key"] = Prompt.ask(i18n.get("label_key"), password=True)
            elif choice == 4: self.config["model"] = Prompt.ask(i18n.get("label_model"), default=self.config.get("model"))
            elif choice == 5:
                new_state = not think_sw
                self.config["think_switch"] = new_state
                # Sync to platform config
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["think_switch"] = new_state
            elif choice == 6:
                val = IntPrompt.ask(i18n.get("prompt_think_depth"), default=think_dp)
                self.config["think_depth"] = val
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["think_depth"] = val

            self.save_config()
    def prompt_menu(self):
        while True:
            self.display_banner(); console.print(Panel(f"[bold]{i18n.get('menu_glossary_rules')}[/bold]"))
            
            trans_sel = self.config.get("translation_prompt_selection", {}).get("last_selected_id", "common")
            polish_sel = self.config.get("polishing_prompt_selection", {}).get("last_selected_id", "common")
            
            dict_sw = self.config.get("prompt_dictionary_switch", False)
            excl_sw = self.config.get("exclusion_list_switch", False)
            
            dict_len = len(self.config.get("prompt_dictionary_data", []))
            excl_len = len(self.config.get("exclusion_list_data", []))

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"{i18n.get('menu_select_trans_prompt')} ([green]{trans_sel}[/green])")
            table.add_row("[cyan]2.[/]", f"{i18n.get('menu_select_polish_prompt')} ([green]{polish_sel}[/green])")
            table.add_row("[cyan]3.[/]", f"{i18n.get('menu_dict_settings')} ([green]{'ON' if dict_sw else 'OFF'}[/green] | {dict_len} items)")
            table.add_row("[cyan]4.[/]", f"{i18n.get('menu_exclusion_settings')} ([green]{'ON' if excl_sw else 'OFF'}[/green] | {excl_len} items)")
            
            console.print(table); console.print(f"\n[dim]0. {i18n.get('menu_exit')}[/dim]")
            
            choice = IntPrompt.ask(i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4"], show_choices=False)
            console.print()
            
            if choice == 0: break
            elif choice == 1: self.select_prompt_template("Translate", "translation_prompt_selection")
            elif choice == 2: self.select_prompt_template("Polishing", "polishing_prompt_selection")
            elif choice == 3: self.manage_text_rule("prompt_dictionary_switch", "prompt_dictionary_data", i18n.get("menu_dict_settings"))
            elif choice == 4: self.manage_text_rule("exclusion_list_switch", "exclusion_list_data", i18n.get("menu_exclusion_settings"))

    def manage_text_rule(self, switch_key, data_key, title):
        while True:
            sw = self.config.get(switch_key, False)
            data = self.config.get(data_key, [])
            
            console.print(Panel(f"[bold]{title}[/bold]"))
            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"{i18n.get('menu_toggle_switch')} (Current: [green]{'ON' if sw else 'OFF'}[/green])")
            table.add_row("[cyan]2.[/]", f"{i18n.get('menu_dict_import' if 'dict' in switch_key else 'menu_exclusion_import')} (Current items: {len(data)})")
            table.add_row("[cyan]3.[/]", f"{i18n.get('menu_clear_data')}")
            console.print(table)
            console.print(f"\n[dim]0. {i18n.get('menu_back')}[/dim]")
            
            c = IntPrompt.ask(i18n.get('prompt_select'), choices=["0", "1", "2", "3"], show_choices=False)
            
            if c == 0: break
            elif c == 1:
                self.config[switch_key] = not sw
            elif c == 2:
                path = Prompt.ask(i18n.get('prompt_json_path')).strip('"').strip("'")
                if os.path.exists(path):
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            new_data = json.load(f)
                            if isinstance(new_data, list):
                                self.config[data_key] = new_data
                                console.print(f"[green]{i18n.get('msg_data_loaded').format(len(new_data))}[/green]")
                            else:
                                console.print(f"[red]{i18n.get('msg_json_root_error')}[/red]")
                    except Exception as e:
                        console.print(f"[red]Error loading file: {e}[/red]")
                else:
                    console.print(f"[red]{i18n.get('err_not_file')}[/red]")
                time.sleep(1)
            elif c == 3:
                if Confirm.ask(i18n.get("menu_clear_data") + "?"):
                    self.config[data_key] = []
                    console.print(f"[yellow]{i18n.get('msg_data_cleared')}[/yellow]")
            self.save_config()

    def select_prompt_template(self, folder, key):
        prompt_dir = os.path.join(PROJECT_ROOT, "Resource", "Prompt", folder)
        if not os.path.exists(prompt_dir): return
        files = [f for f in os.listdir(prompt_dir) if f.endswith(".txt")]
        if not files: return
        for i, f in enumerate(files): console.print(f"{i+1}. {f}")
        
        choice_str = Prompt.ask(f"\n{i18n.get('prompt_template_select')}", choices=[str(i+1) for i in range(len(files))] + ["0"], show_choices=False)
        if choice_str == "0": return
        
        f_name = files[int(choice_str)-1]
        try:
            with open(os.path.join(prompt_dir, f_name), 'r', encoding='utf-8') as f: content = f.read()
            
            # Preview
            console.print(Panel(content, title=f"Preview: {f_name}", border_style="blue", height=15))
            
            if Confirm.ask(f"Apply '{f_name}'?", default=True):
                self.config[key] = {"last_selected_id": f_name.replace(".txt", ""), "prompt_content": content}
                self.save_config()
                console.print(f"[green]{i18n.get('msg_prompt_updated')}[/green]")
            else:
                console.print("[yellow]Cancelled.[/yellow]")
            time.sleep(1)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]"); time.sleep(2)
    def run_task(self, task_mode, target_path=None, continue_status=False, non_interactive=False):
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
            choices = ["0", "1", "2"]
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
                for i, path in enumerate(recent_projects):
                    short_path = path if len(path) < 60 else "..." + path[-57:]
                    menu_text += f"\n{recent_projects_start_idx + i}. {short_path}"
                    choices.append(str(recent_projects_start_idx + i))

            menu_text += f"\n\n[dim]0. {i18n.get('menu_exit')}[/dim]"
            console.print(Panel(menu_text, title=f"[bold]{i18n.get('menu_input_mode')}[/bold]", expand=False))
            
            prompt_text = i18n.get('prompt_select').strip().rstrip(':').rstrip('：')
            choice = IntPrompt.ask(f"\n{prompt_text}", choices=choices, show_choices=False)
            console.print("\n")
            if choice == 0: return
            
            if can_resume and choice == 3:
                target_path = last_path
                continue_status = True
            elif choice >= recent_projects_start_idx:
                recent_idx = choice - recent_projects_start_idx
                if 0 <= recent_idx < len(recent_projects):
                    target_path = recent_projects[recent_idx]
            elif choice == 1: # Single File
                start_path = self.config.get("label_input_path", ".")
                if os.path.isfile(start_path):
                    start_path = os.path.dirname(start_path)
                target_path = self.file_selector.select_path(start_path=start_path, select_file=True, select_dir=False)
            
            elif choice == 2: # Batch Folder
                start_path = self.config.get("label_input_path", ".")
                target_path = self.file_selector.select_path(start_path=start_path, select_file=False, select_dir=True)

            if not target_path:
                return

        # --- 非交互模式的路径处理 ---
        if not os.path.exists(target_path):
            console.print(f"[red]Error: Input path '{target_path}' not found.[/red]")
            return

        self._update_recent_projects(target_path)
        self.config["label_input_path"] = target_path
        
        # 自动设置输出路径 (如果用户未通过 -o 指定)
        if self.config.get("label_output_path") is None or self.config.get("label_output_path") == "":
            abs_input = os.path.abspath(target_path)
            parent_dir = os.path.dirname(abs_input)
            base_name = os.path.basename(abs_input)
            if os.path.isfile(target_path): base_name = os.path.splitext(base_name)[0]
            opath = os.path.join(parent_dir, f"{base_name}_AiNiee_Output")
            self.config["label_output_path"] = opath
        else:
            opath = self.config.get("label_output_path")

        self.save_config()
        
        if not continue_status and os.path.exists(os.path.join(opath, "cache", "AinieeCacheData.json")):
             if non_interactive:
                 continue_status = True # 在 -y 模式下自动恢复
             elif Confirm.ask(f"\n[yellow]Detected existing cache for this file. Resume?[/yellow]", default=True):
                 continue_status = True
        
        console.print(f"[dim]{i18n.get('label_input')}: {target_path}[/dim]")
        console.print(f"[dim]{i18n.get('label_output')}: {opath}[/dim]")

        # Start Logic
        self.ui = TaskUI(); Base.print = self.ui.log
        self.stop_requested = False
        self.live_state = [None] # 必须在这里初始化，防止 LogStream 报错

        # 确保 TaskExecutor 的配置与 CLIMenu 的配置同步
        self.task_executor.config.load_config_from_dict(self.config)
        
        # --- Middleware Conversion Logic ---
        original_ext = os.path.splitext(target_path)[1].lower()
        is_middleware_converted = False
        
        # Supported middleware formats that aren't natively handled well by AiNiee
        middleware_exts = [
            '.mobi', '.azw3', '.kepub', '.fb2', '.lit', '.lrf', 
            '.pdb', '.pmlz', '.rb', '.rtf', '.tcr', '.txtz', '.htmlz'
        ]
        
        if original_ext in middleware_exts:
            is_middleware_converted = True
            self.ui.log(f"[cyan]Detected {original_ext} format, calling conversion middleware...[/cyan]")
            temp_conv_dir = os.path.join(os.path.dirname(target_path), "temp_conv")
            os.makedirs(temp_conv_dir, exist_ok=True)
            
            # Construct the command for the conversion tool
            # -f for file mode, -m 1 for EPUB output
            conv_script = os.path.join(PROJECT_ROOT, "Tools", "批量电子书整合.py")
            cmd = f'uv run python "{conv_script}" -f "{target_path}" -m 1 -o "{temp_conv_dir}"'
            
            try:
                import subprocess
                # Run conversion silently
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    # Find the generated epub in the temp_conv_dir
                    epubs = [f for f in os.listdir(temp_conv_dir) if f.endswith(".epub")]
                    if epubs:
                        new_path = os.path.join(temp_conv_dir, epubs[0])
                        self.ui.log(f"[green]Conversion successful: {os.path.basename(new_path)}[/green]")
                        target_path = new_path # Swap path to the new epub
                    else:
                        raise Exception("Conversion script finished but no EPUB found.")
                else:
                    self.ui.log(f"[red]Conversion failed (Code {result.returncode}): {result.stderr}[/red]")
                    raise Exception("Conversion failed")
            except Exception as e:
                self.ui.log(f"[red]Middleware Error: {e}[/red]")
                Prompt.ask(f"\n{i18n.get('msg_press_enter_abort')}"); return
        # --- End Middleware ---


        # Patch tqdm to avoid conflict with Rich Live
        import ModuleFolders.Service.TaskExecutor.TaskExecutor as TaskExecutorModule
        TaskExecutorModule.tqdm = lambda x, **kwargs: x
        
        # Initialize suppression flags early
        import ModuleFolders.Infrastructure.Tokener.TiktokenLoader as TiktokenLoaderModule
        import ModuleFolders.Domain.FileReader.ReaderUtil as ReaderUtilModule
        TiktokenLoaderModule._SUPPRESS_OUTPUT = True
        ReaderUtilModule._SUPPRESS_OUTPUT = True

        original_stdout, original_stderr = sys.stdout, sys.stderr
        
        # Ensure our UI console uses the REAL stdout to avoid recursion
        self.ui_console = Console(file=original_stdout)
        
        # --- NEW: Session Logger ---
        log_file = None
        if self.config.get("enable_session_logging", True):
            try:
                log_dir = os.path.join(opath, "logs")
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, f"session_{time.strftime('%Y%m%d_%H%M%S')}.log")
                log_file = open(log_path, "w", encoding="utf-8")
            except: pass

        # Redirect stdout/stderr to capture errors in UI
        class LogStream:
            def __init__(self, ui, f=None, parent=None): 
                self.ui = ui
                self.f = f
                self.parent = parent
            def write(self, msg): 
                if not msg: return
                
                # Strip ANSI codes for logic and file
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                clean_msg = ansi_escape.sub('', msg).strip()
                
                if self.f:
                    try:
                        self.f.write(f"[{time.strftime('%H:%M:%S')}] {clean_msg}\n")
                        self.f.flush()
                    except: pass

                if not clean_msg or msg.startswith('\r'): return 
                
                # Aggressive filtering of UI artifacts or huge text blocks
                if " --> " in clean_msg and len(clean_msg) > 100:
                    return
                
                if any(c in clean_msg for c in "╭╮╯╰─│") and len(clean_msg) > 500:
                    return

                # Handle Status Updates specially - 增强拦截
                if "[STATUS]" in clean_msg:
                    # 仅拦截，不更新 Action，防止抖动
                    return

                # Decide where to output based on Live state
                if self.parent and self.parent.live_state[0]:
                    if clean_msg: self.ui.log(msg)
                else:
                    original_stdout.write(msg + "\n")
                    original_stdout.flush()
            def flush(self): pass
        
        sys.stdout = sys.stderr = LogStream(self.ui, log_file, self)

        # 启动键盘监听
        self.input_listener.start()
        self.input_listener.clear()

        # 定义完成事件
        self.task_running = True; finished = threading.Event(); success = threading.Event()

        def on_complete(e, d): 
            self.ui.log(f"[bold green]{i18n.get('msg_task_completed')}[/bold green]")
            success.set(); finished.set()
        def on_stop(e, d): 
            self.ui.log(f"[bold yellow]{i18n.get('msg_task_stopped')}[/bold yellow]")
            finished.set()
        
        # 订阅事件
        from ModuleFolders.Base.EventManager import EventManager
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_COMPLETED, on_complete)
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_STOP_DONE, on_stop)
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_UPDATE, self.ui.update_progress)
        EventManager.get_singleton().subscribe(Base.EVENT.SYSTEM_STATUS_UPDATE, self.ui.update_status)
        
        last_task_data = {"line": 0, "token": 0, "time": 0}
        def track_last_data(e, d):
            nonlocal last_task_data
            last_task_data = d
        EventManager.get_singleton().subscribe(Base.EVENT.TASK_UPDATE, track_last_data)

        try:
            # 提前启动 Live，确保加载过程可见
            with Live(self.ui.layout, console=self.ui_console, refresh_per_second=10, screen=True, transient=False) as live:
                self.ui.log(f"{i18n.get('msg_task_started')}")
                
                # --- 1. 文件与缓存加载 ---
                try:
                    cache_project = self.file_reader.read_files(self.config.get("translation_project", "AutoType"), target_path, self.config.get("exclude_rule_str", ""))
                    
                    if not cache_project:
                        self.ui.log("[red]No files loaded.[/red]")
                        time.sleep(2) # 给用户时间看错误信息
                        raise Exception("Load failed")
                        
                    self.cache_manager.load_from_project(cache_project)
                    total_items = self.cache_manager.get_item_count()
                    untranslated = self.cache_manager.get_item_count_by_status(TranslationStatus.UNTRANSLATED)
                    translated = self.cache_manager.get_item_count_by_status(TranslationStatus.TRANSLATED)

                    already_done = False
                    if task_mode == TaskType.TRANSLATION and untranslated == 0: already_done = True
                    elif task_mode == TaskType.POLISH and translated == 0:
                        if self.cache_manager.get_item_count_by_status(TranslationStatus.POLISHED) == total_items: already_done = True

                    # --- NEW: 极速预扫与成本预估 ---
                    if not already_done and untranslated > 0:
                        self.ui.log(f"[cyan]{i18n.get('msg_estimating')}[/cyan]")
                        from ModuleFolders.Infrastructure.Tokener.Tokener import Tokener
                        
                        est_tokens = 0
                        # 获取正确的配置对象以提取模型信息
                        t_config = self.task_executor.config
                        t_config.initialize()
                        # 核心修正：必须执行准备工作以载入 model 属性
                        t_config.prepare_for_translation(task_mode) 

                        for item in self.cache_manager.project.items_iter():
                            status = item.translation_status
                            if (task_mode == TaskType.TRANSLATION and status == TranslationStatus.UNTRANSLATED) or \
                               (task_mode == TaskType.POLISH and status == TranslationStatus.TRANSLATED):
                                # 修复：传递 t_config 以确保能获取到模型名称
                                token_val = Tokener.calculate_tokens(t_config, [{"role":"user", "content": item.source_text}], "")
                                est_tokens += (token_val or 0)

                        est_cost_usd = (est_tokens / 1000000) * 0.5 
                        target_p = str(self.config.get("target_platform", "")).lower()
                        is_local = any(k in target_p for k in ["local", "sakura"])
                        
                        est_markup = f"\n[bold yellow]>>> {i18n.get('msg_task_report_title')} (EST) <<<[/bold yellow]\n"
                        est_markup += f"{i18n.get('label_est_tokens')}: [bold green]{est_tokens}[/] Tokens\n"
                        
                        if not is_local:
                            est_markup += f"{i18n.get('label_est_cost')}: [bold]~${est_cost_usd:.4f}[/] USD\n"
                            est_minutes = est_tokens / 1000
                            est_markup += f"{i18n.get('label_est_time')}: [bold]{est_minutes:.1f}[/] min\n"
                        
                        est_markup += f"[dim]{i18n.get('msg_ref_only')}[/dim]\n"
                        self.ui.log(Panel(est_markup, border_style="yellow"))
                        time.sleep(2)
                    # --- End Pre-scan ---

                    self.ui.log(f"Loaded {total_items} items. ({translated} translated, {untranslated} remaining)")
                    
                    # 使用正确的方法初始化统计展示
                    self.ui.update_progress(None, {"line": translated, "total_line": total_items})
                    live.refresh()

                    if already_done:
                        self.ui.log("[bold green]All items already processed. Triggering export...[/bold green]")
                        self.task_executor.config.initialize()
                        cfg = self.task_executor.config
                        output_config = {
                            "translated_suffix": cfg.output_filename_suffix,
                            "bilingual_suffix": "_bilingual",
                            "bilingual_order": cfg.bilingual_text_order 
                        }
                        self.file_outputer.output_translated_content(
                            self.cache_manager.project, opath, target_path, output_config
                        )
                        self.ui.log(f"[green]Export completed successfully.[/green]")
                        success.set()
                        finished.set()
                        time.sleep(1) # 展示成功信息
                
                except Exception as e:
                    self.ui.log(f"[red]Error during initialization: {e}[/red]")
                    time.sleep(3)
                    raise e

                # --- 3. 启动任务线程 ---
                if not finished.is_set():
                    # Layout Shake
                    for _ in range(5):
                        self.ui.layout["upper"].ratio = 3 if _ % 2 == 0 else 4
                        live.refresh()
                        time.sleep(0.1)
                    self.ui.layout["upper"].ratio = 6
                    live.refresh()

                    EventManager.get_singleton().emit(
                        Base.EVENT.TASK_START, 
                        {
                            "continue_status": continue_status, 
                            "current_mode": task_mode,
                            "session_input_path": target_path,
                            "session_output_path": opath
                        }
                    )

                # --- 4. 主循环与输入监听 ---
                while not finished.is_set():
                    key = self.input_listener.get_key()
                    if key:
                        Base.global_input_queue.put(key)
                        if key == 'q':
                            self.ui.log("[bold red]Stop requested via keyboard...[/bold red]")
                            self.signal_handler(None, None)
                        elif key == 'p':
                            if Base.work_status == Base.STATUS.TASKING:
                                Base.work_status = Base.STATUS.IDLE
                                self.ui.log("[yellow]Task Paused (Press R to resume)[/yellow]")
                        elif key == 'r':
                            if Base.work_status == Base.STATUS.IDLE:
                                Base.work_status = Base.STATUS.TASKING
                                self.ui.log("[green]Task Resumed[/green]")
                        elif key == '-': # 减少线程
                            old_val = self.task_executor.config.actual_thread_counts
                            new_val = max(1, old_val - 1)
                            self.task_executor.config.actual_thread_counts = new_val
                            self.ui.log(f"[yellow]{i18n.get('msg_thread_changed').format(new_val)}[/yellow]")
                        elif key == '+': # 增加线程
                            old_val = self.task_executor.config.actual_thread_counts
                            new_val = min(100, old_val + 1)
                            self.task_executor.config.actual_thread_counts = new_val
                            self.ui.log(f"[green]{i18n.get('msg_thread_changed').format(new_val)}[/green]")
                        elif key == 'k': # 热切换 API
                            self.ui.log(f"[cyan]{i18n.get('msg_api_switching_manual')}[/cyan]")
                            EventManager.get_singleton().emit(Base.EVENT.TASK_API_STATUS_REPORT, {"force_switch": True})
                    
                    time.sleep(0.1)

        except KeyboardInterrupt: self.signal_handler(None, None)
        except Exception as e:
            # 这里不用 self.ui.log 了，因为 Live 可能已经退出或者异常就是 Live 抛出的
            # 但如果 Live 还在运行，print 会破坏布局。
            # 为了安全，我们只在 finally 后打印严重错误，或者在这里尝试记录
            pass 
        finally:
            self.input_listener.stop()
            if log_file: log_file.close()
            sys.stdout, sys.stderr = original_stdout, original_stderr
            self.task_running = False; Base.print = self.original_print
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_COMPLETED, on_complete)
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_STOP_DONE, on_stop)
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_UPDATE, self.ui.update_progress)
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_UPDATE, track_last_data)
            
            # --- NEW: Summary Report and Notification ---
            if success.is_set():
                # Notification (Simplified to avoid syntax errors)
                if self.config.get("enable_task_notification", True):
                    try:
                        if sys.platform == "win32":
                            import winsound
                            winsound.MessageBeep()
                        else:
                            print("\a") # ASCII Bell
                    except: pass
                
                # Report Panel
                lines = last_task_data.get("line", 0)
                tokens = last_task_data.get("token", 0)
                duration = last_task_data.get("time", 1)
                reqs = last_task_data.get("total_requests", 0)
                errs = last_task_data.get("error_requests", 0)
                
                report_table = Table(show_header=False, box=None, padding=(0, 2))
                report_table.add_row(f"[cyan]{i18n.get('label_report_total_lines')}:[/]", f"[bold]{lines}[/]")
                report_table.add_row(f"[cyan]{i18n.get('label_report_total_tokens')}:[/]", f"[bold]{tokens}[/]")
                report_table.add_row(f"[cyan]{i18n.get('label_report_total_time')}:[/]", f"[bold]{duration:.1f}s[/]")
                report_table.add_row(f"[cyan]{i18n.get('label_report_efficiency')}:[/]", f"[bold]{lines/(duration/60):.1f} L/m | {tokens/(duration/60):.1f} T/m[/]")
                success_rate = ((reqs - errs) / reqs * 100) if reqs > 0 else 100
                report_table.add_row(f"[cyan]{i18n.get('label_report_success_rate')}:[/]", f"[bold]{success_rate:.1f}%[/]")
                
                console.print("\n")
                console.print(Panel(report_table, title=f"[bold green]✓ {i18n.get('msg_task_report_title')}[/bold green]", expand=False))
            # --- End Report ---

            # --- Post-Translation Reverse Conversion ---
            if is_middleware_converted:
                if success.is_set() and self.config.get("enable_auto_restore_ebook", False):
                    console.print(f"\n[cyan]Auto-restoring to {original_ext}...[/cyan]")
                    output_dir = self.config.get("label_output_path")
                    if output_dir and os.path.isdir(output_dir):
                        translated_epubs = [f for f in os.listdir(output_dir) if f.endswith(".epub")]
                        if translated_epubs:
                            translated_epub_path = os.path.join(output_dir, translated_epubs[0])
                            
                            format_map_inv = {
                                'epub': '1', 'mobi': '4', 'azw3': '5',
                                'kepub': '8', 'fb2': '9', 'lit': '10', 'lrf': '11',
                                'pdb': '12', 'pmlz': '13', 'rb': '14', 'rtf': '15',
                                'tcr': '16', 'txtz': '17', 'htmlz': '18'
                            }
                            fmt_idx = format_map_inv.get(original_ext.lstrip('.'))
                            
                            if fmt_idx:
                                conv_script = os.path.join(PROJECT_ROOT, "Tools", "批量电子书整合.py")
                                cmd = f'uv run python "{conv_script}" -f "{translated_epub_path}" -m {fmt_idx} -o "{output_dir}"'
                                try:
                                    import subprocess
                                    console.print(f"[dim]Running: {cmd}[/dim]")
                                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                                    if res.returncode == 0:
                                        console.print(f"[green]Successfully restored to {original_ext}[/green]")
                                    else:
                                        console.print(f"[red]Restore failed: {res.stderr}[/red]")
                                except Exception as e:
                                    console.print(f"[red]Restore error: {e}[/red]")
                
                # 始终尝试清理临时转换目录
                try:
                    temp_dir = os.path.join(os.path.dirname(abs_input), "temp_conv")
                    if os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                        console.print(f"[dim]Cleaned up temporary conversion files.[/dim]")
                except Exception as e:
                    console.print(f"[dim]Note: Could not clean up temp files: {e}[/dim]")
            # --- End Post-Translation ---

            # 显示最近不重复的日志摘要
            unique_logs = []
            for log_entry in list(self.ui.logs)[-10:]:
                # 修正：将 Text 对象转换为字符串再进行正则处理
                log_str = str(log_entry)
                clean_log = re.sub(r'\[\d{2}:\d{2}:\d{2}\] ', '', log_str)
                if clean_log not in unique_logs:
                    unique_logs.append(clean_log)

            if unique_logs:
                console.print(f"\n[dim]{i18n.get('label_recent_summary')}[/dim]")
                for log in unique_logs:
                    # 使用 Text() 包装，防止 log 内容中的 [ 字符被误解析为标记
                    console.print(Text(f"  {log}"))
            
            Prompt.ask(f"\n{i18n.get('msg_task_ended')}")

    def run_export_only(self, target_path=None, non_interactive=False):
        # 1. Select Target (if in interactive mode)
        if target_path is None:
            last_path = self.config.get("label_input_path")
            can_resume = last_path and os.path.exists(last_path)
            
            console.clear()
            menu_text = f"1. {i18n.get('mode_single_file')}\n2. {i18n.get('mode_batch_folder')}"
            if can_resume:
                short_path = last_path if len(last_path) < 40 else "..." + last_path[-37:]
                menu_text += f"\n3. {i18n.get('mode_resume').format(short_path)}"
            
            console.print(Panel(menu_text, title=f"[bold]{i18n.get('menu_export_only')}[/bold]", expand=False))
            
            choices = ["0", "1", "2"]
            if can_resume:
                choices.append("3")
                
            prompt_txt = i18n.get('prompt_select').strip().rstrip(':').rstrip('：')
            choice = IntPrompt.ask(f"\n{prompt_txt}", choices=choices, show_choices=False)
            
            if choice == 0: return
            
            if choice == 3:
                target_path = last_path
            else:
                is_file_mode = choice == 1
                start_path = self.config.get("label_input_path", ".")
                if is_file_mode and os.path.isfile(start_path):
                    start_path = os.path.dirname(start_path)

                target_path = self.file_selector.select_path(
                    start_path=start_path,
                    select_file=is_file_mode,
                    select_dir=not is_file_mode
                )
            if not target_path: 
                return

        # 2. Setup paths
        if not os.path.exists(target_path):
            console.print(f"[red]Error: Input path '{target_path}' not found.[/red]")
            return
            
        abs_input = os.path.abspath(target_path)
        parent_dir = os.path.dirname(abs_input)
        base_name = os.path.basename(abs_input)
        if os.path.isfile(target_path):
            base_name = os.path.splitext(base_name)[0]
        opath = os.path.join(parent_dir, f"{base_name}_AiNiee_Output")
        
        # 3. Load cache
        cache_path = os.path.join(opath, "cache", "AinieeCacheData.json")
        while not os.path.exists(cache_path):
            console.print(f"\n[yellow]Cache not found at default path: {cache_path}[/yellow]")
            if non_interactive:
                console.print(f"[red]Aborting in non-interactive mode.[/red]")
                return
            opath = Prompt.ask(i18n.get('msg_enter_output_path')).strip('"').strip("'")
            if opath.lower() == 'q':
                return
            cache_path = os.path.join(opath, "cache", "AinieeCacheData.json")

        try:
            with console.status(f"[cyan]{i18n.get('msg_export_started')}[/cyan]"):
                project = CacheManager.read_from_file(cache_path)
                
                self.task_executor.config.initialize()
                cfg = self.task_executor.config
                output_config = {
                    "translated_suffix": cfg.output_filename_suffix,
                    "bilingual_suffix": "_bilingual",
                    "bilingual_order": cfg.bilingual_text_order 
                }
                
                self.file_outputer.output_translated_content(
                    self.cache_manager.project if hasattr(self.cache_manager, 'project') and self.cache_manager.project else project,
                    opath, 
                    target_path, 
                    output_config
                )
            console.print(f"\n[green]✓ {i18n.get('msg_export_completed')}[/green]")
            console.print(f"[dim]Output: {opath}[/dim]")
        except Exception as e:
            console.print(f"[red]Export Error: {e}[/red]")
            
        Prompt.ask(f"\n{i18n.get('msg_press_enter')}")

def main():
    parser = argparse.ArgumentParser(description="AiNiee CLI - A powerful tool for AI-driven translation and polishing.", add_help=False)
    
    # 将 --help 参数单独处理，以便自定义帮助信息
    parser.add_argument('-h', '--help', action='help', default=argparse.SUPPRESS, help='Show this help message and exit.')

    parser.add_argument('task', nargs='?', choices=['translate', 'polish', 'export'], help=i18n.get('help_task'))
    parser.add_argument('input_path', nargs='?', help=i18n.get('help_input'))
    
    parser.add_argument('-o', '--output', dest='output_path', help=i18n.get('help_output'))
    parser.add_argument('-p', '--profile', dest='profile', help=i18n.get('help_profile'))
    parser.add_argument('-s', '--source', dest='source_lang', help=i18n.get('help_source'))
    parser.add_argument('-t', '--target', dest='target_lang', help=i18n.get('help_target'))
    parser.add_argument('-r', '--resume', action='store_true', help=i18n.get('help_resume'))
    parser.add_argument('-y', '--yes', action='store_true', dest='non_interactive', help=i18n.get('help_yes'))

    args = parser.parse_args()

    cli = CLIMenu()
    if args.task and args.input_path:
        cli.run_non_interactive(args)
    else:
        cli.main_menu()

if __name__ == "__main__":
    main()
