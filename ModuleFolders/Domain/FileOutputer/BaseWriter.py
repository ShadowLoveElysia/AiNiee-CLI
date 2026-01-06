from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypedDict

import rich

from ModuleFolders.Infrastructure.Cache.CacheFile import CacheFile
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig


def can_encode_text(text: str, encoding: str) -> bool:
    """检查文本是否可以用指定编码正确表示"""
    if not text:
        return True
    try:
        text.encode(encoding, errors='strict')
        return True
    except UnicodeEncodeError:
        return False


@dataclass
class TranslationOutputConfig:
    enabled: bool = False
    name_suffix: str = ""
    output_root: Path = None

# 双语排序枚举
class BilingualOrder(Enum):
    SOURCE_FIRST = "source_first"
    TRANSLATION_FIRST = "translation_first"

@dataclass
class OutputConfig:
    translated_config: TranslationOutputConfig = None
    bilingual_config: TranslationOutputConfig = None
    input_root: Path = None
    bilingual_order: BilingualOrder = field(default=BilingualOrder.TRANSLATION_FIRST)  # 双语排序配置

    def __post_init__(self):
        if self.translated_config is None:
            self.translated_config = TranslationOutputConfig(True, "_translated")
        if self.bilingual_config is None:
            self.bilingual_config = TranslationOutputConfig(False, "_bilingual")


class WriterInitParams(TypedDict):
    """writer的初始化参数，必须包含output_config，其他参数随意"""
    output_config: OutputConfig


@dataclass
class PreWriteMetadata:
    encoding: str = "utf-8"


class BaseTranslationWriter(ABC):
    """Writer基类，在其生命周期内可以输出多个文件"""
    def __init__(self, output_config: OutputConfig) -> None:
        self.output_config = output_config

    class TranslationMode(Enum):
        TRANSLATED = ('translated_config', 'write_translated_file')
        BILINGUAL = ('bilingual_config', 'write_bilingual_file')

        def __init__(self, config_attr, write_method) -> None:
            self.config_attr = config_attr
            self.write_method = write_method

    def can_write(self, mode: TranslationMode) -> bool:
        """判断writer是否支持该输出方式"""
        if mode == self.TranslationMode.TRANSLATED:
            return isinstance(self, BaseTranslatedWriter) and self.output_config.translated_config.enabled
        elif mode == self.TranslationMode.BILINGUAL:
            return isinstance(self, BaseBilingualWriter) and self.output_config.bilingual_config.enabled
        return False

    def __enter__(self):
        """申请整个Writer生命周期用到的耗时资源，单个文件的资源则在write_xxx_file方法中申请释放"""
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        """释放耗时资源"""
        pass

    @classmethod
    @abstractmethod
    def get_project_type(self) -> str:
        """获取Writer对应的项目类型标识符（用于动态实例化），如 Mtool"""
        pass

    @classmethod
    def is_environ_supported(cls) -> bool:
        """用于判断当前环境是否支持该writer"""
        return True


class BaseTranslatedWriter(BaseTranslationWriter):
    """译文输出基类"""

    def write_translated_file(self, translation_file_path: str, cache_file: CacheFile, source_file_path:str = None, task_config: TaskConfig = None):
        """
        写入翻译文件
        :param translation_file_path: 翻译文件路径
        :param cache_file: 缓存文件
        """

        pre_write_metadata = self.pre_write_translated(translation_file_path, cache_file, task_config)
        self.on_write_translated(Path(translation_file_path), cache_file, pre_write_metadata, Path(source_file_path) if source_file_path else None)
        self.post_write_translated(Path(translation_file_path))

    def pre_write_translated(self, translation_file_path: str, cache_file: CacheFile, task_config: TaskConfig = None) -> PreWriteMetadata:
        """
        在写入翻译文件之前执行的操作,可以被子类重写
        :param translation_file_path: 翻译文件路径
        :param cache_file: 缓存文件
        :return: 返回一个包含预处理信息的元数据对象
        """

        # 如果没有传入 task_config，则尝试获取一个（为了兼容旧代码）
        if task_config is None:
            # This path should ideally not be taken in the new flow
            from ModuleFolders.Service.TaskExecutor.TaskExecutor import TaskExecutor
            task_config = TaskExecutor.config
        
        keep_original_encoding_config = task_config.keep_original_encoding if task_config else True

        if keep_original_encoding_config:
            encoding = cache_file.encoding
        else:
            encoding = "utf-8"

        return PreWriteMetadata(encoding=encoding)

    @abstractmethod
    def on_write_translated(
        self, translation_file_path: Path, cache_file: CacheFile,
        pre_write_metadata: PreWriteMetadata,
        source_file_path: Path = None,
    ):
        """执行实际的文件写入操作"""
        pass

    def post_write_translated(self, translation_file_path: Path):
        """输出后操作，如验证"""
        pass


class BaseBilingualWriter(BaseTranslationWriter):
    """双语输出基类"""

    def write_bilingual_file(
        self, translation_file_path: Path, cache_file: CacheFile,
        source_file_path: Path = None,
        task_config: TaskConfig = None,
    ):
        """输出双语文件"""
        pre_write_metadata = self.pre_write_bilingual(cache_file)
        self.on_write_bilingual(Path(translation_file_path), cache_file, pre_write_metadata, Path(source_file_path) if source_file_path else None)
        self.post_write_bilingual(Path(translation_file_path))

    def pre_write_bilingual(self, cache_file: CacheFile) -> PreWriteMetadata:
        """根据文件内容做输出前操作，如输出编码检测"""
        return PreWriteMetadata()

    @abstractmethod
    def on_write_bilingual(
        self, translation_file_path: Path, cache_file: CacheFile,
        pre_write_metadata: PreWriteMetadata,
        source_file_path: Path = None,
    ):
        """执行实际的文件写入操作"""
        pass

    def post_write_bilingual(self, translation_file_path: Path):
        """输出后操作，如验证"""
        pass