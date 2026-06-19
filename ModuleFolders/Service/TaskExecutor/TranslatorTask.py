import copy
import threading
import re
import time
import requests
import itertools
import os

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
        # 原文上下文数据（用于上下文增强）
        self.source_context_items = []
        # 角色召回上下文仅用于本地判断，不会直接进入 LLM 提示词。
        self.character_recall_previous_items = []
        self.character_recall_lookahead_items = []
        self._prepared = False
        self.consistency_context_provider = None
        self.consistency_state_updater = None
        self.consistency_context = {}
        self._pending_consistency_update = None
        self.translation_memory_provider = None
        self.translation_memory_references = []


    # 设置缓存数据
    def set_items(self, items: list[CacheItem]) -> None:
        self.items = items

    # 设置上文数据
    def set_previous_items(self, previous_items: list[CacheItem]) -> None:
        self.previous_items = previous_items

    # 设置原文上下文数据（用于上下文增强）
    def set_source_context_items(self, source_context_items: list[CacheItem]) -> None:
        self.source_context_items = source_context_items

    def set_character_recall_items(self, previous_items: list[CacheItem], lookahead_items: list[CacheItem]) -> None:
        self.character_recall_previous_items = previous_items or []
        self.character_recall_lookahead_items = lookahead_items or []

    def set_consistency_context_provider(self, provider) -> None:
        self.consistency_context_provider = provider

    def set_consistency_state_updater(self, updater) -> None:
        self.consistency_state_updater = updater

    def set_translation_memory_provider(self, provider) -> None:
        self.translation_memory_provider = provider

    # 消息构建预处理
    def prepare(self, target_platform: str) -> None:

        # 生成上文文本列表
        self.previous_text_list = [v.source_text for v in self.previous_items]

        # 生成原文上下文文本列表（用于上下文增强）
        self.source_context_text_list = [v.source_text for v in self.source_context_items]

        self.character_recall_previous_text_list = [v.source_text for v in self.character_recall_previous_items]
        self.character_recall_lookahead_text_list = [v.source_text for v in self.character_recall_lookahead_items]

        if callable(self.consistency_context_provider):
            try:
                self.consistency_context = self.consistency_context_provider() or {}
            except Exception:
                self.consistency_context = {}
        else:
            self.consistency_context = {}

        # 生成原文文本字典
        self.source_text_dict = {str(i): v.source_text for i, v in enumerate(self.items)}

        # 生成文本行数信息
        self.row_count = len(self.source_text_dict)

        # 触发插件事件 - 文本正规化
        self.plugin_manager.broadcast_event("normalize_text", self.config, self.source_text_dict)

        if callable(self.translation_memory_provider):
            try:
                self.translation_memory_references = self.translation_memory_provider(self.source_text_dict) or []
            except Exception:
                self.translation_memory_references = []
        else:
            self.translation_memory_references = []

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
        
        prompt_config = self._config_for_prompt()

        # 生成请求指令
        if target_platform == "sakura":
            self.messages, self.system_prompt, self.extra_log = PromptBuilderSakura.generate_prompt_sakura(
                prompt_config,
                self.source_text_dict,
                self.previous_text_list,
                self.source_lang,
                self.rag_context,
                self.translation_memory_references,
            )
        elif target_platform == "LocalLLM":
            self.messages, self.system_prompt, self.extra_log = PromptBuilderLocal.generate_prompt_LocalLLM(
                prompt_config,
                self.source_text_dict,
                self.previous_text_list,
                self.source_lang,
                self.rag_context,
                self.translation_memory_references,
            )
        else:
            self.messages, self.system_prompt, self.extra_log = PromptBuilder.generate_prompt(
                prompt_config,
                self.source_text_dict,
                self.previous_text_list,
                self.source_lang,
                self.rag_context,
                self.source_context_text_list,
                self.consistency_context,
                self.character_recall_previous_text_list,
                self.character_recall_lookahead_text_list,
                self.translation_memory_references,
            )

        # 预估 Token 消费
        self.request_tokens_consume = Tokener.calculate_tokens(self,self.messages,self.system_prompt,)
        self._prepared = True

    def _config_for_prompt(self):
        dynamic_volume = self._dynamic_glossary_volume_for_file()
        if dynamic_volume is None:
            return self.config

        if hasattr(self.config, "clone"):
            prompt_config = self.config.clone()
        else:
            prompt_config = copy.deepcopy(self.config)
        prompt_config.dynamic_glossary_volume = dynamic_volume
        return prompt_config

    def _dynamic_glossary_volume_for_file(self):
        if not getattr(self.config, "dynamic_glossary_switch", False):
            return None

        volume_map = getattr(self.config, "dynamic_glossary_volume_map", {}) or {}
        file_path = getattr(self, "file_path_full", "")
        volume = None
        if isinstance(volume_map, dict):
            volume = volume_map.get(file_path)
            if volume is None and file_path:
                volume = volume_map.get(os.path.basename(str(file_path)))
        if volume is None:
            volume = getattr(self.config, "dynamic_glossary_volume", None)
        return volume


    # 启动任务
    def start(self) -> dict:
        if not self._prepared:
            self.prepare(self.config.target_platform)
        return self.unit_translation_task()

    def _build_translation_consistency_tools(self) -> list[dict]:
        if self.config.target_language in ("chinese_simplified", "chinese_traditional"):
            description = "提交本轮译文，以及截至当前批次整理后的累计剧情梗概和累计人物信息。必须调用一次。"
            translation_desc = "最终译文，必须完整包裹在 <textarea>...</textarea> 中，只保留译文正文和编号，不要附加说明。"
            story_desc = "截至当前批次的累计剧情梗概，概括关键事件、场景推进和关系变化。"
            character_desc = "截至当前批次的累计人物信息，记录姓名、身份、关系、称呼、口癖、状态变化和译名约定。"
        else:
            description = "Submit the current translation plus cumulative story summary and cumulative character notes. This tool must be called exactly once."
            translation_desc = "Final translation wrapped in <textarea>...</textarea>. Keep only the numbered translation content without extra commentary."
            story_desc = "Cumulative story summary up to the current batch, including key events, scene progression, and relationship changes."
            character_desc = "Cumulative character notes up to the current batch, including names, roles, relationships, naming choices, verbal traits, and status changes."

        return [
            {
                "type": "function",
                "function": {
                    "name": "submit_translation_consistency",
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "translation": {"type": "string", "description": translation_desc},
                            "story_summary": {"type": "string", "description": story_desc},
                            "character_info": {"type": "string", "description": character_desc},
                        },
                        "required": ["translation", "story_summary", "character_info"],
                    },
                },
            }
        ]

    def _normalize_translation_from_tool(self, translation: str) -> str:
        translation_text = str(translation or "").strip()
        if not translation_text:
            return ""
        if "<textarea" in translation_text.lower():
            return translation_text
        return f"<textarea>\n{translation_text}\n</textarea>"


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
            
            # 防止无限等待死锁
            if time.time() - wait_start_time > 600:
                 self.error(f"[{self.task_id}] Queue wait timeout (10m). Skipping.")
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
                self.print(f"[dim][{self.task_id}] Translating: {preview_text}[/dim]")
                self.print(f"[STATUS] [{self.task_id}] Translating: {preview_text}")

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

            # 1. 获取最新配置 (以防 API 已切换)
            platform_config = self.config.get_platform_configuration("translationReq")
            current_api = platform_config.get("target_platform", "Unknown")
            is_local = current_api.lower() in ["localllm", "sakura", "murasaki"]
            consistency_enabled = bool(getattr(self.config, "translation_consistency_enhancement", False))
            error_msg = ""

            # 2. 发起请求
            if consistency_enabled:
                if platform_config.get("api_format") != "OpenAI":
                    skip = True
                    status_tag = "UNSUPPORTED_TOOL_CALLS"
                    error_msg = "翻译一致性增强仅支持 OpenAI 兼容的 Tool Calls 接口。"
                    p_tokens = 0
                    c_tokens = 0
                else:
                    from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester

                    tool_requester = OpenaiRequester()
                    skip, status_tag, tool_result, p_tokens, c_tokens = tool_requester.request_openai_tool_call(
                        self.messages,
                        self.system_prompt,
                        platform_config,
                        self._build_translation_consistency_tools(),
                        "submit_translation_consistency",
                    )

                    if skip:
                        error_msg = str(tool_result or "")
                    elif isinstance(tool_result, dict):
                        response_content = self._normalize_translation_from_tool(tool_result.get("translation", ""))
                        if not response_content:
                            skip = True
                            status_tag = "TOOL_CALL_EMPTY_TRANSLATION"
                            error_msg = "工具调用成功，但 translation 字段为空。"
                        else:
                            response_think = status_tag
                            prompt_tokens = p_tokens
                            completion_tokens = c_tokens
                            self._pending_consistency_update = {
                                "story_summary": str(tool_result.get("story_summary") or "").strip(),
                                "character_info": str(tool_result.get("character_info") or "").strip(),
                            }
                            break
                    else:
                        skip = True
                        status_tag = "TOOL_CALL_PARSE_ERROR"
                        error_msg = "工具调用返回内容无法解析为结构化数据。"
            else:
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
                # 记录 Token 消耗 (即使失败也可能消耗了)
                self.request_tokens_consume = p_tokens if p_tokens else self.request_tokens_consume

                # 如果是用户停止，直接静默返回
                if status_tag == "STOPPED" or Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
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
            if not Base.is_task_session_active(session_id):
                return {}
            # 上报成功，触发 TUI 状态恢复
            self.emit(Base.EVENT.SYSTEM_STATUS_UPDATE, {"status": "normal"})
            self.emit(Base.EVENT.TASK_API_STATUS_REPORT, {"is_success": True})
            
            # 赋值并跳出循环进行后续处理
            response_content = error_msg # 这里的 error_msg 实际上是 response_content，因为 LLMRequester 返回签名是 (skip, think, content...)
            response_think = status_tag # status_tag 实际上是 think
            prompt_tokens = p_tokens
            completion_tokens = c_tokens
            break 
        
        # 0.5 检查停止信号
        if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active(session_id):
            return {"check_result": False, "row_count": 0, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

        # ---------------------------------------------------------
        # 后续处理 (提取、检查、保存)
        # ---------------------------------------------------------

        # 返空判断 (Double check)
        if response_content is None or not response_content.strip():
             # Logic same as above for generic error
             return { "check_result": False, "row_count": 0, "prompt_tokens": prompt_tokens, "completion_tokens": 0 }

        if not Base.is_task_session_active(session_id):
            return {}

        # 提取回复内容
        response_dict = ResponseExtractor.text_extraction(self, self.source_text_dict, response_content)
        response_dict = ResponseExtractor.normalize_numbered_prefixes(self, response_dict, self.source_text_dict)

        if not Base.is_task_session_active(session_id):
            return {}

        # 获取漏翻检测重试次数（从第一个item的extra中获取）
        untranslated_retry_count = 0
        if self.items:
            untranslated_retry_count = self.items[0].extra.get('untranslated_retry_count', 0)

        # 检查回复内容
        check_result, error_content = ResponseChecker.check_response_content(
            self,
            self.config,
            self.placeholder_order,
            response_content,
            response_dict,
            self.source_text_dict,
            self.source_lang,
            untranslated_retry_count
        )

        if not Base.is_task_session_active(session_id):
            return {}

        # 去除回复内容的数字序号
        response_dict = ResponseExtractor.remove_numbered_prefix(self, response_dict, self.source_text_dict)

        # ---------------------------------------------------------
        # 结果处理与数据发送
        # ---------------------------------------------------------
        
        # 1. 后处理与恢复
        restore_response_dict = {}
        if response_dict:
            try:
                temp_dict = copy.copy(response_dict)
                restore_response_dict = self.text_processor.restore_all(self.config, temp_dict, self.prefix_codes, self.suffix_codes, self.placeholder_order, self.affix_whitespace_storage)
            except Exception as e:
                self.error(f"[{self.task_id}] Post-processing error: {e}")
                restore_response_dict = response_dict

        if not Base.is_task_session_active(session_id):
            return {}

        # 2. 强制发送 TUI 数据 (双通道)
        if Base.is_task_session_active(session_id) and (restore_response_dict or self.source_text_dict):
            all_trans = "\n".join(restore_response_dict.values()) if restore_response_dict else "[Error: No Data]"
            source_preview = list(self.source_text_dict.values())
            all_source = "\n".join(source_preview) if source_preview else ""

            # 通道1: 事件总线 (用于宿主进程/监控模式)
            self.emit(Base.EVENT.TUI_RESULT_DATA, {"source": all_source, "data": all_trans})
            if not Base.is_task_session_active(session_id):
                return {}
            
            # 通道2: 网页端同步 (直接调用 WebServer 内部接口)
            import os as system_os
            try:
                # 动态获取父进程传递的 WebServer 地址
                webserver_port = getattr(self.config, "webserver_port", 8000)
                internal_api_base = system_os.environ.get("AINIEE_INTERNAL_API_URL", f"http://127.0.0.1:{webserver_port}")
                if not Base.is_task_session_active(session_id):
                    return {}
                requests.post(
                    f"{internal_api_base}/api/internal/update_comparison",
                    json={"source": all_source, "translation": all_trans},
                    timeout=1
                )
            except:
                pass

        # 3. 模型回复日志
        if response_think:
            self.extra_log.append("模型思考内容：\n" + response_think)
        if self.is_debug():
            self.extra_log.append("模型回复内容：\n" + response_content)

        # 4. 检查译文并决定返回 (完全恢复原始逻辑结构)
        if check_result == False:
            error = f"[{self.task_id}] [ERROR] 译文文本未通过检查，将在下一轮次的翻译中重新翻译 - {error_content}"

            # 如果是漏翻检测失败，增加重试计数
            if "漏翻检测" in error_content:
                for item in self.items:
                    if not Base.is_task_session_active(session_id):
                        return {}
                    with item.atomic_scope():
                        if not Base.is_task_session_active(session_id):
                            return {}
                        current_count = item.extra.get('untranslated_retry_count', 0)
                        item.extra['untranslated_retry_count'] = current_count + 1

            # 打印任务结果
            if self.is_debug() and not self.config.show_detailed_logs:
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
                
            return {
                "check_result": False,
                "row_count": 0,
                "prompt_tokens": self.request_tokens_consume,
                "completion_tokens": 0,
                "extra_info": getattr(self, "extra_info", {}),
                "task_session_id": session_id,
            }
        else:
            if not Base.is_task_session_active(session_id):
                return {}
            # 更新译文结果到缓存数据中
            for item, response in zip(self.items, restore_response_dict.values()):
                if not Base.is_task_session_active(session_id):
                    return {}
                with item.atomic_scope():
                    if not Base.is_task_session_active(session_id):
                        return {}
                    item.model = self.config.model
                    item.translated_text = response
                    item.translation_status = TranslationStatus.TRANSLATED

            if not Base.is_task_session_active(session_id):
                return {}

            if self._pending_consistency_update and callable(self.consistency_state_updater):
                try:
                    self.consistency_state_updater(
                        self._pending_consistency_update.get("story_summary", ""),
                        self._pending_consistency_update.get("character_info", ""),
                    )
                except Exception as e:
                    self.warning(f"[{self.task_id}] Failed to update consistency memory: {e}")

            # 打印成功日志
            if Base.work_status != Base.STATUS.STOPING and Base.is_task_session_active(session_id):
                self.print(f"[bold green]√ [{self.task_id}] Done! ({self.row_count} lines processed) | {(time.time() - task_start_time):.2f}s | {prompt_tokens}+{completion_tokens}T[/bold green]")
                # 在对照模式下，不打印详细的表格日志，避免 TUI 日志区过于拥挤
                if self.is_debug() and not self.config.show_detailed_logs:
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
