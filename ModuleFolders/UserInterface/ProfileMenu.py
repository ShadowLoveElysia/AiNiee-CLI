"""
配置档菜单模块
从 ainiee_cli.py 分离
"""
import os
import time

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table

from ModuleFolders.Infrastructure.TaskConfig.ConfigProfileService import (
    atomic_write_json,
    deep_merge,
    load_json_file,
    load_master_preset,
    resolve_profile_path,
    split_effective_config,
)


console = Console()


class ProfileMenu:
    """配置档管理菜单。"""

    def __init__(self, host):
        self.host = host

    @property
    def i18n(self):
        return self.host.i18n

    def show(self):
        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{self.i18n.get('menu_profiles')}[/bold]"))

            profiles = self.host._get_profiles_list(self.host.profiles_dir)

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", self.i18n.get("menu_profile_select"))
            table.add_row("[cyan]2.[/]", self.i18n.get("menu_profile_create"))
            table.add_row("[cyan]3.[/]", self.i18n.get("menu_profile_rename"))
            table.add_row("[red]4.[/]", self.i18n.get("menu_profile_delete"))
            console.print(table)
            console.print(f"\n[dim]0. {self.i18n.get('menu_exit')}[/dim]")

            choice = IntPrompt.ask(
                f"\n{self.i18n.get('prompt_select')}",
                choices=["0", "1", "2", "3", "4"],
                show_choices=False,
            )

            if choice == 0:
                break
            if choice == 1:
                if self._switch_profile(profiles):
                    break
            elif choice == 2:
                self._create_profile()
            elif choice == 3:
                self._rename_profile()
            elif choice == 4:
                self._delete_profile(profiles)

    def _switch_profile(self, profiles):
        console.print(Panel(self.i18n.get("menu_profile_select")))
        profile_table = Table(show_header=False, box=None)
        for index, profile_name in enumerate(profiles):
            is_active = profile_name == self.host.active_profile_name
            suffix = " [green](Active)[/]" if is_active else ""
            profile_table.add_row(f"[cyan]{index + 1}.[/]", f"{profile_name}{suffix}")
        console.print(profile_table)
        console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")

        selected_index = IntPrompt.ask(
            self.i18n.get("prompt_select"),
            choices=[str(i) for i in range(len(profiles) + 1)],
            show_choices=False,
        )
        if selected_index == 0:
            return False

        selected_profile = profiles[selected_index - 1]
        self.host.switch_active_profile(selected_profile)
        console.print(f"[green]{self.i18n.get('msg_active_platform').format(selected_profile)}[/green]")
        time.sleep(1)
        return True

    def _create_profile(self):
        new_name = Prompt.ask(self.i18n.get("prompt_profile_name")).strip()
        try:
            new_path, new_name = resolve_profile_path(self.host.profiles_dir, new_name)
            active_path, _ = resolve_profile_path(self.host.profiles_dir, self.host.active_profile_name)
        except ValueError:
            console.print(f"[red]{self.i18n.get('msg_profile_invalid')}[/red]")
            time.sleep(1)
            return

        if new_name and not os.path.exists(new_path):
            preset = load_master_preset()
            if os.path.exists(active_path):
                base_config = deep_merge(preset, load_json_file(active_path, {}))
            else:
                base_config = deep_merge(preset, self.host.config)
            settings_only, _, _ = split_effective_config(base_config)
            atomic_write_json(new_path, settings_only)
            console.print(f"[green]{self.i18n.get('msg_profile_created').format(new_name)}[/green]")
        else:
            console.print(f"[red]{self.i18n.get('msg_profile_invalid')}[/red]")
        time.sleep(1)

    def _rename_profile(self):
        new_name = Prompt.ask(self.i18n.get("prompt_profile_rename")).strip()
        try:
            active_path, _ = resolve_profile_path(self.host.profiles_dir, self.host.active_profile_name)
            new_path, new_name = resolve_profile_path(self.host.profiles_dir, new_name)
        except ValueError:
            console.print(f"[red]{self.i18n.get('msg_profile_invalid')}[/red]")
            time.sleep(1)
            return

        if new_name and not os.path.exists(new_path):
            os.rename(active_path, new_path)
            self.host.switch_active_profile(new_name)
            console.print(f"[green]{self.i18n.get('msg_profile_renamed').format(new_name)}[/green]")
        else:
            console.print(f"[red]{self.i18n.get('msg_profile_invalid')}[/red]")
        time.sleep(1)

    def _delete_profile(self, profiles):
        if len(profiles) <= 1:
            console.print(f"[red]{self.i18n.get('msg_cannot_delete_last')}[/red]")
            time.sleep(1)
            return

        delete_candidates = [profile for profile in profiles if profile != self.host.active_profile_name]
        console.print(Panel(f"{self.i18n.get('menu_profile_delete')}"))
        profile_table = Table(show_header=False, box=None)
        for index, profile_name in enumerate(delete_candidates):
            profile_table.add_row(f"[cyan]{index + 1}.[/]", profile_name)
        console.print(profile_table)
        console.print(f"\n[dim]0. {self.i18n.get('menu_cancel')}[/dim]")

        selected_index = IntPrompt.ask(
            self.i18n.get("prompt_select"),
            choices=[str(i) for i in range(len(delete_candidates) + 1)],
            show_choices=False,
        )
        if selected_index == 0:
            return

        selected_profile = delete_candidates[selected_index - 1]
        if Confirm.ask(f"[bold red]{self.i18n.get('msg_profile_delete_confirm').format(selected_profile)}[/bold red]"):
            target_path, _ = resolve_profile_path(self.host.profiles_dir, selected_profile)
            os.remove(target_path)
            console.print(f"[green]{self.i18n.get('msg_profile_deleted').format(selected_profile)}[/green]")
        else:
            console.print(f"[yellow]{self.i18n.get('msg_delete_cancel')}[/yellow]")
        time.sleep(1)
