import copy
import re
import time
import requests
import itertools

from rich import box
from rich.table import Table
from rich.markup import escape

from ModuleFolders.Base.Base import Base
from ModuleFolders.Base.PluginManager import PluginManager
from ModuleFolders.Infrastructure.Cache.CacheItem import CacheItem, TranslationStatus
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
from ModuleFolders.Domain.PromptBuilder.PromptBuilderPolishing import PromptBuilderPolishing
from ModuleFolders.Domain.ResponseExtractor.ResponseExtractor import ResponseExtractor
from ModuleFolders.Domain.ResponseChecker.ResponseChecker import ResponseChecker
from ModuleFolders.Infrastructure.RequestLimiter.RequestLimiter import RequestLimiter
from ModuleFolders.Infrastructure.Tokener.Tokener import Tokener

from ModuleFolders.Domain.TextProcessor.PolishTextProcessor import PolishTextProcessor

class PolisherTask(Base):

    def __init__(self, config: TaskConfig, plugin_manager: PluginManager, request_limiter: RequestLimiter) -> None:
        super().__init__()

        self.config = config
        self.plugin_manager = plugin_manager
        self.request_limiter = request_limiter
        self.text_processor = PolishTextProcessor(self.config) # 文本处理器

        # 提示词与信息内容存储
        self.messages = []
        self.system_prompt = ""

        # 输出日志存储
        self.extra_log = []


    # 设置缓存数据
    def set_items(self, items: list[CacheItem]) -> None:
        self.items = items

    # 设置上文数据
    def set_previous_items(self, previous_items: list[CacheItem]) -> None:
        self.previous_items = previous_items

    # 消息构建预处理
    def prepare(self) -> None:

        # 生成上文文本列表
        self.previous_text_list = [v.source_text for v in self.previous_items]

        # 生成原文文本字典
        self.source_text_dict = {str(i): v.source_text for i, v in enumerate(self.items)}

        # 如果开启了翻译文本润色模式
        self.translation_text_dict = {}
        if self.config.polishing_mode_selection == "translated_text_polish":
            self.translation_text_dict = {str(i): v.translated_text for i, v in enumerate(self.items)}

        # 生成文本行数信息
        self.row_count = len(self.source_text_dict)

        # 译前处理
        self.source_text_dict = self.text_processor.replace_all(
            self.config,
            self.source_text_dict
            )
        if self.config.polishing_mode_selection == "translated_text_polish":
            self.translation_text_dict = self.text_processor.replace_all(
                self.config,
                self.translation_text_dict
                )

        # 生成请求指令
        self.messages, self.system_prompt, self.extra_log = PromptBuilderPolishing.generate_prompt(
            self.config,
            self.source_text_dict,
            self.translation_text_dict,
            self.previous_text_list,
        )

        # 预估 Token 消费
        self.request_tokens_consume = Tokener.calculate_tokens(self,self.messages,self.system_prompt,)


    # 启动任务
    def start(self) -> dict:
        return self.unit_translation_task()

    # 单请求翻译任务
    def unit_translation_task(self) -> dict:
        session_id = getattr(self, "task_session_id", Base.current_task_session())
        
        wait_start_time = time.time()
        while True:
            # 检测是否收到停止翻译事件
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
                return {}

            # 检查 RPM 和 TPM 限制，如果符合条件，则继续
            if self.request_limiter.check_limiter(self.request_tokens_consume):
                break
            
            # Prevent infinite wait
            if time.time() - wait_start_time > 600:
                self.error(f"[{self.task_id}] Queue wait timeout. Skipping.")
                return {}

            # 如果以上条件都不符合，则间隔 0.1 秒再次检查
            time.sleep(0.1)

        # 任务开始的时间 (真正开始处理，通过限流后)
        task_start_time = time.time()

        # Log source text for UI feedback (Moved to after rate limit)
        if Base.work_status != Base.STATUS.STOPING and Base.is_task_session_active(session_id):
            source_preview = list(self.source_text_dict.values())
            if source_preview:
                preview_text = source_preview[0][:50] + "..." if len(source_preview[0]) > 50 else source_preview[0]
                if len(source_preview) > 1:
                    preview_text += f" (+{len(source_preview)-1} lines)"
                self.print(f"[dim][{self.task_id}] Polishing: {preview_text}[/dim]")
                self.print(f"[STATUS] [{self.task_id}] Polishing: {preview_text}")

        # --- NEW: Dry Run Logic ---
        if self.config.get("enable_dry_run", True) and not getattr(Base, "_dry_run_done", False):
            dry_run_lock = getattr(Base, "_dry_run_lock", threading.Lock())
            Base._dry_run_lock = dry_run_lock
            
            with dry_run_lock:
                if not getattr(Base, "_dry_run_done", False):
                    self.print(f"\n[bold yellow]{Base.i18n.get('msg_dry_run_title')}[/bold yellow]")
                    self.print(f"[dim]{Base.i18n.get('msg_dry_run_hint')}[/dim]")
                    self.print(f"[blue]SYSTEM:[/blue]\n{self.system_prompt}")
                    if self.messages:
                        self.print(f"\n[green]USER (First Item):[/green]\n{self.messages[0].get('content')}")
                    
                    # Handle input using Base.global_input_queue
                    self.print(f"\n[bold magenta]{Base.i18n.get('msg_dry_run_confirm')} (y/n): [/bold magenta]")
                    
                    user_response = None
                    wait_start = time.time()
                    
                    while True:
                        if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
                            user_response = False
                            break
                            
                        # Check timeout (e.g. 120s)
                        if time.time() - wait_start > 120:
                            self.print("[dim]Dry run timeout, auto-confirming...[/dim]")
                            user_response = True
                            break
                            
                        try:
                            # Read from the shared queue populated by CLI InputListener
                            import queue
                            key = Base.global_input_queue.get_nowait()
                            if key in ['y', 'Y', '\r', '\n']:
                                user_response = True
                                break
                            elif key in ['n', 'N', 'q']:
                                if key == 'q': self.signal_handler(None, None)
                                user_response = False
                                break
                        except queue.Empty:
                            time.sleep(0.1)
                    
                    if not user_response:
                        # Stop task if user cancels
                        Base.cancel_active_task_session()
                        Base.work_status = Base.STATUS.STOPING
                        return {}
                    
                    Base._dry_run_done = True
        # --- End Dry Run ---

        # ---------------------------------------------------------
        # API 请求重试循环 (Failover Loop)
        # ---------------------------------------------------------
        response_content = None
        prompt_tokens = 0
        completion_tokens = 0
        response_think = None

        while True:
            # 0. 检查停止信号
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
                return {"check_result": False, "row_count": 0, "prompt_tokens": 0, "completion_tokens": 0}

            # 1. 获取最新配置
            platform_config = self.config.get_platform_configuration("polishingReq")
            current_api = platform_config.get("target_platform", "Unknown")
            is_local = current_api.lower() in ["localllm", "sakura", "murasaki"]

            # 2. 发起请求
            requester = LLMRequester()
            skip, status_tag, error_msg, p_tokens, c_tokens = requester.sent_request(
                self.messages,
                self.system_prompt,
                platform_config
            )

            if not Base.is_task_session_active(session_id):
                return {}

            # 3. 处理失败
            if skip:
                self.request_tokens_consume = p_tokens if p_tokens else self.request_tokens_consume

                # 如果是用户停止，直接静默返回
                if status_tag == "STOPPED" or Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
                    return {}

                # Failover logic
                if status_tag == "API_FAIL" and self.config.enable_api_failover and not is_local:
                    self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "fixing"})
                    self.emit(Base.EVENT.TASK_API_STATUS_REPORT, {"is_success": False})
                    self.print(f"[yellow][{self.task_id}] API Error ({current_api}): {error_msg}. Retrying in 2s...[/yellow]")
                    self.print(f"[STATUS] [{self.task_id}] API Error. Retrying...")
                    time.sleep(2)
                    continue 
                
                else:
                    self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "error"})
                    error = f"API请求错误 ({status_tag})，回复为空或出错，将在下一轮次重试"
                    self.print(
                        self.generate_log_table(
                            *self.generate_log_rows(
                                error,
                                task_start_time,
                                p_tokens if p_tokens else 0,
                                0,
                                [],
                                [],
                                [f"Error: {error_msg}"]
                            )
                        )
                    )
                    return {
                        "check_result": False,
                        "row_count": 0,
                        "prompt_tokens": self.request_tokens_consume,
                        "completion_tokens": 0,
                        "task_session_id": session_id,
                    }

            # 4. 处理成功
            if not Base.is_task_session_active(session_id):
                return {}
            self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "normal"})
            self.emit(Base.EVENT.TASK_API_STATUS_REPORT, {"is_success": True})
            response_content = error_msg 
            response_think = status_tag
            prompt_tokens = p_tokens
            completion_tokens = c_tokens
            break

        # 0.5 检查停止信号
        if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
            return {"check_result": False, "row_count": 0, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

        # ---------------------------------------------------------
        # 后续处理
        # ---------------------------------------------------------

        # 返空判断
        if response_content is None or not response_content.strip():
            error = "API请求错误，模型回复内容为空，将在下一轮次重试"
            self.print(
                self.generate_log_table(
                    *self.generate_log_rows(
                        error,
                        task_start_time,
                        prompt_tokens if prompt_tokens is not None else self.request_tokens_consume,
                        0,
                        [],
                        [],
                        []
                    )
                )
            )
            return {
                "check_result": False,
                "row_count": 0,
                "prompt_tokens": self.request_tokens_consume,
                "completion_tokens": 0,
                "task_session_id": session_id,
            }

        if not Base.is_task_session_active(session_id):
            return {}

        # 根据润色模式调整文本对象
        if self.config.polishing_mode_selection == "source_text_polish":
            # 如果是源文本润色模式，则直接使用源文本字典
            text_dict = self.source_text_dict
        elif self.config.polishing_mode_selection == "translated_text_polish":
            # 如果是译文润色模式，则使用译文文本字典
            text_dict = self.translation_text_dict

        # 提取回复内容
        response_dict = ResponseExtractor.text_extraction(self, text_dict, response_content)
        response_dict = ResponseExtractor.normalize_numbered_prefixes(self, response_dict, text_dict)

        if not Base.is_task_session_active(session_id):
            return {}

        # 检查回复内容
        check_result, error_content = ResponseChecker.check_polish_response_content(
            self,
            self.config,
            response_content,
            response_dict,
            text_dict
        )

        if not Base.is_task_session_active(session_id):
            return {}

        # 去除回复内容的数字序号
        response_dict = ResponseExtractor.remove_numbered_prefix(self, response_dict, text_dict)


        # 模型回复日志
        if response_think:
            self.extra_log.append("模型思考内容：\n" + response_think)
        if self.is_debug():
            self.extra_log.append("模型回复内容：\n" + response_content)

        # 检查译文
        restore_response_dict = {}
        if check_result == False:
            error = f"[{self.task_id}] [ERROR] 译文文本未通过检查，将在下一轮次的翻译中重新翻译 - {error_content}"

            # 打印任务结果
            if self.is_debug() and not self.config.show_detailed_logs:
                self.print(
                    self.generate_log_table(
                        *self.generate_log_rows(
                            error,
                            task_start_time,
                            prompt_tokens,
                            completion_tokens,
                            text_dict.values(),
                            response_dict.values(),
                            self.extra_log,
                        )
                    )
                )
            else:
                self.error(error)
        else:
            # 各种翻译后处理
            restore_response_dict = copy.copy(response_dict)
            restore_response_dict = self.text_processor.restore_all(self.config, restore_response_dict)

        if not Base.is_task_session_active(session_id):
            return {}

        # 2. 强制发送 TUI 数据 (双通道)
        if Base.is_task_session_active(session_id) and (restore_response_dict or self.source_text_dict):
            all_res = "\n".join(restore_response_dict.values()) if restore_response_dict else "[Error: No Data]"
            source_preview = list(self.source_text_dict.values())
            all_source = "\n".join(source_preview) if source_preview else ""

            # 通道1: 事件总线 (用于宿主进程/监控模式)
            self.emit(Base.EVENT.TUI_RESULT_DATA, {"source": all_source, "data": all_res})
            if not Base.is_task_session_active(session_id):
                return {}

            # 通道2: 网页端同步
            import os as system_os
            try:
                webserver_port = getattr(self.config, "webserver_port", 8000)
                internal_api_base = system_os.environ.get("AINIEE_INTERNAL_API_URL", f"http://127.0.0.1:{webserver_port}")
                if not Base.is_task_session_active(session_id):
                    return {}
                requests.post(
                    f"{internal_api_base}/api/internal/update_comparison",
                    json={"source": all_source, "translation": all_res},
                    timeout=1
                )
            except:
                pass

            # 更新译文结果到缓存数据中
            if not Base.is_task_session_active(session_id):
                return {}
            for item, response in zip(self.items, restore_response_dict.values()):
                if not Base.is_task_session_active(session_id):
                    return {}
                with item.atomic_scope():
                    if not Base.is_task_session_active(session_id):
                        return {}
                    item.model = self.config.model
                    item.polished_text = response
                    item.translation_status = TranslationStatus.POLISHED


            # 打印任务结果
            if Base.work_status != Base.STATUS.STOPING and Base.is_task_session_active(session_id):
                self.print(f"[bold green]√ [{self.task_id}] Done! ({self.row_count} lines processed) | {(time.time() - task_start_time):.2f}s | {prompt_tokens}+{completion_tokens}T[/bold green]")
                # 对照模式下隐藏详细表格以节省空间
                if self.is_debug() and not self.config.show_detailed_logs:
                    self.print(
                        self.generate_log_table(
                            *self.generate_log_rows(
                                f"[{self.task_id}] 任务结果",
                                task_start_time,
                                prompt_tokens,
                                completion_tokens,
                                text_dict.values(),
                                response_dict.values(),
                                self.extra_log,
                            )
                        )
                    )


        # 否则返回译文检查的结果
        if check_result == False:
            return {
                "check_result": False,
                "row_count": 0,
                "prompt_tokens": self.request_tokens_consume,
                "completion_tokens": 0,
                "extra_info": getattr(self, "extra_info", {}),
                "task_session_id": session_id,
            }
        else:
            return {
                "check_result": check_result,
                "row_count": self.row_count,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "extra_info": getattr(self, "extra_info", {}),
                "task_session_id": session_id,
            }



    # 生成日志行
    def generate_log_rows(self, error: str, start_time: int, prompt_tokens: int, completion_tokens: int, source: list[str], translated: list[str], extra_log: list[str]) -> tuple[list[str], bool]:
        rows = []

        if error != "":
            rows.append(error)
        else:
            rows.append(
                f"任务耗时 {(time.time() - start_time):.2f} 秒，"
                + f"文本行数 {len(source)} 行，提示消耗 {prompt_tokens} Tokens，补全消耗 {completion_tokens} Tokens"
            )

        # 添加额外日志
        for v in extra_log:
            rows.append(v.strip())

        # 原文译文对比
        pair = ""
        # 修复变量名冲突问题，将循环变量改为 s 和 t
        for idx, (s, t) in enumerate(itertools.zip_longest(source, translated, fillvalue=""), 1):
            pair += f"\n"
            # 处理原文和译文的换行，分割成多行
            s_lines = s.split('\n') if s is not None else ['']
            t_lines = t.split('\n') if t is not None else ['']
            # 逐行对比，确保对齐
            for s_line, t_line in itertools.zip_longest(s_lines, t_lines, fillvalue=""):
                pair += f"{s_line} [bright_blue]-->[/] {t_line}\n"

        rows.append(pair.strip())

        return rows, error == ""

    # 生成日志表格
    def generate_log_table(self, rows: list, success: bool) -> Table:
        table = Table(
            box = box.ASCII2,
            expand = True,
            title = " ",
            caption = " ",
            highlight = True,
            show_lines = True,
            show_header = False,
            show_footer = False,
            collapse_padding = True,
            border_style = "green" if success else "red",
        )
        table.add_column("", style = "white", ratio = 1, overflow = "fold")

        for row in rows:
            if isinstance(row, str):
                table.add_row(escape(row, re.compile(r"(\\*)(\[(?!bright_blue\]|\/\])[a-z#/@][^[]*?)").sub)) # 修复rich table不显示[]内容问题
            else:
                table.add_row(*row)

        return table
