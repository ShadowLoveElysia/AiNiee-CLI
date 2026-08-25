"""
任务队列菜单模块
从 ainiee_cli.py 分离
"""
import os
import time

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.table import Table

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.Automation.WorkflowRunner import describe_workflow_steps
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.UserInterface.UIHelpers import open_in_editor


console = Console()


class TaskQueueMenu:
    """任务队列菜单。"""

    def __init__(self, host):
        self.host = host

    @property
    def config(self):
        return self.host.config

    @property
    def i18n(self):
        return self.host.i18n

    def show(self):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()

        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{self.i18n.get('menu_task_queue')}[/bold]"))

            self._render_queue_status(queue_manager)
            choice = self._ask_main_choice(queue_manager)

            if choice == 0:
                break
            if choice == 1:
                self._add_task(queue_manager)
            elif choice == 2:
                self._remove_task(queue_manager)
            elif choice == 3:
                self._edit_task(queue_manager)
            elif choice == 4:
                self._edit_queue_json(queue_manager)
            elif choice == 5:
                self._clear_queue(queue_manager)
            elif choice == 6:
                self._start_queue(queue_manager)
                break
            elif choice == 7:
                self._reorder_queue(queue_manager)

        self._wait_for_queue_completion(queue_manager)

    def _render_queue_status(self, queue_manager):
        if not queue_manager.tasks:
            console.print(f"[dim]{self.i18n.get('msg_queue_empty')}[/dim]")
            return

        table = Table(show_header=True, box=None)
        table.add_column(self.i18n.get("table_column_id"), style="dim")
        table.add_column(self.i18n.get("table_column_task"))
        table.add_column(self.i18n.get("table_column_details"))
        table.add_column(self.i18n.get("table_column_status"))

        for index, task in enumerate(queue_manager.tasks):
            status_style = "green" if task.status == "completed" else "yellow" if task.status in {"running", "workflow", "partial"} else "dim"
            type_str = self._get_task_type_tag(task.task_type)
            if getattr(task, "workflow_steps", None):
                details = (
                    f"{task.profile or 'def'}/{task.rules_profile or 'def'} | "
                    f"{describe_workflow_steps(task.workflow_steps, self.i18n)}"
                )
            else:
                details = (
                    f"{task.profile or 'def'}/{task.rules_profile or 'def'} | "
                    f"{task.source_lang or 'auto'}->{task.target_lang or 'auto'}"
                )
            table.add_row(
                str(index + 1),
                f"[{type_str}] {os.path.basename(task.input_path)}",
                details,
                f"[{status_style}]{self._get_localized_status(task.status)}[/]",
            )

        console.print(table)

    def _ask_main_choice(self, queue_manager):
        console.print(f"\n[cyan]1.[/] {self.i18n.get('menu_queue_add')}")
        if queue_manager.tasks:
            console.print(f"[cyan]2.[/] {self.i18n.get('menu_queue_remove')}")
            console.print(f"[cyan]3.[/] {self.i18n.get('menu_queue_edit_fine')}")
            console.print(f"[cyan]4.[/] {self.i18n.get('menu_queue_edit_json')}")
            console.print(f"[cyan]5.[/] {self.i18n.get('menu_queue_clear')}")
            console.print(f"[bold green]6.[/] {self.i18n.get('menu_queue_start')}")
            if len(queue_manager.tasks) > 1:
                console.print(f"[cyan]7.[/] {self.i18n.get('menu_queue_reorder')}")

        console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")

        choices = ["0", "1"]
        if queue_manager.tasks:
            choices.extend(["2", "3", "4", "5", "6"])
            if len(queue_manager.tasks) > 1:
                choices.append("7")

        return IntPrompt.ask(
            f"\n{self.i18n.get('prompt_select')}",
            choices=choices,
            show_choices=False,
        )

    def _add_task(self, queue_manager):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueTaskItem

        console.print(f"\n[bold]{self.i18n.get('prompt_queue_add_task_type')}[/bold]")
        type_table = Table(show_header=False, box=None)
        type_table.add_row("[cyan]1.[/]", self.i18n.get("task_type_translation"))
        type_table.add_row("[cyan]2.[/]", self.i18n.get("task_type_polishing"))
        type_table.add_row("[cyan]3.[/]", self.i18n.get("task_type_all_in_one"))
        console.print(type_table)
        task_choice = IntPrompt.ask(
            self.i18n.get("prompt_queue_add_task_type"),
            choices=["1", "2", "3"],
            default=1,
            show_choices=False,
        )
        type_map = {
            1: TaskType.TRANSLATION,
            2: TaskType.POLISH,
            3: TaskType.TRANSLATE_AND_POLISH,
        }
        task_type = type_map[task_choice]
        start_path = self.config.get("label_input_path", ".")
        input_path = self.host.file_selector.select_path(start_path=start_path)
        if input_path:
            queue_manager.add_task(QueueTaskItem(task_type, input_path))
            console.print(f"[green]{self.i18n.get('msg_queue_task_added_default')}[/green]")
        time.sleep(1)

    def _remove_task(self, queue_manager):
        index = IntPrompt.ask(self.i18n.get("prompt_queue_task_id_remove"), default=1) - 1
        if queue_manager.remove_task(index):
            console.print(f"[green]{self.i18n.get('msg_queue_task_removed')}[/green]")
        else:
            console.print(f"[red]{self.i18n.get('msg_queue_task_remove_failed')}[/red]")
        time.sleep(1)

    def _edit_task(self, queue_manager):
        index = IntPrompt.ask(self.i18n.get("prompt_queue_task_id_edit"), default=1) - 1
        if not 0 <= index < len(queue_manager.tasks):
            console.print(f"[red]{self.i18n.get('msg_queue_invalid_task_id')}[/red]")
            time.sleep(1)
            return

        task = queue_manager.tasks[index]
        console.print(
            Panel(
                f"[bold]{self.i18n.get('menu_queue_edit_fine')}[/bold]: "
                f"#{index + 1} {os.path.basename(task.input_path)}"
            )
        )

        task_type_map = {
            TaskType.TRANSLATION: self.i18n.get("task_type_translation"),
            TaskType.POLISH: self.i18n.get("task_type_polishing"),
            TaskType.TRANSLATE_AND_POLISH: self.i18n.get("task_type_all_in_one"),
        }
        console.print(f"\n[cyan]{self.i18n.get('ui_recent_type')}:[/] {task_type_map.get(task.task_type, self.i18n.get('label_unknown'))}")
        new_task_type = Prompt.ask(
            f"{self.i18n.get('prompt_task_type_queue')}{self.i18n.get('tip_follow_profile')}",
            choices=list(task_type_map.values()) + [""],
            default=task_type_map.get(task.task_type, ""),
        )
        if new_task_type:
            task.task_type = {value: key for key, value in task_type_map.items()}[new_task_type]

        task.input_path = Prompt.ask(
            f"{self.i18n.get('setting_input_path')}{self.i18n.get('tip_follow_profile')}",
            default=task.input_path,
        )
        task.output_path = Prompt.ask(
            f"{self.i18n.get('setting_output_path')}{self.i18n.get('tip_follow_profile')}",
            default=task.output_path or "",
        ) or None

        console.print(
            f"\n[cyan]{self.i18n.get('label_current_project_type')}:[/] "
            f"{task.project_type or self.config.get('translation_project', 'AutoType')}"
        )
        task.project_type = Prompt.ask(
            f"{self.i18n.get('prompt_project_type_queue')}{self.i18n.get('tip_follow_profile')}",
            default=task.project_type or "",
        ) or None

        console.print(
            f"\n[cyan]{self.i18n.get('label_current_lang')}:[/] "
            f"{task.source_lang or self.config.get('source_language')} -> "
            f"{task.target_lang or self.config.get('target_language')}"
        )
        task.source_lang = Prompt.ask(
            f"{self.i18n.get('prompt_source_lang_queue')}{self.i18n.get('tip_follow_profile')}",
            default=task.source_lang or "",
        ) or None
        task.target_lang = Prompt.ask(
            f"{self.i18n.get('prompt_target_lang_queue')}{self.i18n.get('tip_follow_profile')}",
            default=task.target_lang or "",
        ) or None

        profiles = self.host._get_profiles_list(self.host.profiles_dir)
        rules = ["None"] + self.host._get_profiles_list(self.host.rules_profiles_dir)
        console.print(f"\n[cyan]{self.i18n.get('label_profiles')}:[/] {', '.join(profiles)}")
        task.profile = Prompt.ask(
            f"{self.i18n.get('prompt_profile_queue')}{self.i18n.get('tip_follow_profile')}",
            default=task.profile or "",
        ) or None
        console.print(f"[cyan]{self.i18n.get('label_rules_profiles')}:[/] {', '.join(rules)}")
        task.rules_profile = Prompt.ask(
            f"{self.i18n.get('prompt_rules_profile_queue')}{self.i18n.get('tip_follow_profile')}",
            choices=rules + [""],
            default=task.rules_profile or "",
        ) or None

        current_platform = task.platform or self.config.get("target_platform")
        console.print(f"\n[cyan]{self.i18n.get('label_platform_override')}:[/] {current_platform or self.i18n.get('label_default')}")
        platforms_list = list(self.config.get("platforms", {}).keys())
        task.platform = Prompt.ask(
            f"{self.i18n.get('label_platform_override')}{self.i18n.get('tip_follow_profile')}",
            choices=platforms_list + [""],
            default=task.platform or "",
        ) or None

        self._edit_task_model_settings(task)

        task.api_url = Prompt.ask(
            f"{self.i18n.get('label_url_override')}{self.i18n.get('tip_follow_profile')}",
            default=task.api_url or "",
        ) or None
        task.api_key = None
        console.print(
            "[dim]Queue files do not store API keys. Use a profile, or provide a "
            "temporary key when running a task directly.[/dim]"
        )

        task.threads = IntPrompt.ask(
            f"{self.i18n.get('label_threads_override')}{self.i18n.get('tip_follow_profile')}",
            default=task.threads if task.threads is not None else 0,
        ) or None
        task.retry = IntPrompt.ask(
            f"{self.i18n.get('setting_retry_count')}{self.i18n.get('tip_follow_profile')}",
            default=task.retry if task.retry is not None else 0,
        ) or None
        task.timeout = IntPrompt.ask(
            f"{self.i18n.get('setting_request_timeout')}{self.i18n.get('tip_follow_profile')}",
            default=task.timeout if task.timeout is not None else 0,
        ) or None
        task.rounds = IntPrompt.ask(
            f"{self.i18n.get('setting_round_limit')}{self.i18n.get('tip_follow_profile')}",
            default=task.rounds if task.rounds is not None else 0,
        ) or None
        task.pre_lines = IntPrompt.ask(
            f"{self.i18n.get('setting_pre_line_counts')}{self.i18n.get('tip_follow_profile')}",
            default=task.pre_lines if task.pre_lines is not None else 0,
        ) or None

        self._edit_task_segmentation(task)
        self._edit_task_thinking(task)

        queue_manager.save_tasks()
        console.print(f"[green]{self.i18n.get('msg_queue_task_updated')}[/green]")
        time.sleep(1)

    def _edit_task_model_settings(self, task):
        if task.platform:
            available_models = self._get_available_models_for_platform(task)
            if available_models:
                console.print(f"[cyan]  {self.i18n.get('label_available_models')} ({task.platform}):[/] {', '.join(available_models)}")
                task.model = Prompt.ask(
                    f"{self.i18n.get('label_model_override')}{self.i18n.get('tip_follow_profile')}",
                    choices=available_models + [""],
                    default=task.model or "",
                ) or None
                return

        task.model = Prompt.ask(
            f"{self.i18n.get('label_model_override')}{self.i18n.get('tip_follow_profile')}",
            default=task.model or "",
        ) or None

    def _get_available_models_for_platform(self, task):
        platform_config = self.config.get("platforms", {}).get(task.platform, {})
        api_format = platform_config.get("api_format")
        platform_config_for_fetch = {
            "api_url": task.api_url or self.config.get("base_url"),
            "api_key": task.api_key or self.config.get("api_key"),
            "auto_complete": platform_config.get("auto_complete", False),
        }

        if api_format == "Anthropic":
            from ModuleFolders.Infrastructure.LLMRequester.AnthropicRequester import AnthropicRequester

            return AnthropicRequester().get_model_list(platform_config_for_fetch)
        if api_format == "OpenAI":
            from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester

            return OpenaiRequester().get_model_list(platform_config_for_fetch)
        if api_format == "Google":
            from ModuleFolders.Infrastructure.LLMRequester.GoogleRequester import GoogleRequester

            return GoogleRequester().get_model_list(platform_config_for_fetch)
        return []

    def _edit_task_segmentation(self, task):
        default_limit_mode = "tokens" if self.config.get("tokens_limit_switch") else "lines"
        current_limit_mode = (
            "lines"
            if task.lines_limit is not None
            else "tokens"
            if task.tokens_limit is not None
            else default_limit_mode
        )

        limit_choice = Prompt.ask(
            f"{self.i18n.get('setting_limit_mode')}{self.i18n.get('tip_follow_profile')}",
            choices=["lines", "tokens", ""],
            default=current_limit_mode,
        )
        if limit_choice == "lines":
            task.lines_limit = IntPrompt.ask(
                f"{self.i18n.get('prompt_limit_val')} (Lines){self.i18n.get('tip_follow_profile')}",
                default=task.lines_limit or self.config.get("lines_limit"),
            ) or None
            task.tokens_limit = None
        elif limit_choice == "tokens":
            task.tokens_limit = IntPrompt.ask(
                f"{self.i18n.get('prompt_limit_val')} (Tokens){self.i18n.get('tip_follow_profile')}",
                default=task.tokens_limit or self.config.get("tokens_limit"),
            )
            task.tokens_limit = max(400, task.tokens_limit) if task.tokens_limit else None
            task.lines_limit = None
        else:
            task.lines_limit = None
            task.tokens_limit = None

    def _edit_task_thinking(self, task):
        current_think_depth = task.think_depth or self.config.get("think_depth", "low")
        is_anthropic = (
            task.platform
            and self.config.get("platforms", {}).get(task.platform, {}).get("api_format") == "Anthropic"
        )
        is_deepseek = bool(task.platform and task.platform.lower() == "deepseek")
        if is_anthropic:
            task.think_depth = Prompt.ask(
                f"{self.i18n.get('prompt_think_depth_claude')}{self.i18n.get('tip_follow_profile')}",
                choices=["low", "medium", "high", ""],
                default=current_think_depth,
            ) or None
        elif is_deepseek:
            task.think_depth = Prompt.ask(
                f"{self.i18n.get('prompt_think_depth')}{self.i18n.get('tip_follow_profile')}",
                choices=["low", "medium", "high", "xhigh", "max", ""],
                default=current_think_depth,
            ) or None
        else:
            task.think_depth = Prompt.ask(
                f"{self.i18n.get('prompt_think_depth')}{self.i18n.get('tip_follow_profile')}",
                choices=["minimal", "low", "medium", "high", "xhigh", ""],
                default=current_think_depth,
            ) or None

        console.print(f"[dim]{self.i18n.get('hint_think_budget')}[/dim]")
        budget_str = Prompt.ask(
            f"{self.i18n.get('menu_api_think_budget')}{self.i18n.get('tip_follow_profile')}",
            default=str(task.thinking_budget) if task.thinking_budget is not None else "0",
        )
        try:
            task.thinking_budget = int(budget_str) if budget_str else None
        except ValueError:
            task.thinking_budget = None

    def _edit_queue_json(self, queue_manager):
        if open_in_editor(queue_manager.queue_file):
            Prompt.ask(f"\n{self.i18n.get('msg_press_enter_after_save')}")
            queue_manager.load_tasks()
            console.print(f"[green]{self.i18n.get('msg_queue_reloaded_from_file')}[/green]")
        time.sleep(1)

    def _clear_queue(self, queue_manager):
        if queue_manager.clear_tasks():
            console.print(f"[green]{self.i18n.get('msg_queue_cleared')}[/green]")
        else:
            console.print(f"[red]{self.i18n.get('msg_queue_clear_failed_running')}[/red]")
        time.sleep(1)

    def _start_queue(self, queue_manager):
        if not queue_manager.tasks:
            return
        if queue_manager.is_running:
            console.print(f"[yellow]{self.i18n.get('msg_queue_already_running')}[/yellow]")
            time.sleep(1)
            return

        console.print(f"\n[bold green]{self.i18n.get('msg_queue_starting')}[/bold green]")
        self.host._is_queue_mode = True
        self.host.start_queue_log_monitor()
        queue_manager.start_queue(self.host)

    def _reorder_queue(self, queue_manager):
        if len(queue_manager.tasks) <= 1:
            console.print(f"[yellow]{self.i18n.get('msg_queue_reorder_need_two')}[/yellow]")
            time.sleep(1)
            return

        console.print(Panel(f"[bold]{self.i18n.get('menu_queue_reorder')}[/bold]"))
        console.print(f"\n[cyan]{self.i18n.get('label_current_order')}:[/]")
        for index, task in enumerate(queue_manager.tasks):
            console.print(f"  {index + 1}. [{self._get_task_type_tag(task.task_type)}] {os.path.basename(task.input_path)}")

        console.print(f"\n[cyan]{self.i18n.get('options_label')}:[/]")
        console.print(f"[cyan]1.[/] {self.i18n.get('menu_queue_move_up')}")
        console.print(f"[cyan]2.[/] {self.i18n.get('menu_queue_move_down')}")
        console.print(f"[cyan]3.[/] {self.i18n.get('menu_queue_move_to')}")
        console.print(f"[dim]0. {self.i18n.get('menu_back')}[/dim]")

        reorder_choice = IntPrompt.ask(
            f"\n{self.i18n.get('prompt_select')}",
            choices=["0", "1", "2", "3"],
            show_choices=False,
        )
        if reorder_choice == 0:
            return
        if reorder_choice == 1:
            task_id = IntPrompt.ask(self.i18n.get("prompt_task_id"), default=1)
            if queue_manager.move_task_up(task_id - 1):
                console.print(f"[green]{self.i18n.get('msg_task_moved_up').format(task_id)}[/green]")
            else:
                console.print(f"[red]{self.i18n.get('msg_task_move_failed')}[/red]")
        elif reorder_choice == 2:
            task_id = IntPrompt.ask(self.i18n.get("prompt_task_id"), default=1)
            if queue_manager.move_task_down(task_id - 1):
                console.print(f"[green]{self.i18n.get('msg_task_moved_down').format(task_id)}[/green]")
            else:
                console.print(f"[red]{self.i18n.get('msg_task_move_failed')}[/red]")
        elif reorder_choice == 3:
            from_id = IntPrompt.ask(self.i18n.get("prompt_task_id_from"), default=1)
            to_id = IntPrompt.ask(self.i18n.get("prompt_task_id_to"), default=1)
            if queue_manager.move_task(from_id - 1, to_id - 1):
                console.print(f"[green]{self.i18n.get('msg_task_moved_to').format(from_id, to_id)}[/green]")
            else:
                console.print(f"[red]{self.i18n.get('msg_task_move_failed')}[/red]")
        time.sleep(1)

    def _wait_for_queue_completion(self, queue_manager):
        if not getattr(self.host, "_is_queue_mode", False):
            return

        try:
            console.print("[green]Waiting for queue to complete...[/green]")
            while queue_manager.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            Base.cancel_active_task_session()
            Base.work_status = Base.STATUS.STOPING
            console.print("\n[bold red]Queue stopped by user.[/bold red]")
        finally:
            self.host.stop_queue_log_monitor()
            self.host._is_queue_mode = False

    def _get_localized_status(self, status):
        status_map = {
            "waiting": self.i18n.get("task_status_waiting"),
            "workflow": self.i18n.get("task_status_workflow"),
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

    def _get_task_type_tag(self, task_type):
        if str(task_type).lower() in {"translation", "translate"}:
            return "T"
        if str(task_type).lower() in {"polishing", "polish"}:
            return "P"
        if str(task_type).lower() in {"all_in_one", "translate_and_polish"}:
            return "T+P"
        if task_type == TaskType.TRANSLATE_AND_POLISH:
            return "T+P"
        if task_type == TaskType.TRANSLATION:
            return "T"
        return "P"
