"""
编辑器菜单模块
从 ainiee_cli.py 分离
"""
import os
import time
import rapidjson as json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table

from ModuleFolders.UserInterface.Editor import TUIEditor

console = Console()


class EditorMenu:
    """编辑器菜单"""

    def __init__(self, host):
        """
        初始化编辑器菜单

        Args:
            host: CLIMenu实例，提供config、i18n等依赖
        """
        self.host = host

    @property
    def config(self):
        return self.host.config

    @property
    def i18n(self):
        return self.host.i18n

    def show(self):
        """显示编辑器菜单（入口方法）"""
        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{self.i18n.get('menu_editor') or 'Translation Editor'}[/bold]"))

            # 检查是否有可用的项目
            if not hasattr(self.host, 'cache_manager') or not self.host.cache_manager:
                console.print("[red]No cache manager available. Please run a translation task first.[/red]")
                time.sleep(2)
                break

            # 检查是否有可用的项目缓存
            output_path = self.config.get("label_output_path", "./output")
            cache_file = os.path.join(output_path, "cache", "AinieeCacheData.json")

            if not os.path.exists(cache_file):
                console.print(f"[yellow]{self.i18n.get('editor_no_cache')}[/yellow]")
                console.print(f"[dim]{self.i18n.get('editor_expected_cache')}: {cache_file}[/dim]")
                input("\nPress Enter to continue...")
                break

            # 显示菜单选项
            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", self.i18n.get("menu_editor_open_current"))
            table.add_row("[cyan]2.[/]", self.i18n.get("menu_editor_input_path"))
            table.add_row("[cyan]3.[/]", self.i18n.get("menu_editor_scan_projects"))
            table.add_row("[cyan]4.[/]", self.i18n.get("menu_ai_proofread") or "AI辅助校对")
            table.add_row("[cyan]5.[/]", self.i18n.get("menu_view_proofread_report") or "查看校对报告")
            console.print(table)
            console.print(f"\n[dim]0. {self.i18n.get('menu_back') or 'Back'}[/dim]")

            choice = IntPrompt.ask(f"\n{self.i18n.get('prompt_select') or 'Select'}",
                                 choices=["0", "1", "2", "3", "4", "5"], show_choices=False)

            if choice == 0:
                break
            elif choice == 1:
                self._start_editor_with_current_project()
            elif choice == 2:
                self._input_project_path_for_editor()
            elif choice == 3:
                self._select_project_for_editor()
            elif choice == 4:
                self.host.ai_proofread_menu.show()
            elif choice == 5:
                self.host.ai_proofread_menu._view_proofread_reports()

    def _start_editor_with_current_project(self):
        """使用当前项目启动编辑器"""
        try:
            # 获取术语表数据
            glossary_data = getattr(self.host, 'prompt_dictionary_data', [])

            # 创建编辑器实例
            editor = TUIEditor(
                cache_manager=self.host.cache_manager,
                config=self.config,
                i18n=self.i18n,
                glossary_data=glossary_data
            )

            # 获取当前项目路径
            project_path = self.config.get("label_output_path", "./output")

            console.print(f"[green]{self.i18n.get('editor_starting')}: {os.path.basename(project_path)}[/green]")
            console.print(f"[dim]{self.i18n.get('editor_quit_tip')}[/dim]")
            time.sleep(1)

            # 启动编辑器
            success = editor.start_editor(project_path)

            if success:
                console.print(f"[green]{self.i18n.get('editor_session_completed')}[/green]")
            else:
                console.print(f"[red]{self.i18n.get('editor_failed_start')}[/red]")

        except Exception as e:
            console.print(f"[red]{self.i18n.get('editor_error_start')}: {e}[/red]")

        input("\nPress Enter to continue...")

    def _input_project_path_for_editor(self):
        """手动输入项目路径进行编辑"""
        try:
            console.print(Panel(f"[bold]{self.i18n.get('editor_input_path_title')}[/bold]"))
            console.print(f"[dim]{self.i18n.get('editor_input_path_tip')}[/dim]")
            console.print(f"[dim]{self.i18n.get('editor_input_path_example')}[/dim]\n")

            # 提示用户输入路径
            project_path = Prompt.ask(self.i18n.get('editor_project_path_prompt'))

            if not project_path or not project_path.strip():
                console.print(f"[yellow]{self.i18n.get('editor_path_empty')}[/yellow]")
                input("\nPress Enter to continue...")
                return

            project_path = project_path.strip()

            # 检查路径是否存在
            if not os.path.exists(project_path):
                console.print(f"[red]{self.i18n.get('editor_path_not_exist')}: {project_path}[/red]")
                input("\nPress Enter to continue...")
                return

            # 检查缓存文件是否存在
            cache_file = os.path.join(project_path, "cache", "AinieeCacheData.json")
            if not os.path.exists(cache_file):
                console.print(f"[red]{self.i18n.get('editor_cache_not_found')}: {cache_file}[/red]")
                console.print(f"[dim]{self.i18n.get('editor_cache_path_tip')}[/dim]")
                input("\nPress Enter to continue...")
                return

            # 分析项目信息
            project_info = self._analyze_cache_file(cache_file)
            if not project_info:
                console.print(f"[red]{self.i18n.get('editor_parse_failed')}[/red]")
                input("\nPress Enter to continue...")
                return

            # 显示项目信息并确认
            console.print(f"\n[green]{self.i18n.get('editor_found_project')}:[/green] {project_info['name']}")
            console.print(f"[dim]{self.i18n.get('editor_path_label')}: {project_info['path']}[/dim]")
            console.print(f"[dim]{self.i18n.get('editor_items_label')}: {project_info['item_count']}[/dim]")
            console.print(f"[dim]{self.i18n.get('editor_size_label')}: {project_info['size']}[/dim]")

            confirm = Confirm.ask(f"\n{self.i18n.get('editor_confirm_open')}", default=True)
            if confirm:
                self._start_editor_with_selected_project(project_info)
            else:
                console.print(f"[yellow]{self.i18n.get('editor_cancelled')}[/yellow]")
                input("\nPress Enter to continue...")

        except KeyboardInterrupt:
            console.print(f"\n[yellow]{self.i18n.get('editor_cancelled')}[/yellow]")
        except Exception as e:
            console.print(f"[red]{self.i18n.get('editor_input_error')}: {e}[/red]")
            input("\nPress Enter to continue...")

    def _select_project_for_editor(self):
        """选择项目缓存进行编辑"""
        try:
            console.print(f"[blue]{self.i18n.get('editor_scanning_cache')}[/blue]")

            # 扫描可用的缓存文件（调用host的方法）
            cache_projects = self.host._scan_cache_files()

            if not cache_projects:
                console.print(f"[yellow]{self.i18n.get('editor_no_cached_projects')}[/yellow]")
                console.print(f"[dim]{self.i18n.get('editor_cache_pattern_tip')}[/dim]")
                input("\nPress Enter to continue...")
                return

            while True:
                self.host.display_banner()
                console.print(Panel(f"[bold]{self.i18n.get('menu_editor_select_project')}[/bold]"))

                # 显示可用的项目
                table = Table(show_header=True, box=None)
                table.add_column("ID", style="cyan", width=4)
                table.add_column(self.i18n.get("editor_project_name"), style="green")
                table.add_column(self.i18n.get("editor_project_path"), style="dim")
                table.add_column(self.i18n.get("editor_item_count"), style="yellow", width=10)
                table.add_column(self.i18n.get("editor_file_size"), style="blue", width=10)

                for i, project in enumerate(cache_projects):
                    table.add_row(
                        f"{i+1}",
                        project["name"],
                        project["path"],
                        str(project["item_count"]),
                        project["size"]
                    )

                console.print(table)
                console.print(f"\n[dim]0. {self.i18n.get('menu_back') or 'Back'}[/dim]")

                try:
                    choice = IntPrompt.ask(f"\n{self.i18n.get('prompt_select') or 'Select'}",
                                         choices=[str(i) for i in range(len(cache_projects) + 1)],
                                         show_choices=False)

                    if choice == 0:
                        break
                    elif 1 <= choice <= len(cache_projects):
                        selected_project = cache_projects[choice - 1]
                        self._start_editor_with_selected_project(selected_project)
                        break

                except (ValueError, KeyboardInterrupt):
                    break

        except Exception as e:
            console.print(f"[red]Error in project selection: {e}[/red]")
            input("\nPress Enter to continue...")

    def _analyze_cache_file(self, cache_file_path):
        """分析缓存文件获取项目信息"""
        try:
            # 获取项目路径（cache文件所在目录的父目录）
            project_path = os.path.dirname(os.path.dirname(cache_file_path))
            project_name = os.path.basename(project_path)

            # 获取文件统计信息
            stat_info = os.stat(cache_file_path)
            file_size = stat_info.st_size
            modified_time = stat_info.st_mtime

            # 尝试解析缓存文件获取条目数量（只统计有翻译内容的）
            item_count = 0
            translated_count = 0
            try:
                with open(cache_file_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)

                # 根据缓存文件结构计算条目数量
                if isinstance(cache_data, dict):
                    # 新格式缓存
                    files_data = cache_data.get("files", {})
                    for file_data in files_data.values():
                        if isinstance(file_data, dict):
                            items = file_data.get("items", [])
                            for item in items:
                                if isinstance(item, dict):
                                    item_count += 1
                                    # 检查是否有翻译内容
                                    translated_text = item.get("translated_text", "") or item.get("polished_text", "")
                                    if translated_text and translated_text.strip():
                                        translated_count += 1
                elif isinstance(cache_data, list) and len(cache_data) > 1:
                    # 旧格式缓存
                    for item in cache_data[1:]:  # 跳过项目信息
                        if isinstance(item, dict):
                            item_count += 1
                            translated_text = item.get("translated_text", "") or item.get("polished_text", "")
                            if translated_text and translated_text.strip():
                                translated_count += 1

            except Exception:
                item_count = 0
                translated_count = 0

            # 如果没有翻译内容，跳过这个项目
            if translated_count == 0:
                return None

            # 格式化文件大小
            if file_size < 1024:
                size_str = f"{file_size} B"
            elif file_size < 1024 * 1024:
                size_str = f"{file_size / 1024:.1f} KB"
            else:
                size_str = f"{file_size / (1024 * 1024):.1f} MB"

            return {
                "name": project_name,
                "path": project_path,
                "cache_file": cache_file_path,
                "item_count": translated_count,  # 显示实际有翻译内容的条目数
                "total_items": item_count,  # 保留总条目数用于调试
                "size": size_str,
                "modified_time": modified_time
            }

        except Exception as e:
            # 如果分析失败，返回None
            return None

    def _start_editor_with_selected_project(self, project_info):
        """使用选定的项目启动编辑器"""
        try:
            # 获取术语表数据
            glossary_data = getattr(self.host, 'prompt_dictionary_data', [])

            # 创建编辑器实例
            editor = TUIEditor(
                cache_manager=self.host.cache_manager,
                config=self.config,
                i18n=self.i18n,
                glossary_data=glossary_data
            )

            console.print(f"[green]{self.i18n.get('editor_starting')}: {project_info['name']}[/green]")
            console.print(f"[dim]{self.i18n.get('editor_project_info')}: {project_info['path']}[/dim]")
            console.print(f"[dim]{self.i18n.get('editor_item_info')}: {project_info['item_count']}[/dim]")
            console.print(f"[dim]{self.i18n.get('editor_quit_tip')}[/dim]")
            time.sleep(1)

            # 启动编辑器
            success = editor.start_editor(project_info['path'])

            if success:
                console.print(f"[green]{self.i18n.get('editor_session_completed')}[/green]")

                # 将此项目添加到最近使用列表
                self._add_to_recent_projects(project_info['path'])
            else:
                console.print(f"[red]{self.i18n.get('editor_failed_start')}[/red]")

        except Exception as e:
            console.print(f"[red]{self.i18n.get('editor_error_start')}: {e}[/red]")
            import traceback
            console.print(f"[dim]Traceback: {traceback.format_exc()}[/dim]")

        input("\nPress Enter to continue...")

    def _add_to_recent_projects(self, project_path):
        """将项目添加到最近使用列表"""
        try:
            recent_projects = self.config.get("recent_projects", [])

            # 移除已存在的相同路径
            if project_path in recent_projects:
                recent_projects.remove(project_path)

            # 添加到列表开头
            recent_projects.insert(0, project_path)

            # 只保留最近的10个项目
            recent_projects = recent_projects[:10]

            # 保存到配置
            self.config["recent_projects"] = recent_projects
            self.host.save_config()

        except Exception:
            pass  # 静默处理错误，不影响主要功能
