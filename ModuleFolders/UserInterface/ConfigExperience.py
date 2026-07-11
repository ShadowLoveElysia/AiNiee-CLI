import os

from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table

from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.UserInterface.RulePreview import (
    RuleInspector,
    build_rule_issue_panel,
    data_count,
    i18n_text,
)


console = Console()


def _selection_id(selection):
    if isinstance(selection, dict):
        return str(selection.get("last_selected_id") or "").strip()
    return str(selection or "").strip()


def _is_unselected(value):
    return str(value or "").strip().lower() in {"", "common", "command", "none", "null"}


def _selection_status(i18n, selection):
    selected_id = _selection_id(selection)
    if _is_unselected(selected_id):
        return f"[red]{i18n_text(i18n, 'label_not_selected', 'Not selected')}[/red]"
    return f"[green]{selected_id}[/green]"


def _switch_status(i18n, enabled):
    label = i18n_text(i18n, "banner_on", "ON") if enabled else i18n_text(i18n, "banner_off", "OFF")
    color = "green" if enabled else "red"
    return f"[{color}]{label}[/{color}]"


def _task_label(i18n, task_mode):
    if task_mode == TaskType.TRANSLATION:
        return i18n_text(i18n, "task_type_translation", "Translation")
    if task_mode == TaskType.POLISH:
        return i18n_text(i18n, "task_type_polishing", "Polishing")
    if task_mode == TaskType.TRANSLATE_AND_POLISH:
        return i18n_text(i18n, "task_type_all_in_one", "All in One")
    return str(task_mode)


def _output_path_for(config, target_path):
    if not target_path:
        return config.get("label_output_path", "")
    output_path = config.get("label_output_path", "")
    if config.get("auto_set_output_path", False) or not output_path:
        abs_input = os.path.abspath(target_path)
        parent_dir = os.path.dirname(abs_input)
        base_name = os.path.basename(abs_input)
        if os.path.isfile(target_path):
            base_name = os.path.splitext(base_name)[0]
        return os.path.join(parent_dir, f"{base_name}_AiNiee_Output")
    return output_path


def _active_platform_config(config):
    target_platform = config.get("target_platform", "")
    platforms = config.get("platforms", {})
    platform_config = platforms.get(target_platform, {}) if isinstance(platforms, dict) else {}
    return target_platform, platform_config


class ConfigExperience:
    def __init__(self, host):
        self.host = host

    @property
    def i18n(self):
        return self.host.i18n

    @property
    def config(self):
        return self.host.config

    def show_effective_config(self):
        self.host.display_banner()
        console.print(Panel(f"[bold]{i18n_text(self.i18n, 'menu_effective_config_preview', 'Effective Config Preview')}[/bold]"))
        console.print(self._build_effective_config_table())
        self._print_rule_summary()
        Prompt.ask(f"\n{i18n_text(self.i18n, 'msg_press_enter', 'Press Enter to continue')}")

    def confirm_before_task(self, task_mode, target_path, output_path, continue_status=False):
        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{i18n_text(self.i18n, 'title_preflight_check', 'Preflight Check')}[/bold]"))
            console.print(self._build_task_table(task_mode, target_path, output_path, continue_status))
            self._print_rule_summary(compact=True)

            console.print(f"\n[green]1.[/] {i18n_text(self.i18n, 'option_continue_task', 'Continue')}")
            console.print(f"[cyan]2.[/] {i18n_text(self.i18n, 'menu_api_settings', 'API Settings')}")
            console.print(f"[cyan]3.[/] {i18n_text(self.i18n, 'menu_glossary_rules', 'Glossary Rules')}")
            console.print(f"[cyan]4.[/] {i18n_text(self.i18n, 'menu_effective_config_preview', 'Effective Config Preview')}")
            console.print(f"[dim]0. {i18n_text(self.i18n, 'menu_cancel', 'Cancel')}[/dim]")

            choice = IntPrompt.ask(
                f"\n{i18n_text(self.i18n, 'prompt_select', 'Select')}",
                choices=["0", "1", "2", "3", "4"],
                default=1,
                show_choices=False,
            )
            if choice == 1:
                return True
            if choice == 0:
                return False
            if choice == 2:
                self.host.api_manager.api_settings_menu()
            elif choice == 3:
                self.host.glossary_menu.prompt_menu()
            elif choice == 4:
                self.show_effective_config()

    def _build_effective_config_table(self):
        table = Table(show_header=True)
        table.add_column(i18n_text(self.i18n, "label_setting_name", "Setting"))
        table.add_column(i18n_text(self.i18n, "label_value", "Value"), overflow="fold")

        target_platform, platform_config = _active_platform_config(self.config)
        model = self.config.get("model") or platform_config.get("model", "")
        api_url = self.config.get("base_url") or platform_config.get("api_url", "")
        api_key = self.config.get("api_key") or platform_config.get("api_key", "")
        threads = self.config.get("user_thread_counts", 0)
        threads_display = i18n_text(self.i18n, "label_auto", "Auto") if threads == 0 else str(threads)

        rows = [
            (i18n_text(self.i18n, "label_profile", "Profile"), getattr(self.host, "active_profile_name", "")),
            (i18n_text(self.i18n, "label_rules_profile", "Rules Profile"), getattr(self.host, "active_rules_profile_name", "")),
            (i18n_text(self.i18n, "label_platform", "Platform"), target_platform),
            (i18n_text(self.i18n, "label_model", "Model"), model),
            (i18n_text(self.i18n, "label_url", "URL"), api_url),
            (i18n_text(self.i18n, "label_key", "Key"), i18n_text(self.i18n, "label_configured", "Configured") if api_key else i18n_text(self.i18n, "label_not_set", "Not set")),
            (i18n_text(self.i18n, "label_lang_pair", "Languages"), f"{self.config.get('source_language', '')} -> {self.config.get('target_language', '')}"),
            (i18n_text(self.i18n, "banner_threads", "Threads"), threads_display),
            (i18n_text(self.i18n, "setting_retry_count", "Retry Count"), str(self.config.get("retry_count", ""))),
            (i18n_text(self.i18n, "setting_round_limit", "Round Limit"), str(self.config.get("round_limit", ""))),
            (i18n_text(self.i18n, "setting_request_timeout", "Request Timeout"), str(self.config.get("request_timeout", ""))),
        ]
        for label, value in rows:
            table.add_row(label, str(value))
        return table

    def _build_task_table(self, task_mode, target_path, output_path, continue_status):
        table = Table(show_header=True)
        table.add_column(i18n_text(self.i18n, "label_setting_name", "Setting"))
        table.add_column(i18n_text(self.i18n, "label_value", "Value"), overflow="fold")

        target_platform, platform_config = _active_platform_config(self.config)
        model = self.config.get("model") or platform_config.get("model", "")
        cache_path = os.path.join(output_path, "cache", "AinieeCacheData.json") if output_path else ""
        cache_status = (
            i18n_text(self.i18n, "label_resume_available", "Resume available")
            if cache_path and os.path.exists(cache_path)
            else i18n_text(self.i18n, "label_new_task", "New task")
        )
        if continue_status:
            cache_status = i18n_text(self.i18n, "option_resume", "Resume")

        rows = [
            (i18n_text(self.i18n, "label_task_type", "Task Type"), _task_label(self.i18n, task_mode)),
            (i18n_text(self.i18n, "label_input", "Input"), target_path),
            (i18n_text(self.i18n, "label_output", "Output"), output_path),
            (i18n_text(self.i18n, "label_profile", "Profile"), getattr(self.host, "active_profile_name", "")),
            (i18n_text(self.i18n, "label_rules_profile", "Rules Profile"), getattr(self.host, "active_rules_profile_name", "")),
            (i18n_text(self.i18n, "label_platform", "Platform"), target_platform),
            (i18n_text(self.i18n, "label_model", "Model"), model),
            (i18n_text(self.i18n, "banner_trans", "Trans"), _selection_status(self.i18n, self.config.get("translation_prompt_selection", {}))),
            (i18n_text(self.i18n, "banner_polish", "Polish"), _selection_status(self.i18n, self.config.get("polishing_prompt_selection", {}))),
            (i18n_text(self.i18n, "label_cache_status", "Cache"), cache_status),
        ]
        for label, value in rows:
            table.add_row(label, str(value))
        return table

    def _print_rule_summary(self, compact=False):
        report = RuleInspector(self.config, self.i18n).inspect()
        master_enabled = report["master_enabled"]
        master = _switch_status(self.i18n, master_enabled)
        console.print(
            f"[bold]{i18n_text(self.i18n, 'banner_glossary_profile', 'Glossary Master Switch')}:[/bold] {master}"
        )
        if master_enabled:
            console.print(
                f"[bold]{i18n_text(self.i18n, 'banner_selected_glossary', 'Selected Glossary')}:[/bold] "
                f"[green]{getattr(self.host, 'active_rules_profile_name', '')}[/green]"
            )

        table = Table(show_header=not compact, box=None)
        if not compact:
            table.add_column(i18n_text(self.i18n, "label_rule", "Rule"))
            table.add_column(i18n_text(self.i18n, "label_effective", "Effective"))
            table.add_column(i18n_text(self.i18n, "label_count", "Count"), justify="right")
        for item in report["summaries"]:
            if compact and item["switch_key"] not in {
                "prompt_dictionary_switch",
                "characterization_switch",
                "world_building_switch",
            }:
                continue
            effective = _switch_status(self.i18n, item["effective_enabled"])
            table.add_row(item["label"], effective, str(item["count"]))
        console.print(table)

        if report["issues"]:
            issue_count = len(report["issues"])
            console.print(
                f"[yellow]{i18n_text(self.i18n, 'msg_rule_issue_count', 'Rule issues detected: {}').format(issue_count)}[/yellow]"
            )
            console.print(build_rule_issue_panel(report["issues"], self.i18n))


def calculate_output_path(config, target_path):
    return _output_path_for(config or {}, target_path)
