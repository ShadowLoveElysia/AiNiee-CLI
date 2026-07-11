"""
配置注册表 - 集中管理所有配置项的元数据

配置层级:
- SYSTEM: 系统级，不可见不可改（内部逻辑用）
- ADVANCED: 高级用户可见可改（需要理解后果）
- USER: 普通用户可见可改（安全的常用配置）
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional, Callable

from ModuleFolders.Infrastructure.TaskConfig.PolishingMode import (
    POLISH_TRANSLATED_TEXT,
    POLISHING_MODE_CHOICES,
)


class ConfigLevel(Enum):
    """配置层级"""
    SYSTEM = 0      # 系统级：不可见，不可改
    ADVANCED = 1    # 高级：可见，需谨慎修改
    USER = 2        # 用户级：可见，安全修改


class ConfigType(Enum):
    """配置值类型"""
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    LIST = "list"
    DICT = "dict"
    PATH = "path"
    CHOICE = "choice"  # 枚举选择


@dataclass
class ConfigItem:
    """配置项定义"""
    key: str                                    # 配置键名
    default: Any                                # 默认值
    level: ConfigLevel                          # 配置层级
    config_type: ConfigType                     # 值类型
    i18n_key: str = ""                          # 国际化键名（用于显示名称）
    i18n_desc_key: str = ""                     # 国际化键名（用于描述）
    choices: list = field(default_factory=list) # 可选值（用于 CHOICE 类型）
    min_value: Optional[float] = None           # 最小值（用于数值类型）
    max_value: Optional[float] = None           # 最大值（用于数值类型）
    validator: Optional[Callable] = None        # 自定义验证函数
    depends_on: Optional[str] = None            # 依赖的配置项
    category: str = "general"                   # 配置分类
    online_only: bool = False                   # 仅在线API支持


# ============================================================
# 配置注册表
# ============================================================

CONFIG_REGISTRY: dict[str, ConfigItem] = {}


def register_config(item: ConfigItem):
    """注册配置项"""
    CONFIG_REGISTRY[item.key] = item
    return item


def get_config_item(key: str) -> Optional[ConfigItem]:
    """获取配置项定义"""
    return CONFIG_REGISTRY.get(key)


def get_default_value(key: str) -> Any:
    """获取配置项默认值"""
    item = CONFIG_REGISTRY.get(key)
    return item.default if item else None


def get_configs_by_level(level: ConfigLevel) -> list[ConfigItem]:
    """获取指定层级的所有配置项"""
    return [item for item in CONFIG_REGISTRY.values() if item.level == level]


def get_configs_by_category(category: str) -> list[ConfigItem]:
    """获取指定分类的所有配置项"""
    return [item for item in CONFIG_REGISTRY.values() if item.category == category]


def is_user_visible(key: str) -> bool:
    """判断配置是否对用户可见"""
    item = CONFIG_REGISTRY.get(key)
    if not item:
        return False
    return item.level in (ConfigLevel.USER, ConfigLevel.ADVANCED)


def is_user_editable(key: str, is_advanced_user: bool = False) -> bool:
    """判断配置是否可被用户编辑"""
    item = CONFIG_REGISTRY.get(key)
    if not item:
        return False
    if item.level == ConfigLevel.SYSTEM:
        return False
    if item.level == ConfigLevel.ADVANCED:
        return is_advanced_user
    return True


# ============================================================
# 注册所有配置项
# ============================================================

# --- 路径配置 (USER) ---
register_config(ConfigItem(
    key="label_input_path",
    default="",
    level=ConfigLevel.USER,
    config_type=ConfigType.PATH,
    i18n_key="setting_input_path",
    category="path"
))

register_config(ConfigItem(
    key="label_output_path",
    default="",
    level=ConfigLevel.USER,
    config_type=ConfigType.PATH,
    i18n_key="setting_output_path",
    category="path"
))

register_config(ConfigItem(
    key="polishing_output_path",
    default="",
    level=ConfigLevel.USER,
    config_type=ConfigType.PATH,
    i18n_key="setting_polishing_output_path",
    category="path"
))

# --- 语言配置 (USER) ---
register_config(ConfigItem(
    key="source_language",
    default="auto",
    level=ConfigLevel.USER,
    config_type=ConfigType.STRING,
    i18n_key="setting_src_lang",
    category="language"
))

register_config(ConfigItem(
    key="target_language",
    default="Chinese",
    level=ConfigLevel.USER,
    config_type=ConfigType.STRING,
    i18n_key="setting_tgt_lang",
    category="language"
))

# --- 界面语言配置 (USER) ---
register_config(ConfigItem(
    key="interface_language",
    default="zh_CN",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_interface_language",
    i18n_desc_key="setting_interface_language_desc",
    choices=["zh_CN", "zh_CNTW", "ja", "en", "ko", "ru", "es"],
    category="project_general"
))

register_config(ConfigItem(
    key="main_menu_layout",
    default="flat",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_main_menu_layout",
    i18n_desc_key="setting_main_menu_layout_desc",
    choices=["grouped", "flat"],
    category="project_general"
))

# --- 翻译核心配置 (USER) ---
register_config(ConfigItem(
    key="lines_limit",
    default=20,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_lines_limit",
    min_value=1,
    max_value=100,
    category="translation"
))

register_config(ConfigItem(
    key="chunk_soft_limit_extra_lines",
    default=10,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_chunk_soft_limit_extra_lines",
    i18n_desc_key="setting_chunk_soft_limit_extra_lines_desc",
    min_value=0,
    max_value=50,
    category="translation"
))

register_config(ConfigItem(
    key="line_split_optimization_mode",
    default="off",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_line_split_optimization_mode",
    i18n_desc_key="setting_line_split_optimization_mode_desc",
    choices=["off", "dynamic", "tail"],
    depends_on="chunk_soft_limit_extra_lines",
    category="translation"
))

register_config(ConfigItem(
    key="retry_split_min_lines",
    default=15,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_retry_split_min_lines",
    i18n_desc_key="setting_retry_split_min_lines_desc",
    min_value=1,
    max_value=100,
    category="translation"
))

register_config(ConfigItem(
    key="tokens_limit",
    default=1500,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_tokens_limit",
    min_value=400,
    max_value=16000,
    depends_on="tokens_limit_switch",
    category="translation"
))

register_config(ConfigItem(
    key="pre_line_counts",
    default=3,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_pre_line_counts",
    min_value=0,
    max_value=10,
    category="translation"
))

register_config(ConfigItem(
    key="polishing_mode_selection",
    default=POLISH_TRANSLATED_TEXT,
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_polishing_mode_selection",
    i18n_desc_key="setting_polishing_mode_selection_desc",
    choices=POLISHING_MODE_CHOICES,
    category="translation"
))

register_config(ConfigItem(
    key="enable_context_enhancement",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_context_enhancement",
    i18n_desc_key="setting_enable_context_enhancement_desc",
    category="translation"
))

register_config(ConfigItem(
    key="round_limit",
    default=3,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_round_limit",
    min_value=1,
    max_value=10,
    category="translation"
))

register_config(ConfigItem(
    key="retry_count",
    default=3,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_retry_count",
    min_value=0,
    max_value=10,
    category="translation"
))

register_config(ConfigItem(
    key="request_timeout",
    default=60,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_request_timeout",
    min_value=10,
    max_value=600,
    category="translation"
))

register_config(ConfigItem(
    key="user_thread_counts",
    default=0,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_thread_count",
    i18n_desc_key="setting_thread_count_desc",
    min_value=0,
    max_value=100,
    category="translation"
))

# --- 输出配置 (USER) ---
register_config(ConfigItem(
    key="enable_bilingual_output",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_bilingual_output",
    i18n_desc_key="setting_enable_bilingual_output_desc",
    category="output"
))

register_config(ConfigItem(
    key="bilingual_text_order",
    default="translation_first",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_bilingual_order",
    choices=["translation_first", "source_first"],
    depends_on="enable_bilingual_output",
    category="output"
))

register_config(ConfigItem(
    key="output_filename_suffix",
    default="",
    level=ConfigLevel.USER,
    config_type=ConfigType.STRING,
    i18n_key="setting_output_suffix",
    category="output"
))

register_config(ConfigItem(
    key="keep_original_encoding",
    default=True,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_keep_encoding",
    i18n_desc_key="setting_keep_encoding_desc",
    category="output"
))

register_config(ConfigItem(
    key="epub_language_update_mode",
    default="auto",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_epub_language_update_mode",
    i18n_desc_key="setting_epub_language_update_mode_desc",
    choices=["auto", "disabled", "zh_CN", "zh_CNTW", "ja", "en", "ko", "ru", "es"],
    category="output"
))

# --- 功能开关 (USER) ---
register_config(ConfigItem(
    key="auto_set_output_path",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_auto_set_output_path",
    category="path"
))

register_config(ConfigItem(
    key="response_conversion_toggle",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_response_conversion_toggle",
    i18n_desc_key="setting_response_conversion_toggle_desc",
    category="feature"
))

register_config(ConfigItem(
    key="opencc_preset",
    default="s2twp.json",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_opencc_preset",
    choices=["s2t.json", "t2s.json", "s2tw.json", "tw2s.json", "s2twp.json", "tw2sp.json"],
    depends_on="response_conversion_toggle",
    category="feature"
))

register_config(ConfigItem(
    key="enable_cache_backup",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_cache_backup",
    category="feature"
))

register_config(ConfigItem(
    key="cache_save_interval",
    default=8,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_cache_save_interval",
    min_value=1,
    max_value=3600,
    category="feature"
))

register_config(ConfigItem(
    key="cache_backup_limit",
    default=10,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_cache_backup_limit",
    min_value=1,
    max_value=50,
    depends_on="enable_cache_backup",
    category="feature"
))

register_config(ConfigItem(
    key="enable_auto_restore_ebook",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_auto_restore_ebook",
    category="feature"
))

register_config(ConfigItem(
    key="japanese_text_quote_style_switch",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_japanese_text_quote_style",
    i18n_desc_key="setting_japanese_text_quote_style_desc",
    category="utility"
))

# --- 提示词功能开关 (USER) ---
register_config(ConfigItem(
    key="pre_translation_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_pre_translation_switch",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="post_translation_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_post_translation_switch",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="prompt_dictionary_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_prompt_dictionary_switch",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="exclusion_list_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_exclusion_list_switch",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="characterization_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_characterization_switch",
    category="prompt_feature",
    online_only=True
))

register_config(ConfigItem(
    key="world_building_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_world_building_switch",
    category="prompt_feature",
    online_only=True
))

register_config(ConfigItem(
    key="writing_style_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_writing_style_switch",
    category="prompt_feature",
    online_only=True
))

register_config(ConfigItem(
    key="translation_example_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_translation_example_switch",
    category="prompt_feature",
    online_only=True
))

register_config(ConfigItem(
    key="few_shot_and_example_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_few_shot_and_example_switch",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="translation_memory_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_translation_memory_switch",
    i18n_desc_key="feature_translation_memory_switch_desc",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="character_recall_switch",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_character_recall_switch",
    i18n_desc_key="feature_character_recall_switch_desc",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="character_recall_context_lines",
    default=50,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_character_recall_context_lines",
    i18n_desc_key="setting_character_recall_context_lines_desc",
    min_value=0,
    max_value=200,
    depends_on="character_recall_switch",
    category="prompt_feature"
))

register_config(ConfigItem(
    key="character_recall_lookahead_lines",
    default=10,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_character_recall_lookahead_lines",
    i18n_desc_key="setting_character_recall_lookahead_lines_desc",
    min_value=0,
    max_value=50,
    depends_on="character_recall_switch",
    category="prompt_feature"
))

# --- 数据存储 (SYSTEM) - 不对用户显示 ---
register_config(ConfigItem(
    key="pre_translation_data",
    default=[],
    level=ConfigLevel.USER,
    config_type=ConfigType.LIST,
    i18n_key="feature_pre_translation_switch",
    depends_on="pre_translation_switch",
    category="data"
))

register_config(ConfigItem(
    key="post_translation_data",
    default=[],
    level=ConfigLevel.USER,
    config_type=ConfigType.LIST,
    i18n_key="feature_post_translation_switch",
    depends_on="post_translation_switch",
    category="data"
))

register_config(ConfigItem(
    key="prompt_dictionary_data",
    default=[],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="data"
))

register_config(ConfigItem(
    key="exclusion_list_data",
    default=[],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="data"
))

register_config(ConfigItem(
    key="characterization_data",
    default=[],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="data"
))

register_config(ConfigItem(
    key="translation_example_data",
    default=[],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="data"
))

# --- API故障转移 (ADVANCED) ---
register_config(ConfigItem(
    key="enable_api_failover",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_api_failover",
    category="api"
))

register_config(ConfigItem(
    key="api_failover_threshold",
    default=3,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_failover_threshold",
    min_value=1,
    max_value=10,
    depends_on="enable_api_failover",
    category="api"
))

register_config(ConfigItem(
    key="critical_error_threshold",
    default=5,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_critical_error_threshold",
    min_value=1,
    max_value=20,
    category="api"
))

register_config(ConfigItem(
    key="backup_apis",
    default=[],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="api"
))

# API池管理入口 (显示为子菜单入口)
register_config(ConfigItem(
    key="api_pool_management",
    default=None,
    level=ConfigLevel.USER,
    config_type=ConfigType.DICT,
    i18n_key="menu_api_pool_settings",
    category="api"
))

# --- 响应检查 (ADVANCED) - 拆分为独立配置项 ---
register_config(ConfigItem(
    key="untranslated_retry_limit",
    default=3,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="check_untranslated_retry_limit",
    i18n_desc_key="check_untranslated_retry_limit_desc",
    min_value=0,
    max_value=10,
    category="response_check"
))

register_config(ConfigItem(
    key="untranslated_reduction_rate",
    default=0.5,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.FLOAT,
    i18n_key="check_untranslated_reduction_rate",
    i18n_desc_key="check_untranslated_reduction_rate_desc",
    min_value=0.1,
    max_value=0.9,
    category="response_check"
))

register_config(ConfigItem(
    key="newline_character_count_check",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="check_newline_character_count_check",
    category="response_check"
))

register_config(ConfigItem(
    key="return_to_original_text_check",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="check_return_to_original_text_check",
    category="response_check"
))

register_config(ConfigItem(
    key="residual_original_text_check",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="check_residual_original_text_check",
    category="response_check"
))

register_config(ConfigItem(
    key="reply_format_check",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="check_reply_format_check",
    category="response_check"
))

# --- 其他功能开关 (USER) ---
register_config(ConfigItem(
    key="enable_retry_backoff",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_retry_backoff",
    category="feature"
))

register_config(ConfigItem(
    key="enable_session_logging",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_session_logging",
    category="feature"
))

register_config(ConfigItem(
    key="enable_task_notification",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_task_notification",
    category="feature"
))

register_config(ConfigItem(
    key="enable_github_promotion",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_github_promotion",
    i18n_desc_key="setting_github_promotion_desc",
    category="project_general"
))

register_config(ConfigItem(
    key="enable_github_status_bar",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_github_status_bar",
    i18n_desc_key="setting_github_status_bar_desc",
    category="feature"
))

register_config(ConfigItem(
    key="show_detailed_logs",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_detailed_logs",
    category="feature"
))

register_config(ConfigItem(
    key="enable_auto_update",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_auto_update",
    category="feature"
))

register_config(ConfigItem(
    key="enable_operation_logging",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_operation_logging",
    i18n_desc_key="setting_operation_logging_desc",
    category="feature"
))

# --- API请求模式 (USER) ---
register_config(ConfigItem(
    key="sdk_request_mode",
    default="httpx",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_sdk_request_mode",
    i18n_desc_key="setting_sdk_request_mode_desc",
    choices=["httpx", "openai", "anthropic", "google"],
    category="api"
))

# --- 兼容旧配置：由 sdk_request_mode 取代，不再显示 ---
register_config(ConfigItem(
    key="use_openai_sdk",
    default=False,
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.BOOL,
    i18n_key="setting_use_openai_sdk",
    i18n_desc_key="setting_use_openai_sdk_desc",
    category="api"
))

# --- 流式API配置 (USER) ---
register_config(ConfigItem(
    key="enable_stream_api",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_stream_api",
    i18n_desc_key="setting_enable_stream_api_desc",
    category="api"
))

# --- 上下文缓存配置 (ADVANCED) ---
# 启用后可缓存系统提示词和术语表等内容，显著降低API费用
register_config(ConfigItem(
    key="enable_prompt_caching",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_prompt_caching",
    i18n_desc_key="setting_enable_prompt_caching_desc",
    category="api",
    online_only=True
))

# 流式API支持状态缓存 (SYSTEM级别，用于存储各API的流式支持状态)
# 格式: {"api_url_hash": True/False}
register_config(ConfigItem(
    key="stream_api_cache",
    default={},
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.DICT,
    category="system"
))

# --- Thinking 配置 (ADVANCED) ---
register_config(ConfigItem(
    key="think_switch",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="menu_api_think_switch",
    category="advanced",
    online_only=True
))

register_config(ConfigItem(
    key="think_depth",
    default="low",
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.CHOICE,
    i18n_key="menu_api_think_depth",
    choices=["minimal", "low", "medium", "high", "xhigh", "max"],
    depends_on="think_switch",
    category="advanced",
    online_only=True
))

register_config(ConfigItem(
    key="thinking_budget",
    default=-1,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="menu_api_think_budget",
    min_value=-1,
    max_value=128000,
    depends_on="think_switch",
    category="advanced",
    online_only=True
))

register_config(ConfigItem(
    key="auto_process_text_code_segment",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="feature_auto_process_text_code_segment",
    category="prompt_feature"
))

# --- 高级配置 (ADVANCED) ---
register_config(ConfigItem(
    key="enable_smart_round_limit",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_smart_round_limit",
    i18n_desc_key="setting_enable_smart_round_limit_desc",
    category="advanced"
))

# --- AI校对配置 (ADVANCED) ---
register_config(ConfigItem(
    key="enable_auto_proofread",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_auto_proofread",
    i18n_desc_key="setting_enable_auto_proofread_desc",
    category="advanced"
))

register_config(ConfigItem(
    key="proofread_context_lines",
    default=5,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_proofread_context_lines",
    min_value=0,
    max_value=20,
    category="advanced"
))

register_config(ConfigItem(
    key="proofread_batch_size",
    default=20,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_proofread_batch_size",
    min_value=1,
    max_value=50,
    category="advanced"
))

register_config(ConfigItem(
    key="proofread_report_mode",
    default="archive",
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_proofread_report_mode",
    i18n_desc_key="setting_proofread_report_mode_desc",
    choices=["archive", "overwrite"],
    category="advanced"
))

register_config(ConfigItem(
    key="proofread_archive_limit",
    default=20,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_proofread_archive_limit",
    i18n_desc_key="setting_proofread_archive_limit_desc",
    min_value=0,
    max_value=999,
    category="advanced"
))

register_config(ConfigItem(
    key="proofread_suggestion_mode",
    default="proofread",
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_proofread_suggestion_mode",
    i18n_desc_key="setting_proofread_suggestion_mode_desc",
    choices=["proofread", "annotation"],
    category="advanced"
))

register_config(ConfigItem(
    key="proofread_confidence_threshold",
    default=0.7,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.FLOAT,
    i18n_key="setting_proofread_confidence_threshold",
    min_value=0.0,
    max_value=1.0,
    category="advanced"
))

register_config(ConfigItem(
    key="enable_dry_run",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_dry_run",
    i18n_desc_key="setting_dry_run_desc",
    category="advanced"
))

register_config(ConfigItem(
    key="enable_batch_auto_merge_ebook",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_batch_auto_merge_ebook",
    i18n_desc_key="setting_enable_batch_auto_merge_ebook_desc",
    category="advanced"
))

register_config(ConfigItem(
    key="tokens_limit_switch",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_tokens_limit_switch",
    i18n_desc_key="setting_tokens_limit_switch_desc",
    category="translation"
))

# --- 异步执行模式 (ADVANCED) ---
# 使用 aiohttp 异步请求，适合高并发场景（100+并发）
register_config(ConfigItem(
    key="enable_async_mode",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_async_mode",
    i18n_desc_key="setting_enable_async_mode_desc",
    category="advanced"
))

# --- 速率限制配置 (ADVANCED) ---
# 启用后将严格限制请求速率，可能降低翻译速度
register_config(ConfigItem(
    key="enable_rate_limit",
    default=False,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_rate_limit",
    i18n_desc_key="setting_enable_rate_limit_desc",
    category="advanced"
))

register_config(ConfigItem(
    key="custom_rpm_limit",
    default=0,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_custom_rpm_limit",
    i18n_desc_key="setting_custom_rpm_limit_desc",
    min_value=0,
    max_value=100000,
    depends_on="enable_rate_limit",
    category="advanced"
))

register_config(ConfigItem(
    key="custom_tpm_limit",
    default=0,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_custom_tpm_limit",
    i18n_desc_key="setting_custom_tpm_limit_desc",
    min_value=0,
    max_value=100000000,
    depends_on="enable_rate_limit",
    category="advanced"
))

# --- WebServer 配置 (ADVANCED) ---
register_config(ConfigItem(
    key="webserver_port",
    default=8000,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_webserver_port",
    i18n_desc_key="setting_webserver_port_desc",
    min_value=1,
    max_value=65535,
    category="advanced"
))

# --- MCP Server 配置 (ADVANCED) ---
register_config(ConfigItem(
    key="mcp_server_port",
    default=8765,
    level=ConfigLevel.ADVANCED,
    config_type=ConfigType.INT,
    i18n_key="setting_mcp_server_port",
    i18n_desc_key="setting_mcp_server_port_desc",
    min_value=1,
    max_value=65535,
    category="advanced"
))

register_config(ConfigItem(
    key="enable_background_prewarm",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_background_prewarm",
    i18n_desc_key="setting_enable_background_prewarm_desc",
    category="project_general"
))

# --- MangaCore Runtime 配置 (ADVANCED) ---
_manga_engine_defaults = {
    "manga_detect_engine": "comic-text-bubble-detector",
    "manga_segment_engine": "comic-text-detector",
    "manga_ocr_engine": "mit48px-ocr",
    "manga_inpaint_engine": "aot-inpainting",
}

for _manga_engine_key, _manga_engine_default in _manga_engine_defaults.items():
    register_config(ConfigItem(
        key=_manga_engine_key,
        default=_manga_engine_default,
        level=ConfigLevel.ADVANCED,
        config_type=ConfigType.CHOICE,
        i18n_key=f"setting_{_manga_engine_key}",
        category="manga"
    ))

for _manga_device_key in (
    "manga_runtime_device",
    "manga_detect_device",
    "manga_ocr_device",
    "manga_inpaint_device",
):
    register_config(ConfigItem(
        key=_manga_device_key,
        default="auto",
        level=ConfigLevel.ADVANCED,
        config_type=ConfigType.CHOICE,
        i18n_key=f"setting_{_manga_device_key}",
        choices=["auto", "cpu", "cuda", "mps"],
        category="manga"
    ))

# --- 系统级配置 (SYSTEM) - 不对用户显示 ---
register_config(ConfigItem(
    key="translation_project",
    default="AutoType",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_project_type",
    choices=[
        "AutoType",
        # 游戏翻译
        "Mtool", "Renpy", "VNText",
        # 电子书 (原生支持)
        "Epub", "Docx", "Txt",
        # 电子书 (需要Calibre中间件)
        "Mobi", "Azw3", "Fb2", "Kepub",
        # 字幕
        "Srt", "Lrc", "Vtt", "Ass",
        # 开发者格式
        "Excel", "Json", "Paratranz", "Po"
    ],
    category="translation"
))

# --- 格式转换配置 (USER) ---
register_config(ConfigItem(
    key="enable_calibre_middleware",
    default=True,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_calibre_middleware",
    category="format_conversion"
))

register_config(ConfigItem(
    key="enable_post_conversion",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_enable_post_conversion",
    category="format_conversion"
))

register_config(ConfigItem(
    key="fixed_output_format_switch",
    default=False,
    level=ConfigLevel.USER,
    config_type=ConfigType.BOOL,
    i18n_key="setting_fixed_output_format_switch",
    depends_on="enable_post_conversion",
    category="format_conversion"
))

register_config(ConfigItem(
    key="fixed_output_format",
    default="epub",
    level=ConfigLevel.USER,
    config_type=ConfigType.CHOICE,
    i18n_key="setting_fixed_output_format",
    choices=["epub", "mobi", "azw3", "fb2", "pdf", "txt", "docx", "htmlz"],
    depends_on="fixed_output_format_switch",
    category="format_conversion"
))

# Calibre中间件支持的格式列表 (SYSTEM级别，用户不可见)
register_config(ConfigItem(
    key="calibre_middleware_exts",
    default=[".mobi", ".azw3", ".kepub", ".fb2", ".lit", ".lrf", ".pdb", ".pmlz", ".rb", ".rtf", ".tcr", ".txtz", ".htmlz"],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="system"
))

# XLSX中间件支持的格式列表 (SYSTEM级别)
register_config(ConfigItem(
    key="xlsx_middleware_exts",
    default=[".xlsx"],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="system"
))

register_config(ConfigItem(
    key="actual_thread_counts",
    default=3,
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.INT,
    category="system"
))

register_config(ConfigItem(
    key="apikey_list",
    default=[],
    level=ConfigLevel.SYSTEM,
    config_type=ConfigType.LIST,
    category="system"
))

# --- 术语多翻译配置 (USER) ---
register_config(ConfigItem(
    key="term_translation_rounds",
    default=3,
    level=ConfigLevel.USER,
    config_type=ConfigType.INT,
    i18n_key="setting_term_translation_rounds",
    i18n_desc_key="setting_term_translation_rounds_desc",
    min_value=1,
    max_value=10,
    category="translation"
))

# --- 自动化设置入口 (显示为子菜单入口) ---
register_config(ConfigItem(
    key="automation_settings",
    default=None,
    level=ConfigLevel.USER,
    config_type=ConfigType.DICT,
    i18n_key="menu_automation",
    category="automation"
))


# ============================================================
# 辅助函数
# ============================================================

def generate_default_config() -> dict:
    """生成包含所有配置默认值的字典"""
    return {item.key: item.default for item in CONFIG_REGISTRY.values()}


def get_user_visible_configs() -> list[ConfigItem]:
    """获取用户可见的所有配置项"""
    return [
        item for item in CONFIG_REGISTRY.values()
        if item.level in (ConfigLevel.USER, ConfigLevel.ADVANCED)
    ]


def get_categories() -> list[str]:
    """获取所有配置分类"""
    return list(set(item.category for item in CONFIG_REGISTRY.values()))


def validate_config_value(key: str, value: Any) -> tuple[bool, str]:
    """
    验证配置值是否合法
    返回: (是否合法, 错误信息)
    """
    item = CONFIG_REGISTRY.get(key)
    if not item:
        return True, ""  # 未注册的配置不验证

    # 类型检查
    if item.config_type == ConfigType.INT:
        if not isinstance(value, int):
            return False, f"配置 {key} 必须是整数"
        if item.min_value is not None and value < item.min_value:
            return False, f"配置 {key} 不能小于 {item.min_value}"
        if item.max_value is not None and value > item.max_value:
            return False, f"配置 {key} 不能大于 {item.max_value}"

    elif item.config_type == ConfigType.BOOL:
        if not isinstance(value, bool):
            return False, f"配置 {key} 必须是布尔值"

    elif item.config_type == ConfigType.CHOICE and item.choices:
        if value not in item.choices:
            return False, f"配置 {key} 必须是 {item.choices} 之一"

    # 自定义验证
    if item.validator:
        try:
            if not item.validator(value):
                return False, f"配置 {key} 验证失败"
        except Exception as e:
            return False, f"配置 {key} 验证出错: {e}"

    return True, ""
