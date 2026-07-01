"""
命令行非交互运行模块
从 ainiee_cli.py 分离
"""
import os
import re
import time

from rich.console import Console
from rich.panel import Panel

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.MangaFeatureGuard import get_manga_feature_status
from ModuleFolders.Infrastructure.TaskConfig.PolishingMode import normalize_polishing_mode
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType


console = Console()

_MANGA_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
_MANGA_PACKAGE_SUFFIXES = {
    ".pdf",
    ".zip",
    ".cbz",
    ".rar",
    ".cbr",
}
_MANGA_FILE_SUFFIXES = _MANGA_IMAGE_SUFFIXES | _MANGA_PACKAGE_SUFFIXES


def normalize_cli_path(path: str) -> str:
    raw_path = str(path or "").strip().strip('"').strip("'")
    if not raw_path:
        return ""
    if os.name != "nt":
        normalized = raw_path.replace("\\", "/")
        drive_match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
        if drive_match:
            drive = drive_match.group(1).lower()
            rest = drive_match.group(2)
            raw_path = os.path.join("/mnt", drive, rest)
    return os.path.abspath(os.path.expanduser(raw_path))


def derive_manga_output_path(input_path: str) -> str:
    normalized = normalize_cli_path(input_path)
    base_name = os.path.basename(normalized.rstrip(os.sep))
    suffix = os.path.splitext(base_name)[1].lower()
    if os.path.isfile(normalized) or suffix in _MANGA_FILE_SUFFIXES:
        base_name = os.path.splitext(base_name)[0]
    return os.path.join(os.path.dirname(normalized), f"{base_name}_AiNiee_Output")


def resolve_manga_output_path(config: dict, args, input_path: str) -> str:
    explicit_output = str(getattr(args, "output_path", "") or "").strip()
    if explicit_output:
        return normalize_cli_path(explicit_output)

    configured_output = str(config.get("label_output_path") or "").strip()
    if config.get("auto_set_output_path", False) or not configured_output:
        return derive_manga_output_path(input_path)

    return normalize_cli_path(configured_output)


def is_supported_manga_input_path(input_path: str) -> bool:
    normalized = normalize_cli_path(input_path)
    if os.path.isdir(normalized):
        return True
    if not os.path.isfile(normalized):
        return False
    return os.path.splitext(normalized)[1].lower() in _MANGA_FILE_SUFFIXES


def describe_supported_manga_inputs() -> str:
    return ", ".join(sorted(_MANGA_FILE_SUFFIXES)) + ", or a directory containing supported images"


def resolve_manga_translation_settings(config: dict, args) -> dict[str, str]:
    platforms = config.get("platforms") if isinstance(config.get("platforms"), dict) else {}
    api_settings = config.get("api_settings") if isinstance(config.get("api_settings"), dict) else {}

    platform = str(
        getattr(args, "platform", None)
        or config.get("target_platform", "")
        or api_settings.get("translate")
        or ""
    ).strip()
    platform_config = platforms.get(platform) if platform else {}
    if not isinstance(platform_config, dict):
        platform_config = {}

    model = str(getattr(args, "model", None) or config.get("model") or platform_config.get("model", "") or "").strip()
    api_url = str(
        getattr(args, "api_url", None)
        or config.get("base_url")
        or platform_config.get("api_url", "")
        or ""
    ).strip()
    api_key = str(getattr(args, "api_key", None) or config.get("api_key") or platform_config.get("api_key", "") or "").strip()
    return {
        "platform": platform,
        "model": model,
        "api_url": api_url,
        "api_key_state": "configured" if api_key else "empty",
    }


def validate_manga_translation_settings(settings: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if not settings.get("platform"):
        problems.append("No translation platform is configured.")
    if not settings.get("model"):
        problems.append("No translation model is configured.")
    if not settings.get("api_url"):
        problems.append("No translation API URL is configured.")
    return problems


def format_manga_page_stats(stats: dict[str, object]) -> str:
    labels = (
        ("page_count", "pages"),
        ("total_blocks", "blocks"),
        ("total_translated_blocks", "translated"),
        ("translation_warnings", "translation_warnings"),
        ("no_text_pages", "no_text_pages"),
        ("inpainted_pages", "inpainted_pages"),
        ("final_blocked_pages", "final_blocked_pages"),
    )
    parts = [f"{label}={stats[key]}" for key, label in labels if key in stats]
    return ", ".join(parts)


def _clean_markup_for_text(value: object) -> str:
    return re.sub(r"\[/?[a-zA-Z0-9_#=\s.-]+\]", "", str(value or "")).strip()


class CommandModeRunner:
    """CLI 非交互模式任务分发。"""

    def __init__(self, host):
        self.host = host

    def _i18n_get(self, key: str, default: str | None = None) -> str:
        for source in (getattr(self.host, "i18n", None), getattr(Base, "i18n", None)):
            if source is not None and hasattr(source, "get"):
                value = source.get(key)
                if value and value != key:
                    return str(value)
        return default if default is not None else key

    def _i18n_format(self, key: str, default: str = "", *args: object) -> str:
        if not key:
            return default
        template = self._i18n_get(key, default or key)
        try:
            return template.format(*args)
        except Exception:
            result = template
            for arg in args:
                result = result.replace("{}", str(arg), 1)
            return result

    def run(self, args):
        if args.task == "mcp":
            # MCP 命令行模式交给专用桥接层处理，保持 ainiee_cli.py 只做委托。
            return self.host.mcp_runtime_bridge.run_mcp_server_from_command(
                transport=getattr(args, "mcp_transport", "stdio"),
            )

        if getattr(args, "manga", False) and args.task != "translate":
            console.print("[red]Error: --manga currently only supports the translate task.[/red]")
            return 2

        if getattr(args, "manga_runtime_check", False):
            return self._run_manga_runtime_check(args)

        if args.profile:
            self.host.switch_active_profile(args.profile)

        if args.rules_profile:
            self.host.switch_active_rules_profile(args.rules_profile)

        self._apply_config_overrides(args)
        self.host.save_config()

        task_map = {
            "translate": TaskType.TRANSLATION,
            "polish": TaskType.POLISH,
            "all_in_one": TaskType.TRANSLATE_AND_POLISH,
        }

        if args.task == "queue":
            self._run_queue(args)
            return 0

        if args.task in task_map:
            if not args.input_path:
                console.print("[red]Error: input_path is required for this task.[/red]")
                return 2
            if getattr(args, "manga", False):
                return self._run_manga(args)
            if args.task == "all_in_one":
                self._run_all_in_one(args)
            else:
                self.host.run_task(
                    task_map[args.task],
                    target_path=args.input_path,
                    continue_status=args.resume,
                    non_interactive=args.non_interactive,
                    web_mode=args.web_mode,
                )
            return 0

        if args.task == "export":
            if not args.input_path:
                console.print("[red]Error: input_path is required for export.[/red]")
                return 2
            self.host.run_export_only(
                target_path=args.input_path,
                non_interactive=args.non_interactive,
            )
            return 0

        return 0

    def _apply_config_overrides(self, args):
        if args.source_lang:
            self.host.config["source_language"] = args.source_lang
        if args.target_lang:
            self.host.config["target_language"] = args.target_lang
        if args.output_path:
            self.host.config["label_output_path"] = args.output_path
        if args.project_type:
            self.host.config["translation_project"] = args.project_type

        if args.threads is not None:
            self.host.config["user_thread_counts"] = args.threads
        if args.retry is not None:
            self.host.config["retry_count"] = args.retry
        if args.timeout is not None:
            self.host.config["request_timeout"] = args.timeout
        if args.rounds is not None:
            self.host.config["round_limit"] = args.rounds
        if args.pre_lines is not None:
            self.host.config["pre_line_counts"] = args.pre_lines
        if getattr(args, "polish_mode", None):
            runtime_overrides = getattr(self.host, "runtime_config_overrides", {})
            if not isinstance(runtime_overrides, dict):
                runtime_overrides = {}
            runtime_overrides["polishing_mode_selection"] = normalize_polishing_mode(args.polish_mode)
            self.host.runtime_config_overrides = runtime_overrides

        if args.lines is not None:
            self.host.config["tokens_limit_switch"] = False
            self.host.config["lines_limit"] = max(1, min(100, int(args.lines)))
        if args.tokens is not None:
            self.host.config["tokens_limit_switch"] = True
            self.host.config["tokens_limit"] = max(400, min(16000, int(args.tokens)))

        if args.platform:
            self.host.config["target_platform"] = args.platform
        if args.model:
            self.host.config["model"] = args.model
        if args.api_url:
            self.host.config["base_url"] = args.api_url
        if args.api_key:
            self.host.config["api_key"] = args.api_key
            target_platform = self.host.config.get("target_platform", "")
            if target_platform and target_platform in self.host.config.get("platforms", {}):
                self.host.config["platforms"][target_platform]["api_key"] = args.api_key

        if args.think_depth is not None:
            think_depth = args.think_depth.strip() if isinstance(args.think_depth, str) else args.think_depth
            if isinstance(think_depth, str) and think_depth.isdigit():
                think_depth = int(think_depth)
            self.host.config["think_depth"] = think_depth
            target_platform = self.host.config.get("target_platform", "")
            if target_platform and target_platform in self.host.config.get("platforms", {}):
                self.host.config["platforms"][target_platform]["think_depth"] = think_depth
        if args.thinking_budget is not None:
            self.host.config["thinking_budget"] = args.thinking_budget
            target_platform = self.host.config.get("target_platform", "")
            if target_platform and target_platform in self.host.config.get("platforms", {}):
                self.host.config["platforms"][target_platform]["thinking_budget"] = args.thinking_budget
        if args.failover is not None:
            self.host.config["enable_api_failover"] = args.failover == "on"
        for manga_key in (
            "manga_ocr_engine",
            "manga_detect_engine",
            "manga_segment_engine",
            "manga_inpaint_engine",
            "manga_runtime_device",
            "manga_detect_device",
            "manga_ocr_device",
            "manga_inpaint_device",
        ):
            manga_value = str(getattr(args, manga_key, "") or "").strip()
            if manga_value:
                self.host.config[manga_key] = manga_value

    def _run_queue(self, args):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()
        if args.queue_file:
            queue_manager.load_tasks(args.queue_file)

        if not queue_manager.tasks:
            console.print(f"[red]Error: Task queue is empty (File: {queue_manager.queue_file}). Cannot run queue task.[/red]")
            return

        console.print(f"[bold green]Running Task Queue ({len(queue_manager.tasks)} items)...[/bold green]")
        self.host._is_queue_mode = True
        self.host.start_queue_log_monitor()
        queue_manager.start_queue(self.host)

        try:
            while queue_manager.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            Base.cancel_active_task_session()
            Base.work_status = Base.STATUS.STOPING
        finally:
            self.host.stop_queue_log_monitor()
            self.host._is_queue_mode = False

    def _run_all_in_one(self, args):
        if args.input_path:
            if not self.host.prompt_selection_guard.ensure_prompts_selected(
                TaskType.TRANSLATE_AND_POLISH,
                interactive=False,
            ):
                return

            translate_ok = self.host.run_task(
                TaskType.TRANSLATION,
                target_path=args.input_path,
                continue_status=args.resume,
                non_interactive=True,
                web_mode=args.web_mode,
                from_queue=True,
                skip_prompt_validation=True,
            )
            if translate_ok and Base.work_status != Base.STATUS.STOPING:
                self.host.run_task(
                    TaskType.POLISH,
                    target_path=args.input_path,
                    continue_status=True,
                    non_interactive=True,
                    web_mode=args.web_mode,
                    skip_prompt_validation=True,
                )
            return

        self.host.run_all_in_one()

    def _manga_log(self, message: object) -> None:
        manga_tui = getattr(self.host, "_manga_tui", None)
        if manga_tui is not None and hasattr(manga_tui, "log"):
            manga_tui.log(message)
        else:
            console.print(message)

    def _show_manga_startup_notice(self) -> None:
        title = self._i18n_get("menu_start_manga_translation", "Manga Translation (MangaCore)")
        notice = self._i18n_get(
            "msg_manga_startup_notice",
            (
                "About to start manga translation. This mode is intended for fast, readable "
                "first-pass manga translation. If you are a scanlation group or professional "
                "translator, use the WebUI for fine editing. Make sure you own or have permission "
                "to use the content you translate with this project. This project is for learning "
                "purposes only; delete any output generated by this project within 24 hours."
            ),
        )
        self._manga_log(
            Panel(
                f"[yellow]{notice}[/yellow]",
                title=f"[bold yellow]{title}[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )
        time.sleep(4)

    def _manga_update_progress(self, payload: dict[str, object]) -> None:
        manga_tui = getattr(self.host, "_manga_tui", None)
        if manga_tui is None or not hasattr(manga_tui, "update_progress"):
            return

        total_pages = int(payload.get("total_pages") or payload.get("total") or 1)
        processed_pages = int(payload.get("processed_pages") or payload.get("completed") or 0)
        stage = _clean_markup_for_text(payload.get("stage") or "MangaCore")
        message_args = payload.get("message_args") if isinstance(payload.get("message_args"), (list, tuple)) else []
        message = self._i18n_format(
            str(payload.get("message_key") or ""),
            str(payload.get("message") or stage),
            *message_args,
        )
        message = _clean_markup_for_text(message)
        input_path = str(payload.get("input_path") or getattr(self.host, "_manga_tui_input_path", "") or "")
        file_name = os.path.basename(input_path) if input_path else "MangaCore"
        if message:
            file_name = f"{file_name} | {message}"

        started_at = float(getattr(self.host, "_manga_tui_started_at", time.time()) or time.time())
        elapsed = max(0.0, time.time() - started_at)
        warning_count = int(payload.get("translation_warnings") or 0)
        error_count = int(payload.get("error_count") or 0)
        if str(payload.get("status") or "").lower() == "failed":
            error_count = max(1, error_count)

        manga_tui.update_progress(
            None,
            {
                "line": processed_pages,
                "total_line": max(1, total_pages),
                "token": 0,
                "time": elapsed,
                "file_name": file_name,
                "file_path_full": input_path,
                "total_requests": max(processed_pages, warning_count + error_count),
                "success_requests": max(0, processed_pages - error_count),
                "error_requests": warning_count + error_count,
                "session_requests": processed_pages,
                "session_token": 0,
            },
        )
        if hasattr(manga_tui, "update_status"):
            if error_count:
                manga_tui.update_status(None, {"status": "error"})
            elif warning_count:
                manga_tui.update_status(None, {"status": "warning"})
            else:
                manga_tui.update_status(None, {"status": "normal"})

    def _format_manga_quality_issue(self, issue: object) -> str:
        message_key = str(getattr(issue, "message_key", "") or "")
        message_args = getattr(issue, "message_args", [])
        if not isinstance(message_args, (list, tuple)):
            message_args = []
        fallback = str(getattr(issue, "message", "") or getattr(issue, "code", "") or "")
        return self._i18n_format(message_key, fallback, *message_args)

    def _format_manga_runtime_preflight_issue(self, issue: object) -> str:
        if isinstance(issue, dict):
            message_key = str(issue.get("message_key") or "")
            message_args = issue.get("message_args") if isinstance(issue.get("message_args"), (list, tuple)) else []
            fallback = str(issue.get("message") or issue.get("code") or "")
            return self._i18n_format(message_key, fallback, *message_args)
        return self._format_manga_quality_issue(issue)

    def _log_manga_runtime_preflight_status(self, status, *, style: str = "red") -> None:
        message_args = getattr(status, "message_args", [])
        if not isinstance(message_args, (list, tuple)):
            message_args = []
        message = self._i18n_format(
            str(getattr(status, "message_key", "") or ""),
            str(getattr(status, "message", "") or ""),
            *message_args,
        )
        if message:
            self._manga_log(f"[{style}][MangaCore][/{style}] {message}")
        issues = getattr(status, "issues", [])
        if isinstance(issues, list) and issues:
            for issue in issues:
                self._manga_log(f"[{style}][MangaCore][/{style}] {self._format_manga_runtime_preflight_issue(issue)}")
        for detail in getattr(status, "details", []) or []:
            detail_text = str(detail)
            if "runtime diagnostics:" in detail_text:
                self._manga_log(f"[dim][MangaCore][/dim] {detail_text}")
            else:
                self._manga_log(f"[{style}][MangaCore][/{style}] {detail_text}")

    def _format_manga_runtime_readiness_item(self, item: object) -> str:
        message_key = str(getattr(item, "message_key", "") or "")
        message_args = getattr(item, "message_args", [])
        if not isinstance(message_args, (list, tuple)):
            message_args = []
        fallback = str(getattr(item, "message", "") or getattr(item, "status", "") or "")
        return self._i18n_format(message_key, fallback, *message_args)

    def _run_manga_runtime_check(self, args) -> int:
        from ModuleFolders.MangaCore.bridge.configAdapter import build_cli_config_snapshot
        from ModuleFolders.MangaCore.pipeline.runtimeReadiness import build_manga_runtime_readiness

        config_snapshot = build_cli_config_snapshot(self.host, args)
        report = build_manga_runtime_readiness(config_snapshot=config_snapshot)
        summary = report.summary if isinstance(report.summary, dict) else {}
        if report.ok:
            message = self._i18n_format(
                "manga_runtime_readiness_check_ok",
                "MangaCore runtime readiness check passed: {} stage(s) ready.",
                summary.get("ready_stage_count", len(report.items)),
            )
            self._manga_log(f"[green][MangaCore][/green] {message}")
        else:
            message = self._i18n_format(
                "manga_runtime_readiness_check_failed",
                "MangaCore runtime readiness check failed: {} blocking issue(s).",
                report.issue_count,
            )
            self._manga_log(f"[red][MangaCore][/red] {message}")

        self._manga_log(f"[cyan][MangaCore][/cyan] Model root: {report.model_root}")
        for item in report.items:
            style = "green" if not getattr(item, "blocking", False) else "red"
            status = str(getattr(item, "status", "") or "")
            model_id = str(getattr(item, "model_id", "") or "")
            stage = str(getattr(item, "stage", "") or "")
            self._manga_log(
                f"[{style}][MangaCore][/{style}] "
                f"{stage} / {model_id}: {status} | {self._format_manga_runtime_readiness_item(item)}"
            )
            missing_modules = getattr(item, "missing_modules", [])
            if isinstance(missing_modules, list) and missing_modules:
                self._manga_log(f"  - missing modules: {', '.join(str(module) for module in missing_modules)}")
            missing_assets = getattr(item, "missing_asset_paths", [])
            if isinstance(missing_assets, list) and missing_assets:
                for asset_path in missing_assets[:3]:
                    self._manga_log(f"  - missing asset: {asset_path}")
                if len(missing_assets) > 3:
                    self._manga_log(f"  - missing asset: ... +{len(missing_assets) - 3} more")
            action_key = str(getattr(item, "action_hint_key", "") or "")
            action_args = getattr(item, "action_hint_args", [])
            if action_key:
                if not isinstance(action_args, (list, tuple)):
                    action_args = []
                action = self._i18n_format(action_key, "", *action_args)
                if action:
                    self._manga_log(f"  - action: {action}")
        return 0 if report.ok else 2

    def _log_manga_quality_gate_summary(self, result) -> None:
        page_stats = result.page_job.result if result.page_job and isinstance(result.page_job.result, dict) else {}
        final_blocked_pages = int(page_stats.get("final_blocked_pages") or 0)
        if final_blocked_pages <= 0:
            return

        from ModuleFolders.MangaCore.pipeline.qualityGate import (
            load_quality_gate,
            page_blocked_from_final,
            quality_gate_path,
        )

        self._manga_log(
            f"[yellow][MangaCore][/yellow] "
            f"{self._i18n_format('manga_cli_quality_gate_final_empty', '', final_blocked_pages)}"
        )
        for page_ref in result.session.scene.pages:
            page = result.session.pages[page_ref.page_id]
            blocked, reasons = page_blocked_from_final(result.session, page)
            if not blocked:
                continue
            gate = load_quality_gate(result.session, page)
            issue_texts = (
                [
                    self._format_manga_quality_issue(issue)
                    for issue in gate.issues
                    if issue.blocks_final
                ]
                if gate is not None
                else reasons
            )
            reason = "；".join([text for text in issue_texts if text][:3]) or "needs review"
            draft_path = result.session.project_path / page.layers.rendered
            report_path = quality_gate_path(result.session, page)
            self._manga_log(
                "  - "
                + self._i18n_format(
                    "manga_cli_quality_gate_page_summary",
                    "Page {}: {}",
                    page.index,
                    reason,
                )
            )
            self._manga_log(
                "    "
                + self._i18n_format(
                    "manga_cli_quality_gate_draft_path",
                    "Draft preview: {}",
                    draft_path,
                )
            )
            self._manga_log(
                "    "
                + self._i18n_format(
                    "manga_cli_quality_gate_report_path",
                    "Quality report: {}",
                    report_path,
                )
            )

    def _run_manga(self, args):
        input_path = normalize_cli_path(args.input_path)
        if not os.path.exists(input_path):
            self._manga_log(f"[red][MangaCore][/red] Input path not found: {input_path}")
            return 2
        if not is_supported_manga_input_path(input_path):
            self._manga_log(f"[red][MangaCore][/red] Unsupported manga input source: {input_path}")
            self._manga_log(f"[yellow][MangaCore][/yellow] Supported inputs: {describe_supported_manga_inputs()}")
            return 2

        manga_status = get_manga_feature_status(require_models=False)
        if not manga_status.available:
            self._manga_log(f"[yellow][MangaCore][/yellow] {manga_status.message}")
            for detail in manga_status.details:
                self._manga_log(f"[yellow][MangaCore][/yellow] {detail}")
            return 2

        from ModuleFolders.MangaCore.bridge.configAdapter import build_cli_config_snapshot
        from ModuleFolders.MangaCore.pipeline.runnerBatch import MangaBatchRunner

        output_path = resolve_manga_output_path(self.host.config, args, input_path)
        self.host.config["label_input_path"] = input_path
        self.host.config["label_output_path"] = output_path
        config_snapshot = build_cli_config_snapshot(self.host, args)

        translation_settings = resolve_manga_translation_settings(self.host.config, args)
        translation_problems = validate_manga_translation_settings(translation_settings)
        if translation_problems:
            self._manga_log("[red][MangaCore][/red] Translation API configuration is incomplete.")
            for problem in translation_problems:
                self._manga_log(f"[red][MangaCore][/red] {problem}")
            self._manga_log("[yellow][MangaCore][/yellow] Configure API settings first, or pass --platform, --model, and --api-url.")
            return 2

        model_status = get_manga_feature_status(config_snapshot=config_snapshot, require_models=True)
        if not model_status.available:
            allow_runtime_fallback = bool(getattr(args, "manga_allow_fallback", False)) and not bool(getattr(args, "manga_strict_models", False))
            if not allow_runtime_fallback:
                self._log_manga_runtime_preflight_status(model_status, style="red")
                return 2

            self._manga_log(
                "[yellow][MangaCore][/yellow] "
                + self._i18n_format(
                    "manga_runtime_preflight_allow_fallback",
                    "Runtime preflight failed, but --manga-allow-fallback was set; first-pass pipeline will use fallback runtimes where possible.",
                )
            )
            self._log_manga_runtime_preflight_status(model_status, style="yellow")

        self.host.save_config()

        self._show_manga_startup_notice()
        self._manga_update_progress(
            {
                "input_path": input_path,
                "total_pages": 1,
                "processed_pages": 0,
                "stage": "starting",
                "message": "Starting MangaCore.",
            }
        )
        self._manga_log(f"[bold cyan][MangaCore][/bold cyan] Input: {input_path}")
        self._manga_log(f"[bold cyan][MangaCore][/bold cyan] Output: {output_path}")
        self._manga_log(
            "[bold cyan][MangaCore][/bold cyan] Translation: "
            f"{translation_settings['platform']} / {translation_settings['model']} "
            f"({translation_settings['api_key_state']} API key)"
        )
        self._manga_log(
            "[bold cyan][MangaCore][/bold cyan] "
            + self._i18n_format(
                "manga_cli_visual_engines",
                "Visual engines: ocr={}, detect={}, segment={}, inpaint={}",
                config_snapshot.get("manga_ocr_engine"),
                config_snapshot.get("manga_detect_engine"),
                config_snapshot.get("manga_segment_engine"),
                config_snapshot.get("manga_inpaint_engine"),
            )
        )
        self._manga_log(
            "[bold cyan][MangaCore][/bold cyan] "
            + self._i18n_format(
                "manga_cli_visual_devices",
                "Visual devices: default={}, detect={}, ocr={}, inpaint={}",
                config_snapshot.get("manga_runtime_device"),
                config_snapshot.get("manga_detect_device"),
                config_snapshot.get("manga_ocr_device"),
                config_snapshot.get("manga_inpaint_device"),
            )
        )

        try:
            result = MangaBatchRunner(
                logger=self._manga_log,
                progress_callback=self._manga_update_progress,
            ).run(
                input_path=input_path,
                output_path=output_path,
                config_snapshot=config_snapshot,
                profile_name=self.host.root_config.get("active_profile", "default"),
                rules_profile_name=self.host.root_config.get("active_rules_profile", "default"),
                source_lang=self.host.config.get("source_language", "ja"),
                target_lang=self.host.config.get("target_language", "zh_cn"),
            )
        except Exception as exc:
            self._manga_update_progress(
                {
                    "input_path": input_path,
                    "total_pages": 1,
                    "processed_pages": 0,
                    "status": "failed",
                    "stage": "failed",
                    "message": "Manga batch pipeline failed.",
                    "error_count": 1,
                }
            )
            self._manga_log(f"[red][MangaCore][/red] Manga batch pipeline failed: {exc}")
            return 1

        self._manga_log(f"[bold green][MangaCore][/bold green] Project ready: {result.session.project_path}")
        if result.page_job:
            self._manga_log(
                f"[bold cyan][MangaCore][/bold cyan] Page pipeline: "
                f"{result.page_job.status} | {result.page_job.message}"
            )
            if isinstance(getattr(result.page_job, "result", None), dict):
                stats = format_manga_page_stats(result.page_job.result)
                if stats:
                    self._manga_log(f"[bold cyan][MangaCore][/bold cyan] Page stats: {stats}")
        self._log_manga_quality_gate_summary(result)
        if result.exports.exported_paths:
            self._manga_log("[bold green][MangaCore][/bold green] Exported files:")
            for key, path in result.exports.exported_paths.items():
                self._manga_log(f"  - {key}: {path}")
        if result.warnings:
            self._manga_log("[yellow][MangaCore][/yellow] Finished with warning(s):")
            for warning in result.warnings:
                self._manga_log(f"  - {warning}")
        if not result.ok:
            page_stats = result.page_job.result if result.page_job and isinstance(result.page_job.result, dict) else {}
            total_pages = int(page_stats.get("page_count") or 1)
            processed_pages = int(page_stats.get("processed_pages") or total_pages)
            final_blocked_pages = int(page_stats.get("final_blocked_pages") or 0)
            self._manga_update_progress(
                {
                    "input_path": input_path,
                    "total_pages": total_pages,
                    "processed_pages": processed_pages,
                    "status": "needs_review" if final_blocked_pages else "failed",
                    "stage": "needs_review",
                    "message": "Finished with warnings.",
                    "message_key": "manga_notice_quality_gate_blocked_pages" if final_blocked_pages else "",
                    "message_args": [final_blocked_pages] if final_blocked_pages else [],
                    "translation_warnings": int(page_stats.get("translation_warnings") or len(result.warnings) or 1),
                }
            )
        return 0 if result.ok else 1
