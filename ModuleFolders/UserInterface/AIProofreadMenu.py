"""
AI校对菜单模块
从 ainiee_cli.py 分离
"""
import os
import time
import threading
import rapidjson as json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, SpinnerColumn

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.Cache.CacheManager import CacheManager
from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig

console = Console()


class AIProofreadMenu:
    """AI校对菜单"""

    def __init__(self, host):
        """
        初始化AI校对菜单

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

    def _tr(self, key: str) -> str:
        return self.i18n.get(key) if self.i18n else key

    def _press_enter(self) -> None:
        input(f"\n{self._tr('msg_press_enter_to_continue')}")

    @staticmethod
    def _resolve_correction_target_field(report_item, cache_item) -> str:
        """确定AI校对修正应写回的缓存字段。"""
        target_field = report_item.ai_check.get("target_field")
        if target_field in ("translated_text", "polished_text"):
            return target_field

        report_text = report_item.translated_text or ""
        if report_text == (cache_item.translated_text or ""):
            return "translated_text"
        if report_text == (cache_item.polished_text or ""):
            return "polished_text"
        return "translated_text"

    @classmethod
    def _apply_correction_to_cache_item(cls, report_item, cache_item, corrected_text: str) -> str:
        """应用AI校对修正，并返回实际写回字段。"""
        target_field = cls._resolve_correction_target_field(report_item, cache_item)

        if target_field == "polished_text":
            cache_item.polished_text = corrected_text
            cache_item.translation_status = TranslationStatus.POLISHED
        else:
            cache_item.translated_text = corrected_text
            cache_item.polished_text = ""
            cache_item.translation_status = TranslationStatus.TRANSLATED

        if cache_item.extra is None:
            cache_item.extra = {}
        cache_item.extra["ai_proofread"] = {
            "target_field": target_field,
            "applied": True,
        }
        return target_field

    def show(self):
        """显示AI校对菜单（入口方法）"""
        from ModuleFolders.UserInterface.Proofreader import ProofreadTUI

        tui = ProofreadTUI(self.i18n)

        while True:
            self.host.display_banner()
            console.print(Panel(f"[bold]{self._tr('menu_ai_proofread')}[/bold]"))

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", self._tr("proofread_start_current"))
            table.add_row("[cyan]2.[/]", self._tr("proofread_select_project"))
            table.add_row("[cyan]3.[/]", self._tr("proofread_settings"))
            table.add_row("[cyan]4.[/]", self._tr("proofread_suggestion_review"))
            table.add_row("[cyan]5.[/]", self._tr("proofread_suggestion_review_saved"))
            console.print(table)
            console.print(f"\n[dim]0. {self._tr('menu_back')}[/dim]")

            choice = IntPrompt.ask(
                f"\n{self._tr('prompt_select')}",
                choices=["0", "1", "2", "3", "4", "5"],
                show_choices=False
            )

            if choice == 0:
                break
            elif choice == 1:
                if tui.display_warning():
                    self._start_ai_proofread()
            elif choice == 2:
                if tui.display_warning():
                    self._select_project_for_proofread()
            elif choice == 3:
                self._proofread_settings_menu()
            elif choice == 4:
                self._start_proofread_suggestion_review()
            elif choice == 5:
                self._review_saved_proofread_suggestions()

    def _start_ai_proofread(self):
        """开始AI校对当前项目"""
        try:
            output_path = self.config.get("label_output_path", "./output")
            cache_file = os.path.join(output_path, "cache", "AinieeCacheData.json")

            if not os.path.exists(cache_file):
                console.print(f"[red]{self._tr('proofread_no_cache')}[/red]")
                self._press_enter()
                return

            self._execute_proofread(output_path)

        except Exception as e:
            console.print(f"[red]{self._tr('proofread_error')}: {e}[/red]")
            self._press_enter()

    def _start_proofread_suggestion_review(self):
        """生成AI校对建议并打开独立采纳界面。"""
        try:
            output_path = self.config.get("label_output_path", "./output")
            cache_file = os.path.join(output_path, "cache", "AinieeCacheData.json")
            if not os.path.exists(cache_file):
                console.print(f"[red]{self._tr('proofread_no_cache')}[/red]")
                self._press_enter()
                return

            if not Confirm.ask(self._tr("proofread_suggestion_confirm_generate"), default=False):
                return

            self._execute_proofread_suggestion_review(output_path)
        except Exception as e:
            console.print(f"[red]{self._tr('proofread_error')}: {e}[/red]")
            self._press_enter()

    def _execute_proofread_suggestion_review(self, project_path: str):
        """执行建议式AI校对，并进入独立采纳系统。"""
        import concurrent.futures

        from ModuleFolders.Infrastructure.RequestLimiter.RequestLimiter import RequestLimiter
        from ModuleFolders.Service.Proofreader import (
            ProofreadSuggestionStore,
            build_proofread_batch,
            collect_suggestion_items,
        )
        from ModuleFolders.Service.Proofreader.ProofreadSuggestionTask import ProofreadSuggestionTask
        from ModuleFolders.UserInterface.Proofreader import ProofreadSuggestionTUI

        cache_path = os.path.join(project_path, "cache", "AinieeCacheData.json")
        project = CacheManager.read_from_file(cache_path)
        items = collect_suggestion_items(project)
        if not items:
            console.print(f"[yellow]{self._tr('proofread_suggestion_no_translated_items')}[/yellow]")
            self._press_enter()
            return

        task_config = TaskConfig()
        task_config.load_config_from_dict(self.config)
        task_config.prepare_for_translation(TaskType.TRANSLATION)

        request_limiter = RequestLimiter()
        request_limiter.set_limit(
            self.config.get("tpm_limit", 100000),
            self.config.get("rpm_limit", 60),
        )

        try:
            batch_size = max(1, int(self.config.get("proofread_batch_size", self.config.get("lines_limit", 30))))
        except (TypeError, ValueError):
            batch_size = 30
        try:
            context_count = max(0, int(self.config.get("proofread_context_lines", self.config.get("pre_line_counts", 3))))
        except (TypeError, ValueError):
            context_count = 3

        batches = [
            build_proofread_batch(items, batch_index, batch_size)
            for batch_index in range((len(items) + batch_size - 1) // batch_size)
        ]
        glossary = self.config.get("prompt_dictionary_data", []) or []
        store = ProofreadSuggestionStore(project_path)
        store.load()

        total_tokens = 0
        suggestions_found = 0
        closed_batches = 0
        completed_tasks = 0
        lock = threading.Lock()

        def build_context_for_batch(batch_index: int) -> list[dict]:
            if context_count <= 0:
                return []
            start = max(0, batch_index * batch_size - context_count)
            end = batch_index * batch_size
            return [
                {
                    "source": item.get("source_text", ""),
                    "translation": item.get("translation", ""),
                }
                for item in items[start:end]
            ]

        def task_done_callback(future):
            nonlocal total_tokens, suggestions_found, closed_batches, completed_tasks
            try:
                task_result = future.result()
                with lock:
                    completed_tasks += 1
                    if task_result.get("skip", True):
                        closed_batches += 1
                        return
                    total_tokens += task_result.get("prompt_tokens", 0) + task_result.get("completion_tokens", 0)
                    parsed = task_result.get("result")
                    if parsed is None:
                        closed_batches += 1
                        return
                    store.add_batch_result(parsed)
                    if parsed.closed_without_suggestions and not parsed.suggestions:
                        closed_batches += 1
                    suggestions_found += len(parsed.suggestions)
            except Exception:
                with lock:
                    completed_tasks += 1

        tasks_list = []
        for index, batch in enumerate(batches):
            task = ProofreadSuggestionTask(
                task_config,
                request_limiter,
                batch,
                glossary=glossary,
                context_lines=build_context_for_batch(index),
            )
            task.prepare()
            tasks_list.append(task)

        console.print(f"[blue]{self._tr('proofread_suggestion_generating_batches').format(len(batches))}[/blue]")
        with Progress(
            SpinnerColumn(),
            TextColumn(f"[bold blue]{self._tr('proofread_suggestion_progress_title')}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("•"),
            TextColumn(f"[cyan]{self._tr('proofread_suggestion_progress_tokens')}: {{task.fields[tokens]}}"),
            TextColumn("•"),
            TextColumn(f"[yellow]{self._tr('proofread_suggestion_progress_suggestions')}: {{task.fields[suggestions]}}"),
            TimeElapsedColumn(),
            console=console,
            refresh_per_second=10,
        ) as progress:
            progress_task = progress.add_task(
                self._tr("proofread_suggestion_progress_title"),
                total=len(tasks_list),
                tokens=0,
                suggestions=0,
            )
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=task_config.actual_thread_counts,
                thread_name_prefix="proofread-suggestion",
            ) as executor:
                futures = []
                for task in tasks_list:
                    if Base.work_status == Base.STATUS.STOPING:
                        break
                    future = executor.submit(task.run)
                    future.add_done_callback(task_done_callback)
                    futures.append(future)

                while not all(f.done() for f in futures):
                    with lock:
                        progress.update(
                            progress_task,
                            completed=completed_tasks,
                            tokens=total_tokens,
                            suggestions=suggestions_found,
                        )
                    time.sleep(0.1)

                with lock:
                    progress.update(
                        progress_task,
                        completed=len(tasks_list),
                        tokens=total_tokens,
                        suggestions=suggestions_found,
                    )

        console.print(
            f"[green]{self._tr('proofread_suggestion_generation_done').format(total_tokens, suggestions_found, closed_batches)}[/green]"
        )
        if store.report_path.exists():
            console.print(f"[green]{self._tr('proofread_suggestion_report_saved').format(store.report_path)}[/green]")

        if suggestions_found == 0:
            console.print(f"[yellow]{self._tr('proofread_suggestion_none_to_review')}[/yellow]")
            self._press_enter()
            return

        tui = ProofreadSuggestionTUI(console, self.i18n)
        review_result = tui.run(store, project)
        if review_result.accepted > 0:
            manager = CacheManager()
            manager.project = project
            manager.save_to_file_require_path = project_path
            manager.save_to_file()
            console.print(f"[green]{self._tr('proofread_suggestion_saved_cache').format(review_result.accepted)}[/green]")
        console.print(
            f"[cyan]{self._tr('proofread_suggestion_review_summary').format(review_result.accepted, review_result.rejected, review_result.ignored, review_result.conflicts)}[/cyan]"
        )
        self._press_enter()

    def _review_saved_proofread_suggestions(self):
        """读取已保存的建议报告，并进入独立采纳系统。"""
        try:
            output_path = self.config.get("label_output_path", "./output")
            self._execute_saved_proofread_suggestion_review(output_path)
        except Exception as e:
            console.print(f"[red]{self._tr('proofread_error')}: {e}[/red]")
            self._press_enter()

    def _execute_saved_proofread_suggestion_review(self, project_path: str):
        from ModuleFolders.Service.Proofreader import ProofreadSuggestionStore
        from ModuleFolders.UserInterface.Proofreader import ProofreadSuggestionTUI

        cache_file = os.path.join(project_path, "cache", "AinieeCacheData.json")
        if not os.path.exists(cache_file):
            console.print(f"[red]{self._tr('proofread_no_cache')}[/red]")
            self._press_enter()
            return

        store = ProofreadSuggestionStore(project_path)
        if not store.load_report():
            console.print(f"[yellow]{self._tr('proofread_suggestion_no_saved_report')}[/yellow]")
            self._press_enter()
            return

        project = CacheManager.read_from_file(cache_file)
        tui = ProofreadSuggestionTUI(console, self.i18n)
        review_result = tui.run(store, project, reload_store=False)
        if review_result.accepted > 0:
            manager = CacheManager()
            manager.project = project
            manager.save_to_file_require_path = project_path
            manager.save_to_file()
            console.print(f"[green]{self._tr('proofread_suggestion_saved_cache').format(review_result.accepted)}[/green]")
        console.print(
            f"[cyan]{self._tr('proofread_suggestion_review_summary').format(review_result.accepted, review_result.rejected, review_result.ignored, review_result.conflicts)}[/cyan]"
        )
        self._press_enter()

    def _execute_proofread(self, project_path: str):
        """执行校对"""
        from ModuleFolders.Service.Proofreader import (
            RuleBasedChecker, AIProofreader, ProofreadReport
        )
        from ModuleFolders.Service.Proofreader.ProofreadReport import ProofreadReportItem
        from ModuleFolders.UserInterface.Proofreader import ProofreadTUI

        tui = ProofreadTUI(self.i18n)

        # 加载缓存
        cache_file = os.path.join(project_path, "cache", "AinieeCacheData.json")
        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        # 从 files 结构中提取所有 items
        items = []
        files_data = cache_data.get("files", {})
        for file_path, file_info in files_data.items():
            if isinstance(file_info, dict):
                file_items = file_info.get("items", {})
                if isinstance(file_items, dict):
                    items.extend(file_items.values())
                elif isinstance(file_items, list):
                    items.extend(file_items)

        if not items:
            console.print("[yellow]没有可校对的内容[/yellow]")
            input("\nPress Enter to continue...")
            return

        # 获取源文件名
        source_file = cache_data.get("project_id", "unknown")
        model_name = self.config.get("model", "unknown")

        # 询问校对模式
        console.print(Panel("[bold]选择校对模式[/bold]"))
        console.print("  [1] 仅规则检查 (免费，检查格式问题)")
        console.print("  [2] 规则检查 + AI校对 (消耗API，检查翻译质量)")
        console.print("  [0] 返回")

        mode = IntPrompt.ask("请选择", choices=["0", "1", "2"])
        if mode == 0:
            return

        # 初始化检查器
        rule_checker = RuleBasedChecker()
        report = ProofreadReport(source_file=source_file, model=model_name)

        # 筛选需要校对的条目
        to_check = []
        for item in items:
            status = item.get("translation_status", 0)
            if status not in [TranslationStatus.TRANSLATED, TranslationStatus.POLISHED]:
                continue
            source = item.get("source_text", "")
            polished_text = item.get("polished_text", "")
            translated_text = item.get("translated_text", "")
            if status == TranslationStatus.POLISHED and polished_text:
                target = polished_text
                target_field = "polished_text"
            else:
                target = translated_text
                target_field = "translated_text"
            if source and target:
                to_check.append({
                    "index": item.get("text_index", 0),
                    "source": source,
                    "translation": target,
                    "target_field": target_field,
                })

        console.print(f"[blue]开始校对 {len(to_check)} 条内容...[/blue]")

        # 执行规则检查
        console.print("[dim]执行规则检查...[/dim]")
        for i, item in enumerate(to_check):
            rule_results = rule_checker.check(item["source"], item["translation"])
            if rule_results:
                report_item = ProofreadReportItem(
                    index=item["index"],
                    source_text=item["source"],
                    translated_text=item["translation"],
                    rule_check={
                        "has_issues": True,
                        "issues": [{"rule_name": r.rule_name, "severity": r.severity, "description": r.description} for r in rule_results]
                    }
                )
                report.add_item(report_item)
            if (i + 1) % 100 == 0:
                console.print(f"[dim]规则检查: {i + 1}/{len(to_check)}[/dim]")

        console.print(f"[green]规则检查完成，发现 {len(report.items)} 个问题[/green]")

        # AI校对
        if mode == 2:
            self._execute_ai_proofread(to_check, report, project_path)

        # 完成报告
        report.finalize(len(items), len(to_check))
        report_path = report.save(project_path)
        console.print(f"[green]校对完成，报告已保存: {report_path}[/green]")

        tui.display_summary(report)
        self._handle_proofread_result(report, project_path)

    def _execute_ai_proofread(self, to_check: list, report, project_path: str):
        """执行AI校对 - 批量处理，复用翻译任务逻辑"""
        import concurrent.futures
        from ModuleFolders.Service.Proofreader.ProofreaderTask import ProofreaderTask
        from ModuleFolders.Service.Proofreader.ProofreadReport import ProofreadReportItem
        from ModuleFolders.Infrastructure.RequestLimiter.RequestLimiter import RequestLimiter

        # 初始化TaskConfig (复用翻译配置逻辑)
        task_config = TaskConfig()
        task_config.load_config_from_dict(self.config)
        task_config.prepare_for_translation(TaskType.TRANSLATION)

        # 初始化限流器
        request_limiter = RequestLimiter()
        tpm_limit = self.config.get("tpm_limit", 100000)
        rpm_limit = self.config.get("rpm_limit", 60)
        request_limiter.set_limit(tpm_limit, rpm_limit)

        # 获取AI校对批量大小与上下文行数
        lines_limit = self.config.get("proofread_batch_size", self.config.get("lines_limit", 30))
        pre_line_counts = self.config.get("proofread_context_lines", self.config.get("pre_line_counts", 3))
        try:
            lines_limit = max(1, int(lines_limit))
        except (TypeError, ValueError):
            lines_limit = 30
        try:
            pre_line_counts = max(0, int(pre_line_counts))
        except (TypeError, ValueError):
            pre_line_counts = 3

        # 分割数据为批次
        chunks = []
        previous_chunks = []
        for i in range(0, len(to_check), lines_limit):
            chunk = to_check[i:i + lines_limit]
            chunks.append(chunk)
            # 上文
            start = max(0, i - pre_line_counts)
            previous_chunks.append(to_check[start:i])

        # 生成校对任务
        tasks_list = []
        for i, (chunk, prev_chunk) in enumerate(zip(chunks, previous_chunks)):
            task = ProofreaderTask(task_config, request_limiter)
            task.task_id = f"P-{i+1:03d}"
            task.set_items(chunk)
            task.set_previous_items(prev_chunk)
            task.prepare()
            tasks_list.append(task)

        # 统计数据
        total_tokens = 0
        issues_found = 0
        completed_tasks = 0
        lock = threading.Lock()

        # 任务完成回调
        def task_done_callback(future):
            nonlocal total_tokens, issues_found, completed_tasks
            try:
                result = future.result()
                with lock:
                    completed_tasks += 1  # 无论成功失败都计数

                    if result is None or result.get("skip", True):
                        return

                    total_tokens += result.get("prompt_tokens", 0) + result.get("completion_tokens", 0)

                    # 处理发现的问题 (key是行ID)
                    issues = result.get("issues", {})
                    corrections = result.get("corrections", {})
                    task = future.task_ref

                    # 建立行ID到item的映射
                    id_to_item = {item["index"]: item for item in task.items}

                    for line_id, issue_list in issues.items():
                        item = id_to_item.get(line_id)
                        if item:
                            issues_found += 1
                            report_item = ProofreadReportItem(
                                index=line_id,
                                source_text=item["source"],
                                translated_text=item["translation"],
                                ai_check={
                                    "has_issues": True,
                                    "issues": issue_list,
                                    "corrected_translation": corrections.get(line_id, ""),
                                    "target_field": item.get("target_field", "translated_text"),
                                }
                            )
                            report.add_item(report_item)
            except Exception as e:
                with lock:
                    completed_tasks += 1

        # 并发执行任务
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]AI校对"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("•"),
            TextColumn("[cyan]Token: {task.fields[tokens]}"),
            TextColumn("•"),
            TextColumn("[yellow]问题: {task.fields[issues]}"),
            TimeElapsedColumn(),
            console=console,
            refresh_per_second=10
        ) as progress:
            progress_task = progress.add_task("AI校对", total=len(tasks_list), tokens=0, issues=0)

            with concurrent.futures.ThreadPoolExecutor(max_workers=task_config.actual_thread_counts, thread_name_prefix="proofreader") as executor:
                futures = []
                for task in tasks_list:
                    if Base.work_status == Base.STATUS.STOPING:
                        break
                    future = executor.submit(task.run)
                    future.task_ref = task
                    future.add_done_callback(task_done_callback)
                    futures.append(future)

                # 等待所有任务完成，同时更新进度
                while not all(f.done() for f in futures):
                    with lock:
                        progress.update(progress_task, completed=completed_tasks, tokens=total_tokens, issues=issues_found)
                    time.sleep(0.1)

                # 最终更新
                with lock:
                    progress.update(progress_task, completed=len(tasks_list), tokens=total_tokens, issues=issues_found)

        console.print(f"[green]AI校对完成，共消耗 {total_tokens} Token，发现 {issues_found} 个问题[/green]")

    def _handle_proofread_result(self, report, project_path: str):
        """处理校对结果"""
        from ModuleFolders.UserInterface.Proofreader import ProofreadTUI

        tui = ProofreadTUI(self.i18n)

        while True:
            choice = tui.display_action_menu()

            if choice == "0":
                break
            elif choice == "1":
                self._review_issues(report)
            elif choice == "2":
                self._apply_high_severity_fixes(report, project_path)
            elif choice == "3":
                self._export_proofread_file(report, project_path)
            elif choice == "4":
                console.print(f"[green]报告已保存[/green]")

        input("\nPress Enter to continue...")

    def _review_issues(self, report):
        """逐条审阅问题"""
        if not report.items:
            console.print("[yellow]没有发现问题[/yellow]")
            return

        for i, item in enumerate(report.items):
            console.print(f"\n[bold cyan]═══ 问题 {i + 1}/{len(report.items)} ═══[/bold cyan]")
            console.print(f"[dim]索引: {item.index}[/dim]")

            # 显示原文和译文
            console.print(f"\n[bold]原文:[/bold]")
            console.print(f"  {item.source_text[:200]}{'...' if len(item.source_text) > 200 else ''}")
            console.print(f"\n[bold]译文:[/bold]")
            console.print(f"  {item.translated_text[:200]}{'...' if len(item.translated_text) > 200 else ''}")

            # 显示规则检查问题
            if item.rule_check and item.rule_check.get("has_issues"):
                console.print(f"\n[bold yellow]规则检查问题:[/bold yellow]")
                for issue in item.rule_check.get("issues", []):
                    console.print(f"  • [{issue.get('severity', 'low')}] {issue.get('rule_name', '')}: {issue.get('description', '')}")

            # 显示AI检查问题
            if item.ai_check and item.ai_check.get("has_issues"):
                console.print(f"\n[bold yellow]AI检查问题:[/bold yellow]")
                for issue in item.ai_check.get("issues", []):
                    console.print(f"  • [{issue.get('severity', 'low')}] {issue.get('type', '')}: {issue.get('description', '')}")
                    if issue.get('suggestion'):
                        console.print(f"    建议: {issue.get('suggestion')}")
                if item.ai_check.get("corrected_translation"):
                    console.print(f"\n[bold green]AI建议译文:[/bold green]")
                    console.print(f"  {item.ai_check.get('corrected_translation')}")

            action = Prompt.ask(
                "\n[A]采纳 [S]跳过 [Q]退出 [a/s/q]",
                choices=["a", "s", "q", "A", "S", "Q"],
                default="s"
            )
            if action.lower() == "q":
                break

    def _apply_high_severity_fixes(self, report, project_path: str):
        """应用高危修改，生成AI校对版本的cache文件"""
        # 统计有修正译文的条目数量
        corrections_count = 0
        for item in report.items:
            if item.ai_check and item.ai_check.get("corrected_translation"):
                corrections_count += 1

        if corrections_count == 0:
            console.print("[yellow]没有AI修正译文可应用[/yellow]")
            return

        if not Confirm.ask(f"确认应用 {corrections_count} 个AI修正译文？将生成校对版本的cache文件", default=False):
            return

        # 加载原始cache文件
        cache_path = os.path.join(project_path, "cache", "AinieeCacheData.json")
        if not os.path.exists(cache_path):
            console.print("[red]找不到原始cache文件[/red]")
            return

        try:
            project = CacheManager.read_from_file(cache_path)

            # 应用修正
            applied_count = 0
            for item in report.items:
                if not item.ai_check or not item.ai_check.get("corrected_translation"):
                    continue

                corrected_text = item.ai_check.get("corrected_translation")
                text_index = item.index

                # 在所有文件中查找并更新对应条目
                for cache_file in project.files.values():
                    for cache_item in cache_file.items:
                        if cache_item.text_index == text_index:
                            self._apply_correction_to_cache_item(item, cache_item, corrected_text)
                            applied_count += 1
                            break

            # 保存为AI校对版本的cache文件
            proofread_cache_path = os.path.join(project_path, "cache", "AinieeCacheData_proofread.json")
            import msgspec
            content_bytes = msgspec.json.encode(project)
            with open(proofread_cache_path, "wb") as f:
                f.write(content_bytes)

            console.print(f"[green]已应用 {applied_count} 个修正，校对版本已保存: {proofread_cache_path}[/green]")
            console.print(f"[dim]提示: 导出时可选择使用校对版本[/dim]")

        except Exception as e:
            console.print(f"[red]应用修正失败: {e}[/red]")

    def _export_proofread_file(self, report, project_path: str):
        """导出校对后文件"""
        # 检查是否为电子书格式
        source_file = report.meta.source_file
        ebook_exts = [".epub", ".mobi", ".azw3", ".fb2", ".txt", ".docx", ".pdf", ".htmlz", ".kepub"]
        ext = os.path.splitext(source_file)[1].lower()

        if ext in ebook_exts:
            # 电子书格式，询问导出格式
            console.print(f"\n原文件格式: {ext}")
            console.print("选择导出格式:")
            formats = ["epub", "mobi", "azw3", "fb2", "pdf", "txt", "docx", "htmlz"]
            for i, fmt in enumerate(formats):
                marker = "(原格式)" if f".{fmt}" == ext else ""
                console.print(f"  [{i+1}] {fmt} {marker}")
            console.print("  [0] 取消")

            choice = IntPrompt.ask("请选择", choices=[str(i) for i in range(len(formats)+1)])
            if choice == 0:
                return
            output_format = formats[choice - 1]
        else:
            # 非电子书格式，直接导出原格式
            output_format = ext.lstrip(".")

        console.print(f"[green]导出格式: {output_format}[/green]")
        console.print("[dim]导出功能开发中...[/dim]")

    def _view_proofread_reports(self):
        """查看校对报告"""
        from ModuleFolders.Service.Proofreader import ProofreadReport
        from ModuleFolders.UserInterface.Proofreader import ProofreadTUI

        output_path = self.config.get("label_output_path", "./output")
        reports = ProofreadReport.list_reports(output_path)

        if not reports:
            console.print("[yellow]没有找到校对报告[/yellow]")
            input("\nPress Enter to continue...")
            return

        console.print(Panel("[bold]校对报告列表[/bold]"))
        for i, report_path in enumerate(reports[:10]):
            filename = os.path.basename(report_path)
            console.print(f"  [{i+1}] {filename}")
        console.print("  [0] 返回")

        choice = IntPrompt.ask("请选择", choices=[str(i) for i in range(min(len(reports), 10)+1)])
        if choice == 0:
            return

        report = ProofreadReport.load(reports[choice - 1])
        tui = ProofreadTUI(self.i18n)
        tui.display_summary(report)
        self._handle_proofread_result(report, output_path)

    def _select_project_for_proofread(self):
        """选择项目进行校对"""
        console.print(Panel("[bold]选择项目[/bold]"))
        console.print("  [1] 手动输入项目路径")
        console.print("  [2] 从扫描结果中选择")
        console.print("  [0] 返回")

        choice = IntPrompt.ask("请选择", choices=["0", "1", "2"])
        if choice == 0:
            return
        elif choice == 1:
            # 手动输入路径
            path = Prompt.ask("请输入项目路径 (包含cache目录的父目录)")
            if not path or path.lower() == 'q':
                return

            cache_file = os.path.join(path, "cache", "AinieeCacheData.json")
            if not os.path.exists(cache_file):
                console.print(f"[red]未找到缓存文件: {cache_file}[/red]")
                input("\nPress Enter to continue...")
                return

            self._execute_proofread(path)
        else:
            # 从扫描结果中选择
            console.print("[dim]正在扫描缓存项目...[/dim]")
            cache_projects = self.host._scan_cache_files()

            if not cache_projects:
                console.print("[yellow]没有找到可校对的项目[/yellow]")
                input("\nPress Enter to continue...")
                return

            console.print(Panel("[bold]选择项目[/bold]"))
            for i, project in enumerate(cache_projects[:10]):
                console.print(f"  [{i+1}] {project['name']}")
            console.print("  [0] 返回")

            sub_choice = IntPrompt.ask("请选择", choices=[str(i) for i in range(min(len(cache_projects), 10)+1)])
            if sub_choice == 0:
                return

            selected = cache_projects[sub_choice - 1]
            self._execute_proofread(selected['path'])

    def _proofread_settings_menu(self):
        """校对设置菜单"""
        while True:
            self.host.display_banner()
            console.print(Panel("[bold]校对设置[/bold]"))

            context_lines = self.config.get("proofread_context_lines", 5)
            batch_size = self.config.get("proofread_batch_size", 20)
            threshold = self.config.get("proofread_confidence_threshold", 0.7)

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"上下文行数: {context_lines}")
            table.add_row("[cyan]2.[/]", f"批量大小: {batch_size}")
            table.add_row("[cyan]3.[/]", f"置信度阈值: {threshold}")
            console.print(table)
            console.print(f"\n[dim]0. 返回[/dim]")

            choice = IntPrompt.ask("请选择", choices=["0", "1", "2", "3"])

            if choice == 0:
                break
            elif choice == 1:
                new_val = IntPrompt.ask("上下文行数", default=context_lines)
                self.config["proofread_context_lines"] = new_val
            elif choice == 2:
                new_val = IntPrompt.ask("批量大小", default=batch_size)
                self.config["proofread_batch_size"] = new_val
            elif choice == 3:
                new_val = Prompt.ask("置信度阈值", default=str(threshold))
                self.config["proofread_confidence_threshold"] = float(new_val)

            self.host.save_config()
