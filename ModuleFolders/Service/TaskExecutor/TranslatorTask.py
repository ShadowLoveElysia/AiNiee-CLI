import copy
import threading
import re
import time
import itertools

from rich import box
from rich.table import Table
from rich.markup import escape

from ModuleFolders.Base.Base import Base
from ModuleFolders.Base.PluginManager import PluginManager
from ModuleFolders.Infrastructure.Cache.CacheItem import CacheItem, TranslationStatus
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
from ModuleFolders.Domain.PromptBuilder.PromptBuilder import PromptBuilder
from ModuleFolders.Domain.PromptBuilder.PromptBuilderLocal import PromptBuilderLocal
from ModuleFolders.Domain.PromptBuilder.PromptBuilderSakura import PromptBuilderSakura
from ModuleFolders.Domain.ResponseExtractor.ResponseExtractor import ResponseExtractor
from ModuleFolders.Domain.ResponseChecker.ResponseChecker import ResponseChecker
from ModuleFolders.Infrastructure.RequestLimiter.RequestLimiter import RequestLimiter
from ModuleFolders.Infrastructure.Tokener.Tokener import Tokener

from ModuleFolders.Domain.TextProcessor.TextProcessor import TextProcessor


class TranslatorTask(Base):

    def __init__(self, config: TaskConfig, plugin_manager: PluginManager, request_limiter: RequestLimiter, source_lang) -> None:
        super().__init__()

        self.config = config
        self.plugin_manager = plugin_manager
        self.request_limiter = request_limiter
        self.text_processor = TextProcessor(self.config) # 文本处理器

        # 源语言对象
        self.source_lang = source_lang

        # 提示词与信息内容存储
        self.messages = []
        self.system_prompt = ""

        # 输出日志存储
        self.extra_log = []
        # 前后缀处理信息存储
        self.prefix_codes = {}
        self.suffix_codes = {}
        # 占位符顺序存储结构
        self.placeholder_order = {}
        # 前后换行空格处理信息存储
        self.affix_whitespace_storage = {}


    # 设置缓存数据
    def set_items(self, items: list[CacheItem]) -> None:
        self.items = items

    # 设置上文数据
    def set_previous_items(self, previous_items: list[CacheItem]) -> None:
        self.previous_items = previous_items

    # 消息构建预处理
    def prepare(self, target_platform: str) -> None:

        # 生成上文文本列表
        self.previous_text_list = [v.source_text for v in self.previous_items]

        # 生成原文文本字典
        self.source_text_dict = {str(i): v.source_text for i, v in enumerate(self.items)}

        # 生成文本行数信息
        self.row_count = len(self.source_text_dict)

        # 触发插件事件 - 文本正规化
        self.plugin_manager.broadcast_event("normalize_text", self.config, self.source_text_dict)

        # 触发插件事件 - RAG 上下文构建
        rag_context_data = {
            "source_text_dict": self.source_text_dict,
            "previous_text_list": self.previous_text_list,
            "rag_context": "" # 插件可以填充此字段
        }
        self.plugin_manager.broadcast_event("build_rag_context", self.config, rag_context_data)
        self.rag_context = rag_context_data.get("rag_context", "")

        # 各种替换步骤，译前替换，提取首尾与占位中间代码
        self.source_text_dict, self.prefix_codes, self.suffix_codes, self.placeholder_order, self.affix_whitespace_storage = \
            self.text_processor.replace_all(
                self.config,
                self.source_lang, 
                self.source_text_dict
            )
        
        # 生成请求指令
        if target_platform == "sakura":
            self.messages, self.system_prompt, self.extra_log = PromptBuilderSakura.generate_prompt_sakura(
                self.config,
                self.source_text_dict,
                self.previous_text_list, 
                self.source_lang, 
                self.rag_context
            )
        elif target_platform == "LocalLLM":
            self.messages, self.system_prompt, self.extra_log = PromptBuilderLocal.generate_prompt_LocalLLM(
                self.config,
                self.source_text_dict,
                self.previous_text_list,
                self.source_lang,
                self.rag_context
            )
        else:
            self.messages, self.system_prompt, self.extra_log = PromptBuilder.generate_prompt(
                self.config,
                self.source_text_dict,
                self.previous_text_list,
                self.source_lang,
                self.rag_context
            )

        # 预估 Token 消费
        self.request_tokens_consume = Tokener.calculate_tokens(self,self.messages,self.system_prompt,)


    # 启动任务
    def start(self) -> dict:
        return self.unit_translation_task()


    # 单请求翻译任务
    def unit_translation_task(self) -> dict:
        
        # Log source text for UI feedback
        source_preview = list(self.source_text_dict.values())
        if source_preview:
            preview_text = source_preview[0][:50] + "..." if len(source_preview[0]) > 50 else source_preview[0]
            if len(source_preview) > 1:
                preview_text += f" (+{len(source_preview)-1} lines)"
            self.print(f"[dim][{self.task_id}] Translating: {preview_text}[/dim]")
            self.print(f"[STATUS] [{self.task_id}] Translating: {preview_text}")

        wait_start_time = time.time()
        while True:
            # 检测是否收到停止翻译事件
            if Base.work_status == Base.STATUS.STOPING:
                return {}

            # 检查 RPM 和 TPM 限制，如果符合条件，则继续
            if self.request_limiter.check_limiter(self.request_tokens_consume):
                break
            
            # 防止无限等待死锁
            if time.time() - wait_start_time > 600:
                 self.error(f"[{self.task_id}] Queue wait timeout (10m). Skipping.")
                 return {}

            # 如果以上条件都不符合，则间隔 1 秒再次检查
            time.sleep(1)
            
        # 任务开始的时间 (真正开始处理，通过限流后)
        task_start_time = time.time()

        # --- NEW: Dry Run Logic (Only once) ---
        if self.config.get("enable_dry_run", True) and not getattr(Base, "_dry_run_done", False):
            # Use a lock to ensure only one thread triggers dry run if multiple start simultaneously
            dry_run_lock = getattr(Base, "_dry_run_lock", threading.Lock())
            Base._dry_run_lock = dry_run_lock
            
            with dry_run_lock:
                if not getattr(Base, "_dry_run_done", False):
                    self.print(f"\n[bold yellow]{Base.i18n.get('msg_dry_run_title')}[/bold yellow]")
                    self.print(f"[dim]{Base.i18n.get('msg_dry_run_hint')}[/dim]")
                    self.print(f"[blue]SYSTEM:[/blue]\n{self.system_prompt}")
                    # Show the first message's content
                    if self.messages:
                        self.print(f"\n[green]USER (First Item):[/green]\n{self.messages[0].get('content')}")
                    
                    # Handle input using Base.global_input_queue
                    self.print(f"\n[bold magenta]{Base.i18n.get('msg_dry_run_confirm')} (y/n): [/bold magenta]")
                    
                    user_response = None
                    wait_start = time.time()
                    
                    while True:
                        if Base.work_status == Base.STATUS.STOPING:
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
            if Base.work_status == Base.STATUS.STOPING:
                return {"check_result": False, "row_count": 0, "prompt_tokens": 0, "completion_tokens": 0}

            # 1. 获取最新配置 (以防 API 已切换)
            platform_config = self.config.get_platform_configuration("translationReq")
            current_api = platform_config.get("target_platform", "Unknown")
            is_local = current_api.lower() in ["localllm", "sakura"]

            # 2. 发起请求
            requester = LLMRequester()
            skip, status_tag, error_msg, p_tokens, c_tokens = requester.sent_request(
                self.messages,
                self.system_prompt,
                platform_config
            )

            # 3. 处理失败
            if skip:
                # 记录 Token 消耗 (即使失败也可能消耗了)
                self.request_tokens_consume = p_tokens if p_tokens else self.request_tokens_consume

                # 如果是用户停止，直接静默返回
                if status_tag == "STOPPED" or Base.work_status == Base.STATUS.STOPING:
                    return {}

                # 判断是否为 API 错误且允许重试
                if status_tag == "API_FAIL" and self.config.enable_api_failover and not is_local:
                    # 触发 TUI 状态更新：修复中
                    self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "fixing"})
                    
                    # 上报失败，触发计数和可能的切换
                    self.emit(Base.EVENT.TASK_API_STATUS_REPORT, {"is_success": False})
                    
                    # 打印重试日志 (只发送 STATUS 消息，因为它会被 LogStream 拦截并处理)
                    self.print(f"[STATUS] [{self.task_id}] API Error ({current_api}): {error_msg}. Retrying...")
                    
                    time.sleep(2)
                    continue # 原地重试 (下一次循环会获取新的 platform_config)
                
                else:
                    # 无法重试的错误，触发 TUI 状态更新：错误
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
                    }

            # 4. 处理成功
            # 上报成功，触发 TUI 状态恢复
            self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "normal"})
            self.emit(Base.EVENT.TASK_API_STATUS_REPORT, {"is_success": True})
            
            # 赋值并跳出循环进行后续处理
            response_content = error_msg # 这里的 error_msg 实际上是 response_content，因为 LLMRequester 返回签名是 (skip, think, content...)
            response_think = status_tag # status_tag 实际上是 think
            # 修正变量名映射:
            # Original: skip, response_think, response_content, prompt_tokens, completion_tokens
            # Current:  skip, status_tag,     error_msg,        p_tokens,      c_tokens
            # If skip=False: status_tag=think, error_msg=content
            
            prompt_tokens = p_tokens
            completion_tokens = c_tokens
            break 
        
        # 0.5 检查停止信号
        if Base.work_status == Base.STATUS.STOPING:
            return {"check_result": False, "row_count": 0, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

        # ---------------------------------------------------------
        # 后续处理 (提取、检查、保存)
        # ---------------------------------------------------------

        # 返空判断 (Double check)
        if response_content is None or not response_content.strip():
             # Logic same as above for generic error
             return { "check_result": False, "row_count": 0, "prompt_tokens": prompt_tokens, "completion_tokens": 0 }

        # 提取回复内容
        response_dict = ResponseExtractor.text_extraction(self, self.source_text_dict, response_content)

        # 检查回复内容
        check_result, error_content = ResponseChecker.check_response_content(
            self,
            self.config,
            self.placeholder_order,
            response_content,
            response_dict,
            self.source_text_dict,
            self.source_lang
        )

        # 去除回复内容的数字序号
        response_dict = ResponseExtractor.remove_numbered_prefix(self, response_dict)


        # 模型回复日志
        if response_think:
            self.extra_log.append("模型思考内容：\n" + response_think)
        if self.is_debug():
            self.extra_log.append("模型回复内容：\n" + response_content)

        # 检查译文
        if check_result == False:
            error = f"[{self.task_id}] [ERROR] 译文文本未通过检查，将在下一轮次的翻译中重新翻译 - {error_content}"

            # 打印任务结果
            if self.is_debug() or self.config.show_detailed_logs:
                self.print(
                    self.generate_log_table(
                        *self.generate_log_rows(
                            error,
                            task_start_time,
                            prompt_tokens,
                            completion_tokens,
                            self.source_text_dict.values(),
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
            restore_response_dict = self.text_processor.restore_all(self.config, restore_response_dict, self.prefix_codes, self.suffix_codes, self.placeholder_order, self.affix_whitespace_storage)

            # 更新译文结果到缓存数据中
            for item, response in zip(self.items, restore_response_dict.values()):
                with item.atomic_scope():
                    item.model = self.config.model
                    item.translated_text = response
                    item.translation_status = TranslationStatus.TRANSLATED


            # 打印任务结果
            if Base.work_status != Base.STATUS.STOPING:
                self.print(f"[bold green]√ [{self.task_id}] Done! ({self.row_count} lines processed) | {(time.time() - task_start_time):.2f}s | {prompt_tokens}+{completion_tokens}T[/bold green]")
                if self.is_debug() or self.config.show_detailed_logs:
                    self.print(
                        self.generate_log_table(
                            *self.generate_log_rows(
                                f"[{self.task_id}] 任务结果",
                                task_start_time,
                                prompt_tokens,
                                completion_tokens,
                                self.source_text_dict.values(),
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
                "extra_info": getattr(self, "extra_info", {})
            }
        else:
            return {
                "check_result": check_result,
                "row_count": self.row_count,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "extra_info": getattr(self, "extra_info", {})
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
