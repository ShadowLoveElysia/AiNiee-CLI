"""
自动化菜单模块
从 ainiee_cli.py 分离
"""
import os
import threading
import time
from datetime import datetime

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table
from rich.tree import Tree

from ModuleFolders.Infrastructure.Automation.WorkflowRunner import (
    describe_workflow_steps,
    normalize_workflow_steps,
    task_type_to_step_type,
)

console = Console()


class AutomationMenu:
    """自动化设置菜单"""

    def __init__(self, host):
        """
        初始化自动化菜单

        Args:
            host: CLIMenu实例，提供config、i18n等依赖
        """
        self.host = host
        self.scheduler_manager = None
        self.watch_manager = None
        self._automation_status_view_active = False

    @property
    def config(self):
        return self.host.config

    @property
    def i18n(self):
        return self.host.i18n

    def _ensure_managers(self):
        """确保管理器已初始化"""
        from ModuleFolders.Infrastructure.Automation import SchedulerManager, WatchManager

        if self.scheduler_manager is None:
            self.scheduler_manager = SchedulerManager(execute_callback=self._execute_scheduled_task)
            self.scheduler_manager.load_from_config(self.config)
            self.scheduler_manager.set_callback(self._execute_scheduled_task)
        if self.watch_manager is None:
            self.watch_manager = WatchManager(
                task_callback=self._execute_watch_task,
                queue_callback=self._enqueue_watch_task,
                queue_start_callback=self._run_queue_if_needed,
            )
            self.watch_manager.load_from_config(self.config)
            self.watch_manager.set_callbacks(
                task_callback=self._execute_watch_task,
                queue_callback=self._enqueue_watch_task,
                queue_start_callback=self._run_queue_if_needed,
            )

    def _enqueue_watch_task(self, task_config):
        """Add a watch-triggered workflow task to the central queue."""
        from ModuleFolders.Infrastructure.TaskContract import TaskSpec, select_task_contract_fields
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager, QueueTaskItem

        queue_manager = QueueManager()
        task_payload = select_task_contract_fields(task_config)
        task_payload["task_type"] = task_config.get("task_type", "translation")
        task_payload["input_path"] = task_config.get("input_path", "")
        task_fields = TaskSpec.from_mapping(task_payload).to_queue_fields()
        task_item = QueueTaskItem(
            **task_fields,
            resume_explicit="resume" in task_payload,
            workflow_steps=task_config.get("workflow_steps") or [],
            source=task_config.get("source"),
            rule_id=task_config.get("rule_id"),
            trigger_file_path=task_config.get("trigger_file_path"),
            trigger_file_name=task_config.get("trigger_file_name"),
            trigger_detected_at=task_config.get("trigger_detected_at"),
            series_incremental=task_config.get("series_incremental", False),
            series_key=task_config.get("series_key"),
            series_volume=task_config.get("series_volume"),
            extra=self._automation_task_extra(task_config),
        )
        queue_manager.add_task(task_item)

        ahead_count = sum(
            1
            for item in queue_manager.tasks[:-1]
            if getattr(item, "status", "waiting") in {"waiting", "workflow", "translating", "translated", "polishing"}
        )
        file_name = (
            task_config.get("trigger_file_name")
            or os.path.basename(task_config.get("trigger_file_path", ""))
            or os.path.basename(task_config.get("input_path", ""))
        )
        self.watch_manager._log(
            "info",
            self.i18n.get("automation_watch_file_queued").format(file_name, ahead_count, file_name),
        )

        if self._can_start_automation_queue() and task_config.get("auto_start", True) and not task_config.get("defer_auto_start"):
            self._run_queue_if_needed()

    @staticmethod
    def _automation_task_extra(task_config):
        extra = dict(task_config.get("extra") or {})
        for key in (
            "folder_batch_id",
            "folder_batch_index",
            "folder_batch_total",
            "series_initial_volume",
            "series_batch_volumes",
            "series_container_path",
            "series_missing_volumes",
            "defer_auto_start",
        ):
            if key in task_config and task_config.get(key) is not None:
                extra[key] = task_config.get(key)
        return extra

    def _can_start_automation_queue(self) -> bool:
        return bool(self._automation_status_view_active)

    def _run_queue_if_needed(self):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        if not self._can_start_automation_queue():
            if self.watch_manager:
                self.watch_manager._log("info", self.i18n.get("automation_queue_start_preview_required"))
            return

        queue_manager = QueueManager()
        if not queue_manager.is_running:
            queue_manager.hot_reload_queue(quiet=True)
        if not queue_manager.tasks or queue_manager.is_running:
            return

        self.host._is_queue_mode = True
        queue_manager.start_queue(self.host, automation_background=True)

        def queue_cleanup():
            try:
                while queue_manager.is_running:
                    time.sleep(0.5)
            finally:
                self.host._is_queue_mode = False

        threading.Thread(target=queue_cleanup, daemon=True).start()

    def _run_queue_blocking(self):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()
        if not queue_manager.is_running:
            queue_manager.hot_reload_queue(quiet=True)
        if not queue_manager.tasks:
            return

        self._run_queue_if_needed()
        while queue_manager.is_running:
            time.sleep(0.5)

    def _execute_watch_task(self, task_config):
        return self._run_background_workflow_blocking(task_config)

    def _execute_scheduled_task(self, task_config):
        workflow_steps = task_config.get("workflow_steps") or []
        input_path = str(task_config.get("input_path") or "").strip().lower()
        if (
            task_config.get("run_queue")
            or task_config.get("trigger_type") in {"queue_added", "queue_pending"}
            or input_path in {"queue", "__queue__", "队列"}
            or self._workflow_runs_queue(workflow_steps)
        ):
            return self._run_queue_blocking()
        if workflow_steps:
            return self._run_background_workflow_blocking(task_config)
        return self._execute_watch_task(task_config)

    @staticmethod
    def _workflow_runs_queue(workflow_steps: list) -> bool:
        for step in workflow_steps or []:
            if isinstance(step, dict) and str(step.get("type") or "").strip().lower() == "run_queue":
                return True
        return False

    def _run_background_workflow_blocking(self, task_config):
        from ModuleFolders.Infrastructure.Automation.AutomationProcessRunner import AutomationProcessRunner
        from ModuleFolders.Infrastructure.Automation.AutomationProgress import TERMINAL_STATUSES, read_progress_file

        run_info = AutomationProcessRunner.start(
            task_config,
            project_root=getattr(self.host, "PROJECT_ROOT", None),
        )
        run_id = run_info.get("run_id")
        progress_file = run_info.get("progress_file")
        process = AutomationProcessRunner.get_process(run_id)
        while process and process.poll() is None:
            time.sleep(0.5)

        state = read_progress_file(progress_file)
        status = state.get("status")
        if status in TERMINAL_STATUSES:
            return status in {"completed", "partial", "enqueued"}
        return bool(process and process.returncode == 0)

    def show(self):
        """显示自动化菜单（入口方法）"""
        self._ensure_managers()

        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{self.i18n.get('menu_automation')}[/bold]"))
            console.print(f"[dim]{self.i18n.get('automation_cooperation_hint')}[/dim]")
            console.print(f"[yellow]{self.i18n.get('automation_preview_required_tip')}[/yellow]\n")

            # 获取状态
            sched_status = self.scheduler_manager.get_status()
            watch_status = self.watch_manager.get_status()

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"{self.i18n.get('menu_automation_scheduler')}",
                         f"[{'green' if sched_status['running'] else 'dim'}]{self.i18n.get('automation_running') if sched_status['running'] else self.i18n.get('automation_stopped')}[/] ({sched_status['task_count']} {self.i18n.get('automation_task_count')})")
            table.add_row("[cyan]2.[/]", f"{self.i18n.get('menu_automation_watch')}",
                         f"[{'green' if watch_status['running'] else 'dim'}]{self.i18n.get('automation_running') if watch_status['running'] else self.i18n.get('automation_stopped')}[/] ({watch_status['rule_count']} {self.i18n.get('automation_task_count')})")
            table.add_row("[cyan]3.[/]", f"{self.i18n.get('menu_automation_status')}")
            table.add_row("[cyan]4.[/]", f"{self.i18n.get('menu_automation_queue')}")
            console.print(table)
            console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")

            choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4"], show_choices=False)

            if choice == 0:
                # 保存配置
                self.scheduler_manager.save_to_config(self.config)
                self.watch_manager.save_to_config(self.config)
                self.host.save_config()
                break
            elif choice == 1:
                self.scheduler_submenu()
            elif choice == 2:
                self.watch_submenu()
            elif choice == 3:
                self.automation_status_view()
            elif choice == 4:
                self._open_task_queue_menu()

    def _open_task_queue_menu(self):
        from ModuleFolders.UserInterface.TaskQueueMenu import TaskQueueMenu

        handler = getattr(self.host, "task_queue_menu_handler", None)
        if handler is not None:
            handler.show()
            return
        TaskQueueMenu(self.host).show()

    def automation_output_collection_settings(self):
        while True:
            self.host.display_banner()
            config = self._get_automation_output_collection_config()
            path_value = config["output_path"] or self.i18n.get("label_not_set")

            console.print(Panel(
                f"[bold]{self.i18n.get('automation_output_collection_title')}[/bold]\n\n"
                f"{self.i18n.get('automation_output_collection_desc')}",
                border_style="blue",
            ))

            status_table = Table(show_header=False, box=None)
            status_table.add_row(
                self.i18n.get("automation_output_collection_enabled"),
                f"[{'green' if config['enabled'] else 'red'}]{'ON' if config['enabled'] else 'OFF'}[/]",
            )
            status_table.add_row(self.i18n.get("automation_output_collection_path"), path_value)
            console.print(status_table)

            option_table = Table(show_header=False, box=None)
            option_table.add_row("[cyan]1.[/]", self.i18n.get("automation_output_collection_toggle"))
            option_table.add_row("[cyan]2.[/]", self.i18n.get("automation_output_collection_set_path"))
            option_table.add_row("[cyan]3.[/]", self.i18n.get("automation_output_collection_clear_path"))
            console.print(option_table)
            console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")

            choice = IntPrompt.ask(self.i18n.get("prompt_select"), choices=["0", "1", "2", "3"], show_choices=False)
            if choice == 0:
                break
            if choice == 1:
                config["enabled"] = not config["enabled"]
                self._save_automation_output_collection_config(config)
                console.print(f"[green]{self.i18n.get('automation_output_collection_saved')}[/green]")
                if config["enabled"] and not config["output_path"]:
                    console.print(f"[yellow]{self.i18n.get('automation_output_collection_no_path')}[/yellow]")
            elif choice == 2:
                new_path = Prompt.ask(
                    self.i18n.get("automation_output_collection_path_prompt"),
                    default=config["output_path"],
                ).strip().strip('"').strip("'")
                if new_path:
                    config["output_path"] = new_path
                    self._save_automation_output_collection_config(config)
                    console.print(f"[green]{self.i18n.get('automation_output_collection_saved')}[/green]")
            elif choice == 3:
                config["output_path"] = ""
                self._save_automation_output_collection_config(config)
                console.print(f"[green]{self.i18n.get('automation_output_collection_saved')}[/green]")

    def _get_automation_output_collection_config(self):
        from ModuleFolders.Infrastructure.Automation.OutputCollector import (
            AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY,
            normalize_output_collection_config,
        )

        return normalize_output_collection_config(
            self.config.get(AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY, {})
        )

    def _save_automation_output_collection_config(self, config: dict):
        from ModuleFolders.Infrastructure.Automation.OutputCollector import (
            AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY,
            normalize_output_collection_config,
        )

        self.config[AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY] = normalize_output_collection_config(config)
        self.host.save_config()

    def scheduler_submenu(self):
        """定时任务子菜单"""
        from ModuleFolders.Infrastructure.Automation.SchedulerManager import ScheduledTask

        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{self.i18n.get('scheduler_title')}[/bold]"))
            console.print(f"[dim]{self.i18n.get('scheduler_usage_hint')}[/dim]")
            console.print(f"[yellow]{self.i18n.get('automation_preview_required_tip')}[/yellow]\n")

            status = self.scheduler_manager.get_status()

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"{self.i18n.get('scheduler_enabled')}: [{'green' if status['running'] else 'red'}]{'ON' if status['running'] else 'OFF'}[/]")
            table.add_row("[cyan]2.[/]", self.i18n.get('scheduler_add_task'))
            table.add_row("[cyan]3.[/]", self.i18n.get('scheduler_edit_task'))
            table.add_row("[cyan]4.[/]", self.i18n.get('scheduler_remove_task'))
            table.add_row("[cyan]5.[/]", self.i18n.get('scheduler_view_logs'))
            console.print(table)

            # 显示任务列表
            tasks = self.scheduler_manager.get_all_tasks()
            if tasks:
                console.print(f"\n[bold]{self.i18n.get('scheduler_task_list')}:[/bold]")
                task_table = Table(box=None)
                task_table.add_column("ID", style="cyan")
                task_table.add_column(self.i18n.get('scheduler_task_name'))
                task_table.add_column(self.i18n.get('scheduler_trigger_type'))
                task_table.add_column(self.i18n.get('scheduler_schedule_expr'))
                task_table.add_column(self.i18n.get('scheduler_next_run'))
                task_table.add_column(self.i18n.get('label_status'))

                for task in tasks:
                    schedule_text = self._format_scheduler_schedule(task)
                    next_run = self._format_scheduler_next_run(task)
                    status_str = "[green]●[/]" if task.enabled else "[dim]○[/]"
                    task_table.add_row(task.id, task.name, self._format_scheduler_trigger_type(task.trigger_type), schedule_text, next_run, status_str)
                console.print(task_table)
            else:
                console.print(f"\n[dim]{self.i18n.get('scheduler_no_tasks')}[/dim]")

            console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")
            choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4", "5"], show_choices=False)

            if choice == 0:
                break
            elif choice == 1:
                if self.scheduler_manager.running:
                    self.scheduler_manager.stop()
                else:
                    self.scheduler_manager.start()
            elif choice == 2:
                self.add_scheduled_task()
            elif choice == 3:
                self.edit_scheduled_task()
            elif choice == 4:
                self.remove_scheduled_task()
            elif choice == 5:
                self.view_automation_logs(self.scheduler_manager.get_logs())

    def add_scheduled_task(self):
        """添加定时任务"""
        from ModuleFolders.Infrastructure.Automation.SchedulerManager import ScheduledTask

        console.print(Panel(f"[bold]{self.i18n.get('scheduler_add_task')}[/bold]"))

        task_id = Prompt.ask(self.i18n.get('prompt_task_id_new'))
        if self.scheduler_manager.get_task(task_id):
            console.print(f"[red]{self.i18n.get('msg_id_exists')}[/red]")
            return

        name = Prompt.ask(self.i18n.get('scheduler_task_name'))
        trigger_type = self._prompt_scheduler_trigger_type()
        if trigger_type == "queue_pending":
            schedule = ""
            self._print_scheduler_schedule_hint(trigger_type)
        else:
            schedule_default = "02:00" if trigger_type == "scheduled" else ""
            self._print_scheduler_schedule_hint(trigger_type)
            schedule = Prompt.ask(self.i18n.get('scheduler_schedule_expr'), default=schedule_default)

        # 验证时间表达式
        try:
            from ModuleFolders.Infrastructure.Automation.SchedulerManager import ScheduleParser
            ScheduleParser.parse(schedule, allow_empty=trigger_type in {"queue_added", "queue_pending"})
        except ValueError:
            console.print(f"[red]{self.i18n.get('msg_invalid_schedule')}[/red]")
            return

        if trigger_type in {"queue_added", "queue_pending"}:
            console.print(f"[yellow]{self.i18n.get('scheduler_preview_required_warning')}[/yellow]")

        input_default = "queue" if trigger_type in {"queue_added", "queue_pending"} else ""
        input_path = Prompt.ask(self.i18n.get('scheduler_input_path'), default=input_default)
        run_queue = trigger_type in {"queue_added", "queue_pending"} or input_path.strip().lower() in {"queue", "__queue__", "队列"}
        if not run_queue and not os.path.exists(input_path):
            console.print(f"[red]{self.i18n.get('msg_path_not_exist')}[/red]")
            return

        # 选择任务类型
        task_types = ["translation", "polishing", "all_in_one"]
        console.print(f"\n{self.i18n.get('scheduler_task_type')}:")
        for i, t in enumerate(task_types):
            console.print(f"  [cyan]{i+1}.[/] {self.i18n.get(f'task_type_{t}')}")
        type_choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["1", "2", "3"], default=1, show_choices=False)
        task_type = task_types[type_choice - 1]

        # 选择配置
        profiles = self.host._get_profiles_list(self.host.profiles_dir)
        console.print(f"\n{self.i18n.get('scheduler_profile')}:")
        for i, p in enumerate(profiles):
            console.print(f"  [cyan]{i+1}.[/] {p}")
        profile_choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=[str(i+1) for i in range(len(profiles))], default=1, show_choices=False)
        profile = profiles[profile_choice - 1]

        workflow_steps = []
        if run_queue:
            workflow_steps = [{"type": "run_queue"}]
        elif Confirm.ask(self.i18n.get("scheduler_configure_workflow"), default=False):
            workflow_steps, _ = self._prompt_workflow_steps(default_task_type=task_type)

        task = ScheduledTask(
            task_id=task_id,
            name=name,
            schedule=schedule,
            trigger_type=trigger_type,
            input_path=input_path,
            profile=profile,
            task_type=task_type,
            workflow_steps=workflow_steps,
            run_queue=run_queue,
        )

        if self.scheduler_manager.add_task(task):
            console.print(f"[green]{self.i18n.get('scheduler_task_added')}[/green]")
            self.scheduler_manager.save_to_config(self.config)
            self.host.save_config()

    def edit_scheduled_task(self):
        """编辑定时任务"""
        tasks = self.scheduler_manager.get_all_tasks()
        if not tasks:
            console.print(f"[dim]{self.i18n.get('scheduler_no_tasks')}[/dim]")
            return

        console.print(Panel(f"[bold]{self.i18n.get('scheduler_edit_task')}[/bold]"))
        for i, task in enumerate(tasks):
            console.print(f"  [cyan]{i+1}.[/] {task.id} - {task.name}")

        choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=[str(i+1) for i in range(len(tasks))] + ["0"], default=0, show_choices=False)
        if choice == 0:
            return

        task = tasks[choice - 1]

        # 编辑选项
        console.print(f"\n[bold]{task.name}[/bold]")
        console.print(f"1. {self.i18n.get('label_enabled')}: {'ON' if task.enabled else 'OFF'}")
        console.print(f"2. {self.i18n.get('scheduler_trigger_type')}: {self._format_scheduler_trigger_type(task.trigger_type)}")
        if task.trigger_type == "queue_pending":
            console.print(f"3. {self.i18n.get('scheduler_schedule_expr')}: {self.i18n.get('scheduler_schedule_immediate')}")
        else:
            console.print(f"3. {self.i18n.get('scheduler_schedule_expr')}: {task.schedule or '-'}")
        console.print(f"4. {self.i18n.get('scheduler_input_path')}: {task.input_path}")
        console.print(f"5. {self.i18n.get('watch_mode')}: {describe_workflow_steps(task.workflow_steps, self.i18n) if task.workflow_steps else '-'}")

        edit_choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4", "5"], default=0, show_choices=False)

        updated = False
        if edit_choice == 1:
            self.scheduler_manager.update_task(task.id, enabled=not task.enabled)
            updated = True
        elif edit_choice == 2:
            trigger_type = self._prompt_scheduler_trigger_type(default=task.trigger_type)
            schedule = "" if trigger_type == "queue_pending" else task.schedule or ("02:00" if trigger_type == "scheduled" else "")
            self._print_scheduler_schedule_hint(trigger_type)
            try:
                from ModuleFolders.Infrastructure.Automation.SchedulerManager import ScheduleParser
                ScheduleParser.parse(schedule, allow_empty=trigger_type in {"queue_added", "queue_pending"})
            except ValueError:
                console.print(f"[red]{self.i18n.get('msg_invalid_schedule')}[/red]")
                return
            if trigger_type in {"queue_added", "queue_pending"}:
                console.print(f"[yellow]{self.i18n.get('scheduler_preview_required_warning')}[/yellow]")
            self.scheduler_manager.update_task(
                task.id,
                trigger_type=trigger_type,
                event_type=trigger_type if trigger_type in {"queue_added", "queue_pending"} else "",
                schedule=schedule,
                run_queue=trigger_type in {"queue_added", "queue_pending"} or task.run_queue,
                input_path="queue" if trigger_type in {"queue_added", "queue_pending"} and not task.input_path else task.input_path,
            )
            updated = True
        elif edit_choice == 3:
            if task.trigger_type == "queue_pending":
                console.print(f"[yellow]{self.i18n.get('scheduler_schedule_not_used_queue_pending')}[/yellow]")
                return
            self._print_scheduler_schedule_hint(task.trigger_type)
            new_schedule = Prompt.ask(self.i18n.get('scheduler_schedule_expr'), default=task.schedule)
            try:
                from ModuleFolders.Infrastructure.Automation.SchedulerManager import ScheduleParser
                ScheduleParser.parse(new_schedule, allow_empty=task.trigger_type in {"queue_added", "queue_pending"})
                self.scheduler_manager.update_task(task.id, schedule=new_schedule)
                updated = True
            except ValueError:
                console.print(f"[red]{self.i18n.get('msg_invalid_schedule')}[/red]")
        elif edit_choice == 4:
            new_path = Prompt.ask(self.i18n.get('scheduler_input_path'), default=task.input_path)
            run_queue = task.trigger_type in {"queue_added", "queue_pending"} or new_path.strip().lower() in {"queue", "__queue__", "队列"}
            if run_queue or os.path.exists(new_path):
                workflow_steps = [{"type": "run_queue"}] if run_queue else task.workflow_steps
                self.scheduler_manager.update_task(task.id, input_path=new_path, run_queue=run_queue, workflow_steps=workflow_steps)
                updated = True
            else:
                console.print(f"[red]{self.i18n.get('msg_path_not_exist')}[/red]")
        elif edit_choice == 5:
            if task.trigger_type in {"queue_added", "queue_pending"}:
                console.print(f"[yellow]{self.i18n.get('scheduler_queue_trigger_workflow_not_used')}[/yellow]")
                return
            workflow_steps, _ = self._prompt_workflow_steps(
                default_task_type=task.task_type,
                current_steps=task.workflow_steps,
                current_auto_start=True,
            )
            self.scheduler_manager.update_task(task.id, workflow_steps=workflow_steps, run_queue=False)
            updated = True

        if updated:
            console.print(f"[green]{self.i18n.get('scheduler_task_updated')}[/green]")
            self.scheduler_manager.save_to_config(self.config)
            self.host.save_config()

    def _prompt_scheduler_trigger_type(self, default="scheduled") -> str:
        trigger_types = ["scheduled", "queue_added", "queue_pending"]
        default_index = trigger_types.index(default) + 1 if default in trigger_types else 1
        console.print(f"\n{self.i18n.get('scheduler_trigger_type')}:")
        for i, trigger_type in enumerate(trigger_types, 1):
            console.print(f"  [cyan]{i}.[/] {self._format_scheduler_trigger_type(trigger_type)}")
        choice = IntPrompt.ask(
            self.i18n.get('prompt_select'),
            choices=[str(i) for i in range(1, len(trigger_types) + 1)],
            default=default_index,
            show_choices=False,
        )
        return trigger_types[choice - 1]

    def _format_scheduler_trigger_type(self, trigger_type: str) -> str:
        return self.i18n.get(f"scheduler_trigger_{trigger_type}")

    def _format_scheduler_schedule(self, task) -> str:
        if getattr(task, "trigger_type", "") == "queue_pending":
            return self.i18n.get("scheduler_schedule_immediate")
        return getattr(task, "schedule", "") or "-"

    def _format_scheduler_next_run(self, task) -> str:
        if getattr(task, "trigger_type", "") == "queue_pending":
            return self.i18n.get("scheduler_schedule_immediate")
        next_run = getattr(task, "next_run", None)
        return next_run.strftime("%m-%d %H:%M") if next_run else "-"

    def _print_scheduler_schedule_hint(self, trigger_type: str):
        if trigger_type == "queue_pending":
            console.print(f"[yellow]{self.i18n.get('scheduler_schedule_hint_queue_pending')}[/yellow]")
            return
        hint_key = "scheduler_schedule_hint_event" if trigger_type == "queue_added" else "scheduler_schedule_hint_scheduled"
        console.print(f"[yellow]{self.i18n.get(hint_key)}[/yellow]")
        console.print(f"[dim]{self.i18n.get('scheduler_schedule_hint')}[/dim]")

    def remove_scheduled_task(self):
        """删除定时任务"""
        tasks = self.scheduler_manager.get_all_tasks()
        if not tasks:
            console.print(f"[dim]{self.i18n.get('scheduler_no_tasks')}[/dim]")
            return

        console.print(Panel(f"[bold]{self.i18n.get('scheduler_remove_task')}[/bold]"))
        for i, task in enumerate(tasks):
            console.print(f"  [cyan]{i+1}.[/] {task.id} - {task.name}")

        choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=[str(i+1) for i in range(len(tasks))] + ["0"], default=0, show_choices=False)
        if choice == 0:
            return

        task = tasks[choice - 1]
        if Confirm.ask(self.i18n.get('scheduler_confirm_remove')):
            self.scheduler_manager.remove_task(task.id)
            console.print(f"[green]{self.i18n.get('scheduler_task_removed')}[/green]")
            self.scheduler_manager.save_to_config(self.config)
            self.host.save_config()

    def watch_submenu(self):
        """文件夹监控子菜单"""
        from ModuleFolders.Infrastructure.Automation.WatchManager import WatchRule

        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{self.i18n.get('watch_title')}[/bold]"))
            console.print(f"[dim]{self.i18n.get('watch_usage_hint')}[/dim]\n")
            console.print(f"[yellow]{self.i18n.get('automation_preview_required_tip')}[/yellow]\n")
            console.print(f"[dim]{self.i18n.get('watch_auto_glossary_ignored_tip')}[/dim]")
            console.print(f"[dim]{self.i18n.get('watch_series_incremental_tip')}[/dim]\n")

            status = self.watch_manager.get_status()

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"{self.i18n.get('watch_enabled')}: [{'green' if status['running'] else 'red'}]{'ON' if status['running'] else 'OFF'}[/]")
            table.add_row("[cyan]2.[/]", self.i18n.get('watch_add_rule'))
            table.add_row("[cyan]3.[/]", self.i18n.get('watch_edit_rule'))
            table.add_row("[cyan]4.[/]", self.i18n.get('watch_remove_rule'))
            table.add_row("[cyan]5.[/]", self.i18n.get('watch_view_logs'))
            table.add_row("[cyan]6.[/]", self.i18n.get('watch_clear_history'))
            table.add_row("[cyan]7.[/]", self.i18n.get('watch_requeue_detected_file'))
            collection_config = self._get_automation_output_collection_config()
            collection_status = self.i18n.get("label_enabled") if collection_config["enabled"] else self.i18n.get("label_disabled")
            table.add_row("[cyan]8.[/]", self.i18n.get('menu_automation_output_collection'), collection_status)
            console.print(table)

            # 显示规则列表
            rules = self.watch_manager.get_all_rules()
            if rules:
                console.print(f"\n[bold]{self.i18n.get('watch_rule_list')}:[/bold]")
                rule_table = Table(box=None)
                rule_table.add_column("ID", style="cyan")
                rule_table.add_column(self.i18n.get('watch_path'))
                rule_table.add_column(self.i18n.get('watch_patterns'))
                rule_table.add_column(self.i18n.get('watch_detect_target_type'))
                rule_table.add_column(self.i18n.get('watch_mode'))
                rule_table.add_column(self.i18n.get('watch_series_incremental_column'))
                rule_table.add_column(self.i18n.get('label_status'))

                for rule in rules:
                    patterns = ", ".join(rule.file_patterns[:3])
                    if len(rule.file_patterns) > 3:
                        patterns += "..."
                    mode = self._format_watch_rule_workflow(rule)
                    status_str = "[green]●[/]" if rule.enabled else "[dim]○[/]"
                    series_status = self.i18n.get("label_enabled") if getattr(rule, "series_incremental", False) else self.i18n.get("label_disabled")
                    rule_table.add_row(
                        rule.id,
                        os.path.basename(rule.watch_path),
                        patterns,
                        self._format_watch_target_type(getattr(rule, "watch_target_type", "file")),
                        mode,
                        series_status,
                        status_str,
                    )
                console.print(rule_table)
                self._render_watch_file_status()
            else:
                console.print(f"\n[dim]{self.i18n.get('watch_no_rules')}[/dim]")

            console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")
            choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"], show_choices=False)

            if choice == 0:
                break
            elif choice == 1:
                if self.watch_manager.running:
                    self.watch_manager.stop()
                else:
                    self.watch_manager.start()
            elif choice == 2:
                self.add_watch_rule()
            elif choice == 3:
                self.edit_watch_rule()
            elif choice == 4:
                self.remove_watch_rule()
            elif choice == 5:
                self.view_automation_logs(self.watch_manager.get_logs())
            elif choice == 6:
                self.watch_manager.clear_processed_history()
                console.print(f"[green]{self.i18n.get('watch_history_cleared')}[/green]")
            elif choice == 7:
                self.requeue_detected_watch_file()
            elif choice == 8:
                self.automation_output_collection_settings()

    def _render_watch_file_status(self):
        snapshots = self.watch_manager.get_file_status_snapshot(limit_per_rule=15, include_unmatched=True)
        if not snapshots:
            return

        has_files = any(snapshot["files"] for snapshot in snapshots)
        console.print(f"\n[bold]{self.i18n.get('watch_file_status_title')}:[/bold]")
        if not has_files:
            console.print(f"[dim]{self.i18n.get('watch_file_status_empty')}[/dim]")
            return

        group = []
        for snapshot in snapshots:
            tree = self._build_watch_status_tree(snapshot)
            group.append(tree)
            if snapshot.get("omitted"):
                tree.add(f"[dim]{self.i18n.get('watch_file_status_omitted').format(snapshot['omitted'])}[/]")

        for renderable in group:
            console.print(renderable)

    def _format_watch_match(self, matched: bool) -> str:
        return f"[green]{self.i18n.get('watch_match_yes')}[/]" if matched else f"[dim]{self.i18n.get('watch_match_no')}[/]"

    def _format_watch_target_type(self, value: str) -> str:
        labels = {
            "file": "watch_detect_target_file",
            "folder": "watch_detect_target_folder",
            "both": "watch_detect_target_both",
        }
        return self.i18n.get(labels.get(value, "watch_detect_target_file"))

    def _prompt_watch_target_type(self, current: str = "file") -> str:
        options = ["file", "folder", "both"]
        current = current if current in options else "file"
        console.print(f"\n{self.i18n.get('watch_detect_target_type')}:")
        for index, value in enumerate(options, 1):
            console.print(f"  [cyan]{index}.[/] {self._format_watch_target_type(value)}")
        choice = IntPrompt.ask(
            self.i18n.get("prompt_select"),
            choices=[str(index) for index in range(1, len(options) + 1)],
            default=options.index(current) + 1,
            show_choices=False,
        )
        return options[choice - 1]

    def _build_watch_status_tree(self, snapshot: dict) -> Tree:
        root_label = f"[cyan]{snapshot.get('rule_id')}[/] [bold]{os.path.basename(snapshot.get('watch_path') or '') or snapshot.get('watch_path')}[/]"
        tree = Tree(root_label)
        node_cache = {"": tree}
        for item in snapshot.get("files", []):
            self._add_watch_status_tree_item(tree, item, node_cache)
        return tree

    def _add_watch_status_tree_item(self, tree: Tree, item: dict, node_cache: dict):
        parts = [part for part in str(item.get("file") or "").replace("\\", "/").split("/") if part]
        if not parts:
            parts = [os.path.basename(item.get("path", "")) or "-"]

        current = tree
        key_parts = []
        for folder in parts[:-1]:
            key_parts.append(folder)
            key = "/".join(key_parts)
            if key not in node_cache:
                node_cache[key] = current.add(f"[bold cyan]{folder}/[/]")
            current = node_cache[key]

        leaf_key = "/".join(parts)
        label = self._format_watch_status_tree_leaf(parts[-1], item)
        if item.get("path_type") == "folder":
            if leaf_key not in node_cache:
                node_cache[leaf_key] = current.add(label)
            else:
                node_cache[leaf_key].label = label
        else:
            current.add(label)

    def _format_watch_status_tree_leaf(self, name: str, item: dict) -> str:
        icon = "[bold cyan][DIR][/]" if item.get("path_type") == "folder" else "[dim][FILE][/]"
        fields = [
            icon,
            f"[bold]{name}[/]",
            self._format_watch_match(item.get("matched")),
            self._format_watch_file_status(item.get("status", "")),
            self.i18n.get("watch_entered_yes") if item.get("entered_workflow") else self.i18n.get("watch_entered_no"),
        ]
        series_note = self._format_watch_series_note(item)
        if series_note:
            fields.append(series_note)
        workflow = item.get("workflow") or (self.i18n.get("workflow_preset_queue_only") if item.get("queue_only") else "-")
        fields.append(f"[dim]{workflow}[/]")
        fields.append(f"[dim]{item.get('detected_at') or '-'}[/]")
        return "  ".join(str(field) for field in fields if field)

    def _format_watch_series_note(self, item: dict) -> str:
        if item.get("path_type") == "folder":
            reason = item.get("series_reason") or ""
            if item.get("series_detected"):
                note = self.i18n.get("watch_series_detected_short")
                volumes = item.get("series_volumes") or []
                if volumes:
                    note += f" {volumes[0]}-{volumes[-1]}"
                missing = item.get("series_missing_volumes") or []
                if missing:
                    note += f" {self.i18n.get('watch_series_missing_short').format(', '.join(str(v) for v in missing))}"
                return f"[green]{note}[/]"
            if reason:
                return f"[yellow]{self._format_watch_series_reason(reason)}[/]"
            return ""

        if item.get("series_key") and item.get("series_volume"):
            return f"[green]{item.get('series_key')} {self.i18n.get('watch_series_volume_short').format(item.get('series_volume'))}[/]"
        return ""

    def _format_watch_series_reason(self, reason: str) -> str:
        labels = {
            "no_matching_files": "watch_series_reason_no_matching_files",
            "unparsed_volume": "watch_series_reason_unparsed_volume",
            "mixed_series": "watch_series_reason_mixed_series",
        }
        return self.i18n.get(labels.get(reason, "watch_series_reason_ordinary_queue"))

    def _format_watch_rule_workflow(self, rule) -> str:
        if not getattr(rule, "workflow_steps", None) and not getattr(rule, "auto_start", True):
            return self.i18n.get("workflow_preset_queue_only")
        return describe_workflow_steps(getattr(rule, "workflow_steps", []) or [], self.i18n) or "-"

    def _format_watch_file_status(self, status: str) -> str:
        labels = {
            "ignored": ("dim", "watch_status_ignored"),
            "watch_stopped": ("dim", "watch_status_watch_stopped"),
            "rule_disabled": ("dim", "watch_status_rule_disabled"),
            "ready": ("cyan", "watch_status_ready"),
            "pending": ("yellow", "watch_status_pending"),
            "waiting_stable": ("yellow", "watch_status_waiting_stable"),
            "waiting_capacity": ("yellow", "watch_status_waiting_capacity"),
            "waiting_target": ("yellow", "watch_status_waiting_target"),
            "processing": ("yellow", "watch_status_processing"),
            "waiting": ("yellow", "watch_status_waiting"),
            "enqueued": ("green", "watch_status_queued"),
            "queued": ("green", "watch_status_queued"),
            "workflow": ("yellow", "watch_status_workflow"),
            "translating": ("yellow", "watch_status_translating"),
            "translated": ("cyan", "watch_status_translated"),
            "polishing": ("yellow", "watch_status_polishing"),
            "completed": ("green", "watch_status_completed"),
            "partial": ("yellow", "watch_status_partial"),
            "done": ("green", "watch_status_done"),
            "processed": ("green", "watch_status_processed"),
            "folder_expanded": ("green", "watch_status_folder_expanded"),
            "primed": ("dim", "watch_status_primed"),
            "error": ("red", "watch_status_error"),
            "stopped": ("red", "watch_status_stopped"),
        }
        style, label_key = labels.get(status, ("white", status or "-"))
        label = self.i18n.get(label_key) if label_key.startswith("watch_status_") else label_key
        return f"[{style}]{label}[/]"

    @staticmethod
    def _format_file_size(size: int) -> str:
        try:
            size = int(size or 0)
        except (TypeError, ValueError):
            size = 0
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024

    def add_watch_rule(self):
        """添加监控规则"""
        from ModuleFolders.Infrastructure.Automation.WatchManager import WatchRule

        console.print(Panel(f"[bold]{self.i18n.get('watch_add_rule')}[/bold]"))

        rule_id = Prompt.ask(self.i18n.get('prompt_rule_id'))
        if self.watch_manager.get_rule(rule_id):
            console.print(f"[red]{self.i18n.get('msg_id_exists')}[/red]")
            return

        watch_path = Prompt.ask(self.i18n.get('watch_path'))
        if not os.path.exists(watch_path):
            console.print(f"[red]{self.i18n.get('msg_path_not_exist')}[/red]")
            return

        patterns_str = Prompt.ask(f"{self.i18n.get('watch_patterns')} ({self.i18n.get('watch_patterns_hint')})", default="*.epub, *.txt, *.srt")
        file_patterns = [p.strip() for p in patterns_str.split(",")]

        # 选择任务类型
        task_types = ["translation", "polishing", "all_in_one"]
        console.print(f"\n{self.i18n.get('watch_task_type')}:")
        for i, t in enumerate(task_types):
            console.print(f"  [cyan]{i+1}.[/] {self.i18n.get(f'task_type_{t}')}")
        type_choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["1", "2", "3"], default=1, show_choices=False)
        task_type = task_types[type_choice - 1]

        # 选择监控模式
        workflow_steps, auto_start = self._prompt_workflow_steps(default_task_type=task_type)

        # 选择配置
        profiles = self.host._get_profiles_list(self.host.profiles_dir)
        console.print(f"\n{self.i18n.get('watch_profile')}:")
        for i, p in enumerate(profiles):
            console.print(f"  [cyan]{i+1}.[/] {p}")
        profile_choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=[str(i+1) for i in range(len(profiles))], default=1, show_choices=False)
        profile = profiles[profile_choice - 1]

        # 选择规则配置
        rules_profiles = self.host._get_profiles_list(self.host.rules_profiles_dir)
        rules_profile = ""
        if rules_profiles:
            console.print(f"\n{self.i18n.get('label_rules_profile') or 'Rules Profile'}:")
            for i, p in enumerate(rules_profiles):
                console.print(f"  [cyan]{i+1}.[/] {p}")
            rules_choice = IntPrompt.ask(
                self.i18n.get('prompt_select'),
                choices=[str(i+1) for i in range(len(rules_profiles))],
                default=1,
                show_choices=False,
            )
            rules_profile = rules_profiles[rules_choice - 1]

        output_path = Prompt.ask(self.i18n.get('watch_output_path'), default="")
        done_path = Prompt.ask(self.i18n.get('watch_done_path'), default="")

        # 其他选项
        debounce = IntPrompt.ask(f"{self.i18n.get('watch_debounce')} ({self.i18n.get('watch_debounce_hint')})", default=5)
        watch_target_type = self._prompt_watch_target_type("file")
        if watch_target_type in {"folder", "both"}:
            console.print(f"[yellow]{self.i18n.get('watch_folder_series_strict_tip')}[/yellow]")
        recursive = Confirm.ask(self.i18n.get('watch_recursive'), default=False)
        trigger_mode = "folder" if Confirm.ask(self.i18n.get("watch_trigger_whole_folder"), default=False) else "file"
        series_incremental = Confirm.ask(self.i18n.get("watch_series_incremental_enabled"), default=False)
        if series_incremental:
            console.print(f"[yellow]{self.i18n.get('watch_series_order_warning')}[/yellow]")

        rule = WatchRule(
            rule_id=rule_id,
            watch_path=watch_path,
            output_path=output_path,
            done_path=done_path,
            file_patterns=file_patterns,
            profile=profile,
            rules_profile=rules_profile,
            task_type=task_type,
            auto_start=auto_start,
            debounce_seconds=debounce,
            recursive=recursive,
            workflow_steps=workflow_steps,
            trigger_mode=trigger_mode,
            series_incremental=series_incremental,
            watch_target_type=watch_target_type,
        )

        if self.watch_manager.add_rule(rule):
            console.print(f"[green]{self.i18n.get('watch_rule_added')}[/green]")
            self.watch_manager.save_to_config(self.config)
            self.host.save_config()

    def edit_watch_rule(self):
        """编辑监控规则"""
        rules = self.watch_manager.get_all_rules()
        if not rules:
            console.print(f"[dim]{self.i18n.get('watch_no_rules')}[/dim]")
            return

        console.print(Panel(f"[bold]{self.i18n.get('watch_edit_rule')}[/bold]"))
        for i, rule in enumerate(rules):
            console.print(f"  [cyan]{i+1}.[/] {rule.id} - {rule.watch_path}")

        choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=[str(i+1) for i in range(len(rules))] + ["0"], default=0, show_choices=False)
        if choice == 0:
            return

        rule = rules[choice - 1]

        # 编辑选项
        console.print(f"\n[bold]{rule.id}[/bold]")
        console.print(f"1. {self.i18n.get('label_enabled')}: {'ON' if rule.enabled else 'OFF'}")
        console.print(f"2. {self.i18n.get('watch_mode')}: {self._format_watch_rule_workflow(rule)}")
        console.print(f"3. {self.i18n.get('watch_patterns')}: {', '.join(rule.file_patterns)}")
        console.print(f"4. {self.i18n.get('watch_debounce')}: {rule.debounce_seconds}s")
        console.print(f"5. {self.i18n.get('watch_output_path')}: {rule.output_path or '-'}")
        console.print(f"6. {self.i18n.get('watch_series_incremental_column')}: {'ON' if getattr(rule, 'series_incremental', False) else 'OFF'}")
        console.print(f"7. {self.i18n.get('watch_detect_target_type')}: {self._format_watch_target_type(getattr(rule, 'watch_target_type', 'file'))}")

        edit_choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4", "5", "6", "7"], default=0, show_choices=False)

        if edit_choice == 1:
            self.watch_manager.update_rule(rule.id, enabled=not rule.enabled)
        elif edit_choice == 2:
            workflow_steps, auto_start = self._prompt_workflow_steps(
                default_task_type=rule.task_type,
                current_steps=rule.workflow_steps,
                current_auto_start=rule.auto_start,
            )
            self.watch_manager.update_rule(rule.id, workflow_steps=workflow_steps, auto_start=auto_start)
        elif edit_choice == 3:
            patterns_str = Prompt.ask(self.i18n.get('watch_patterns'), default=", ".join(rule.file_patterns))
            file_patterns = [p.strip() for p in patterns_str.split(",")]
            self.watch_manager.update_rule(rule.id, file_patterns=file_patterns)
        elif edit_choice == 4:
            debounce = IntPrompt.ask(self.i18n.get('watch_debounce'), default=rule.debounce_seconds)
            self.watch_manager.update_rule(rule.id, debounce_seconds=debounce)
        elif edit_choice == 5:
            output_path = Prompt.ask(self.i18n.get('watch_output_path'), default=rule.output_path or "")
            self.watch_manager.update_rule(rule.id, output_path=output_path)
        elif edit_choice == 6:
            enabled = not getattr(rule, "series_incremental", False)
            self.watch_manager.update_rule(rule.id, series_incremental=enabled)
            if enabled:
                console.print(f"[yellow]{self.i18n.get('watch_series_order_warning')}[/yellow]")
        elif edit_choice == 7:
            watch_target_type = self._prompt_watch_target_type(getattr(rule, "watch_target_type", "file"))
            self.watch_manager.update_rule(rule.id, watch_target_type=watch_target_type)
            if watch_target_type in {"folder", "both"}:
                console.print(f"[yellow]{self.i18n.get('watch_folder_series_strict_tip')}[/yellow]")

        if edit_choice > 0:
            console.print(f"[green]{self.i18n.get('watch_rule_updated')}[/green]")
            self.watch_manager.save_to_config(self.config)
            self.host.save_config()

    def remove_watch_rule(self):
        """删除监控规则"""
        rules = self.watch_manager.get_all_rules()
        if not rules:
            console.print(f"[dim]{self.i18n.get('watch_no_rules')}[/dim]")
            return

        console.print(Panel(f"[bold]{self.i18n.get('watch_remove_rule')}[/bold]"))
        for i, rule in enumerate(rules):
            console.print(f"  [cyan]{i+1}.[/] {rule.id} - {rule.watch_path}")

        choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=[str(i+1) for i in range(len(rules))] + ["0"], default=0, show_choices=False)
        if choice == 0:
            return

        rule = rules[choice - 1]
        if Confirm.ask(self.i18n.get('watch_confirm_remove')):
            self.watch_manager.remove_rule(rule.id)
            console.print(f"[green]{self.i18n.get('watch_rule_removed')}[/green]")
            self.watch_manager.save_to_config(self.config)
            self.host.save_config()

    def requeue_detected_watch_file(self):
        from ModuleFolders.Infrastructure.TaskContract import (
            TASK_CONTRACT_INPUT_FIELDS,
            TaskSpec,
            select_task_contract_fields,
        )
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager, QueueTaskItem

        candidates = []
        snapshots = self.watch_manager.get_file_status_snapshot(limit_per_rule=50, include_unmatched=False)
        for snapshot in snapshots:
            rule = self.watch_manager.get_rule(snapshot.get("rule_id"))
            if not rule:
                continue
            for item in snapshot.get("files", []):
                if not item.get("matched") or not os.path.exists(item.get("path", "")):
                    continue
                if item.get("path_type") == "folder":
                    continue
                candidates.append((rule, item))

        if not candidates:
            console.print(f"[dim]{self.i18n.get('watch_requeue_no_files')}[/dim]")
            return

        console.print(Panel(f"[bold]{self.i18n.get('watch_requeue_detected_file')}[/bold]"))
        table = Table(box=None)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column(self.i18n.get("watch_file_status_rule"), no_wrap=True)
        table.add_column(self.i18n.get("watch_file_status_file"))
        table.add_column(self.i18n.get("watch_file_status_status"), no_wrap=True)
        table.add_column(self.i18n.get("watch_file_status_workflow"))
        for index, (rule, item) in enumerate(candidates, 1):
            table.add_row(
                str(index),
                rule.id,
                item.get("file", ""),
                self._format_watch_file_status(item.get("status", "")),
                item.get("workflow") or self._format_watch_rule_workflow(rule),
            )
        console.print(table)

        choice = IntPrompt.ask(
            self.i18n.get("watch_requeue_select_file"),
            choices=[str(i) for i in range(1, len(candidates) + 1)] + ["0"],
            default=0,
            show_choices=False,
        )
        if choice == 0:
            return

        rule, item = candidates[choice - 1]
        workflow_steps, auto_start = self._select_requeue_workflow(rule)
        target_path = item["path"]
        output_path = rule.output_path or self.watch_manager._generate_output_path(target_path)
        task_payload = select_task_contract_fields(rule.extra)
        task_payload.update(
            {
                "task_type": rule.task_type,
                "input_path": target_path,
                "output_path": output_path,
                "profile": rule.profile or None,
                "rules_profile": rule.rules_profile or None,
            }
        )
        task_item = QueueTaskItem(
            **TaskSpec.from_mapping(task_payload).to_queue_fields(),
            resume_explicit="resume" in task_payload,
            workflow_steps=workflow_steps,
            source="watch",
            rule_id=rule.id,
            trigger_file_path=item["path"],
            trigger_file_name=os.path.basename(item["path"]),
            trigger_detected_at=datetime.now().isoformat(timespec="seconds"),
            series_incremental=getattr(rule, "series_incremental", False),
            series_key=item.get("series_key") or None,
            series_volume=item.get("series_volume") or None,
            extra={
                key: value
                for key, value in rule.extra.items()
                if key not in TASK_CONTRACT_INPUT_FIELDS
            },
        )

        queue_manager = QueueManager()
        queue_manager.add_task(task_item)
        self.watch_manager.requeue_file(item["path"], rule.id, describe_workflow_steps(workflow_steps, self.i18n))
        console.print(f"[green]{self.i18n.get('watch_requeue_added').format(os.path.basename(item['path']))}[/green]")
        if auto_start:
            console.print(f"[yellow]{self.i18n.get('watch_requeue_auto_start_tip')}[/yellow]")
            self._run_queue_if_needed()

    def _select_requeue_workflow(self, rule):
        console.print(f"\n[cyan]{self.i18n.get('watch_requeue_workflow_source')}[/cyan]")
        table = Table(show_header=False, box=None)
        table.add_row("[cyan]1.[/]", self.i18n.get("watch_requeue_use_rule_workflow"))
        table.add_row("[cyan]2.[/]", self.i18n.get("watch_requeue_choose_workflow"))
        console.print(table)
        choice = IntPrompt.ask(self.i18n.get("prompt_select"), choices=["1", "2"], default=1, show_choices=False)
        if choice == 1:
            steps = list(getattr(rule, "workflow_steps", []) or [])
            if steps:
                return steps, bool(getattr(rule, "auto_start", True))
            console.print(f"[yellow]{self.i18n.get('watch_requeue_rule_queue_only_warning')}[/yellow]")
        return self._prompt_workflow_steps(
            default_task_type=getattr(rule, "task_type", "translation"),
            current_steps=getattr(rule, "workflow_steps", []) or [],
            current_auto_start=getattr(rule, "auto_start", True),
        )

    def automation_status_view(self):
        """自动化状态总览"""
        console.print(f"[dim]{self.i18n.get('automation_status_live_hint')}[/dim]")
        console.print(f"[dim]{self.i18n.get('automation_status_press_c_hint')}[/dim]")
        console.print(f"[dim]{self.i18n.get('automation_status_press_r_hint')}[/dim]")
        scheduler_was_running = self.scheduler_manager.running
        watch_was_running = self.watch_manager.running
        self._automation_status_view_active = True
        self.scheduler_manager.set_event_triggers_active(True)
        if not scheduler_was_running:
            self.scheduler_manager.start()
        if not watch_was_running:
            self.watch_manager.start()
        input_listener, listener_started = self._start_status_input_listener()
        interrupted = None
        try:
            with Live(
                self._build_automation_status_renderable(),
                console=console,
                refresh_per_second=4,
                transient=False,
            ) as live:
                while True:
                    key = self._read_status_view_key(input_listener)
                    if key in {"q", "0"}:
                        interrupted = self._interrupt_automation_workers()
                        break
                    if key == "c":
                        self._continue_partial_automation_task()
                    if key == "r":
                        self._resume_stopped_automation_task()
                    live.update(self._build_automation_status_renderable())
                    time.sleep(0.25)
        except KeyboardInterrupt:
            interrupted = self._interrupt_automation_workers()
        finally:
            self._automation_status_view_active = False
            self.scheduler_manager.set_event_triggers_active(False)
            if not scheduler_was_running:
                self.scheduler_manager.stop()
            if not watch_was_running:
                self.watch_manager.stop()
            self._stop_status_input_listener(input_listener, listener_started)

        if interrupted is not None:
            console.print(
                f"\n[dim]{self.i18n.get('automation_status_live_stopped')}[/dim] "
                f"[yellow]{self.i18n.get('automation_workers_interrupted').format(interrupted)}[/yellow]"
            )

    def _start_status_input_listener(self):
        input_listener = getattr(self.host, "input_listener", None)
        if input_listener is None or getattr(input_listener, "disabled", False):
            return None, False
        already_running = bool(getattr(input_listener, "running", False))
        input_listener.start()
        input_listener.clear()
        return input_listener, not already_running

    @staticmethod
    def _read_status_view_key(input_listener):
        if input_listener is None:
            return None
        key = input_listener.get_key()
        return str(key).lower() if key is not None else None

    @staticmethod
    def _stop_status_input_listener(input_listener, listener_started: bool):
        if input_listener is None:
            return
        input_listener.clear()
        if listener_started:
            input_listener.stop()

    def _interrupt_automation_workers(self) -> int:
        from ModuleFolders.Infrastructure.Automation.AutomationProcessRunner import AutomationProcessRunner
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()
        queue_manager.request_automation_stop()
        process_lookup = AutomationProcessRunner.snapshot_processes()
        count = AutomationProcessRunner.terminate_all(self.i18n.get("automation_interrupted_by_user"))
        if process_lookup:
            for run_id in process_lookup:
                queue_manager.mark_automation_interrupted(run_id, "stopped")
        return count

    def _continue_partial_automation_task(self):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()
        if not queue_manager.is_running:
            queue_manager.hot_reload_queue(quiet=True)

        partial_tasks = [
            task for task in queue_manager.tasks
            if getattr(task, "status", "") == "partial"
        ]
        if not partial_tasks:
            self.watch_manager._log("info", self.i18n.get("automation_resume_partial_no_task"))
            return

        task = partial_tasks[0]
        run_id = getattr(task, "automation_run_id", "") or ""
        if queue_manager.continue_partial_task(run_id=run_id, input_path=getattr(task, "input_path", "")):
            self.watch_manager._log(
                "info",
                self.i18n.get("automation_resume_partial_started").format(os.path.basename(getattr(task, "input_path", "") or "-")),
            )
            if not queue_manager.is_running:
                self._run_queue_if_needed()
        else:
            self.watch_manager._log("warning", self.i18n.get("automation_resume_partial_no_task"))

    def _resume_stopped_automation_task(self):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()
        if not queue_manager.is_running:
            queue_manager.hot_reload_queue(quiet=True)

        stopped_tasks = [
            task for task in queue_manager.tasks
            if getattr(task, "status", "") in {"stopped", "interrupted"}
        ]
        if not stopped_tasks:
            self.watch_manager._log("info", self.i18n.get("automation_resume_stopped_no_task"))
            return

        task = stopped_tasks[0]
        run_id = getattr(task, "automation_run_id", "") or ""
        if queue_manager.resume_stopped_task(run_id=run_id, input_path=getattr(task, "input_path", "")):
            self.watch_manager._log(
                "info",
                self.i18n.get("automation_resume_stopped_started").format(os.path.basename(getattr(task, "input_path", "") or "-")),
            )
            if not queue_manager.is_running:
                self._run_queue_if_needed()

    def _build_automation_status_renderable(self):
        from ModuleFolders.Infrastructure.Automation.AutomationProcessRunner import AutomationProcessRunner
        from ModuleFolders.Infrastructure.Automation.AutomationProgress import AutomationProgressStore, TERMINAL_STATUSES

        sched_status = self.scheduler_manager.get_status()
        watch_status = self.watch_manager.get_status()
        process_lookup = AutomationProcessRunner.snapshot_processes()
        progress_states = AutomationProgressStore(getattr(self.host, "PROJECT_ROOT", None)).list_states(
            limit=8,
            include_terminal=False,
            cleanup_terminal=True,
        )

        summary_table = Table(show_header=False, box=None)
        summary_table.add_row(
            self.i18n.get('automation_trigger_status'),
            f"[{'green' if sched_status['running'] else 'red'}]{self.i18n.get('automation_running') if sched_status['running'] else self.i18n.get('automation_stopped')}[/] "
            f"{sched_status['enabled_count']}/{sched_status['task_count']}",
        )
        summary_table.add_row(
            self.i18n.get('automation_watch_status'),
            f"[{'green' if watch_status['running'] else 'red'}]{self.i18n.get('automation_running') if watch_status['running'] else self.i18n.get('automation_stopped')}[/] "
            f"{watch_status['enabled_count']}/{watch_status['rule_count']}",
        )
        summary_table.add_row(self.i18n.get('watch_pending_files'), str(watch_status['pending_files']))
        summary_table.add_row(self.i18n.get('automation_total_processed'), str(watch_status['total_processed']))
        if sched_status.get('next_task'):
            next_task = sched_status['next_task']
            if next_task.get("next_run"):
                summary_table.add_row(self.i18n.get('automation_next_task'), f"{next_task['name']} @ {next_task['next_run']}")
            else:
                summary_table.add_row(self.i18n.get('automation_next_task'), f"{next_task['name']} @ {self.i18n.get('scheduler_schedule_immediate')}")

        progress_group = []
        for state in progress_states:
            run_id = state.get("run_id") or state.get("task_id") or "-"
            status = state.get("status", "-")
            process = process_lookup.get(run_id)
            if process and process.poll() is not None and status not in TERMINAL_STATUSES:
                status = "interrupted"
            status_label = self._format_automation_progress_status(status)
            percent = int(state.get("percent") or 0)
            progress = Progress(
                TextColumn("[bold]{task.description}"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                expand=True,
            )
            task_id = progress.add_task(
                f"{state.get('file_name') or os.path.basename(str(state.get('input_path') or '')) or run_id} [{status_label}]",
                total=100,
                completed=max(0, min(100, percent)),
            )
            progress.update(task_id, completed=max(0, min(100, percent)))
            detail_table = Table(show_header=False, box=None)
            detail_table.add_row(self.i18n.get("automation_progress_pid"), str(state.get("pid") or "-"))
            detail_table.add_row(self.i18n.get("automation_progress_rule"), str(state.get("rule_id") or "-"))
            detail_table.add_row(self.i18n.get("automation_progress_step"), f"{state.get('step_index', 0)}/{state.get('step_total', 0)} {state.get('step_name') or state.get('phase') or '-'}")
            detail_table.add_row(self.i18n.get("automation_progress_lines"), self._format_progress_lines(state))
            detail_table.add_row(self.i18n.get("automation_progress_eta"), self._format_progress_timing(state))
            if state.get("rules_profile"):
                detail_table.add_row(self.i18n.get("automation_progress_rules_profile"), str(state.get("rules_profile")))
            detail_table.add_row(self.i18n.get("automation_progress_message"), str(state.get("message") or "-")[:120])
            progress_group.append(Panel(Group(progress, detail_table), title=run_id, border_style=self._automation_progress_border(status)))

        if not progress_group:
            progress_group.append(self._build_automation_waiting_panel())

        watch_file_panel = self._build_watch_file_status_panel()

        logs = self.watch_manager.get_logs(8)
        log_table = Table(box=None)
        log_table.add_column(self.i18n.get("automation_log_time"), style="dim")
        log_table.add_column(self.i18n.get("automation_log_level"))
        log_table.add_column(self.i18n.get("automation_log_message"))
        for log in logs:
            level_style = {"info": "green", "warning": "yellow", "error": "red"}.get(log["level"], "white")
            log_table.add_row(log["time"], f"[{level_style}]{log['level'].upper()}[/]", log["message"])

        return Group(
            Panel(summary_table, title=self.i18n.get('automation_status_title'), border_style="blue"),
            *progress_group,
            watch_file_panel,
            Panel(log_table, title=self.i18n.get("automation_recent_events"), border_style="magenta"),
            f"[dim]{self.i18n.get('automation_status_live_hint')}[/dim]",
            f"[dim]{self.i18n.get('automation_status_press_c_hint')}[/dim]",
            f"[dim]{self.i18n.get('automation_status_press_r_hint')}[/dim]",
            f"[dim]{self.i18n.get('watch_auto_glossary_ignored_tip')}[/dim]",
        )

    def _format_automation_progress_status(self, status: str) -> str:
        labels = {
            "queued": "task_status_waiting",
            "running": "task_status_running",
            "workflow": "task_status_workflow",
            "enqueued": "task_status_enqueued",
            "completed": "task_status_completed",
            "partial": "task_status_partial",
            "error": "task_status_error",
            "stopped": "task_status_stopped",
            "interrupted": "task_status_stopped",
        }
        return self.i18n.get(labels.get(status, "")) if status in labels else (status or "-")

    @staticmethod
    def _automation_progress_border(status: str) -> str:
        if status == "completed":
            return "green"
        if status in {"partial", "enqueued"}:
            return "yellow"
        if status in {"error", "interrupted", "stopped"}:
            return "red"
        return "cyan"

    def _format_progress_lines(self, state: dict) -> str:
        line = self._safe_int(state.get("line") or state.get("completed"))
        total = self._safe_int(state.get("total_line") or state.get("total"))
        if total <= 0:
            return "-"
        remaining = max(0, total - line)
        return self.i18n.get("automation_progress_lines_value").format(line, total, remaining)

    def _format_progress_timing(self, state: dict) -> str:
        elapsed = self._safe_float(state.get("time"))
        if elapsed <= 0:
            elapsed = self._elapsed_from_state(state)
        eta = self._safe_float(state.get("eta"))
        line = self._safe_int(state.get("line") or state.get("completed"))
        total = self._safe_int(state.get("total_line") or state.get("total"))
        if eta <= 0 and elapsed > 0 and line > 0 and total > line:
            eta = (total - line) * elapsed / line
        return self.i18n.get("automation_progress_eta_value").format(
            self._format_duration(elapsed),
            self._format_duration(eta) if eta > 0 else "-",
        )

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_float(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _elapsed_from_state(state: dict) -> float:
        started_at = state.get("started_at")
        if not started_at:
            return 0
        try:
            started = datetime.fromisoformat(str(started_at))
            return max(0, (datetime.now() - started).total_seconds())
        except ValueError:
            return 0

    @staticmethod
    def _format_duration(seconds) -> str:
        try:
            seconds = int(max(0, float(seconds or 0)))
        except (TypeError, ValueError):
            seconds = 0
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:d}:{sec:02d}"

    def _build_automation_waiting_panel(self):
        queue_state = self._get_automation_queue_state()
        if not queue_state:
            return Panel(f"[dim]{self.i18n.get('automation_no_progress')}[/dim]", border_style="dim")

        table = Table(show_header=False, box=None)
        if queue_state["running"]:
            table.add_row("[yellow]*[/]", self.i18n.get("automation_queue_waiting_running"))
        if queue_state["pending_count"]:
            table.add_row(
                "[yellow]*[/]",
                self.i18n.get("automation_queue_waiting_pending").format(
                    queue_state["pending_count"],
                    queue_state["next_file"] or "-",
                ),
            )
        if queue_state["has_glossary"]:
            table.add_row("[cyan]*[/]", self.i18n.get("automation_queue_waiting_glossary"))
        if queue_state["has_plain_queue"]:
            table.add_row("[cyan]*[/]", self.i18n.get("automation_queue_waiting_plain"))
        if queue_state["has_legacy_queue_only"]:
            table.add_row("[yellow]*[/]", self.i18n.get("automation_queue_waiting_queue_only"))

        return Panel(table, title=self.i18n.get("automation_queue_waiting_title"), border_style="yellow")

    @staticmethod
    def _queue_task_filename(task) -> str:
        for attr in ("trigger_file_name", "trigger_file_path", "input_path"):
            value = getattr(task, attr, "") or ""
            if value:
                return os.path.basename(os.path.normpath(str(value)))
        return ""

    @staticmethod
    def _queue_task_has_step(task, step_type: str) -> bool:
        for step in getattr(task, "workflow_steps", []) or []:
            if isinstance(step, dict) and str(step.get("type") or "").strip().lower() == step_type:
                return True
        return False

    @staticmethod
    def _queue_task_is_queue_only(task) -> bool:
        steps = getattr(task, "workflow_steps", []) or []
        if not steps:
            return False
        step_types = [
            str(step.get("type") or "").strip().lower()
            for step in steps
            if isinstance(step, dict)
        ]
        return bool(step_types) and all(step_type in {"queue", "run_queue"} for step_type in step_types)

    def _get_automation_queue_state(self):
        try:
            from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

            queue_manager = QueueManager()
            if not queue_manager.is_running:
                queue_manager.hot_reload_queue(quiet=True)
            tasks = list(queue_manager.tasks)
        except Exception:
            return None

        active_statuses = {"waiting", "workflow", "translating", "translated", "polishing"}
        pending_tasks = [
            task for task in tasks
            if getattr(task, "status", "waiting") in active_statuses
        ]
        legacy_queue_only = [
            task for task in tasks
            if getattr(task, "status", "") == "enqueued" and self._queue_task_is_queue_only(task)
        ]

        if not pending_tasks and not getattr(queue_manager, "is_running", False) and not legacy_queue_only:
            return None

        next_task = pending_tasks[0] if pending_tasks else None
        return {
            "running": bool(getattr(queue_manager, "is_running", False)),
            "pending_count": len(pending_tasks),
            "next_file": self._queue_task_filename(next_task) if next_task else "",
            "has_glossary": any(self._queue_task_has_step(task, "extract_glossary") for task in pending_tasks),
            "has_plain_queue": any(not getattr(task, "workflow_steps", None) for task in pending_tasks),
            "has_legacy_queue_only": bool(legacy_queue_only),
        }

    def _build_watch_file_status_panel(self):
        snapshots = self.watch_manager.get_file_status_snapshot(limit_per_rule=8, include_unmatched=True)
        trees = []
        for snapshot in snapshots:
            if snapshot.get("files"):
                tree = self._build_watch_status_tree(snapshot)
                trees.append(tree)
            if snapshot.get("omitted"):
                if not snapshot.get("files"):
                    tree = self._build_watch_status_tree(snapshot)
                    trees.append(tree)
                trees[-1].add(f"[dim]{self.i18n.get('watch_file_status_omitted').format(snapshot['omitted'])}[/]")

        if not trees:
            return Panel(
                f"[dim]{self.i18n.get('watch_file_status_empty')}[/dim]",
                title=self.i18n.get("watch_file_status_title"),
                border_style="cyan",
            )
        return Panel(Group(*trees), title=self.i18n.get("watch_file_status_title"), border_style="cyan")

    def view_automation_logs(self, logs: list):
        """查看自动化日志"""
        self.host.display_banner()

        if not logs:
            console.print(f"[dim]{self.i18n.get('automation_no_logs')}[/dim]")
        else:
            log_table = Table(box=None)
            log_table.add_column(self.i18n.get("automation_log_time"), style="dim")
            log_table.add_column(self.i18n.get("automation_log_level"))
            log_table.add_column(self.i18n.get("automation_log_message"))

            for log in logs[-20:]:
                level_style = {"info": "green", "warning": "yellow", "error": "red"}.get(log['level'], "white")
                log_table.add_row(log['time'], f"[{level_style}]{log['level'].upper()}[/]", log['message'])

            console.print(log_table)

        Prompt.ask(f"\n{self.i18n.get('msg_press_enter')}")

    def _prompt_workflow_steps(self, default_task_type="translation", current_steps=None, current_auto_start=True):
        """Prompt a queue-managed workflow preset."""
        console.print(f"\n{self.i18n.get('watch_mode')}:")
        table = Table(show_header=False, box=None)
        table.add_row("[cyan]1.[/]", self.i18n.get("workflow_preset_glossary_translate"))
        table.add_row("[cyan]2.[/]", self.i18n.get("workflow_preset_glossary_all_in_one"))
        table.add_row("[cyan]3.[/]", self.i18n.get("workflow_preset_translate"))
        table.add_row("[cyan]4.[/]", self.i18n.get("workflow_preset_queue_only"))
        table.add_row("[cyan]5.[/]", self.i18n.get("workflow_preset_custom"))
        console.print(table)

        choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["1", "2", "3", "4", "5"], default=1, show_choices=False)
        auto_start = choice != 4
        task_step = task_type_to_step_type(default_task_type)

        if choice == 1:
            steps = [
                self._prompt_glossary_step_defaults(),
                {"type": task_step if task_step != "polish" else "translate"},
            ]
        elif choice == 2:
            steps = [
                self._prompt_glossary_step_defaults(),
                {"type": "all_in_one"},
            ]
        elif choice == 3:
            steps = [{"type": task_step}]
        elif choice == 4:
            return [], False
        else:
            steps, auto_start = self._prompt_custom_workflow_steps(current_steps, current_auto_start)

        if any(step.get("type") == "extract_glossary" for step in steps):
            if Confirm.ask(self.i18n.get("workflow_glossary_series_incremental"), default=False):
                console.print(f"[yellow]{self.i18n.get('watch_series_order_warning')}[/yellow]")
                for step in steps:
                    if step.get("type") == "extract_glossary":
                        step["series_incremental"] = True

        return normalize_workflow_steps(steps, default_task_type, auto_start), auto_start

    def _prompt_glossary_step_defaults(self):
        return {
            "type": "extract_glossary",
            "analysis_mode": "full",
            "analysis_percent": IntPrompt.ask(self.i18n.get("workflow_glossary_analysis_percent"), default=100),
            "min_frequency": IntPrompt.ask(self.i18n.get("workflow_glossary_min_frequency"), default=2),
            "translate_during_analysis": True,
            "new": True,
            "replace": True,
            "save_mode": "isolated",
        }

    def _prompt_custom_workflow_steps(self, current_steps=None, current_auto_start=True):
        steps = list(current_steps or [])
        auto_start = current_auto_start
        while True:
            console.print(f"\n[bold]{self.i18n.get('workflow_title')}[/bold]")
            if steps:
                for idx, step in enumerate(steps, 1):
                    console.print(f"  [cyan]{idx}.[/] {self._format_workflow_step(step)}")
            else:
                console.print(f"  [dim]{self.i18n.get('workflow_no_steps')}[/dim]")
            console.print(f"  [cyan]A.[/] {self.i18n.get('workflow_add_step')}")
            console.print(f"  [cyan]R.[/] {self.i18n.get('workflow_remove_last')}")
            console.print(f"  [cyan]D.[/] {self.i18n.get('workflow_remove_step')}")
            console.print(f"  [cyan]U.[/] {self.i18n.get('workflow_move_step_up')}")
            console.print(f"  [cyan]N.[/] {self.i18n.get('workflow_move_step_down')}")
            console.print(f"  [cyan]S.[/] {self.i18n.get('workflow_auto_start')}: {'ON' if auto_start else 'OFF'}")
            console.print(f"  [dim]0. {self.i18n.get('workflow_done')}[/dim]")
            choice = Prompt.ask(self.i18n.get('prompt_select'), choices=["0", "A", "a", "R", "r", "D", "d", "U", "u", "N", "n", "S", "s"], default="0", show_choices=False)
            if choice == "0":
                break
            if choice.upper() == "S":
                auto_start = not auto_start
            elif choice.upper() == "R":
                if steps:
                    steps.pop()
            elif choice.upper() == "D":
                self._remove_workflow_step(steps)
            elif choice.upper() == "U":
                self._move_workflow_step(steps, -1)
            elif choice.upper() == "N":
                self._move_workflow_step(steps, 1)
            elif choice.upper() == "A":
                steps.append(self._prompt_workflow_step())
        return steps, auto_start

    def _format_workflow_step(self, step: dict) -> str:
        step_type = str(step.get("type") or "?")
        label = self.i18n.get(f"workflow_step_{step_type}") if step_type in {"extract_glossary", "translate", "polish", "all_in_one", "queue", "run_queue"} else step_type
        details = []
        if step_type == "extract_glossary":
            details.append(f"{self.i18n.get('workflow_glossary_analysis_percent')}: {step.get('analysis_percent', 100)}")
            details.append(f"{self.i18n.get('workflow_glossary_min_frequency')}: {step.get('min_frequency', 2)}")
            if step.get("series_incremental"):
                details.append(self.i18n.get("watch_series_incremental_column"))
        if step_type == "polish":
            details.append(f"resume={bool(step.get('resume', True))}")
        if step.get("output_path") or step.get("output_root"):
            details.append(str(step.get("output_path") or step.get("output_root")))
        if not details:
            return label
        return f"{label} [dim]({', '.join(details)})[/dim]"

    def _prompt_workflow_step_index(self, steps: list) -> int:
        if not steps:
            console.print(f"[dim]{self.i18n.get('workflow_no_steps')}[/dim]")
            return -1
        index = IntPrompt.ask(
            self.i18n.get("workflow_step_index"),
            choices=[str(i) for i in range(1, len(steps) + 1)] + ["0"],
            default=0,
            show_choices=False,
        )
        return index - 1 if index > 0 else -1

    def _remove_workflow_step(self, steps: list):
        index = self._prompt_workflow_step_index(steps)
        if index >= 0:
            steps.pop(index)

    def _move_workflow_step(self, steps: list, direction: int):
        index = self._prompt_workflow_step_index(steps)
        target = index + direction
        if index < 0 or target < 0 or target >= len(steps):
            return
        steps[index], steps[target] = steps[target], steps[index]

    def _prompt_workflow_step(self):
        table = Table(show_header=False, box=None)
        table.add_row("[cyan]1.[/]", self.i18n.get("workflow_step_extract_glossary"))
        table.add_row("[cyan]2.[/]", self.i18n.get("workflow_step_translate"))
        table.add_row("[cyan]3.[/]", self.i18n.get("workflow_step_polish"))
        table.add_row("[cyan]4.[/]", self.i18n.get("workflow_step_all_in_one"))
        console.print(table)
        choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["1", "2", "3", "4"], default=1, show_choices=False)
        if choice == 1:
            return self._prompt_glossary_step_defaults()
        if choice == 2:
            return {"type": "translate"}
        if choice == 3:
            return {"type": "polish", "resume": True}
        return {"type": "all_in_one"}
