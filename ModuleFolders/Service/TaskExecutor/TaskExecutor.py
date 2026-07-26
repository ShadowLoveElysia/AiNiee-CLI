import time
import os
import threading
import concurrent.futures
from collections import defaultdict

import opencc

from ModuleFolders.Base.Base import Base
from ModuleFolders.Base.EventManager import EventManager
from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Infrastructure.Cache.CacheManager import CacheManager
from ModuleFolders.Infrastructure.Cache.CacheProject import CacheProjectStatistics
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.Service.TaskExecutor.TranslatorTask import TranslatorTask
from ModuleFolders.Service.TaskExecutor.PolisherTask import PolisherTask
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
from ModuleFolders.Infrastructure.TaskConfig.PolishingMode import (
    POLISH_SOURCE_TEXT,
    POLISH_TRANSLATED_TEXT,
)
from ModuleFolders.Domain.PromptBuilder.PromptBuilder import PromptBuilder
from ModuleFolders.Domain.PromptBuilder.PromptBuilderPolishing import PromptBuilderPolishing
from ModuleFolders.Domain.PromptBuilder.PromptBuilderEnum import PromptBuilderEnum
from ModuleFolders.Domain.PromptBuilder.PromptBuilderLocal import PromptBuilderLocal
from ModuleFolders.Domain.PromptBuilder.PromptBuilderSakura import PromptBuilderSakura
from ModuleFolders.Infrastructure.RequestLimiter.RequestLimiter import RequestLimiter
from ModuleFolders.Service.TaskExecutor.TranslatorUtil import get_source_language_for_file


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _halve_line_limit(current_limit, min_limit):
    current_limit = max(1, _safe_int(current_limit, 1))
    min_limit = max(1, _safe_int(min_limit, 1))

    if current_limit <= min_limit:
        return current_limit
    return max(min_limit, int(current_limit / 2))


# 翻译器
class TaskExecutor(Base):

    def __init__(self, plugin_manager,cache_manager, file_reader, file_writer) -> None:
        super().__init__()

        # 初始化
        self.plugin_manager = plugin_manager
        self.cache_manager = cache_manager
        self.file_reader = file_reader
        self.file_writer = file_writer
        self.config = TaskConfig()
        self.request_limiter = RequestLimiter()

        # 注册事件
        self.subscribe(Base.EVENT.TASK_STOP, self.task_stop)
        self.subscribe(Base.EVENT.TASK_START, self.task_start)
        self.subscribe(Base.EVENT.TASK_MANUAL_EXPORT, self.task_manual_export)
        self.subscribe(Base.EVENT.TASK_API_STATUS_REPORT, self.on_api_status_report)
        self.subscribe("TASK_SKIP_FILE_REQUEST", self.on_skip_file_request)
        self.subscribe(Base.EVENT.APP_SHUT_DOWN, self.app_shut_down)

        # Skip File state
        self.skipped_files = set()
        self._skip_lock = threading.Lock()

        # Failover state
        self.global_consecutive_api_errors = 0
        self._api_error_lock = threading.Lock()
        self.api_pipeline = []
        self.current_api_index = 0
        self.current_mode = None

        self._translation_consistency_lock = threading.RLock()
        self.translation_consistency_state = {
            "story_summary": "",
            "character_info": "",
        }
        self.translation_memory_index = {}
        self._translation_memory_lock = threading.RLock()

        # Concurrency Control for Mission Control
        self._concurrency_lock = threading.Lock()
        self._current_active = 0
        self.executor = None
        self._task_session_id = None
        self._stop_lock = threading.Lock()
        self._stop_reason = "stop"

    def _run_with_task_session(self, session_id, target, *args) -> None:
        token = Base.set_task_session(session_id)
        try:
            target(*args)
        finally:
            Base.reset_task_session(token)

    def _mark_task_session_stopped(self, session_id) -> None:
        if Base.is_current_task_session(session_id):
            Base.work_status = Base.STATUS.TASKSTOPPED

    # API 状态报告事件处理
    def on_api_status_report(self, event: int, data: dict) -> None:
        is_success = data.get("is_success", False)
        force_switch = data.get("force_switch", False)

        if force_switch:
            with self._api_error_lock:
                self._switch_api()
                self.consecutive_errors = 0 # Reset on manual switch
            return

        with self._api_error_lock:
            if is_success:
                self.consecutive_errors = 0
            else:
                self.consecutive_errors += 1

                # Check for critical error threshold
                threshold = self.config.critical_error_threshold or 5
                if self.consecutive_errors >= threshold:
                    self.error(f"Critical error threshold ({threshold}) reached. Pausing task.")
                    self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "critical_error"})
                    self.emit(Base.EVENT.TASK_STOP, {"reason": "critical_error", "status": "critical_error"})
                    self.consecutive_errors = 0 # Reset after triggering
                    return # Stop further processing to avoid conflict with failover

                # Standard failover logic
                if self.config.enable_api_failover and self.api_pipeline:
                    failover_threshold = self.config.api_failover_threshold
                    if self.consecutive_errors >= failover_threshold:
                        self.warning(f"API consecutive errors ({self.consecutive_errors}) reached failover threshold ({failover_threshold}). Switching API...")
                        self._switch_api()
                        self.consecutive_errors = 0

    def on_skip_file_request(self, event, data):
        with self._skip_lock:
            path = data.get("file_path_full")
            if path:
                self.skipped_files.add(path)
                self.print(f"[bold yellow]Skip request received for {os.path.basename(path)}. New tasks for this file will be ignored.[/bold yellow]")

    def _gated_run(self, task):
        """指挥中心：动态门禁控制"""
        session_id = getattr(task, "task_session_id", self._task_session_id)
        token = Base.set_task_session(session_id)
        slot_acquired = False
        try:
            while True:
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
                    return None
                with self._concurrency_lock:
                    # 实时检查配置中的线程限制
                    if self._current_active < self.config.actual_thread_counts:
                        self._current_active += 1
                        slot_acquired = True
                        break
                time.sleep(0.01)

            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
                return {}

            with self._skip_lock:
                if hasattr(task, 'file_path_full') and task.file_path_full in self.skipped_files:
                    return None # Skip execution
            self._emit_task_progress_info(task)
            result = task.start()
            if not Base.is_task_session_active(session_id):
                return None
            if isinstance(result, dict):
                result.setdefault("task_session_id", session_id)
            return result
        finally:
            if slot_acquired:
                with self._concurrency_lock:
                    self._current_active -= 1
            Base.reset_task_session(token)

    def _push_web_comparison(self, source_text: str, translated_text: str) -> None:
        """Push comparison snapshot to event bus and WebServer internal API."""
        if not Base.is_task_session_active():
            return
        if not source_text and not translated_text:
            return

        self.emit(Base.EVENT.TUI_RESULT_DATA, {"source": source_text, "data": translated_text})
        if not Base.is_task_session_active():
            return

        try:
            import requests
            from Tools.MCPServer.security import INTERNAL_AUTH_ENV, INTERNAL_AUTH_HEADER

            webserver_port = self.load_config().get("webserver_port", 8000)
            internal_api_base = os.environ.get("AINIEE_INTERNAL_API_URL", f"http://127.0.0.1:{webserver_port}")
            internal_api_token = os.environ.get(INTERNAL_AUTH_ENV, "")
            requests.post(
                f"{internal_api_base}/api/internal/update_comparison",
                json={"source": source_text, "translation": translated_text},
                headers={INTERNAL_AUTH_HEADER: internal_api_token},
                timeout=1,
            )
        except Exception:
            pass

    def _get_non_excluded_item_count(self) -> int:
        total_count = self.cache_manager.get_item_count()
        excluded_count = self.cache_manager.get_item_count_by_status(TranslationStatus.EXCLUDED)
        return max(total_count - excluded_count, 0)

    def _get_file_filter_progress_info(self, file_path: str) -> dict:
        cache_file = self.cache_manager.project.get_file(file_path) if self.cache_manager.project else None
        if not cache_file:
            return {
                "file_raw_total_line": 0,
                "file_filtered_total_line": 0,
                "file_excluded_total_line": 0,
            }

        raw_total = len(cache_file.items)
        excluded_total = sum(1 for item in cache_file.items if item.translation_status == TranslationStatus.EXCLUDED)
        return {
            "file_raw_total_line": raw_total,
            "file_filtered_total_line": max(raw_total - excluded_total, 0),
            "file_excluded_total_line": excluded_total,
        }

    def _build_character_recall_window(self, file_path: str, chunk: list) -> tuple[list, list]:
        if not getattr(self.config, "character_recall_switch", True):
            return [], []
        if not chunk or not self.cache_manager.project:
            return [], []

        previous_count = max(0, _safe_int(getattr(self.config, "character_recall_context_lines", 50), 50))
        lookahead_count = max(0, _safe_int(getattr(self.config, "character_recall_lookahead_lines", 10), 10))
        if previous_count <= 0 and lookahead_count <= 0:
            return [], []

        cache_file = self.cache_manager.project.get_file(file_path)
        if not cache_file or not cache_file.items:
            return [], []

        try:
            start_idx = cache_file.index_of(chunk[0].text_index)
            end_idx = cache_file.index_of(chunk[-1].text_index)
        except (KeyError, IndexError, AttributeError):
            return [], []

        previous_items = cache_file.items[max(0, start_idx - previous_count):start_idx] if previous_count > 0 else []
        lookahead_items = cache_file.items[end_idx + 1:end_idx + 1 + lookahead_count] if lookahead_count > 0 else []
        return previous_items, lookahead_items

    def _build_sparse_completion_context_window(self, file_path: str, chunk: list) -> tuple[list, list]:
        if not chunk or not self.cache_manager.project:
            return [], []

        previous_count = max(0, min(100, _safe_int(getattr(self.config, "sparse_completion_previous_lines", 15), 15)))
        lookahead_count = max(0, min(100, _safe_int(getattr(self.config, "sparse_completion_lookahead_lines", 8), 8)))
        if previous_count <= 0 and lookahead_count <= 0:
            return [], []

        cache_file = self.cache_manager.project.get_file(file_path)
        if not cache_file or not cache_file.items:
            return [], []

        try:
            start_idx = cache_file.index_of(chunk[0].text_index)
            end_idx = cache_file.index_of(chunk[-1].text_index)
        except (KeyError, IndexError, AttributeError):
            return [], []

        previous_items = cache_file.items[max(0, start_idx - previous_count):start_idx] if previous_count > 0 else []
        lookahead_items = cache_file.items[end_idx + 1:end_idx + 1 + lookahead_count] if lookahead_count > 0 else []
        return previous_items, lookahead_items

    def _with_filter_progress_info(self, stats_dict: dict) -> dict:
        raw_total = self.cache_manager.get_item_count()
        excluded_total = self.cache_manager.get_item_count_by_status(TranslationStatus.EXCLUDED)
        stats_dict.update({
            "raw_total_line": raw_total,
            "filtered_total_line": max(raw_total - excluded_total, 0),
            "excluded_total_line": excluded_total,
            "project_file_count": len(self.cache_manager.project.files) if self.cache_manager.project else 0,
        })
        return stats_dict

    def _emit_task_progress_info(self, task) -> None:
        if not hasattr(self, "project_status_data"):
            return
        stats_dict = self._with_filter_progress_info(self.project_status_data.to_dict())
        stats_dict.update(getattr(task, "extra_info", {}) or {})
        self.emit(Base.EVENT.TASK_UPDATE, stats_dict)

    def reset_translation_consistency_state(self) -> None:
        with self._translation_consistency_lock:
            self.translation_consistency_state = {
                "story_summary": "",
                "character_info": "",
            }

    def get_translation_consistency_state_snapshot(self) -> dict:
        with self._translation_consistency_lock:
            return dict(self.translation_consistency_state)

    def update_translation_consistency_state(self, story_summary: str, character_info: str) -> None:
        with self._translation_consistency_lock:
            story_summary = str(story_summary or "").strip()
            character_info = str(character_info or "").strip()

            if story_summary:
                self.translation_consistency_state["story_summary"] = story_summary
            if character_info:
                self.translation_consistency_state["character_info"] = character_info

    def reset_translation_memory_index(self) -> None:
        with self._translation_memory_lock:
            self.translation_memory_index = {}

    def build_translation_memory_index(self) -> None:
        if not getattr(self.config, "translation_memory_switch", False):
            self.reset_translation_memory_index()
            return

        index = defaultdict(list)
        for item in self.cache_manager.project.items_iter():
            key = PromptBuilder.normalize_translation_memory_source(item.source_text)
            if key:
                index[key].append(item)

        self.translation_memory_index = {
            key: items
            for key, items in index.items()
            if len(items) > 1
        }

    def get_translation_memory_references(self, source_text_dict: dict) -> list[dict]:
        if not getattr(self.config, "translation_memory_switch", False):
            return []

        with self._translation_memory_lock:
            index = self.translation_memory_index
            if not index:
                return []

            references = []
            seen_keys = set()
            for source in source_text_dict.values():
                key = PromptBuilder.normalize_translation_memory_source(source)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)

                translations = {}
                for item in index.get(key, ()):
                    if item.translation_status == TranslationStatus.POLISHED:
                        translated_text = str(item.polished_text or item.translated_text or "").strip()
                    elif item.translation_status == TranslationStatus.TRANSLATED:
                        translated_text = str(item.translated_text or "").strip()
                    else:
                        continue
                    if translated_text:
                        translations[translated_text] = item.source_text

                if len(translations) != 1:
                    continue

                translated_text, original_source = next(iter(translations.items()))
                references.append({
                    "src": original_source,
                    "dst": translated_text,
                })

            return references

    def _execute_tasks_async(self, tasks_list):
        """异步执行模式：使用 aiohttp 处理高并发请求"""
        import asyncio
        from ModuleFolders.Infrastructure.LLMRequester.AsyncLLMRequester import AsyncLLMRequester
        from ModuleFolders.Infrastructure.LLMRequester.AsyncSignalHub import get_signal_hub
        from ModuleFolders.Infrastructure.LLMRequester.ErrorClassifier import ErrorClassifier, ErrorType

        # 获取用户设置的并发数
        max_concurrency = self.config.actual_thread_counts
        signal_hub = get_signal_hub()
        signal_hub.reset()  # 重置信号状态
        signal_hub.set_concurrency(max_concurrency)

        # 引用 self 以便在异步函数中使用
        executor_self = self

        async def run_single_task(task):
            """执行单个异步任务"""
            import time as time_module
            task_start = time_module.time()
            slot_acquired = False
            session_id = getattr(task, "task_session_id", executor_self._task_session_id)
            token = Base.set_task_session(session_id)

            try:
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
                    return None

                # 等待暂停恢复
                await signal_hub.wait_if_paused()

                # 统一并发槽控制，支持运行中热调整并发
                slot_acquired = await signal_hub.acquire_slot()
                if (
                    Base.work_status == Base.STATUS.STOPING
                    or await signal_hub.check_stop()
                    or not Base.is_task_session_active(session_id)
                ):
                    return None

                # 检查是否跳过
                with executor_self._skip_lock:
                    if hasattr(task, 'file_path_full') and task.file_path_full in executor_self.skipped_files:
                        return None

                if not getattr(task, "_prepared", False):
                    task.prepare(executor_self.config.target_platform)

                # 打印任务开始日志
                task_id = getattr(task, 'task_id', '???')
                preview = str(list(task.source_text_dict.values())[:1])[:50] if hasattr(task, 'source_text_dict') else ''
                executor_self._emit_task_progress_info(task)
                executor_self.print(f"[dim][{task_id}] Translating: {preview}...[/dim]")

                # 获取平台配置
                platform_config = executor_self.config.get_platform_configuration("translationReq")

                # 发起异步请求
                requester = AsyncLLMRequester()
                skip, error_type, content, pt, ct = await requester.send_request_async(
                    task.messages, task.system_prompt, platform_config
                )

                elapsed = time_module.time() - task_start
                if not Base.is_task_session_active(session_id):
                    return None

                if skip:
                    # 上报错误状态
                    executor_self.report_api_status(False)
                    # 打印错误日志
                    executor_self.print(f"[red]✗ [{task_id}] Failed ({error_type}) | {elapsed:.2f}s[/red]")
                    # 检查是否为软伤错误（可重试）
                    err_type, _ = ErrorClassifier.classify(content)
                    if err_type == ErrorType.SOFT_ERROR:
                        if ErrorClassifier.should_reduce_concurrency(content):
                            signal_hub.broadcast_rate_limit(platform_config.get("api_url", ""))
                    return {
                        "check_result": False,
                        "row_count": 0,
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "error_type": error_type,
                        "extra_info": getattr(task, "extra_info", {}),
                        "task_session_id": session_id,
                    }

                # 上报成功状态
                executor_self.report_api_status(True)

                # 结果处理
                if content:
                    from ModuleFolders.Domain.ResponseExtractor.ResponseExtractor import ResponseExtractor
                    from ModuleFolders.Domain.ResponseChecker.ResponseChecker import ResponseChecker
                    content, sparse_pt, sparse_ct = await executor_self._try_sparse_completion_async(
                        task,
                        content,
                        requester,
                        platform_config,
                    )
                    pt += sparse_pt
                    ct += sparse_ct

                    response_dict = ResponseExtractor.text_extraction(task, task.source_text_dict, content)
                    response_dict = ResponseExtractor.normalize_numbered_prefixes(task, response_dict, task.source_text_dict)

                    if not Base.is_task_session_active(session_id):
                        return None

                    if response_dict:
                        untranslated_retry_count = 0
                        if getattr(task, "items", None):
                            untranslated_retry_count = task.items[0].extra.get('untranslated_retry_count', 0)

                        check_result, error_content = ResponseChecker.check_response_content(
                            task,
                            executor_self.config,
                            getattr(task, "placeholder_order", {}),
                            content,
                            response_dict,
                            task.source_text_dict,
                            getattr(task, "source_lang", ""),
                            untranslated_retry_count
                        )

                        if not check_result:
                            executor_self.print(f"[red]✗ [{task_id}] Check failed: {error_content}[/red]")
                            if "漏翻检测" in error_content:
                                for item in task.items:
                                    if not Base.is_task_session_active(session_id):
                                        return None
                                    with item.atomic_scope():
                                        current_count = item.extra.get('untranslated_retry_count', 0)
                                        item.extra['untranslated_retry_count'] = current_count + 1
                            return {
                                "check_result": False,
                                "row_count": 0,
                                "prompt_tokens": pt,
                                "completion_tokens": ct,
                                "extra_info": getattr(task, "extra_info", {}),
                                "task_session_id": session_id,
                            }

                        response_dict = ResponseExtractor.remove_numbered_prefix(task, response_dict, task.source_text_dict)
                        try:
                            temp_dict = response_dict.copy()
                            restored_response_dict = task.text_processor.restore_all(
                                executor_self.config,
                                temp_dict,
                                getattr(task, "prefix_codes", {}),
                                getattr(task, "suffix_codes", {}),
                                getattr(task, "placeholder_order", {}),
                                getattr(task, "affix_whitespace_storage", {}),
                            )
                        except Exception as exc:
                            executor_self.error(f"[{task_id}] Post-processing error: {exc}")
                            restored_response_dict = response_dict

                        if not Base.is_task_session_active(session_id):
                            return None
                        for item, response in zip(task.items, restored_response_dict.values()):
                            if not Base.is_task_session_active(session_id):
                                return None
                            with item.atomic_scope():
                                if not Base.is_task_session_active(session_id):
                                    return None
                                item.model = executor_self.config.model
                                item.translated_text = response
                                item.translation_status = TranslationStatus.TRANSLATED

                        if not Base.is_task_session_active(session_id):
                            return None

                        source_snapshot = "\n".join(str(v) for v in task.source_text_dict.values()) if getattr(task, "source_text_dict", None) else ""
                        translated_snapshot = "\n".join(str(v) for v in restored_response_dict.values())
                        executor_self._push_web_comparison(source_snapshot, translated_snapshot)

                        if not Base.is_task_session_active(session_id):
                            return None

                        # 打印成功日志
                        executor_self.print(f"[green]√ [{task_id}] Done ({len(task.items)} lines) | {elapsed:.2f}s | {pt}+{ct}T[/green]")

                        return {
                            "check_result": True,
                            "row_count": len(task.items),
                            "prompt_tokens": pt,
                            "completion_tokens": ct,
                            "extra_info": getattr(task, "extra_info", {}),
                            "task_session_id": session_id,
                        }

                return {
                    "check_result": False,
                    "row_count": 0,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "extra_info": getattr(task, "extra_info", {}),
                    "task_session_id": session_id,
                }
            finally:
                if slot_acquired:
                    signal_hub.release_slot()
                Base.reset_task_session(token)

        async def run_all_tasks():
            """运行所有异步任务"""
            # 初始化连接池，传入线程数以保护系统资源
            await AsyncLLMRequester.get_session(max_concurrency)

            tasks = [run_single_task(task) for task in tasks_list]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果
            for result in results:
                if isinstance(result, Exception):
                    if Base.is_task_session_active():
                        self.error(f"Async task error: {result}")
                elif result:
                    self._process_async_result(result)

            # 关闭连接池
            await AsyncLLMRequester.close_session()

        self.info(f"[bold cyan]使用异步模式执行任务 (并发数: {max_concurrency})...[/bold cyan]")
        asyncio.run(run_all_tasks())

    @staticmethod
    async def _try_sparse_completion_async(task, content, requester, platform_config):
        sparse_request = task.build_sparse_completion_request(content)
        if sparse_request is None:
            return content, 0, 0

        task.log_sparse_completion_start(sparse_request.required_numbers)
        if not await TaskExecutor._wait_sparse_completion_rate_limit_async(task, sparse_request):
            task.log_sparse_completion_failed(sparse_request.required_numbers)
            return content, 0, 0

        skip, _, supplement, prompt_tokens, completion_tokens = await requester.send_request_async(
            sparse_request.messages,
            sparse_request.system_prompt,
            platform_config,
        )
        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        if skip or not supplement:
            task.log_sparse_completion_failed(sparse_request.required_numbers)
            return content, prompt_tokens, completion_tokens

        merged = task.merge_sparse_completion_response(
            content,
            supplement,
            sparse_request.required_numbers,
        )
        return merged, prompt_tokens, completion_tokens

    @staticmethod
    async def _wait_sparse_completion_rate_limit_async(task, sparse_request, timeout: float = 600) -> bool:
        if task.request_limiter is None:
            return True

        import asyncio

        estimated_tokens = task.sparse_completion_request_tokens(sparse_request)
        wait_started = time.time()
        while True:
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(
                getattr(task, "task_session_id", Base.current_task_session())
            ):
                return False
            if task.request_limiter.check_limiter(estimated_tokens):
                return True
            if time.time() - wait_started > timeout:
                return False
            await asyncio.sleep(0.1)

    def _process_async_result(self, result):
        """处理异步任务结果"""
        if not result or not isinstance(result, dict):
            return
        if not Base.is_task_session_active(result.get("task_session_id")):
            return

        with self.project_status_data.atomic_scope():
            self.project_status_data.total_requests += 1
            is_error = not result.get("check_result")
            self.project_status_data.error_requests += 1 if is_error else 0
            self.project_status_data.success_requests += 0 if is_error else 1
            self.project_status_data.line += result.get("row_count", 0)
            self.project_status_data.token += result.get("prompt_tokens", 0) + result.get("completion_tokens", 0)
            self.project_status_data.total_completion_tokens += result.get("completion_tokens", 0)
            self.project_status_data.time = time.time() - self.project_status_data.start_time

        self.cache_manager.require_save_to_file(self.session_output_path)
        self.emit(Base.EVENT.TASK_UPDATE, self.project_status_data.to_dict())

    # API 状态上报与轮转逻辑
    def report_api_status(self, is_success: bool) -> None:
        if not self.config.enable_api_failover or not self.api_pipeline:
            return

        with self._api_error_lock:
            if is_success:
                if self.global_consecutive_api_errors > 0:
                    self.global_consecutive_api_errors = 0
            else:
                self.global_consecutive_api_errors += 1
                threshold = self.config.api_failover_threshold
                if self.global_consecutive_api_errors >= threshold:
                    self.warning(f"API consecutive errors ({self.global_consecutive_api_errors}) reached threshold ({threshold}). Switching API...")
                    self._switch_api()
                    self.global_consecutive_api_errors = 0

    def _switch_api(self) -> None:
        try:
            if len(self.api_pipeline) <= 1:
                self.warning("No backup APIs available in pool.")
                return

            # Rotate pipeline
            self.current_api_index = (self.current_api_index + 1) % len(self.api_pipeline)
            new_api = self.api_pipeline[self.current_api_index]

            self.print(f"[bold yellow]↺ Switching API to: {new_api}[/bold yellow]")

            # Update Config safely
            # Note: TaskConfig is shared, but we need to ensure atomic update if possible
            # Or just rely on Python's GIL for dict updates
            if self.current_mode == TaskType.TRANSLATION:
                self.config.api_settings["translate"] = new_api
            elif self.current_mode == TaskType.POLISH:
                self.config.api_settings["polish"] = new_api

            # Re-prepare configuration (updates base_url, keys, models, etc.)
            self.config.prepare_for_translation(self.current_mode)

            # Update limiter with new limits
            self.request_limiter.set_limit(
                self.config.tpm_limit, self.config.rpm_limit,
                getattr(self.config, 'enable_rate_limit', False),
                getattr(self.config, 'custom_rpm_limit', 0),
                getattr(self.config, 'custom_tpm_limit', 0)
            )

            self.info(f"Successfully switched to {new_api}. New Model: {self.config.model}")

        except Exception as e:
            self.error(f"Failed to switch API: {e}")

    # 应用关闭事件
    def app_shut_down(self, event: int, data: dict) -> None:
        Base.cancel_active_task_session()
        Base.work_status = Base.STATUS.STOPING

    # 手动导出事件
    def task_manual_export(self, event: int, data: dict) -> None:

        self.print("")
        self.info(f"正在读取数据，准备输出中 ...")
        self.print("")

        # 获取配置信息，此时 config 是一个字典，后面需要使用get
        config = self.load_config()

        output_path = data.get("export_path")
        inpput_path = config.get("label_input_path")

        # 触发手动导出插件事件
        self.plugin_manager.broadcast_event("manual_export", config, self.cache_manager.project)

        # 如果开启了转换简繁开关功能，则进行文本转换
        if config.get("response_conversion_toggle"):  # 使用 .get()
            self.print("")

            self.info(f"已启动自动简繁转换功能，正在使用 {config.get('opencc_preset')} 配置进行字形转换 ...")
            self.print("")

            converter = opencc.OpenCC(config.get('opencc_preset'))
            cache_list = self.cache_manager.project.items_iter()
            for item in cache_list:
                if item.translation_status == TranslationStatus.TRANSLATED:
                    item.translated_text = converter.convert(item.translated_text)
                if item.translation_status == TranslationStatus.POLISHED:
                    item.polished_text = converter.convert(item.polished_text)
            self.print("")
            self.info(f"简繁转换完成。")
            self.print("")

        # 输出配置包
        output_config = {
            "translated_suffix": config.get('output_filename_suffix'),
            "bilingual_suffix": "_bilingual",
            "bilingual_order": config.get('bilingual_text_order','translation_first'),
            "enable_bilingual_output": config.get('enable_bilingual_output', False)
        }
        task_config = TaskConfig()
        task_config.initialize(config)

        # 写入文件
        self.file_writer.output_translated_content(
            self.cache_manager.project,
            output_path,
            inpput_path,
            output_config,
            task_config
        )

        self.print("")
        self.info(f"翻译结果已成功保存至 {output_path} 目录。")
        self.print("")

    # 任务停止事件
    def task_stop(self, event: int, data: dict) -> None:
        data = data if isinstance(data, dict) else {}
        requested_reason = data.get("reason") or "stop"
        cancelled_session_id = Base.cancel_active_task_session()

        try:
            from ModuleFolders.Infrastructure.LLMRequester.AsyncSignalHub import get_signal_hub
            get_signal_hub().stop()
        except Exception:
            pass

        # 如果已经是停止中或已停止，则只补齐取消信号，不重复启动停止等待线程
        if Base.work_status == Base.STATUS.STOPING:
            if requested_reason != "pause":
                with self._stop_lock:
                    self._stop_reason = requested_reason
            return
        if Base.work_status == Base.STATUS.TASKSTOPPED:
            if requested_reason != "pause":
                EventManager.get_singleton().emit(
                    Base.EVENT.TASK_STOP_DONE,
                    {"reason": requested_reason, "task_session_id": cancelled_session_id},
                )
            return

        with self._stop_lock:
            self._stop_reason = requested_reason

        # 设置运行状态为停止中
        Base.work_status = Base.STATUS.STOPING

        # Best-effort checkpoint for resume safety.
        try:
            if getattr(self, "session_output_path", None):
                self.cache_manager.require_save_to_file(self.session_output_path)
                self.cache_manager.flush_pending_save()
        except Exception as e:
            self.warning(f"Failed to flush cache on stop: {e}")

        # 如果存在执行器，尝试停止接收新任务
        if self.executor:
            try:
                self.executor.shutdown(wait=False)
            except: pass

        def target() -> None:
            # 增加超时退出机制，防止死锁
            start_wait = time.time()
            while True:
                time.sleep(0.5)
                if Base.work_status == Base.STATUS.TASKSTOPPED or time.time() - start_wait > 30:
                    if not Base.is_current_task_session(cancelled_session_id):
                        break
                    if Base.work_status != Base.STATUS.TASKSTOPPED and Base.is_current_task_session(cancelled_session_id):
                         Base.work_status = Base.STATUS.TASKSTOPPED
                    with self._stop_lock:
                        stop_reason = self._stop_reason or requested_reason
                    self.print("")
                    if stop_reason == "pause":
                        self.info("翻译任务已暂停 ...")
                    else:
                        self.info("翻译任务已停止 ...")
                    self.print("")
                    EventManager.get_singleton().emit(
                        Base.EVENT.TASK_STOP_DONE,
                        {"reason": stop_reason, "task_session_id": cancelled_session_id},
                    )
                    break

        # 子线程循环检测停止状态
        threading.Thread(target = target, daemon=True).start()

    # 任务开始事件
    def task_start(self, event: int, data: dict) -> None:
        # 如果正在停止中，不接受新任务
        if Base.work_status == Base.STATUS.STOPING:
            self.warning("系统正在停止中，无法开始新任务。")
            return

        session_id = Base.begin_task_session()
        self._task_session_id = session_id

        # Reset error counter at the very beginning of a new task
        self.consecutive_errors = 0
        if not data.get("silent", False): # Don't reset status on silent resume
            self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "normal"})

        # Clear skipped files list for the new session
        with self._skip_lock:
            self.skipped_files.clear()

        # 获取配置信息
        continue_status = data.get("continue_status")
        current_mode = data.get("current_mode")
        self.current_mode = current_mode
        self.session_input_path = data.get("session_input_path")
        self.session_output_path = data.get("session_output_path")
        self.reset_translation_consistency_state()

        # 翻译任务
        if current_mode == TaskType.TRANSLATION:
            threading.Thread(
                target = self._run_with_task_session,
                args = (session_id, self.translation_start_target, continue_status),
            ).start()

        # 润色任务
        elif current_mode == TaskType.POLISH:
            threading.Thread(
                target = self._run_with_task_session,
                args = (session_id, self.polish_start_target, continue_status),
            ).start()

        else:
            self.print("")
            self.error(f"非法的翻译模式：{current_mode}，请检查配置文件 ...")
            self.print("")
            return None

    # 翻译主流程
    def translation_start_target(self, continue_status: bool) -> None:
        try:
            # 设置翻译状态为正在翻译状态
            if not Base.is_task_session_active():
                return None
            Base.work_status = Base.STATUS.TASKING

            # Initialize failover before preparing translation
            self._initialize_failover()

            # 配置翻译平台信息
            self.config.prepare_for_translation(TaskType.TRANSLATION)

            if getattr(self.config, "translation_consistency_enhancement", False):
                if getattr(self.config, "enable_async_mode", False):
                    self.warning("翻译一致性增强模式将关闭异步执行，改为同步单线程顺序处理 ...")
                    self.config.enable_async_mode = False

                self.info("翻译一致性增强已启用：当前任务将强制使用 Tool Calls 和记忆回灌，线程数锁定为 1 ...")
                if self.config.target_platform == "deepseek" and bool(getattr(self.config, "think_switch", False)):
                    self.warning("DeepSeek 在一致性增强模式下将临时关闭 thinking，以确保 Tool Calls 可以正常执行 ...")
                if not self.config.tokens_limit_switch and self.config.lines_limit < 200:
                    self.warning("建议将单次翻译行数提高到 200-400 行，以减少一致性增强带来的额外提示词往返 ...")

            # 配置请求限制器
            self.request_limiter.set_limit(
                self.config.tpm_limit, self.config.rpm_limit,
                getattr(self.config, 'enable_rate_limit', False),
                getattr(self.config, 'custom_rpm_limit', 0),
                getattr(self.config, 'custom_tpm_limit', 0)
            )

            # --- 修复：确保总行数始终被正确初始化 ---
            if continue_status == False or self.cache_manager.project.stats_data is None:
                self.project_status_data = CacheProjectStatistics()
                self.cache_manager.project.stats_data = self.project_status_data
                self.project_status_data.total_line = self.cache_manager.get_item_count()
            else:
                self.project_status_data = self.cache_manager.project.stats_data
                self.project_status_data.total_line = self.cache_manager.get_item_count()
                self.project_status_data.start_time = time.time() # 重置开始时间
                self.project_status_data.total_completion_tokens = 0 # 重置完成的token数量

                # --- Fix RPM/TPM calculation for resumed tasks ---
                # We need to offset the lines/tokens already processed in previous sessions
                # so that the current session's RPM/TPM is calculated based on current session's delta.
                self.project_status_data.resume_offset_line = self.project_status_data.line
                self.project_status_data.resume_offset_token = self.project_status_data.token
                self.project_status_data.resume_offset_requests = self.project_status_data.total_requests
            # ----------------------------------------------------------------
            # ----------------------------------------------------------------

            # 初始同步一次数据到 UI，确保不会显示为 0/0
            self.project_status_data.line = self.cache_manager.get_item_count_by_status(TranslationStatus.TRANSLATED)
            stats_dict = self.project_status_data.to_dict()
            stats_dict['is_start'] = True
            self.emit(Base.EVENT.TASK_UPDATE, stats_dict)

            # 触发插件事件
            self.plugin_manager.broadcast_event("text_filter", self.config, self.cache_manager.project)
            self.plugin_manager.broadcast_event("preproces_text", self.config, self.cache_manager.project)
            self.build_translation_memory_index()

            # Language/text filters mark rows as EXCLUDED. From here on, the progress bar
            # tracks only rows that can actually be translated, while keeping raw counts for UI hints.
            self.project_status_data.total_line = self._get_non_excluded_item_count()
            self.project_status_data.line = self.cache_manager.get_item_count_by_status(TranslationStatus.TRANSLATED)
            self.emit(Base.EVENT.TASK_UPDATE, self._with_filter_progress_info(self.project_status_data.to_dict()))

            # 根据最大轮次循环
            for current_round in range(self.config.round_limit + 1):
                # 检测是否需要停止任务
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                    # 循环次数比实际最大轮次要多一轮，当触发停止翻译的事件时，最后都会从这里退出任务
                    # 执行到这里说明停止任意的任务已经执行完毕，可以重置内部状态了
                    self._mark_task_session_stopped(Base.current_task_session())
                    return None

                # 获取 待翻译 状态的条目数量
                item_count_status_untranslated = self.cache_manager.get_item_count_by_status(TranslationStatus.UNTRANSLATED)

                # 判断是否需要继续翻译
                if item_count_status_untranslated == 0:
                    self.print("")
                    self.info("所有文本均已翻译，翻译任务已结束 ...")
                    self.print("")
                    break

                # 达到最大翻译轮次时
                if item_count_status_untranslated > 0:
                    if self.config.enable_smart_round_limit and current_round == self.config.round_limit:
                        # 动态增加 round_limit
                        self.config.round_limit = int(self.config.round_limit * self.config.smart_round_limit_multiplier)
                        self.warning(f"已达到最大翻译轮次，但仍有未翻译文本。智能轮次限制已将最大轮次增加到 {self.config.round_limit} ...")
                        # 不中断循环，继续进行翻译
                    elif not self.config.enable_smart_round_limit and current_round == self.config.round_limit:
                        self.print("")
                        self.warning("已达到最大翻译轮次，仍有部分文本未翻译，请检查结果 ...")
                        self.print("")
                        break


                # 第二轮开始对半切分
                if current_round > 0:
                    if self.config.tokens_limit_switch:
                        if hasattr(self.config, 'tokens_limit'):
                            self.config.tokens_limit = max(400, int(self.config.tokens_limit / 2))
                    else:
                        self.config.lines_limit = _halve_line_limit(
                            self.config.lines_limit,
                            getattr(self.config, 'retry_split_min_lines', 15)
                        )

                # 生成缓存数据条目片段的合集列表，原文列表与上文列表一一对应
                chunks, previous_chunks, file_paths, source_context_chunks = self.cache_manager.generate_item_chunks(
                    "line" if self.config.tokens_limit_switch == False else "token",
                    self.config.lines_limit if self.config.tokens_limit_switch == False else self.config.tokens_limit,
                    self.config.pre_line_counts,
                    TaskType.TRANSLATION,
                    getattr(self.config, 'enable_context_enhancement', False),
                    self.config.pre_line_counts,
                    getattr(self.config, 'chunk_soft_limit_extra_lines', 0),
                    getattr(self.config, 'line_split_optimization_mode', 'off')
                )

                # 生成翻译任务合集列表
                tasks_list = []
                self.print("")
                self.info(f"正在生成翻译任务 ...")

                # Pre-calculate file stats for UI
                unique_files = list(dict.fromkeys(file_paths))
                file_info_map = {path: {"name": os.path.basename(path), "index": i} for i, path in enumerate(unique_files, 1)}
                total_files = len(unique_files)

                for i, (chunk, previous_chunk, file_path, source_context) in enumerate(zip(chunks, previous_chunks, file_paths, source_context_chunks), 1):
                    # Skip task creation if file is marked for skipping
                    with self._skip_lock:
                        if file_path in self.skipped_files:
                            continue

                    # 确定该任务的主语言
                    language_stats = self.cache_manager.project.get_file(file_path).language_stats # 获取该文件的语言检测数据
                    file_source_lang = get_source_language_for_file(self.config.source_language,self.config.target_language,language_stats)

                    task = TranslatorTask(self.config, self.plugin_manager, self.request_limiter, file_source_lang)  # 实例化
                    task.task_id = f"{current_round + 1:02d}-{i:03d}"
                    task.task_session_id = Base.current_task_session()
                    task.file_path_full = file_path

                    # Store file info in task for callback
                    f_info = file_info_map.get(file_path)
                    task.extra_info = {
                        "file_name": f_info["name"],
                        "file_index": f_info["index"],
                        "total_files": total_files,
                        "file_path_full": file_path,
                        **self._get_file_filter_progress_info(file_path),
                    }

                    task.set_items(chunk)  # 传入该任务待翻译原文
                    task.set_previous_items(previous_chunk)  # 传入该任务待翻译原文的上文
                    task.set_source_context_items(source_context)  # 传入原文上下文（用于上下文增强）
                    task.set_character_recall_items(
                        *self._build_character_recall_window(file_path, chunk)
                    )
                    task.set_sparse_completion_context_items(
                        *self._build_sparse_completion_context_window(file_path, chunk)
                    )
                    if getattr(self.config, "translation_consistency_enhancement", False):
                        task.set_consistency_context_provider(self.get_translation_consistency_state_snapshot)
                        task.set_consistency_state_updater(self.update_translation_consistency_state)
                    if getattr(self.config, "translation_memory_switch", False):
                        task.set_translation_memory_provider(self.get_translation_memory_references)
                    if (
                        not getattr(self.config, "translation_consistency_enhancement", False)
                        and not getattr(self.config, "translation_memory_switch", False)
                    ):
                        task.prepare(self.config.target_platform)  # 预先构建消息列表
                    tasks_list.append(task)
                self.info(f"已经生成全部翻译任务 ...")
                if tasks_list:
                    self._emit_task_progress_info(tasks_list[0])
                self.print("")

                # 输出开始翻译的日志
                self.print("")

                # 如果是断点续传，抑制重复的基础环境信息输出
                if not continue_status:
                    self.info(f"最大轮次 - {self.config.round_limit}")
                    self.info(f"项目类型 - {self.config.translation_project}")
                    self.info(f"原文语言 - {self.config.source_language}")
                    self.info(f"译文语言 - {self.config.target_language}")

                    platform_name = self.config.platforms.get(self.config.target_platform, {}).get('name', '未知')
                    self.info(f"接口名称 - {platform_name}")
                    self.info(f"接口地址 - {self.config.base_url}")
                    self.info(f"模型名称 - {self.config.model}")

                    self.info(f"RPM 限额 - {self.config.rpm_limit}")
                    self.info(f"TPM 限额 - {self.config.tpm_limit}")

                    # 打印提示词模式信息
                    s_lang = self.config.source_language
                    if self.config.target_platform == "LocalLLM":
                        system = PromptBuilderLocal.build_system(self.config, s_lang)
                    elif self.config.target_platform == "sakura":
                        system = PromptBuilderSakura.build_system(self.config, s_lang)
                    elif self.config.translation_prompt_selection["last_selected_id"] in (PromptBuilderEnum.COMMON, PromptBuilderEnum.COT, PromptBuilderEnum.THINK):
                        system = PromptBuilder.build_system(self.config, s_lang)
                    else:
                        system = self.config.translation_prompt_selection["prompt_content"]

                    self.info(f"并发请求 - {self.config.actual_thread_counts} 线程")

                    # 高并发时建议使用异步模式
                    if self.config.actual_thread_counts >= 15 and not getattr(self.config, 'enable_async_mode', False):
                        hint_msg = self.tra('msg_high_concurrency_hint').format(self.config.actual_thread_counts)
                        self.print(f"[bold yellow]{hint_msg}[/bold yellow]")
                else:
                    # 仅在第一轮显示恢复提示
                    if current_round == 0:
                        self.info(f"[bold yellow]检测到断点续传：正在恢复 {os.path.basename(self.config.label_input_path)} 的任务状态...[/bold yellow]")

                self.info(f"即将开始执行任务，预计任务总数为 {len(tasks_list)}，请注意保持网络通畅 ...")
                time.sleep(3)
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                    return None
                self.print("")

                # 根据配置选择同步或异步执行模式
                if getattr(self.config, 'enable_async_mode', False):
                    self._execute_tasks_async(tasks_list)
                else:
                    # 重置并发计数器，防止上一轮异常退出后卡死
                    with self._concurrency_lock:
                        self._current_active = 0
                    # 开始执行翻译任务,构建异步线程池 (使用 100 高限额，由 gated_run 实际控制并发)
                    self.executor = concurrent.futures.ThreadPoolExecutor(max_workers = 100, thread_name_prefix = "translator")
                    try:
                        with self.executor as executor:
                            for task in tasks_list:
                                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                                    break
                                future = executor.submit(self._gated_run, task)
                                future.add_done_callback(self.task_done_callback)  # 为future对象添加一个回调函数，当任务完成时会被调用，更新数据
                    finally:
                        self.executor = None

            # Ensure latest progress is persisted before post-processing/output.
            if not Base.is_task_session_active():
                return None
            self.cache_manager.flush_pending_save()

            # 触发插件事件
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return None
            self.plugin_manager.broadcast_event("postprocess_text", self.config, self.cache_manager.project)

            # 如果开启了转换简繁开关功能，则进行文本转换
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return None
            if self.config.response_conversion_toggle:

                self.print("")
                self.info(f"已启动自动简繁转换功能，正在使用 {self.config.opencc_preset} 配置进行字形转换 ...")
                self.print("")

                converter = opencc.OpenCC(self.config.opencc_preset)
                cache_list = self.cache_manager.project.items_iter()
                for item in cache_list:
                    if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                        return None
                    if item.translation_status == TranslationStatus.TRANSLATED:
                        item.translated_text = converter.convert(item.translated_text)
                    if item.translation_status == TranslationStatus.POLISHED:
                        item.polished_text = converter.convert(item.polished_text)

            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return None

            # 输出配置包
            output_config = {
                "translated_suffix": self.config.output_filename_suffix,
                "bilingual_suffix": "_bilingual",
                "bilingual_order": self.config.bilingual_text_order,
                "enable_bilingual_output": self.config.enable_bilingual_output
            }

            # 写入文件
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return None
            self.file_writer.output_translated_content(
                self.cache_manager.project,
                self.session_output_path,
                self.session_input_path,
                output_config,
                self.config
            )
            self.print("")
            self.info(f"翻译结果已保存至 {self.session_output_path} 目录 ...")
            self.print("")

            # 重置内部状态（正常完成翻译）
            self._mark_task_session_stopped(Base.current_task_session())

            # 触发翻译停止完成的事件
            self.plugin_manager.broadcast_event("translation_completed", self.config, self.cache_manager.project)

            # 触发翻译完成事件
            self.emit(Base.EVENT.TASK_COMPLETED, {})
        except Exception as e:
            if Base.is_task_session_active():
                self.error(f"翻译任务异常终止: {e}", e)
                self._mark_task_session_stopped(Base.current_task_session())
                self.emit(Base.EVENT.TASK_STOP_DONE, {"reason": "error"})

    def _initialize_failover(self):
        self.consecutive_errors = 0
        self.api_pipeline = []
        self.current_api_index = 0
        if self.config.enable_api_failover:
            primary_api = self.config.api_settings.get("translate")
            if primary_api:
                self.api_pipeline.append(primary_api)

            backup_apis = self.config.backup_apis or []
            for api in backup_apis:
                if api not in self.api_pipeline:
                    self.api_pipeline.append(api)

            if len(self.api_pipeline) > 1:
                self.info(f"API Failover enabled. Pipeline: {' -> '.join(self.api_pipeline)}")

    # 润色主流程
    def polish_start_target(self, continue_status: bool, silent: bool = False) -> None:
        try:
            # 设置翻译状态为正在翻译状态
            if not Base.is_task_session_active():
                return None
            Base.work_status = Base.STATUS.TASKING


            # 配置翻译平台信息
            self.config.prepare_for_translation(TaskType.POLISH)

            # 配置请求限制器
            self.request_limiter.set_limit(
                self.config.tpm_limit, self.config.rpm_limit,
                getattr(self.config, 'enable_rate_limit', False),
                getattr(self.config, 'custom_rpm_limit', 0),
                getattr(self.config, 'custom_tpm_limit', 0)
            )

            # --- 修复：确保总行数始终被正确初始化 ---
            if continue_status == False or self.cache_manager.project.stats_data is None:
                self.project_status_data = CacheProjectStatistics()
                self.cache_manager.project.stats_data = self.project_status_data
                self.project_status_data.total_line = self.cache_manager.get_item_count()
            else:
                self.project_status_data = self.cache_manager.project.stats_data
                self.project_status_data.total_line = self.cache_manager.get_item_count()
                self.project_status_data.start_time = time.time() # 重置开始时间
                self.project_status_data.total_completion_tokens = 0 # 重置完成的token数量

                # --- Fix RPM/TPM calculation for resumed tasks ---
                self.project_status_data.resume_offset_line = self.project_status_data.line
                self.project_status_data.resume_offset_token = self.project_status_data.token
                self.project_status_data.resume_offset_requests = self.project_status_data.total_requests
            # ----------------------------------------------------------------

            # 更新初始进度
            if self.config.polishing_mode_selection == POLISH_SOURCE_TEXT:
                self.project_status_data.line = self.cache_manager.get_item_count_by_status(TranslationStatus.UNTRANSLATED)
            else:
                self.project_status_data.line = self.cache_manager.get_item_count_by_status(TranslationStatus.POLISHED)

            # 更新监控面板信息
            stats_dict = self.project_status_data.to_dict()
            stats_dict['is_start'] = True
            self.emit(Base.EVENT.TASK_UPDATE, stats_dict)

            # 触发插件事件
            self.plugin_manager.broadcast_event("text_filter", self.config, self.cache_manager.project)

            self.project_status_data.total_line = self._get_non_excluded_item_count()
            if self.config.polishing_mode_selection == POLISH_SOURCE_TEXT:
                self.project_status_data.line = self.cache_manager.get_item_count_by_status(TranslationStatus.UNTRANSLATED)
            else:
                self.project_status_data.line = self.cache_manager.get_item_count_by_status(TranslationStatus.POLISHED)
            self.emit(Base.EVENT.TASK_UPDATE, self._with_filter_progress_info(self.project_status_data.to_dict()))


            # 根据最大轮次循环
            for current_round in range(self.config.round_limit + 1):
                # 检测是否需要停止任务
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                    # 循环次数比实际最大轮次要多一轮，当触发停止翻译的事件时，最后都会从这里退出任务
                    # 执行到这里说明停止任意的任务已经执行完毕，可以重置内部状态了
                    self._mark_task_session_stopped(Base.current_task_session())
                    return None

                # 根据润色模式，获取可润色的条目数量
                if self.config.polishing_mode_selection == POLISH_SOURCE_TEXT:
                    item_count_status_unpolishd = self.cache_manager.get_item_count_by_status(TranslationStatus.UNTRANSLATED)
                elif self.config.polishing_mode_selection == POLISH_TRANSLATED_TEXT:
                    item_count_status_unpolishd = self.cache_manager.get_item_count_by_status(TranslationStatus.TRANSLATED)

                # 判断是否需要继续润色
                if item_count_status_unpolishd == 0:
                    if not silent:
                        self.print("")
                        self.info("所有文本均已润色，润色任务已结束 ...")
                        self.print("")
                    break

                # 达到最大任务轮次时
                if item_count_status_unpolishd > 0:
                    if self.config.enable_smart_round_limit and current_round == self.config.round_limit:
                        # 动态增加 round_limit
                        self.config.round_limit = int(self.config.round_limit * self.config.smart_round_limit_multiplier)
                        self.warning(f"已达到最大任务轮次，但仍有未润色文本。智能轮次限制已将最大轮次增加到 {self.config.round_limit} ...")
                        # 不中断循环，继续进行润色
                    elif not self.config.enable_smart_round_limit and current_round == self.config.round_limit:
                        self.print("")
                        self.warning("已达到最大任务轮次，仍有部分文本未翻译，请检查结果 ...")
                        self.print("")
                        break


                # 第一轮时且不是继续润色时，记录总行数
                if current_round == 0 and continue_status == False:
                 self.project_status_data.total_line = item_count_status_unpolishd
                 # 立即同步总行数到 UI
                 self.emit(Base.EVENT.TASK_UPDATE, self.project_status_data.to_dict())

                # 第二轮开始对半切分
                if current_round > 0:
                    if self.config.tokens_limit_switch:
                        if hasattr(self.config, 'tokens_limit'):
                            self.config.tokens_limit = max(400, int(self.config.tokens_limit / 2))
                    else:
                        self.config.lines_limit = _halve_line_limit(
                            self.config.lines_limit,
                            getattr(self.config, 'retry_split_min_lines', 15)
                        )

                # 生成缓存数据条目片段的合集列表
                if self.config.polishing_mode_selection == POLISH_SOURCE_TEXT:
                    chunks, previous_chunks, file_paths, source_context_chunks = self.cache_manager.generate_item_chunks(
                        "line" if self.config.tokens_limit_switch == False else "token",
                        self.config.lines_limit if self.config.tokens_limit_switch == False else self.config.tokens_limit,
                        self.config.polishing_pre_line_counts,
                        TaskType.TRANSLATION,
                        False,  # 润色模式不需要上下文增强
                        0,
                        getattr(self.config, 'chunk_soft_limit_extra_lines', 0),
                        getattr(self.config, 'line_split_optimization_mode', 'off')
                    )
                elif self.config.polishing_mode_selection == POLISH_TRANSLATED_TEXT:
                    chunks, previous_chunks, file_paths, source_context_chunks = self.cache_manager.generate_item_chunks(
                        "line" if self.config.tokens_limit_switch == False else "token",
                        self.config.lines_limit if self.config.tokens_limit_switch == False else self.config.tokens_limit,
                        self.config.polishing_pre_line_counts,
                        TaskType.POLISH,
                        False,  # 润色模式不需要上下文增强
                        0,
                        getattr(self.config, 'chunk_soft_limit_extra_lines', 0),
                        getattr(self.config, 'line_split_optimization_mode', 'off')
                    )

                # 生成润色任务合集列表
                tasks_list = []
                if not silent:
                    self.print("")
                    self.info(f"正在生成润色任务 ...")
                for i, (chunk, previous_chunk, file_path, _) in enumerate(zip(chunks, previous_chunks, file_paths, source_context_chunks), 1):
                    task = PolisherTask(self.config, self.plugin_manager, self.request_limiter)  # 实例化
                    task.task_id = f"{current_round + 1:02d}-{i:03d}"
                    task.task_session_id = Base.current_task_session()
                    task.set_items(chunk)  # 传入该任务待润色文
                    task.set_previous_items(previous_chunk)  # 传入该任务待润色文的上文
                    task.prepare()  # 预先构建消息列表
                    tasks_list.append(task)

                if not silent:
                    self.info(f"已经生成全部润色任务 ...")
                    self.print("")

                    # 输出开始翻译的日志
                    self.print("")
                    self.info(f"当前轮次 - {current_round + 1}")
                    self.info(f"最大轮次 - {self.config.round_limit}")
                    self.info(f"项目类型 - {self.config.translation_project}")
                    self.print("")
                    self.info(f"接口名称 - {self.config.platforms.get(self.config.target_platform, {}).get('name', '未知')}")
                    self.info(f"接口地址 - {self.config.base_url}")
                    self.info(f"模型名称 - {self.config.model}")
                    self.print("")
                    self.info(f"RPM 限额 - {self.config.rpm_limit}")
                    self.info(f"TPM 限额 - {self.config.tpm_limit}")

                    # 根据提示词规则打印基础指令
                    system = ""
                    if self.config.polishing_prompt_selection["last_selected_id"] == PromptBuilderEnum.POLISH_COMMON:
                        system = PromptBuilderPolishing.build_system(self.config)
                    else:
                        system = self.config.polishing_prompt_selection["prompt_content"]
                    self.print("")
                    if system:
                        self.info(f"本次任务使用以下基础提示词：\n{system}\n")

                    self.info(f"即将开始执行润色任务，预计任务总数为 {len(tasks_list)}, 同时执行的任务数量为 {self.config.actual_thread_counts}，请注意保持网络通畅 ...")
                    time.sleep(3)
                    if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                        return None
                    self.print("")

                # 重置并发计数器，防止上一轮异常退出后卡死
                with self._concurrency_lock:
                    self._current_active = 0
                # 开始执行润色务,构建异步线程池 (使用 100 高限额，由 gated_run 实际控制并发)
                self.executor = concurrent.futures.ThreadPoolExecutor(max_workers = 100, thread_name_prefix = "translator")
                try:
                    with self.executor as executor:
                        for task in tasks_list:
                            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                                break
                            future = executor.submit(self._gated_run, task)
                            future.add_done_callback(self.task_done_callback)  # 为future对象添加一个回调函数，当任务完成时会被调用，更新数据
                finally:
                    self.executor = None

            # Ensure latest progress is persisted before post-processing/output.
            if not Base.is_task_session_active():
                return None
            self.cache_manager.flush_pending_save()

            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return None

            # 输出配置包
            output_config = {
                "translated_suffix": self.config.output_filename_suffix,
                "bilingual_suffix": "_bilingual",
                "bilingual_order": self.config.bilingual_text_order,
                "enable_bilingual_output": self.config.enable_bilingual_output
            }

            # 写入文件
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return None
            self.file_writer.output_translated_content(
                self.cache_manager.project,
                self.config.polishing_output_path,
                self.config.label_input_path,
                output_config,
                self.config
            )
            self.print("")
            self.info(f"润色结果已保存至 {self.config.polishing_output_path} 目录 ...")
            self.print("")

            # 重置内部状态
            self._mark_task_session_stopped(Base.current_task_session())

            # 触发事件
            self.plugin_manager.broadcast_event("polish_completed", self.config, self.cache_manager.project)
            self.emit(Base.EVENT.TASK_COMPLETED, {})     # 翻译完成事件
        except Exception as e:
            if Base.is_task_session_active():
                self.error(f"润色任务异常终止: {e}", e)
                self._mark_task_session_stopped(Base.current_task_session())
                self.emit(Base.EVENT.TASK_STOP_DONE, {"reason": "error"})



    # 单个翻译任务完成时,更新项目进度状态
    def task_done_callback(self, future: concurrent.futures.Future) -> None:
        try:
            result = future.result()

            if result is None or len(result) == 0:
                return

            # 确保 result 是字典类型
            if not isinstance(result, dict):
                return
            if not Base.is_task_session_active(result.get("task_session_id")):
                return

            with self.project_status_data.atomic_scope():
                self.project_status_data.total_requests += 1
                is_error = not result.get("check_result")
                self.project_status_data.error_requests += 1 if is_error else 0
                self.project_status_data.success_requests += 0 if is_error else 1
                self.project_status_data.line += result.get("row_count", 0)
                self.project_status_data.token += result.get("prompt_tokens", 0) + result.get("completion_tokens", 0)
                self.project_status_data.total_completion_tokens += result.get("completion_tokens", 0)
                self.project_status_data.time = time.time() - self.project_status_data.start_time
                stats_dict = self.project_status_data.to_dict()

                if "extra_info" in result:
                    stats_dict.update(result["extra_info"])

                # Add session-specific metrics for correct Rate calculation (Resume Fix)
                stats_dict['session_token'] = self.project_status_data.token - self.project_status_data.resume_offset_token
                stats_dict['session_requests'] = self.project_status_data.total_requests - self.project_status_data.resume_offset_requests

            self.cache_manager.require_save_to_file(self.session_output_path)
            self.emit(Base.EVENT.TASK_UPDATE, stats_dict)

        except Exception as e:
            self.error(f"Task callback error: {e}", e if self.is_debug() else None)
