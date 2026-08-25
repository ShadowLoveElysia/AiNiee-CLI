"""
文件夹监控管理器
监控指定目录，检测新文件并自动加入翻译队列
"""
import os
import re
import shutil
import threading
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any, Set, Tuple
from natsort import natsort_keygen, natsorted, ns
from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.Automation.WorkflowRunner import (
    describe_workflow_steps,
    is_queue_only_workflow,
    normalize_workflow_steps,
)
from ModuleFolders.Infrastructure.Automation.AutomationPaths import (
    AUTOMATION_GLOSSARY_DIR_NAME,
    is_under_automation_glossary_dir,
    is_under_automation_output_dir,
)
from ModuleFolders.Infrastructure.Automation.OutputCollector import (
    AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY,
    normalize_output_collection_config,
)
from ModuleFolders.Infrastructure.SensitiveData import sanitize_sensitive_data
from ModuleFolders.Infrastructure.TaskContract import (
    QUEUE_TASK_TYPES,
    QUEUE_TASK_OVERRIDE_FIELDS,
    TaskContractError,
    TaskSpec,
    select_task_contract_fields,
)


SERIES_VOLUME_PATTERNS = (
    re.compile(r"^(?P<prefix>.*?)(?:第\s*)?(?P<volume>\d{1,4})\s*(?:卷|巻|册|冊|集|话|話)(?P<suffix>.*)$", re.IGNORECASE),
    re.compile(r"^(?P<prefix>.*?)(?:vol(?:ume)?|book)\s*\.?\s*(?P<volume>\d{1,4})(?P<suffix>.*)$", re.IGNORECASE),
    re.compile(r"^(?P<prefix>.*?)(?P<volume>\d{1,4})(?P<suffix>\D*)$"),
)

WATCH_TARGET_TYPES = {"file", "folder", "both"}
WATCH_RULE_OVERRIDE_FIELDS = (
    frozenset(QUEUE_TASK_OVERRIDE_FIELDS) | {"lines", "tokens"}
) - {
    "input_path",
    "output_path",
    "profile",
    "rules_profile",
}
WATCH_RULE_RUNTIME_METADATA_FIELDS = frozenset({"files_processed", "last_activity"})
_NAT_SORT_KEY = natsort_keygen(alg=ns.PATH | ns.IGNORECASE)


def parse_series_volume(path: str) -> dict:
    stem = os.path.splitext(os.path.basename(os.path.normpath(path or "")))[0]
    normalized = stem.strip()
    for pattern in SERIES_VOLUME_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        try:
            volume = int(match.group("volume"))
        except (TypeError, ValueError):
            continue
        if volume <= 0:
            continue
        prefix = re.sub(r"[\s._\-\[\]【】()（）]+$", "", match.groupdict().get("prefix") or "")
        suffix = re.sub(r"^[\s._\-\[\]【】()（）]+", "", match.groupdict().get("suffix") or "")
        series_key = (prefix or suffix or os.path.basename(os.path.dirname(os.path.abspath(path or "."))) or "series").strip()
        series_key = re.sub(r"\s+", " ", series_key)
        return {
            "series_key": series_key,
            "volume": volume,
            "label": f"第{volume}卷",
        }
    return {}


def normalize_series_family_key(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return value


def natural_sort_key(value: str) -> list:
    return _NAT_SORT_KEY(os.path.normpath(str(value or "")))


def natural_sort_paths(values) -> list:
    return list(natsorted(
        list(values or []),
        key=lambda value: os.path.normpath(str(value or "")),
        alg=ns.PATH | ns.IGNORECASE,
    ))


class WatchRule:
    """监控规则"""

    def __init__(self, rule_id: str, watch_path: str, output_path: str = "",
                 done_path: str = "", file_patterns: List[str] = None,
                 profile: str = "default", task_type: str = "translation",
                 auto_start: bool = True, debounce_seconds: int = 5,
                 recursive: bool = False, enabled: bool = True,
                 move_to_done: bool = True, workflow_steps: List[dict] = None,
                 trigger_mode: str = "file", rules_profile: str = "",
                 settle_existing: bool = False, series_incremental: bool = False,
                 watch_target_type: str = "file", **kwargs):
        metadata = kwargs.pop("extra", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise TaskContractError("Watch rule extra metadata must be a mapping")
        unknown = sorted(
            set(kwargs) - WATCH_RULE_OVERRIDE_FIELDS - WATCH_RULE_RUNTIME_METADATA_FIELDS
        )
        if unknown:
            raise TaskContractError(f"Unknown watch rule fields: {', '.join(unknown)}")
        overrides = {key: kwargs[key] for key in WATCH_RULE_OVERRIDE_FIELDS if key in kwargs}
        payload = {
            "task_type": task_type,
            "input_path": watch_path or ".",
            "output_path": output_path or None,
            "profile": profile or None,
            "rules_profile": rules_profile or None,
            **overrides,
        }
        normalized = TaskSpec.from_mapping(payload).to_mapping(
            include_none=True,
            include_api_key=True,
        )
        if normalized["task_type"] not in QUEUE_TASK_TYPES or normalized["manga"]:
            raise TaskContractError(
                f"Task type {normalized['task_type']!r} is not supported by watch rules"
            )
        overrides = {
            key: (
                normalized["lines_limit"]
                if key == "lines"
                else normalized["tokens_limit"]
                if key == "tokens"
                else normalized[key]
            )
            for key in WATCH_RULE_OVERRIDE_FIELDS
            if key in overrides
        }
        self.id = rule_id
        self.watch_path = os.path.abspath(watch_path)
        self.output_path = output_path
        self.done_path = done_path
        self.file_patterns = self._normalize_patterns(file_patterns)
        self.profile = profile
        self.rules_profile = rules_profile
        self.task_type = normalized["task_type"]
        self.auto_start = auto_start
        self.debounce_seconds = debounce_seconds
        self.recursive = recursive
        self.enabled = enabled
        self.move_to_done = move_to_done
        self.workflow_steps = self._normalize_rule_workflow_steps(
            workflow_steps,
            normalized["task_type"],
            auto_start,
        )
        self.trigger_mode = trigger_mode if trigger_mode in {"file", "folder"} else "file"
        self.settle_existing = settle_existing
        self.series_incremental = bool(series_incremental)
        self.watch_target_type = self._normalize_watch_target_type(watch_target_type)
        self.extra = {**metadata, **overrides}

        # 运行状态
        self.files_processed = 0
        self.last_activity: Optional[datetime] = None

    @staticmethod
    def _normalize_patterns(file_patterns: List[str] = None) -> List[str]:
        patterns = []
        for pattern in file_patterns or ["*.txt", "*.epub", "*.srt"]:
            pattern = str(pattern or "").strip()
            if not pattern:
                continue
            if pattern.startswith("."):
                pattern = f"*{pattern}"
            elif "*" not in pattern and "?" not in pattern:
                pattern = f"*.{pattern.lstrip('.')}"
            patterns.append(pattern)
        return patterns or ["*.txt", "*.epub", "*.srt"]

    @staticmethod
    def _normalize_rule_workflow_steps(workflow_steps: List[dict], task_type: str, auto_start: bool) -> List[dict]:
        if not workflow_steps and not auto_start:
            return []

        normalized = normalize_workflow_steps(workflow_steps, task_type, auto_start)
        if not auto_start and is_queue_only_workflow(normalized):
            return []
        return normalized

    def matches_pattern(self, filename: str) -> bool:
        """检查文件是否匹配模式"""
        from fnmatch import fnmatch
        return any(fnmatch(filename.lower(), pattern.lower())
                   for pattern in self.file_patterns)

    @staticmethod
    def _normalize_watch_target_type(value: str) -> str:
        value = str(value or "file").strip().lower()
        return value if value in WATCH_TARGET_TYPES else "file"

    def detects_files(self) -> bool:
        return self.watch_target_type in {"file", "both"}

    def detects_folders(self) -> bool:
        return self.watch_target_type in {"folder", "both"}

    def to_dict(self) -> dict:
        """转换为字典"""
        return sanitize_sensitive_data({
            "id": self.id,
            "watch_path": self.watch_path,
            "output_path": self.output_path,
            "done_path": self.done_path,
            "file_patterns": self.file_patterns,
            "profile": self.profile,
            "rules_profile": self.rules_profile,
            "task_type": self.task_type,
            "auto_start": self.auto_start,
            "debounce_seconds": self.debounce_seconds,
            "recursive": self.recursive,
            "enabled": self.enabled,
            "move_to_done": self.move_to_done,
            "workflow_steps": self.workflow_steps,
            "trigger_mode": self.trigger_mode,
            "settle_existing": self.settle_existing,
            "series_incremental": self.series_incremental,
            "watch_target_type": self.watch_target_type,
            **self.extra
        })

    @classmethod
    def from_dict(cls, data: dict) -> "WatchRule":
        """从字典创建"""
        known_fields = {
            "id", "watch_path", "output_path", "done_path", "file_patterns",
            "profile", "rules_profile", "task_type", "auto_start",
            "debounce_seconds", "recursive", "enabled", "move_to_done",
            "workflow_steps", "trigger_mode", "settle_existing",
            "series_incremental", "watch_target_type", "detect_target",
            *WATCH_RULE_RUNTIME_METADATA_FIELDS,
        }
        return cls(
            rule_id=data.get("id", ""),
            watch_path=data.get("watch_path", ""),
            output_path=data.get("output_path", ""),
            done_path=data.get("done_path", ""),
            file_patterns=data.get("file_patterns", ["*.txt", "*.epub", "*.srt"]),
            profile=data.get("profile", "default"),
            rules_profile=data.get("rules_profile", ""),
            task_type=data.get("task_type", "translation"),
            auto_start=data.get("auto_start", True),
            debounce_seconds=data.get("debounce_seconds", 5),
            recursive=data.get("recursive", False),
            enabled=data.get("enabled", True),
            move_to_done=data.get("move_to_done", True),
            workflow_steps=data.get("workflow_steps"),
            trigger_mode=data.get("trigger_mode", "file"),
            settle_existing=data.get("settle_existing", False),
            series_incremental=data.get("series_incremental", False),
            watch_target_type=data.get("watch_target_type", data.get("detect_target", "file")),
            **{key: value for key, value in data.items() if key not in known_fields},
        )


class FileState:
    """文件状态追踪"""

    def __init__(self, path: str):
        self.path = path
        self.size = 0
        self.mtime = 0
        self.hash = ""
        self.first_seen = datetime.now()
        self.stable_since: Optional[datetime] = None
        self.status = "pending"  # pending, stable, processing, done, error

    def update(self) -> bool:
        """更新文件状态，返回是否有变化"""
        try:
            new_size, new_mtime, new_hash = self._snapshot_path(self.path)

            if new_size != self.size or new_mtime != self.mtime or new_hash != self.hash:
                self.size = new_size
                self.mtime = new_mtime
                self.hash = new_hash
                self.stable_since = None
                return True
            else:
                if self.stable_since is None:
                    self.stable_since = datetime.now()
                return False
        except OSError:
            return False

    @staticmethod
    def _snapshot_path(path: str) -> Tuple[int, float, str]:
        if not os.path.isdir(path):
            stat = os.stat(path)
            return stat.st_size, stat.st_mtime, f"file:{stat.st_size}:{stat.st_mtime}"

        total_size = 0
        latest_mtime = 0.0
        file_count = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d != AUTOMATION_GLOSSARY_DIR_NAME]
            for filename in files:
                child = os.path.join(root, filename)
                try:
                    stat = os.stat(child)
                except OSError:
                    continue
                total_size += stat.st_size
                latest_mtime = max(latest_mtime, stat.st_mtime)
                file_count += 1
        stat = os.stat(path)
        latest_mtime = max(latest_mtime, stat.st_mtime)
        return total_size, latest_mtime, f"dir:{file_count}:{total_size}:{latest_mtime}"

    def is_stable(self, debounce_seconds: int) -> bool:
        """检查文件是否稳定（不再被写入）"""
        if self.stable_since is None:
            return False
        elapsed = (datetime.now() - self.stable_since).total_seconds()
        return elapsed >= debounce_seconds


class WatchManager(Base):
    """文件夹监控管理器"""

    def __init__(self, task_callback: Callable[[dict], Any] = None,
                 queue_callback: Callable[[dict], Any] = None,
                 queue_start_callback: Callable[[], Any] = None):
        super().__init__()
        self.task_callback = task_callback  # 直接执行任务
        self.queue_callback = queue_callback  # 加入队列
        self.queue_start_callback = queue_start_callback  # 批量入队后启动队列
        self.rules: Dict[str, WatchRule] = {}
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # 文件状态追踪
        self.file_states: Dict[str, FileState] = {}
        self.processed_files: Set[str] = set()
        self.file_observations: Dict[str, Dict[str, Any]] = {}

        # 配置
        self.scan_interval = 1  # 扫描间隔（秒）
        self.max_concurrent = 2  # 最大并发任务数
        self.current_tasks = 0
        self._active_targets: Set[str] = set()

        # 日志
        self.logs: List[dict] = []
        self.max_logs = 100

        # 持久化已处理文件记录
        self.processed_file_path = ""

    def set_callbacks(self, task_callback: Callable = None,
                      queue_callback: Callable = None,
                      queue_start_callback: Callable = None):
        """设置回调函数"""
        if task_callback:
            self.task_callback = task_callback
        if queue_callback:
            self.queue_callback = queue_callback
        if queue_start_callback:
            self.queue_start_callback = queue_start_callback

    def add_rule(self, rule: WatchRule) -> bool:
        """添加监控规则"""
        with self._lock:
            if rule.id in self.rules:
                return False

            # 确保监控目录存在
            if not os.path.exists(rule.watch_path):
                try:
                    os.makedirs(rule.watch_path)
                except OSError as e:
                    self._log("error", f"Cannot create watch directory: {e}")
                    return False

            # 确保完成目录存在
            if rule.done_path and not os.path.exists(rule.done_path):
                try:
                    os.makedirs(rule.done_path)
                except OSError:
                    pass

            self.rules[rule.id] = rule
            self._log("info", f"Watch rule added: {rule.watch_path}")
            return True

    def remove_rule(self, rule_id: str) -> bool:
        """移除监控规则"""
        with self._lock:
            if rule_id in self.rules:
                rule = self.rules.pop(rule_id)
                self.file_observations = {
                    path: item
                    for path, item in self.file_observations.items()
                    if item.get("rule_id") != rule_id
                }
                self._log("info", f"Watch rule removed: {rule.watch_path}")
                return True
            return False

    def update_rule(self, rule_id: str, **kwargs) -> bool:
        """更新监控规则"""
        with self._lock:
            if rule_id not in self.rules:
                return False

            rule = self.rules[rule_id]
            updates = dict(kwargs)
            task_type = updates.get("task_type", rule.task_type)
            auto_start = updates.get("auto_start", rule.auto_start)

            if "file_patterns" in updates:
                updates["file_patterns"] = WatchRule._normalize_patterns(updates["file_patterns"])
            if "workflow_steps" in updates:
                updates["workflow_steps"] = WatchRule._normalize_rule_workflow_steps(
                    updates["workflow_steps"],
                    task_type,
                    auto_start,
                )

            unknown = sorted(
                key
                for key in updates
                if not hasattr(rule, key) and key not in WATCH_RULE_OVERRIDE_FIELDS
            )
            if unknown:
                raise TaskContractError(f"Unknown watch rule fields: {', '.join(unknown)}")
            override_updates = {
                key: updates.pop(key)
                for key in list(updates)
                if key in WATCH_RULE_OVERRIDE_FIELDS
            }
            candidate = WatchRule(
                rule_id=rule.id,
                watch_path=updates.get("watch_path", rule.watch_path),
                output_path=updates.get("output_path", rule.output_path),
                done_path=updates.get("done_path", rule.done_path),
                file_patterns=updates.get("file_patterns", rule.file_patterns),
                profile=updates.get("profile", rule.profile),
                rules_profile=updates.get("rules_profile", rule.rules_profile),
                task_type=task_type,
                auto_start=auto_start,
                debounce_seconds=updates.get("debounce_seconds", rule.debounce_seconds),
                recursive=updates.get("recursive", rule.recursive),
                enabled=updates.get("enabled", rule.enabled),
                move_to_done=updates.get("move_to_done", rule.move_to_done),
                workflow_steps=updates.get("workflow_steps", rule.workflow_steps),
                trigger_mode=updates.get("trigger_mode", rule.trigger_mode),
                settle_existing=updates.get("settle_existing", rule.settle_existing),
                series_incremental=updates.get("series_incremental", rule.series_incremental),
                watch_target_type=updates.get("watch_target_type", rule.watch_target_type),
                extra={
                    key: value
                    for key, value in rule.extra.items()
                    if key not in WATCH_RULE_OVERRIDE_FIELDS
                },
                **{
                    **{
                        key: value
                        for key, value in rule.extra.items()
                        if key in WATCH_RULE_OVERRIDE_FIELDS
                    },
                    **override_updates,
                },
            )
            updates["task_type"] = candidate.task_type
            updates["workflow_steps"] = candidate.workflow_steps
            rule.extra = candidate.extra

            for key, value in updates.items():
                if hasattr(rule, key):
                    setattr(rule, key, value)
            return True

    def get_rule(self, rule_id: str) -> Optional[WatchRule]:
        """获取规则"""
        return self.rules.get(rule_id)

    def get_all_rules(self) -> List[WatchRule]:
        """获取所有规则"""
        return list(self.rules.values())

    @staticmethod
    def _observation_key(file_path: str, rule: WatchRule) -> str:
        return f"{rule.id}:{os.path.abspath(file_path)}"

    def _record_file_observation(
        self,
        file_path: str,
        rule: WatchRule,
        matched: bool,
        status: str = None,
        path_type: str = None,
        series: dict = None,
    ) -> dict:
        path = os.path.abspath(file_path)
        if self._is_system_ignored_path(path, rule):
            return {}
        now = datetime.now()
        observation_key = self._observation_key(path, rule)
        observation = self.file_observations.get(observation_key)
        if observation is None:
            observation = {
                "path": path,
                "detected_at": now,
                "queued_at": None,
                "processed_at": None,
            }
            self.file_observations[observation_key] = observation

        try:
            relative_path = os.path.relpath(path, rule.watch_path)
        except ValueError:
            relative_path = os.path.basename(path)

        observed_path_type = path_type or ("folder" if os.path.isdir(path) else "file")
        if series is not None:
            series_value = series
        elif observed_path_type == "folder":
            series_value = observation.get("series", {})
        elif rule.series_incremental:
            series_value = parse_series_volume(path)
        else:
            series_value = {}

        observation.update({
            "rule_id": rule.id,
            "name": os.path.basename(path),
            "relative_path": relative_path,
            "matched": matched,
            "path_type": observed_path_type,
            "patterns": list(rule.file_patterns),
            "workflow": self._describe_workflow(rule.workflow_steps),
            "series": series_value,
            "last_seen": now,
        })
        if status:
            observation["status"] = status
            if status == "queued":
                observation["queued_at"] = now
            elif status == "done":
                observation["processed_at"] = now

        try:
            stat = os.stat(path)
            observation["size"] = stat.st_size
            observation["mtime"] = stat.st_mtime
        except OSError:
            observation["missing"] = True

        return observation

    def _is_system_ignored_path(self, path: str, rule: WatchRule = None) -> bool:
        watch_root = getattr(rule, "watch_path", "") if rule else ""
        return (
            is_under_automation_glossary_dir(path, watch_root)
            or is_under_automation_output_dir(path, watch_root)
            or self._is_under_output_collection_dir(path, watch_root)
        )

    def _is_under_output_collection_dir(self, path: str, watch_root: str = "") -> bool:
        try:
            config = normalize_output_collection_config(
                self.config.get(AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY, {})
            )
        except Exception:
            return False
        collection_path = config.get("output_path")
        if not collection_path:
            return False
        try:
            path = os.path.abspath(path)
            collection_path = os.path.abspath(os.path.expanduser(collection_path))
            if watch_root:
                relative_collection = os.path.relpath(collection_path, os.path.abspath(watch_root))
                if self._relative_escapes_root(relative_collection):
                    return False
            relative = os.path.relpath(path, collection_path)
            return not self._relative_escapes_root(relative)
        except (OSError, ValueError):
            return False

    @staticmethod
    def _relative_escapes_root(relative_path: str) -> bool:
        return relative_path == ".." or relative_path.startswith(f"..{os.sep}") or relative_path.startswith("../") or relative_path.startswith("..\\")

    def _filter_system_dirs(self, root: str, dirs: List[str], rule: WatchRule, excluded_dirs: Set[str] = None) -> None:
        excluded_dirs = excluded_dirs or set()
        dirs[:] = [
            d for d in dirs
            if d != AUTOMATION_GLOSSARY_DIR_NAME
            and os.path.abspath(os.path.join(root, d)) not in excluded_dirs
            and not self._is_system_ignored_path(os.path.join(root, d), rule)
        ]

    def _excluded_dirs_for_rule(self, rule: WatchRule) -> Set[str]:
        return {
            os.path.abspath(path)
            for path in (rule.output_path, rule.done_path)
            if path
        }

    @staticmethod
    def _is_dir_excluded(path: str, excluded_dirs: Set[str]) -> bool:
        path = os.path.abspath(path)
        return any(path == excluded or path.startswith(excluded + os.sep) for excluded in excluded_dirs)

    def _is_detectable_directory(self, path: str, rule: WatchRule, excluded_dirs: Set[str] = None) -> bool:
        name = os.path.basename(os.path.normpath(path))
        if name == AUTOMATION_GLOSSARY_DIR_NAME:
            return False
        if self._is_system_ignored_path(path, rule):
            return False
        if excluded_dirs and self._is_dir_excluded(path, excluded_dirs):
            return False
        return True

    def _iter_files_in_folder(self, folder_path: str, rule: WatchRule, include_unmatched: bool = True) -> List[str]:
        result = []
        excluded_dirs = self._excluded_dirs_for_rule(rule)
        try:
            for root, dirs, files in os.walk(folder_path):
                self._filter_system_dirs(root, dirs, rule, excluded_dirs)
                for filename in files:
                    path = os.path.join(root, filename)
                    if self._is_system_ignored_path(path, rule):
                        continue
                    if include_unmatched or rule.matches_pattern(filename):
                        result.append(os.path.abspath(path))
        except OSError:
            pass
        return natural_sort_paths(result)

    def _iter_matching_files_in_folder(self, folder_path: str, rule: WatchRule) -> List[str]:
        return self._iter_files_in_folder(folder_path, rule, include_unmatched=False)

    def _analyze_folder_series(self, folder_path: str, rule: WatchRule) -> dict:
        files = self._iter_matching_files_in_folder(folder_path, rule)
        if not files:
            return {
                "is_series": False,
                "reason": "no_matching_files",
                "files": [],
                "volumes": [],
                "missing_volumes": [],
            }

        parsed_items = []
        missing_parse = []
        for path in files:
            parsed = parse_series_volume(path)
            if not parsed.get("volume"):
                missing_parse.append(path)
                continue
            family_key = normalize_series_family_key(parsed.get("series_key"))
            folder_key = normalize_series_family_key(os.path.basename(os.path.normpath(folder_path)))
            parent_key = normalize_series_family_key(os.path.basename(os.path.dirname(os.path.abspath(path))))
            if not family_key or family_key == "series" or family_key == parent_key:
                family_key = folder_key
            parsed = dict(parsed)
            parsed["series_key"] = family_key or os.path.basename(os.path.normpath(folder_path))
            parsed["path"] = path
            parsed_items.append(parsed)

        if missing_parse:
            return {
                "is_series": False,
                "reason": "unparsed_volume",
                "files": files,
                "unparsed_files": [os.path.basename(path) for path in missing_parse],
                "volumes": [],
                "missing_volumes": [],
            }

        family_keys = {item["series_key"] for item in parsed_items}
        if len(family_keys) != 1:
            return {
                "is_series": False,
                "reason": "mixed_series",
                "files": files,
                "families": natural_sort_paths(family_keys),
                "volumes": sorted(item["volume"] for item in parsed_items),
                "missing_volumes": [],
            }

        volumes = sorted({item["volume"] for item in parsed_items})
        missing_volumes = []
        if volumes:
            expected = set(range(min(volumes), max(volumes) + 1))
            missing_volumes = sorted(expected - set(volumes))

        parsed_by_path = {
            item["path"]: item
            for item in parsed_items
        }
        sorted_files = sorted(files, key=lambda path: (parsed_by_path[path]["volume"], natural_sort_key(path)))
        return {
            "is_series": True,
            "reason": "series_detected",
            "files": sorted_files,
            "series_key": parsed_items[0]["series_key"],
            "volumes": volumes,
            "missing_volumes": missing_volumes,
            "parsed": parsed_by_path,
        }

    def _scan_rule_file_entries(
        self,
        rule: WatchRule,
        include_unmatched: bool,
        scan_limit: int,
    ) -> tuple:
        entries = []
        truncated = False

        def add_entry(path: str, name: str, path_type: str = "file") -> bool:
            nonlocal truncated
            if self._is_system_ignored_path(path, rule):
                return True
            matched = rule.matches_pattern(name) if path_type == "file" else True
            if not matched and not include_unmatched:
                return True
            try:
                if path_type == "folder":
                    size, mtime, _ = FileState._snapshot_path(path)
                else:
                    stat = os.stat(path)
                    size = stat.st_size
                    mtime = stat.st_mtime
            except OSError:
                return True
            entries.append({
                "path": os.path.abspath(path),
                "name": name,
                "matched": matched,
                "size": size,
                "mtime": mtime,
                "path_type": path_type,
            })
            if len(entries) >= scan_limit:
                truncated = True
                return False
            return True

        try:
            excluded_dirs = self._excluded_dirs_for_rule(rule)
            if rule.recursive:
                for root, dirs, files in os.walk(rule.watch_path):
                    if rule.detects_folders() and os.path.abspath(root) == os.path.abspath(rule.watch_path):
                        for dirname in list(dirs):
                            path = os.path.join(root, dirname)
                            if self._is_detectable_directory(path, rule, excluded_dirs):
                                if not add_entry(path, dirname, "folder"):
                                    return entries, truncated
                    self._filter_system_dirs(root, dirs, rule, excluded_dirs)
                    if rule.detects_files() or rule.detects_folders():
                        for filename in files:
                            if not add_entry(os.path.join(root, filename), filename, "file"):
                                return entries, truncated
            else:
                for entry in os.scandir(rule.watch_path):
                    if entry.is_dir() and rule.detects_folders() and self._is_detectable_directory(entry.path, rule, excluded_dirs):
                        if not add_entry(entry.path, entry.name, "folder"):
                            return entries, truncated
                        for child_path in self._iter_files_in_folder(entry.path, rule, include_unmatched=include_unmatched):
                            if not add_entry(child_path, os.path.basename(child_path), "file"):
                                return entries, truncated
                    if entry.is_file() and rule.detects_files() and not add_entry(entry.path, entry.name, "file"):
                        return entries, truncated
        except OSError:
            pass

        return entries, truncated

    def _get_queue_task_lookup(self) -> dict:
        lookup = {"by_rule_path": {}, "by_path": {}}
        try:
            from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

            queue_manager = QueueManager()
            for task in queue_manager.tasks:
                if getattr(task, "source", None) != "watch":
                    continue
                input_path = os.path.abspath(getattr(task, "input_path", "") or "")
                rule_id = getattr(task, "rule_id", None)
                info = {
                    "status": getattr(task, "status", "waiting"),
                    "workflow": self._describe_workflow(getattr(task, "workflow_steps", []) or []),
                    "locked": getattr(task, "locked", False),
                    "is_processing": getattr(task, "is_processing", False),
                    "trigger_file_path": getattr(task, "trigger_file_path", None),
                    "trigger_file_name": getattr(task, "trigger_file_name", None),
                    "trigger_detected_at": getattr(task, "trigger_detected_at", None),
                }
                lookup["by_path"][input_path] = info
                if rule_id:
                    lookup["by_rule_path"][(rule_id, input_path)] = info
                trigger_path = os.path.abspath(getattr(task, "trigger_file_path", "") or "")
                if trigger_path:
                    lookup["by_path"][trigger_path] = info
                    if rule_id:
                        lookup["by_rule_path"][(rule_id, trigger_path)] = info
        except Exception:
            pass
        return lookup

    def _find_queue_task_for_file(self, file_path: str, rule: WatchRule, queue_lookup: dict) -> Optional[dict]:
        file_path = os.path.abspath(file_path)
        direct_task = (
            queue_lookup.get("by_rule_path", {}).get((rule.id, file_path))
            or queue_lookup.get("by_path", {}).get(file_path)
        )
        if direct_task:
            return direct_task

        target_path = rule.watch_path if rule.trigger_mode == "folder" else file_path
        target_path = os.path.abspath(target_path)
        target_task = (
            queue_lookup.get("by_rule_path", {}).get((rule.id, target_path))
            or queue_lookup.get("by_path", {}).get(target_path)
        )
        trigger_file_path = target_task.get("trigger_file_path") if target_task else None
        if trigger_file_path and os.path.abspath(trigger_file_path) != file_path:
            return None
        return target_task

    @staticmethod
    def _format_snapshot_time(value) -> str:
        if isinstance(value, datetime):
            return value.strftime("%m-%d %H:%M:%S")
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value).strftime("%m-%d %H:%M:%S")
            except ValueError:
                return value
        if isinstance(value, (int, float)) and value:
            return datetime.fromtimestamp(value).strftime("%m-%d %H:%M:%S")
        return "-"

    @staticmethod
    def _status_enters_workflow(status: str) -> bool:
        return status in {
            "waiting",
            "queued",
            "workflow",
            "translating",
            "translated",
            "polishing",
            "processing",
            "completed",
            "partial",
            "done",
            "processed",
        }

    def _build_file_status_row(self, rule: WatchRule, entry: dict, queue_lookup: dict) -> dict:
        path = entry["path"]
        matched = entry["matched"]
        observation = self._record_file_observation(
            path,
            rule,
            matched,
            "ignored" if not matched else None,
            path_type=entry.get("path_type"),
        )
        state = self.file_states.get(path)
        path_type = observation.get("path_type") or entry.get("path_type") or "file"
        queue_task = self._find_queue_task_for_file(path, rule, queue_lookup) if matched and path_type != "folder" else None
        processed = self._get_file_key(path) in self.processed_files if matched else False
        observation_status = observation.get("status")

        if not matched:
            status = "ignored"
        elif path_type == "folder":
            if state and state.status in {"pending", "waiting_stable"}:
                status = state.status
            elif observation_status in {"ignored", "primed", "error"}:
                status = observation_status
            elif processed or observation_status in {"queued", "done", "processed", "processing"}:
                status = "folder_expanded"
            elif not rule.enabled:
                status = "rule_disabled"
            elif not self.running:
                status = "watch_stopped"
            else:
                status = "ready"
        elif queue_task:
            status = queue_task.get("status") or "queued"
        elif state:
            status = state.status
        elif processed:
            status = observation_status if observation_status in {"queued", "done", "primed", "processed"} else "processed"
        elif observation_status in {"queued", "done", "error", "processing", "waiting_stable", "waiting_capacity", "waiting_target"}:
            status = observation_status
        elif not rule.enabled:
            status = "rule_disabled"
        elif not self.running:
            status = "watch_stopped"
        else:
            status = "ready"

        detected_at = queue_task.get("trigger_detected_at") if queue_task and queue_task.get("trigger_detected_at") else state.first_seen if state else observation.get("detected_at")
        workflow = queue_task.get("workflow") if queue_task and queue_task.get("workflow") else observation.get("workflow", "")
        entered_workflow = False if path_type == "folder" else bool(queue_task) or self._status_enters_workflow(status)
        series = observation.get("series") or {}

        return {
            "rule_id": rule.id,
            "path": path,
            "file": observation.get("relative_path") or entry["name"],
            "path_type": path_type,
            "matched": matched,
            "patterns": ", ".join(rule.file_patterns),
            "detected_at": self._format_snapshot_time(detected_at),
            "mtime": self._format_snapshot_time(entry.get("mtime")),
            "size": entry.get("size", 0),
            "status": status,
            "entered_workflow": entered_workflow,
            "workflow": workflow,
            "queue_only": bool(matched and not workflow),
            "series_key": series.get("series_key", ""),
            "series_volume": series.get("volume", ""),
            "series_is_folder": bool(path_type == "folder" and series),
            "series_detected": bool(series.get("is_series")),
            "series_reason": series.get("reason", ""),
            "series_missing_volumes": series.get("missing_volumes", []),
            "series_volumes": series.get("volumes", []),
        }

    def get_file_status_snapshot(
        self,
        limit_per_rule: int = 30,
        include_unmatched: bool = True,
    ) -> List[dict]:
        """获取监控目录当前文件状态快照。"""
        queue_lookup = self._get_queue_task_lookup()
        limit_per_rule = max(1, limit_per_rule)
        scan_limit = max(limit_per_rule * 5, 100)

        with self._lock:
            snapshots = []
            for rule in self.rules.values():
                entries, truncated = self._scan_rule_file_entries(rule, include_unmatched, scan_limit)
                rows = [self._build_file_status_row(rule, entry, queue_lookup) for entry in entries]
                rows.sort(key=lambda item: (item["path_type"] != "folder", natural_sort_key(item["file"])))

                visible_rows = rows[:limit_per_rule]
                snapshots.append({
                    "rule_id": rule.id,
                    "watch_path": rule.watch_path,
                    "patterns": list(rule.file_patterns),
                    "enabled": rule.enabled,
                    "running": self.running,
                    "files": visible_rows,
                    "omitted": max(0, len(rows) - len(visible_rows)),
                    "truncated": truncated or len(rows) > len(visible_rows),
                })

            return snapshots

    def start(self):
        """启动监控"""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()
        self._load_processed_files()
        self._prime_existing_files()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        self._log("info", "Watch manager started")

    def stop(self):
        """停止监控"""
        if not self.running:
            return

        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._save_processed_files()
        self._log("info", "Watch manager stopped")

    def _watch_loop(self):
        """监控主循环"""
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    for rule in self.rules.values():
                        if rule.enabled:
                            self._scan_directory(rule)

                # 等待下一次扫描
                self._stop_event.wait(self.scan_interval)

            except Exception as e:
                self._log("error", f"Watch loop error: {e}")
                time.sleep(30)

    def _scan_directory(self, rule: WatchRule):
        """扫描目录"""
        try:
            paths = self._scan_watch_targets(rule)
            for path in paths:
                if os.path.isdir(path):
                    self._process_folder(path, rule)
                else:
                    self._process_file(path, rule)

        except Exception as e:
            self._log("error", f"Scan error for {rule.watch_path}: {e}")

    def _scan_watch_targets(self, rule: WatchRule) -> List[str]:
        result = []
        if rule.detects_folders():
            result.extend(self._scan_folders(rule.watch_path, rule))
        if rule.detects_files():
            if rule.detects_folders():
                result.extend(self._scan_flat(rule.watch_path, rule))
            else:
                result.extend(self._scan_recursive(rule.watch_path, rule) if rule.recursive else self._scan_flat(rule.watch_path, rule))
        return natural_sort_paths(result)

    def _scan_flat(self, directory: str, rule: WatchRule) -> List[str]:
        """扫描单层目录"""
        result = []
        try:
            for entry in os.scandir(directory):
                if entry.is_dir() and entry.name == AUTOMATION_GLOSSARY_DIR_NAME:
                    continue
                if entry.is_file() and rule.matches_pattern(entry.name):
                    result.append(entry.path)
        except OSError:
            pass
        return natural_sort_paths(result)

    def _scan_folders(self, directory: str, rule: WatchRule) -> List[str]:
        result = []
        excluded_dirs = self._excluded_dirs_for_rule(rule)
        try:
            for entry in os.scandir(directory):
                if entry.is_dir() and self._is_detectable_directory(entry.path, rule, excluded_dirs):
                    result.append(entry.path)
        except OSError:
            pass
        return natural_sort_paths(result)

    def _scan_recursive(self, directory: str, rule: WatchRule) -> List[str]:
        """递归扫描目录"""
        result = []
        try:
            excluded_dirs = {
                os.path.abspath(path)
                for path in (rule.output_path, rule.done_path)
                if path
            }
            for root, dirs, files in os.walk(directory):
                self._filter_system_dirs(root, dirs, rule, excluded_dirs)

                for filename in files:
                    path = os.path.join(root, filename)
                    if not self._is_system_ignored_path(path, rule) and rule.matches_pattern(filename):
                        result.append(path)
        except OSError:
            pass
        return natural_sort_paths(result)

    def _prime_existing_files(self):
        """Record existing files so watch mode reacts to future changes by default."""
        for rule in self.rules.values():
            if rule.settle_existing:
                continue
            try:
                paths = self._scan_watch_targets(rule)
                for path in paths:
                    self.processed_files.add(self._get_file_key(path))
                    path_type = "folder" if os.path.isdir(path) else "file"
                    self._record_file_observation(path, rule, True, "primed", path_type=path_type)
                    if path_type == "folder":
                        self._prime_existing_folder_children(path, rule)
            except Exception as e:
                self._log("warning", f"Failed to prime existing files for {rule.watch_path}: {e}")

    def _prime_existing_folder_children(self, folder_path: str, rule: WatchRule):
        for child_path in self._iter_matching_files_in_folder(folder_path, rule):
            self.processed_files.add(self._get_file_key(child_path))
            series = {}
            try:
                folder_series = self._analyze_folder_series(folder_path, rule)
                if folder_series.get("is_series"):
                    series = (folder_series.get("parsed") or {}).get(os.path.abspath(child_path), {}) or {}
            except Exception:
                series = parse_series_volume(child_path) if rule.series_incremental else {}
            self._record_file_observation(child_path, rule, True, "primed", path_type="file", series=series)

    def _process_file(self, file_path: str, rule: WatchRule):
        """处理检测到的文件"""
        self._process_single_target(file_path, rule, None)

    def _process_single_target(
        self,
        file_path: str,
        rule: WatchRule,
        folder_series: dict = None,
        skip_stability: bool = False,
        queue_options: dict = None,
    ) -> bool:
        """处理检测到的单个文件目标"""
        if self._is_system_ignored_path(file_path, rule):
            return False
        file_key = self._get_file_key(file_path)
        if file_key in self.processed_files:
            observation = self.file_observations.get(self._observation_key(file_path, rule))
            if observation and observation.get("status") == "primed":
                return False
            if observation and observation.get("status") == "partial":
                self._record_file_observation(file_path, rule, True, "partial")
                return False
            self._record_file_observation(file_path, rule, True, "processed")
            return False

        self._record_file_observation(file_path, rule, True, "pending")

        # 获取或创建文件状态
        if file_path not in self.file_states:
            self.file_states[file_path] = FileState(file_path)

        state = self.file_states[file_path]

        # 更新状态
        state.update()

        # 检查文件是否稳定
        if not skip_stability and not state.is_stable(rule.debounce_seconds):
            state.status = "waiting_stable"
            self._record_file_observation(file_path, rule, True, "waiting_stable")
            return False

        # 检查并发限制
        if self.current_tasks >= self.max_concurrent:
            state.status = "waiting_capacity"
            self._record_file_observation(file_path, rule, True, "waiting_capacity")
            return False

        # 标记为处理中
        state.status = "processing"
        self._record_file_observation(file_path, rule, True, "processing")
        self.current_tasks += 1
        target_path = file_path if folder_series is not None else rule.watch_path if rule.trigger_mode == "folder" else file_path
        target_key = self._get_file_key(target_path)
        if target_key in self._active_targets:
            self.current_tasks -= 1
            state.status = "waiting_target"
            self._record_file_observation(file_path, rule, True, "waiting_target")
            return False
        self._active_targets.add(target_key)

        # 创建任务
        task_config = {
            "input_path": target_path,
            "output_path": rule.output_path or self._generate_output_path(target_path),
            "profile": rule.profile,
            "rules_profile": rule.rules_profile,
            "task_type": rule.task_type,
            "source": "watch",
            "rule_id": rule.id,
            "auto_start": rule.auto_start,
            "workflow_steps": rule.workflow_steps,
            "series_incremental": rule.series_incremental,
            "trigger_file_path": file_path,
            "trigger_file_name": os.path.basename(file_path),
            "trigger_detected_at": datetime.now().isoformat(timespec="seconds"),
        }
        # Preserve all contract fields stored by older watch-rule versions,
        # including the ``lines``/``tokens`` aliases.
        task_config.update(select_task_contract_fields(rule.extra))
        series_enabled = bool(rule.series_incremental)
        series = {}
        if folder_series is not None:
            series_enabled = False
            if folder_series.get("is_series"):
                series = (folder_series.get("parsed") or {}).get(os.path.abspath(file_path), {}) or {}
                if series:
                    series_enabled = bool(rule.series_incremental)
                    task_config["series_container_path"] = folder_series.get("folder_path")
                    task_config["series_missing_volumes"] = folder_series.get("missing_volumes", [])
        elif rule.series_incremental:
            series = parse_series_volume(file_path)
        task_config["series_incremental"] = bool(series_enabled and series)
        if series_enabled:
            if series:
                task_config["series_key"] = series.get("series_key")
                task_config["series_volume"] = series.get("volume")
                self._apply_series_context_to_workflow(task_config, series)
            else:
                self._log("warning", f"Series volume not recognized: {os.path.basename(file_path)}")

        if queue_options:
            task_config.update(queue_options)

        workflow_description = self._describe_workflow(rule.workflow_steps)
        self._log(
            "info",
            f"New file detected: {os.path.basename(file_path)} | workflow: {workflow_description or 'Queue only'}",
        )

        # 默认交给队列系统管理；只有未配置队列回调时才直接执行。
        if self.queue_callback:
            # 加入队列
            try:
                self.queue_callback(task_config)
                self._mark_processed(file_path, rule, "queued", mark_related=folder_series is None)
                return True
            except Exception as e:
                self._log("error", f"Failed to queue task: {e}")
                state.status = "error"
                self._record_file_observation(file_path, rule, True, "error")
                return False
            finally:
                self.current_tasks -= 1
                self._active_targets.discard(target_key)
        elif rule.auto_start and self.task_callback:
            # 在新线程中执行
            exec_thread = threading.Thread(
                target=self._execute_task,
                args=(file_path, rule, task_config, target_key),
                daemon=True
            )
            exec_thread.start()
            return True
        else:
            self._log("warning", "No task or queue callback configured")
            state.status = "error"
            self._record_file_observation(file_path, rule, True, "error")
            self.current_tasks -= 1
            self._active_targets.discard(target_key)
            return False

    def _process_folder(self, folder_path: str, rule: WatchRule):
        """处理检测到的文件夹。"""
        folder_path = os.path.abspath(folder_path)
        if self._is_system_ignored_path(folder_path, rule):
            return
        folder_key = self._get_file_key(folder_path)
        if folder_key in self.processed_files:
            observation = self.file_observations.get(self._observation_key(folder_path, rule))
            if observation and observation.get("status") == "primed":
                self._prime_existing_folder_children(folder_path, rule)
                return
            pending_files = [
                file_path
                for file_path in self._iter_matching_files_in_folder(folder_path, rule)
                if self._get_file_key(file_path) not in self.processed_files
            ]
            if pending_files:
                self.processed_files.discard(folder_key)
            else:
                self._record_file_observation(folder_path, rule, True, "processed", path_type="folder")
                return

        self._record_file_observation(folder_path, rule, True, "pending", path_type="folder")
        if folder_path not in self.file_states:
            self.file_states[folder_path] = FileState(folder_path)
        state = self.file_states[folder_path]
        state.update()

        if not state.is_stable(rule.debounce_seconds):
            state.status = "waiting_stable"
            self._record_file_observation(folder_path, rule, True, "waiting_stable", path_type="folder")
            return

        files = self._iter_matching_files_in_folder(folder_path, rule)
        if not files:
            state.status = "ignored"
            self._record_file_observation(folder_path, rule, True, "ignored", path_type="folder")
            self.processed_files.add(folder_key)
            del self.file_states[folder_path]
            return

        folder_series = self._analyze_folder_series(folder_path, rule)
        folder_series["folder_path"] = folder_path
        if folder_series.get("is_series"):
            self._log(
                "info",
                f"Folder series detected: {os.path.basename(folder_path)} | volumes: {folder_series.get('volumes', [])}",
            )
            if folder_series.get("missing_volumes"):
                self._log(
                    "warning",
                    f"Folder series has missing volumes: {os.path.basename(folder_path)} | missing: {folder_series.get('missing_volumes')}",
                )
        else:
            self._log(
                "info",
                f"Folder queued as ordinary files: {os.path.basename(folder_path)} | reason: {folder_series.get('reason')}",
            )

        self._record_file_observation(
            folder_path,
            rule,
            True,
            "processing",
            path_type="folder",
            series=folder_series,
        )

        queued_count = 0
        pending_files = [
            os.path.abspath(file_path)
            for file_path in natural_sort_paths(folder_series.get("files", files))
            if self._get_file_key(file_path) not in self.processed_files
        ]
        batch_total = len(pending_files)
        series_volumes = sorted(folder_series.get("volumes") or [])
        series_initial_volume = series_volumes[0] if folder_series.get("is_series") and series_volumes else None
        batch_id = hashlib.sha1(
            f"{rule.id}:{folder_path}:{time.time_ns()}".encode("utf-8", errors="ignore")
        ).hexdigest()[:12] if batch_total else ""
        should_defer_auto_start = bool(self.queue_start_callback)
        for batch_index, file_path in enumerate(pending_files, start=1):
            queue_options = {
                "folder_batch_id": batch_id,
                "folder_batch_index": batch_index,
                "folder_batch_total": batch_total,
                "defer_auto_start": should_defer_auto_start,
            }
            if folder_series.get("is_series"):
                queue_options.update({
                    "series_initial_volume": series_initial_volume,
                    "series_batch_volumes": series_volumes,
                })
            queued = self._process_single_target(
                file_path,
                rule,
                folder_series,
                skip_stability=True,
                queue_options=queue_options,
            )
            if queued:
                queued_count += 1
        if queued_count and rule.auto_start and self.queue_start_callback:
            try:
                self.queue_start_callback()
            except Exception as e:
                self._log("error", f"Failed to start queued folder batch: {e}")

        self.processed_files.add(folder_key)
        state.status = "queued" if queued_count else "processed"
        self._record_file_observation(
            folder_path,
            rule,
            True,
            state.status,
            path_type="folder",
            series=folder_series,
        )
        rule.last_activity = datetime.now()
        if folder_path in self.file_states:
            del self.file_states[folder_path]

    def _execute_task(self, file_path: str, rule: WatchRule, task_config: dict, target_key: str):
        """执行任务"""
        state = self.file_states.get(file_path)
        try:
            self.task_callback(task_config)
            self._mark_processed(file_path, rule, "done")
            self._log("info", f"Task completed: {os.path.basename(file_path)}")
        except Exception as e:
            self._log("error", f"Task failed: {os.path.basename(file_path)} - {e}")
            if state:
                state.status = "error"
            self._record_file_observation(file_path, rule, True, "error")
        finally:
            self.current_tasks -= 1
            self._active_targets.discard(target_key)

    def _mark_processed(self, file_path: str, rule: WatchRule, status: str, mark_related: bool = True):
        """标记文件已处理"""
        file_key = self._get_file_key(file_path)
        self.processed_files.add(file_key)
        observation = self.file_observations.get(self._observation_key(file_path, rule))
        if status == "processed" and observation and observation.get("status") == "partial":
            self._record_file_observation(file_path, rule, True, "partial")
            return
        if mark_related and rule.trigger_mode == "folder":
            try:
                files = self._scan_recursive(rule.watch_path, rule) if rule.recursive else self._scan_flat(rule.watch_path, rule)
                for related_path in files:
                    self.processed_files.add(self._get_file_key(related_path))
            except Exception as e:
                self._log("warning", f"Failed to mark related files: {e}")

        state = self.file_states.get(file_path)
        if state:
            state.status = status
        self._record_file_observation(file_path, rule, True, status)

        rule.files_processed += 1
        rule.last_activity = datetime.now()

        # 移动到完成目录
        if rule.move_to_done and rule.done_path and status == "done":
            try:
                dest = os.path.join(rule.done_path, os.path.basename(file_path))
                # 处理同名文件
                if os.path.exists(dest):
                    base, ext = os.path.splitext(dest)
                    dest = f"{base}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
                shutil.move(file_path, dest)
                self._log("info", f"Moved to done: {os.path.basename(file_path)}")
            except Exception as e:
                self._log("warning", f"Failed to move file: {e}")

        # 清理状态
        if file_path in self.file_states:
            del self.file_states[file_path]

    def _get_file_key(self, file_path: str) -> str:
        """生成文件唯一标识"""
        # 使用路径 + 修改时间 + 大小作为标识
        try:
            stat = os.stat(file_path)
            key_str = f"{file_path}:{stat.st_mtime}:{stat.st_size}"
            return hashlib.md5(key_str.encode()).hexdigest()
        except OSError:
            return hashlib.md5(file_path.encode()).hexdigest()

    def _generate_output_path(self, input_path: str) -> str:
        """生成输出路径"""
        abs_input = os.path.abspath(input_path)
        parent = os.path.dirname(abs_input)
        base_name = os.path.basename(os.path.normpath(abs_input))
        if os.path.isfile(abs_input):
            base_name = os.path.splitext(base_name)[0]
        return os.path.join(parent, f"{base_name}_AiNiee_Output")

    def _load_processed_files(self):
        """加载已处理文件记录"""
        if not self.processed_file_path:
            return

        try:
            if os.path.exists(self.processed_file_path):
                with open(self.processed_file_path, 'r', encoding='utf-8') as f:
                    self.processed_files = set(line.strip() for line in f)
        except Exception:
            pass

    def _save_processed_files(self):
        """保存已处理文件记录"""
        if not self.processed_file_path:
            return

        try:
            with open(self.processed_file_path, 'w', encoding='utf-8') as f:
                for file_key in self.processed_files:
                    f.write(file_key + '\n')
        except Exception:
            pass

    def _log(self, level: str, message: str):
        """记录日志"""
        log_entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": message
        }
        self.logs.append(log_entry)

        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]

    def _describe_workflow(self, steps: List[dict]) -> str:
        return describe_workflow_steps(steps, getattr(Base, "i18n", None))

    @staticmethod
    def _apply_series_context_to_workflow(task_config: dict, series: dict) -> None:
        steps = []
        for step in task_config.get("workflow_steps") or []:
            prepared = dict(step)
            if prepared.get("type") == "extract_glossary":
                prepared["series_incremental"] = True
                prepared.setdefault("source_volume", series.get("volume"))
                prepared.setdefault("source_label", series.get("label"))
            steps.append(prepared)
        task_config["workflow_steps"] = steps

    def get_logs(self, limit: int = 20) -> List[dict]:
        """获取最近的日志"""
        return self.logs[-limit:]

    def load_from_config(self, config: dict):
        """从配置加载规则"""
        watch_config = config.get("watch_mode", {})

        self.scan_interval = watch_config.get("scan_interval", 1)
        self.max_concurrent = watch_config.get("max_concurrent", 2)

        rules_data = watch_config.get("rules", [])
        for rule_data in rules_data:
            try:
                rule = WatchRule.from_dict(rule_data)
                self.add_rule(rule)
            except Exception as e:
                self._log("error", f"Failed to load rule: {e}")

    def save_to_config(self, config: dict):
        """保存规则到配置"""
        if "watch_mode" not in config:
            config["watch_mode"] = {}

        config["watch_mode"]["rules"] = [
            rule.to_dict() for rule in self.rules.values()
        ]
        config["watch_mode"]["enabled"] = self.running
        config["watch_mode"]["scan_interval"] = self.scan_interval
        config["watch_mode"]["max_concurrent"] = self.max_concurrent

    def get_status(self) -> dict:
        """获取监控状态"""
        return {
            "running": self.running,
            "rule_count": len(self.rules),
            "enabled_count": sum(1 for r in self.rules.values() if r.enabled),
            "pending_files": len([s for s in self.file_states.values()
                                  if s.status in {"pending", "waiting_stable", "waiting_capacity", "waiting_target"}]),
            "current_tasks": self.current_tasks,
            "total_processed": sum(r.files_processed for r in self.rules.values())
        }

    def clear_processed_history(self):
        """清除已处理文件历史"""
        self.processed_files.clear()
        self.file_states.clear()
        self.file_observations.clear()
        self._log("info", "Processed history cleared")

    def requeue_file(self, file_path: str, rule_id: str, workflow: str = "") -> bool:
        rule = self.rules.get(rule_id)
        if not rule or not file_path:
            return False

        path = os.path.abspath(file_path)
        observation = self._record_file_observation(path, rule, True, "queued")
        if workflow:
            observation["workflow"] = workflow
        self._log("info", f"Re-queued detected file: {os.path.basename(path)} | workflow: {workflow or 'Queue only'}")
        return True
