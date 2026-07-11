import os
import re
import threading
import urllib
import copy

import rapidjson as json

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.LLMRequester.SdkRequestMode import sync_sdk_request_mode_config
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.Infrastructure.TaskConfig.PolishingMode import (
    POLISH_TRANSLATED_TEXT,
    normalize_polishing_mode,
)
from .default_config import DEFAULT_CONFIG

RULE_CHILD_SWITCH_KEYS = (
    "pre_translation_switch",
    "post_translation_switch",
    "exclusion_list_switch",
    "characterization_switch",
    "world_building_switch",
    "writing_style_switch",
    "translation_example_switch",
)

RULE_RUNTIME_DATA_KEYS = (
    "pre_translation_data",
    "post_translation_data",
    "prompt_dictionary_data",
    "exclusion_list_data",
    "characterization_data",
    "world_building_content",
    "writing_style_content",
    "translation_example_data",
)

# 接口请求器
class TaskConfig(Base):

    # 打印时的类型过滤器
    TYPE_FILTER = (int, str, bool, float, list, dict, tuple)

    def __init__(self) -> None:
        super().__init__()
        
        # 初始化实例级线程锁和密钥索引
        self._config_lock = threading.Lock()

        self._api_key_lock = threading.Lock()
        self.apikey_index = 0
        self.apikey_list = []

        # --- 初始化默认属性以防止 AttributeError ---
        
        # 文本处理相关数据
        self.pre_translation_data = []
        self.post_translation_data = []
        self.exclusion_list_data = [] 
        self.auto_process_text_code_segment = False
        self.pre_translation_switch = False
        self.post_translation_switch = False
        self.prompt_dictionary_switch = False
        self.prompt_dictionary_data = []
        self.exclusion_list_switch = False
        self.characterization_switch = False
        self.characterization_data = []
        self.world_building_switch = False
        self.world_building_content = ""
        self.writing_style_switch = False
        self.writing_style_content = ""
        self.translation_example_switch = False
        self.translation_example_data = []
        self.world_building_history = []
        self.writing_style_history = []
        self.dynamic_glossary_switch = False
        self.dynamic_glossary_series = ""
        self.dynamic_glossary_volume = None
        self.dynamic_glossary_volume_map = {}
        self.character_recall_switch = True
        self.character_recall_context_lines = 50
        self.character_recall_lookahead_lines = 10
        self.character_recall_max_strong = 5
        self.character_recall_max_candidates = 2
        self.character_recall_min_strong_score = 80
        self.character_recall_min_candidate_score = 40
        self.translation_memory_switch = False
        self.few_shot_and_example_switch = False
        self.main_menu_layout = "flat"

        # 故障转移配置
        self.enable_api_failover = False
        self.backup_apis = []
        self.api_failover_threshold = 3

        # 平台和 API 设置默认值
        self.platforms = {}
        self.api_settings = {}
        self.translation_prompt_selection = {}
        self.polishing_prompt_selection = {}
        self.user_thread_counts = 0
        self.auto_set_output_path = False
        self.request_timeout = 60

        # 响应检查相关设置
        self.response_check_switch = {
            'newline_character_count_check': False,
            'return_to_original_text_check': False,
            'residual_original_text_check': False,
            'reply_format_check': False
        }

        self.show_detailed_logs = False # Fix: Initialize show_detailed_logs

        # 文件输出相关设置
        self.keep_original_encoding = True

        # 翻译和润色相关设置
        self.source_language = "auto"
        self.target_language = "zh"
        self.label_input_path = ""
        self.label_output_path = ""
        self.translated_output_path = ""
        self.polishing_output_path = ""
        self.translation_project = "AutoType" # NEW: 项目类型设置
        self.target_platform = ""
        self.base_url = ""
        self.model = ""
        self.rpm_limit = 4096
        self.tpm_limit = 10000000
        
        # 任务执行相关设置
        self.retry_count = 3
        self.round_limit = 3 # Changed default from 0 to 3 to allow multiple passes
        self.enable_smart_round_limit = False # ADDED: Enable dynamic round limit adjustment
        self.smart_round_limit_multiplier = 2 # ADDED: Multiplier for dynamic round limit adjustment
        self.enable_fast_translate = False
        self.enable_line_breaks = False
        self.line_breaks_style = 0
        self.response_conversion_toggle = False # NEW: 简繁转换开关
        self.opencc_preset = "s2twp.json" # NEW: 简繁转换配置
        self.tokens_limit_switch = False # NEW: 切换 Token 模式还是 Line 模式
        self.lines_limit = 20 # NEW: 每次翻译的行数限制，在 Token 模式下无效
        self.chunk_soft_limit_extra_lines = 10
        self.line_split_optimization_mode = "off"
        self.retry_split_min_lines = 15
        self.tokens_limit = 1500 # NEW: 每次翻译的 Token 限制，在 Line 模式下无效
        self.pre_line_counts = 3 # NEW: 每次翻译获取上文的行数
        self.actual_thread_counts = 3 # NEW: 实际线程数
        self.output_filename_suffix = "" # NEW: 输出文件名后缀
        self.enable_bilingual_output = False # NEW: 是否启用双语输出
        self.bilingual_text_order = "translation_first" # NEW: 双语文本顺序
        self.epub_language_update_mode = "auto"
        self.polishing_mode_selection = POLISH_TRANSLATED_TEXT # NEW: 润色模式选择
        self.polishing_pre_line_counts = 2 # NEW: 润色时获取上文的行数
        self.proofread_report_mode = "archive"
        self.proofread_archive_limit = 20
        self.proofread_suggestion_mode = "proofread"
        self.cache_backup_limit = 10
        self.cache_save_interval = 8
        self.enable_cache_backup = True
        self.enable_auto_restore_ebook = True
        self.japanese_text_quote_style_switch = True
        self.enable_dry_run = False
        self.enable_retry_backoff = True
        self.enable_session_logging = True
        self.enable_task_notification = True
        self.enable_github_promotion = True
        self.enable_background_prewarm = True
        self.exclude_rule_str = ""
        self.think_switch = False
        self.think_depth = 0
        self.thinking_budget = -1
        self.structured_output_mode = 0
        self.enable_stream_api = True  # 流式API开关，默认启用
        self.sdk_request_mode = "httpx" # 请求模式：httpx/openai/anthropic/google
        self.use_openai_sdk = False     # 兼容旧配置：True 视为 OpenAI SDK
        self.manga_detect_engine = "comic-text-bubble-detector"
        self.manga_segment_engine = "comic-text-detector"
        self.manga_ocr_engine = "mit48px-ocr"
        self.manga_inpaint_engine = "aot-inpainting"
        self.manga_runtime_device = "auto"
        self.manga_detect_device = "auto"
        self.manga_ocr_device = "auto"
        self.manga_inpaint_device = "auto"
        self.show_detailed_logs = False # Fix: Initialize show_detailed_logs

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({self.get_vars()})"
        )

    def get_vars(self) -> dict:
        return {
            k:v
            for k, v in vars(self).items()
            if isinstance(v, __class__.TYPE_FILTER)
        }

    # 修复：添加 get 方法以兼容字典操作，解决 AttributeError
    def get(self, key, default=None):
        return getattr(self, key, default)

    def clone(self):
        cloned = TaskConfig()
        cloned.load_config_from_dict(copy.deepcopy(self.get_vars()))
        return cloned

    # 从字典加载配置 (用于从外部传入配置)
    def load_config_from_dict(self, config_dict: dict) -> None:
        explicit_sdk_request_mode = "sdk_request_mode" in config_dict
        sync_sdk_request_mode_config(config_dict, prefer_sdk_request_mode=explicit_sdk_request_mode)

        for key, value in config_dict.items():
            if key == "response_check_switch":
                if isinstance(value, dict):
                    current_value = getattr(self, key, {})
                    if isinstance(current_value, dict):
                        new_value = current_value.copy()
                        new_value.update(value)
                        setattr(self, key, new_value)
                    else:
                        setattr(self, key, value)
                continue

            if key == "platforms":
                if isinstance(value, dict):
                    # 对于 platforms 字典，进行深度合并而不是完全覆盖
                    current_platforms = getattr(self, key, {})
                    if isinstance(current_platforms, dict):
                        for platform_key, platform_value in value.items():
                            if platform_key in current_platforms and isinstance(current_platforms[platform_key], dict) and isinstance(platform_value, dict):
                                current_platforms[platform_key].update(platform_value)
                            else:
                                current_platforms[platform_key] = platform_value
                        setattr(self, key, current_platforms)
                    else:
                        setattr(self, key, value)
                continue

            if key == "api_settings":
                if isinstance(value, dict):
                    current_api_settings = getattr(self, key, {})
                    if isinstance(current_api_settings, dict):
                        current_api_settings.update(value)
                        setattr(self, key, current_api_settings)
                    else:
                        setattr(self, key, value)
                continue

            setattr(self, key, value)

        synced = sync_sdk_request_mode_config(
            {
                "sdk_request_mode": getattr(self, "sdk_request_mode", "httpx"),
                "use_openai_sdk": getattr(self, "use_openai_sdk", False),
            },
            prefer_sdk_request_mode=explicit_sdk_request_mode,
        )
        self.sdk_request_mode = synced["sdk_request_mode"]
        self.use_openai_sdk = synced["use_openai_sdk"]
        self._normalize_runtime_limits()
        self.polishing_mode_selection = normalize_polishing_mode(
            getattr(self, "polishing_mode_selection", POLISH_TRANSLATED_TEXT)
        )

    def _normalize_runtime_limits(self) -> None:
        try:
            self.lines_limit = max(1, min(100, int(getattr(self, "lines_limit", 20) or 20)))
        except (TypeError, ValueError):
            self.lines_limit = 20

        try:
            self.tokens_limit = max(400, min(16000, int(getattr(self, "tokens_limit", 1500) or 1500)))
        except (TypeError, ValueError):
            self.tokens_limit = 1500

    def get_next_apikey(self) -> str:
        """
        线程安全的轮询获取 API Key
        """
        with self._api_key_lock:
            if not self.apikey_list:
                return "no_key_required"
            
            # 边界检查
            if self.apikey_index >= len(self.apikey_list):
                self.apikey_index = 0

            key = self.apikey_list[self.apikey_index]

            # 更新索引（如果还有下一个 key，则递增，否则归零）
            if len(self.apikey_list) > 1:
                self.apikey_index = (self.apikey_index + 1) % len(self.apikey_list)

            return key

    # 读取配置文件
    def initialize(self, config_dict: dict) -> None:
        self.load_config_from_dict(config_dict)
        
        # 确保关键配置有合理的默认值，防止因配置文件缺失导致逻辑错误
        if not hasattr(self, 'request_timeout') or self.request_timeout <= 0:
            self.request_timeout = 60
            
        if not hasattr(self, 'actual_thread_counts') or self.actual_thread_counts <= 0:
            self.actual_thread_counts = 3

    def disable_rule_runtime_payload(self) -> None:
        """
        Apply the glossary/rules master switch to this in-memory runtime config.

        This intentionally does not write back to profile files. It only makes
        prompt construction, text preprocessing, and proofread requests ignore
        all rules when the master glossary switch is off.
        """
        if bool(getattr(self, "prompt_dictionary_switch", False)):
            return

        self.prompt_dictionary_data = []
        for switch_key in RULE_CHILD_SWITCH_KEYS:
            setattr(self, switch_key, False)
        for data_key in RULE_RUNTIME_DATA_KEYS:
            empty_value = "" if data_key in ("world_building_content", "writing_style_content") else []
            setattr(self, data_key, empty_value)

    # API_URL 自动处理方法
    def process_api_url(self, raw_url: str, target_platform: str, auto_complete: bool) -> str:
        if not raw_url:
            return ""

        # 1. 基础清洗
        url = raw_url.strip().rstrip('/')

        # 只有在开启自动补全或者特定本地平台时，才进行规范化裁剪
        # 否则尊重原始输入，防止用户手动填写的完整地址被破坏
        should_auto_process = (target_platform in ["sakura", "LocalLLM", "murasaki"]) or auto_complete

        if not should_auto_process:
            return url

        # 2. 裁剪后缀
        # 允许输入如: http://127.0.0.1:5000/v1/chat/completions -> 裁剪为 -> http://127.0.0.1:5000/v1
        redundant_suffixes = ["/chat/completions", "/completions", "/chat"]
        for suffix in redundant_suffixes:
            if url.endswith(suffix):
                url = url[:-len(suffix)]
                url = url.rstrip('/') # 再次去除可能暴露出来的斜杠
                break

        # 3. 自动补全 /v1 逻辑
        # 某些平台强制补全，或者配置开启了 auto_complete
        should_auto_complete = (target_platform in ["sakura", "LocalLLM", "murasaki"]) or auto_complete

        if should_auto_complete:
            version_suffixes = ["/v1", "/v2", "/v3", "/v4", "/v5", "/v6"]
            # 如果当前 URL 不以任何版本号结尾，则添加 /v1
            if not any(url.endswith(suffix) for suffix in version_suffixes):
                url += "/v1"

        # 4. 返回处理后的 URL
        return url

    # 准备翻译
    def prepare_for_translation(self,mode) -> None:
        self.disable_rule_runtime_payload()

        # 获取目标平台

        if mode == TaskType.TRANSLATION:
            self.target_platform = self.api_settings.get("translate")
        elif mode == TaskType.POLISH:
            self.target_platform = self.api_settings.get("polish")

        # 增加获取不到内容时的异常处理
        if self.target_platform is None:
            raise ValueError(f"当前配置文件中未设置 {mode} 的目标平台，请重新检查接口管理页面，是否设置了执行任务的接口。")

        # 获取平台配置引用，方便后续操作
        platform_conf = self.platforms.get(self.target_platform)
        if not platform_conf:
            raise ValueError(f"未找到平台 {self.target_platform} 的具体配置信息。")

        # --- 智能同步逻辑 ---
        # 优先使用实例属性 (来自 root config)，如果实例属性为空，则从平台配置中加载
        if not self.model:
            self.model = platform_conf.get("model")
        else:
            # 如果实例属性有值，反向同步到平台配置中，确保一致性
            platform_conf["model"] = self.model

        # 处理 API URL
        raw_url = platform_conf.get("api_url", "")
        auto_complete_setting = platform_conf.get("auto_complete", False)
        
        # 优先使用实例属性 (来自 root config)，如果实例属性为空，则从平台配置中加载
        target_url = self.base_url if self.base_url else raw_url
        
        # 无论如何都通过 process_api_url 处理一次，以确保 /v1 等后缀的一致性
        # 特别是当用户在菜单修改了 base_url 后，这里如果不处理会导致丢失自动补全逻辑
        self.base_url = self.process_api_url(target_url, self.target_platform, auto_complete_setting)
        
        # 同步回平台配置
        if self.base_url and not raw_url:
            platform_conf["api_url"] = target_url

        # 分割密钥字符串
        # 优先使用 root config 的 api_key (如果存在)
        api_key = getattr(self, "api_key", "")
        if not api_key:
            api_key = platform_conf.get("api_key", "")
        else:
            # 反向同步
            platform_conf["api_key"] = api_key

        if api_key == "":
            self.apikey_list = ["no_key_required"]
            self.apikey_index = 0
        else:
            self.apikey_list = re.sub(r"\s+","", api_key).split(",")
            self.apikey_index = 0

        # 获取接口限额
        self.rpm_limit = platform_conf.get("rpm_limit", 4096)
        self.tpm_limit = platform_conf.get("tpm_limit", 10000000)

        # 根据密钥数量给 RPM 和 TPM 限额翻倍
        self.rpm_limit = self.rpm_limit * len(self.apikey_list)
        self.tpm_limit = self.tpm_limit * len(self.apikey_list)

        # 如果开启自动设置输出文件夹功能，设置为输入文件夹的平级目录
        if self.auto_set_output_path == True:
            abs_input_path = os.path.abspath(self.label_input_path)
            parent_dir = os.path.dirname(abs_input_path)
            base_name = os.path.basename(abs_input_path)
            # 如果是文件，去除后缀
            if os.path.isfile(abs_input_path):
                base_name = os.path.splitext(base_name)[0]
            
            output_folder_name = f"{base_name}_AiNiee_Output"
            self.label_output_path = os.path.join(parent_dir, output_folder_name)

            # 润色文本输出路径
            output_folder_name = f"{base_name}_Polishing_Output"
            self.polishing_output_path = os.path.join(parent_dir, output_folder_name)

        # 计算实际线程数
        self.actual_thread_counts = self.thread_counts_setting(self.user_thread_counts,self.target_platform,self.rpm_limit)


    # 自动计算实际请求线程数
    def thread_counts_setting(self,user_thread_counts,target_platform,rpm_limit) -> None:
        # 如果用户指定了线程数，则使用用户指定的线程数
        if user_thread_counts > 0:
            actual_thread_counts = user_thread_counts

        # 如果是本地类接口，尝试访问slots数
        elif target_platform in ("sakura","LocalLLM","murasaki"):
            num = self.get_llama_cpp_slots_num(self.platforms.get(target_platform).get("api_url"))
            actual_thread_counts = num if num > 0 else 4
            self.info(f"根据 llama.cpp 接口信息，自动设置同时执行的翻译任务数量为 {actual_thread_counts} 个 ...")

        # 如果用户没有指定线程数，则自动计算
        else :
            actual_thread_counts = self.calculate_thread_count(rpm_limit)
            self.info(f"根据账号类型和接口限额，自动设置同时执行的翻译任务数量为 {actual_thread_counts} 个 ...")

        return actual_thread_counts

    # 获取 llama.cpp 的 slots 数量，获取失败则返回 -1
    def get_llama_cpp_slots_num(self,url: str) -> int:
        try:
            num = -1
            url = url.replace("/v1", "") if url.endswith("/v1") else url
            with urllib.request.urlopen(f"{url}/slots") as response:
                data = json.loads(response.read().decode("utf-8"))
                num = len(data) if data != None and len(data) > 0 else num
        except Exception:
            pass
        finally:
            return num
        
    # 线性计算并发线程数
    def calculate_thread_count(self,rpm_limit):

        min_rpm = 1
        max_rpm = 10000
        min_threads = 1
        max_threads = 100

        if rpm_limit <= min_rpm:
            rpm_threads = min_threads
        elif rpm_limit >= max_rpm:
            rpm_threads = max_threads
        else:
            # 线性插值计算 RPM 对应的线程数
            rpm_threads = min_threads + (rpm_limit - min_rpm) * (max_threads - min_threads) / (max_rpm - min_rpm)

        rpm_threads = int(round(rpm_threads)) # 四舍五入取整

        # 确保线程数在 1-100 范围内，并使用 CPU 核心数作为辅助上限 
        # 更简洁的方式是直接限制在 1-100 范围内，因为 100 通常已经足够高
        actual_thread_counts = max(1, min(100, rpm_threads)) # 限制在 1-100

        return actual_thread_counts


    # 获取接口配置信息包
    def get_platform_configuration(self,platform_type):

        if platform_type == "translationReq":
            target_platform = self.api_settings["translate"]
        elif platform_type == "polishingReq":
            target_platform = self.api_settings["polish"]

        api_url = self.base_url
        api_key = self.get_next_apikey()
        api_format = self.platforms.get(target_platform).get("api_format")
        model_name = self.model
        region = self.platforms.get(target_platform).get("region",'')
        access_key = self.platforms.get(target_platform).get("access_key",'')
        secret_key = self.platforms.get(target_platform).get("secret_key",'')
        request_timeout = self.request_timeout
        temperature = self.platforms.get(target_platform).get("temperature")
        top_p = self.platforms.get(target_platform).get("top_p")
        presence_penalty = self.platforms.get(target_platform).get("presence_penalty")
        frequency_penalty = self.platforms.get(target_platform).get("frequency_penalty")
        extra_body = self.platforms.get(target_platform).get("extra_body",{})
        think_switch = self.platforms.get(target_platform).get("think_switch")
        think_depth = self.platforms.get(target_platform).get("think_depth")
        thinking_budget = self.platforms.get(target_platform).get("thinking_budget")
        if thinking_budget is None:
            thinking_budget = -1
            
        structured_output_mode = self.platforms.get(target_platform).get("structured_output_mode", 0)
        auto_complete = self.platforms.get(target_platform).get("auto_complete", False)
        enable_stream_api = getattr(self, "enable_stream_api", True)
        enable_prompt_caching = getattr(self, "enable_prompt_caching", False)
        sdk_request_mode = getattr(self, "sdk_request_mode", "httpx")
        use_openai_sdk = getattr(self, "use_openai_sdk", False)

        params = {
            "target_platform": target_platform,
            "api_url": api_url,
            "api_key": api_key,
            "api_format": api_format,
            "model_name": model_name,
            "region": region,
            "access_key": access_key,
            "secret_key": secret_key,
            "request_timeout": request_timeout,
            "temperature": temperature,
            "top_p": top_p,
            "presence_penalty": presence_penalty,
            "frequency_penalty": frequency_penalty,
            "extra_body": extra_body,
            "think_switch": think_switch,
            "think_depth": think_depth,
            "thinking_budget": thinking_budget,
            "structured_output_mode": structured_output_mode,
            "auto_complete": auto_complete,
            "enable_stream_api": enable_stream_api,
            "enable_prompt_caching": enable_prompt_caching,
            "sdk_request_mode": sdk_request_mode,
            "use_openai_sdk": use_openai_sdk
        }

        return params
