import threading
import time
import os
import copy
import uuid
from collections import Counter
import rapidjson as json
from datetime import datetime, timedelta
from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.SensitiveData import (
    contains_sensitive_data,
    sanitize_sensitive_data,
)
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType

class QueueTaskItem:
    def __init__(self, task_type, input_path, output_path=None, profile=None, rules_profile=None, 
                 source_lang=None, target_lang=None, project_type=None,
                 platform=None, api_url=None, api_key=None, model=None, 
                 threads=None, retry=None, timeout=None, rounds=None, 
                 pre_lines=None, lines_limit=None, tokens_limit=None, 
                 think_depth=None, thinking_budget=None, workflow_steps=None,
                 source=None, rule_id=None, automation_run_id=None,
                 automation_progress_file=None, automation_worker_pid=None,
                 trigger_file_path=None, trigger_file_name=None, trigger_detected_at=None,
                 series_incremental=False, series_key=None, series_volume=None, extra=None,
                 task_id=None):
        self.task_id = self.normalize_task_id(task_id)
        self.task_type = task_type
        self.input_path = input_path
        self.output_path = output_path
        self.profile = profile
        self.rules_profile = rules_profile
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.project_type = project_type
        
        # 精细化 API 覆盖参数
        self.platform = platform
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        
        # 性能覆盖参数
        self.threads = threads
        self.retry = retry
        self.timeout = timeout
        self.rounds = rounds
        self.pre_lines = pre_lines
        self.lines_limit = lines_limit
        self.tokens_limit = tokens_limit
        self.think_depth = think_depth
        self.thinking_budget = thinking_budget
        self.workflow_steps = workflow_steps or []
        self.source = source
        self.rule_id = rule_id
        self.automation_run_id = automation_run_id
        self.automation_progress_file = automation_progress_file
        self.automation_worker_pid = automation_worker_pid
        self.trigger_file_path = trigger_file_path
        self.trigger_file_name = trigger_file_name
        self.trigger_detected_at = trigger_detected_at
        self.series_incremental = bool(series_incremental)
        self.series_key = series_key
        self.series_volume = series_volume
        self.extra = extra if isinstance(extra, dict) else {}
        
        self.status = "waiting" # waiting, workflow, translating, translated, polishing, completed, partial, error, stopped
        self.locked = False  # 是否被锁定（正在执行中不可修改）

        # 新增：准确的处理状态跟踪
        self.is_processing = False  # 是否真正在处理中
        self.last_activity_time = None  # 最后活动时间（ISO格式字符串）
        self.process_start_time = None  # 处理开始时间

    @staticmethod
    def normalize_task_id(task_id):
        """Return a canonical UUID, generating one for legacy or invalid values."""
        try:
            return str(uuid.UUID(str(task_id)))
        except (AttributeError, TypeError, ValueError):
            return str(uuid.uuid4())

    def to_runtime_dict(self):
        """Return the full in-memory task, including temporary credential overrides."""
        return copy.deepcopy({k: v for k, v in vars(self).items() if not k.startswith('_')})

    def to_persistent_dict(self):
        """Return a task representation that is safe to write or expose."""
        return sanitize_sensitive_data(self.to_runtime_dict())

    def to_dict(self):
        """Keep the generic serializer safe by default."""
        return self.to_persistent_dict()

    @classmethod
    def from_dict(cls, data):
        # 兼容旧数据，剔除运行时字段后传入构造函数
        params = data.copy()
        status = params.pop("status", "waiting")
        locked = params.pop("locked", False)  # 移除locked字段，它不属于构造函数参数

        # 新增字段的处理（兼容旧数据）
        is_processing = params.pop("is_processing", False)
        last_activity_time = params.pop("last_activity_time", None)
        process_start_time = params.pop("process_start_time", None)

        item = cls(**params)
        item.status = status
        item.locked = locked
        item.is_processing = is_processing
        item.last_activity_time = last_activity_time
        item.process_start_time = process_start_time
        return item

class QueueManager(Base):
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(QueueManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized: return
        super().__init__()
        # 使用绝对路径确保跨目录一致性
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.join(script_dir, "..", "..", "..")
        project_root = os.path.normpath(project_root)
        self.default_queue_file = os.path.join(project_root, "Resource", "queue_tasks.json")
        self.queue_file = self.default_queue_file

        # 添加队列操作日志文件
        self.queue_log_file = os.path.join(project_root, "Resource", "queue_operations.log")

        self.tasks = []
        self.is_running = False
        self._automation_stop_requested = False
        self.current_task_index = -1
        self.load_tasks()
        self._initialized = True

    def _log_queue_operation(self, message):
        """记录队列操作日志到文件，用于跨进程通信"""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_entry = f"[{timestamp}] {message}\n"

            # 确保日志目录存在
            os.makedirs(os.path.dirname(self.queue_log_file), exist_ok=True)

            # 追加写入日志文件
            with open(self.queue_log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)

            # 同时输出到控制台（保留原有行为）
            print(message)

        except Exception as e:
            # 如果日志文件写入失败，至少保证控制台输出
            print(message)
            print(f"[WARNING] Failed to write queue log: {e}")

    def get_queue_log_path(self):
        """获取队列日志文件路径"""
        return self.queue_log_file

    def get_recent_queue_logs(self, lines=10):
        """获取最近的队列操作日志"""
        try:
            if not os.path.exists(self.queue_log_file):
                return []

            with open(self.queue_log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()

            # 返回最后几行，去掉换行符
            recent_lines = all_lines[-lines:] if len(all_lines) >= lines else all_lines
            return [line.strip() for line in recent_lines if line.strip()]

        except Exception as e:
            self.warning(f"Failed to read queue log: {e}")
            return []

    def clear_queue_logs(self):
        """清空队列操作日志"""
        try:
            if os.path.exists(self.queue_log_file):
                with open(self.queue_log_file, 'w', encoding='utf-8') as f:
                    f.write('')  # 清空文件内容
                return True
        except Exception as e:
            self.warning(f"Failed to clear queue log: {e}")
        return False

    def load_tasks(self, custom_path=None):
        if custom_path:
            self.queue_file = custom_path
        if os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                task_ids_changed = self._replace_tasks_from_data(data)
                # Legacy queue files may contain plaintext credentials. Keep them only
                # in this process for compatibility, then immediately scrub the file.
                if contains_sensitive_data(data) or task_ids_changed:
                    self.save_tasks()
            except Exception as e:
                self.error(f"Failed to load queue tasks: {e}")
                self.tasks = []
        else:
            self.tasks = []

    def save_tasks(self):
        try:
            os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
            with open(self.queue_file, 'w', encoding='utf-8') as f:
                json.dump([t.to_persistent_dict() for t in self.tasks], f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.error(f"Failed to save queue tasks: {e}")

    @staticmethod
    def _credential_context(task):
        return tuple(
            getattr(task, field, None)
            for field in ("platform", "api_url", "profile")
        )

    @staticmethod
    def _runtime_credentials_by_task_id(tasks):
        task_id_counts = Counter(getattr(task, "task_id", None) for task in tasks)
        return {
            task.task_id: (
                QueueManager._credential_context(task),
                getattr(task, "api_key", None),
            )
            for task in tasks
            if task_id_counts.get(getattr(task, "task_id", None)) == 1
        }

    @staticmethod
    def _restore_runtime_api_keys(old_tasks, new_tasks):
        runtime_credentials = QueueManager._runtime_credentials_by_task_id(old_tasks)
        incoming_id_counts = Counter(task.task_id for task in new_tasks)
        duplicate_ids = {
            task_id for task_id, count in incoming_id_counts.items() if count > 1
        }
        used_ids = {
            task.task_id for task in new_tasks if task.task_id not in duplicate_ids
        }

        for task in new_tasks:
            if task.task_id in duplicate_ids:
                task.task_id = QueueTaskItem.normalize_task_id(None)
                while task.task_id in used_ids:
                    task.task_id = QueueTaskItem.normalize_task_id(None)
                used_ids.add(task.task_id)
                continue
            credential_context, api_key = runtime_credentials.get(
                task.task_id,
                (None, None),
            )
            if (
                task.api_key is None
                and credential_context == QueueManager._credential_context(task)
            ):
                task.api_key = api_key

    def _replace_tasks_from_data(self, data):
        old_tasks = self.tasks
        new_tasks = [QueueTaskItem.from_dict(item) for item in data]
        self._restore_runtime_api_keys(old_tasks, new_tasks)
        task_ids_changed = any(
            not isinstance(item, dict) or item.get("task_id") != task.task_id
            for item, task in zip(data, new_tasks)
        )
        self.tasks = new_tasks
        return task_ids_changed

    def add_task(self, task_item):
        self.tasks.append(task_item)
        self.save_tasks()

    def remove_task(self, index):
        if 0 <= index < len(self.tasks):
            task_name = os.path.basename(self.tasks[index].input_path)
            self.tasks.pop(index)
            self.save_tasks()
            self.hot_reload_queue(quiet=True)  # 静默热刷新队列
            self._log_queue_operation(Base.i18n.get('msg_task_removed').format(task_name))
            return True
        return False

    def detect_parameter_changes(self, old_task, new_task):
        """检测任务参数变更并返回变更详情"""
        changes = []

        # 定义需要监控的参数及其对应的I18N键
        params_to_monitor = {
            'platform': 'param_platform',
            'api_url': 'param_api_url',
            'api_key': 'param_api_key',
            'model': 'param_model',
            'threads': 'param_threads',
            'source_lang': 'param_source_lang',
            'target_lang': 'param_target_lang',
            'retry': 'param_retry',
            'timeout': 'param_timeout',
            'rounds': 'param_rounds',
            'pre_lines': 'param_pre_lines',
            'lines_limit': 'param_lines_limit',
            'tokens_limit': 'param_tokens_limit',
            'think_depth': 'param_think_depth',
            'thinking_budget': 'param_thinking_budget',
            'task_type': 'param_task_type',
            'profile': 'param_profile',
            'rules_profile': 'param_rules_profile',
            'project_type': 'param_project_type'
        }

        for param, i18n_key in params_to_monitor.items():
            old_value = getattr(old_task, param, None)
            new_value = getattr(new_task, param, None)

            # 对于API密钥等敏感信息，只显示部分内容
            if param == 'api_key':
                if old_value != new_value:
                    old_display = self._mask_sensitive_value(old_value)
                    new_display = self._mask_sensitive_value(new_value)
                    if old_value is None and new_value is not None:
                        changes.append(Base.i18n.get('param_added').format(Base.i18n.get(i18n_key), new_display))
                    elif old_value is not None and new_value is None:
                        changes.append(Base.i18n.get('param_removed').format(Base.i18n.get(i18n_key)))
                    elif old_value != new_value:
                        changes.append(Base.i18n.get('param_changed').format(Base.i18n.get(i18n_key), old_display, new_display))
            else:
                # 普通参数的处理
                if old_value != new_value:
                    if old_value is None and new_value is not None:
                        changes.append(Base.i18n.get('param_added').format(Base.i18n.get(i18n_key), new_value))
                    elif old_value is not None and new_value is None:
                        changes.append(Base.i18n.get('param_removed').format(Base.i18n.get(i18n_key)))
                    elif old_value != new_value:
                        changes.append(Base.i18n.get('param_changed').format(Base.i18n.get(i18n_key), old_value, new_value))

        return changes

    def _mask_sensitive_value(self, value):
        """遮掩敏感信息"""
        if value is None:
            return None
        if len(str(value)) <= 8:
            return "****"
        else:
            return str(value)[:4] + "****" + str(value)[-4:]

    def update_task(self, index, task_item):
        if 0 <= index < len(self.tasks):
            try:
                old_task = self.tasks[index]
                task_name = os.path.basename(old_task.input_path)

                # 检测参数变更
                changes = self.detect_parameter_changes(old_task, task_item)

                # 更新任务
                self.tasks[index] = task_item
                self.save_tasks()
                self.hot_reload_queue(quiet=True)

                # 打印详细的变更日志
                if changes:
                    self._log_queue_operation(Base.i18n.get('msg_task_updated').format(task_name))
                    for change in changes:
                        self._log_queue_operation(change)
                else:
                    # 如果没有参数变更，只是简单的更新
                    self._log_queue_operation(f"[INFO] {Base.i18n.get('msg_task_updated').format(task_name)} {Base.i18n.get('msg_no_config_changes')}")

            except Exception as e:
                self._log_queue_operation(f"[ERROR] Failed to update task: {e}")
                return False

            return True
        return False

    def clear_tasks(self):
        self.tasks = []
        self.save_tasks()
        return True

    def lock_task(self, index):
        """锁定任务（正在执行中）"""
        if 0 <= index < len(self.tasks):
            self.tasks[index].locked = True
            self.save_tasks()
            return True
        return False

    def unlock_task(self, index):
        """解锁任务"""
        if 0 <= index < len(self.tasks):
            self.tasks[index].locked = False
            self.save_tasks()
            return True
        return False

    def can_modify_task(self, index):
        """检查任务是否可以被修改（未锁定）"""
        if 0 <= index < len(self.tasks):
            return not self.tasks[index].locked
        return False

    # ================ 智能处理状态管理 ================

    def update_task_activity(self, index):
        """更新任务活动时间（心跳机制）"""
        if 0 <= index < len(self.tasks):
            self.tasks[index].last_activity_time = datetime.now().isoformat()
            self.save_tasks()
            return True
        return False

    def start_task_processing(self, index):
        """开始处理任务 - 设置处理状态和时间戳"""
        if 0 <= index < len(self.tasks):
            task = self.tasks[index]
            now = datetime.now().isoformat()
            task.is_processing = True
            task.process_start_time = now
            task.last_activity_time = now
            task.locked = True
            self.save_tasks()
            return True
        return False

    def stop_task_processing(self, index):
        """停止处理任务 - 清除处理状态"""
        if 0 <= index < len(self.tasks):
            task = self.tasks[index]
            task.is_processing = False
            task.process_start_time = None
            task.last_activity_time = None
            task.locked = False
            self.save_tasks()
            return True
        return False

    def is_task_actually_processing(self, index, timeout_minutes=5):
        """
        检查任务是否真正在处理中

        Args:
            index: 任务索引
            timeout_minutes: 超时时间（分钟），超过此时间没有活动则认为不在处理中

        Returns:
            bool: 是否真正在处理中
        """
        if not (0 <= index < len(self.tasks)):
            return False

        task = self.tasks[index]

        # 如果明确标记为未处理，直接返回False
        if not task.is_processing:
            return False

        # 检查最后活动时间
        if not task.last_activity_time:
            return False

        try:
            last_activity = datetime.fromisoformat(task.last_activity_time)
            now = datetime.now()
            inactive_time = now - last_activity

            # 如果超过超时时间没有活动，认为不在处理中
            if inactive_time > timedelta(minutes=timeout_minutes):
                self.warning(f"Task {index+1} has been inactive for {inactive_time.total_seconds():.1f} seconds, marking as not processing")
                return False

            return True

        except (ValueError, TypeError) as e:
            self.warning(f"Invalid activity time format for task {index+1}: {e}")
            return False

    def cleanup_stale_locks(self, timeout_minutes=5):
        """
        清理过期的锁定状态

        Args:
            timeout_minutes: 超时时间（分钟）

        Returns:
            int: 清理的任务数量
        """
        cleaned_count = 0

        for i, task in enumerate(self.tasks):
            if task.locked and not self.is_task_actually_processing(i, timeout_minutes):
                self.info(f"Cleaning stale lock for task {i+1}: {task.input_path}")
                task.locked = False
                task.is_processing = False
                task.process_start_time = None
                task.last_activity_time = None

                # 重置状态到合适的值
                if task.status in ["translating", "polishing"]:
                    task.status = "waiting"

                cleaned_count += 1

        if cleaned_count > 0:
            self.save_tasks()
            self.info(f"Cleaned {cleaned_count} stale task locks")

        return cleaned_count

    def get_task_processing_status(self, index):
        """
        获取任务的详细处理状态

        Returns:
            dict: 包含处理状态信息的字典
        """
        if not (0 <= index < len(self.tasks)):
            return None

        task = self.tasks[index]
        is_actually_processing = self.is_task_actually_processing(index)

        status_info = {
            "locked": task.locked,
            "is_processing": task.is_processing,
            "is_actually_processing": is_actually_processing,
            "process_start_time": task.process_start_time,
            "last_activity_time": task.last_activity_time,
            "status": task.status
        }

        return status_info

    def move_task_up(self, index):
        """将指定索引的任务向上移动一位"""
        if (1 <= index < len(self.tasks) and
            self.can_modify_task(index) and self.can_modify_task(index - 1)):
            task_name = os.path.basename(self.tasks[index].input_path)
            self.tasks[index], self.tasks[index - 1] = self.tasks[index - 1], self.tasks[index]
            self.save_tasks()
            self.hot_reload_queue(quiet=True)  # 静默热刷新队列
            self._log_queue_operation(Base.i18n.get('msg_task_moved_up').format(task_name, index+1, index))
            return True
        return False

    def move_task_down(self, index):
        """将指定索引的任务向下移动一位"""
        if (0 <= index < len(self.tasks) - 1 and
            self.can_modify_task(index) and self.can_modify_task(index + 1)):
            task_name = os.path.basename(self.tasks[index].input_path)
            self.tasks[index], self.tasks[index + 1] = self.tasks[index + 1], self.tasks[index]
            self.save_tasks()
            self.hot_reload_queue(quiet=True)  # 静默热刷新队列
            self._log_queue_operation(Base.i18n.get('msg_task_moved_down').format(task_name, index+1, index+2))
            return True
        return False

    def move_task(self, from_index, to_index):
        """将任务从from_index移动到to_index位置"""
        if (0 <= from_index < len(self.tasks) and
            0 <= to_index < len(self.tasks) and
            from_index != to_index and
            self.can_modify_task(from_index)):

            # 检查移动路径上是否有锁定的任务
            start, end = min(from_index, to_index), max(from_index, to_index)
            for i in range(start, end + 1):
                if i != from_index and not self.can_modify_task(i):
                    return False

            # 移除任务
            task = self.tasks.pop(from_index)
            task_name = os.path.basename(task.input_path)
            # 插入到新位置
            self.tasks.insert(to_index, task)
            self.save_tasks()
            self.hot_reload_queue(quiet=True)  # 静默热刷新队列
            self._log_queue_operation(Base.i18n.get('msg_task_moved').format(task_name, from_index+1, to_index+1))
            return True
        return False

    def reorder_tasks(self, new_order):
        """根据新的索引顺序重新排列任务

        Args:
            new_order: 新的索引顺序列表，如 [2, 0, 1] 表示原来的第2个任务移到第0位
        """
        if (len(new_order) == len(self.tasks) and
            set(new_order) == set(range(len(self.tasks)))):

            # 重新排序任务
            self.tasks = [self.tasks[i] for i in new_order]
            self.save_tasks()
            return True
        return False

    def hot_reload_queue(self, quiet=False):
        """热重载队列：在不影响锁定任务的情况下重新加载队列

        Args:
            quiet (bool): 如果为True，不打印成功日志。用于操作后的静默刷新。
        """
        if not os.path.exists(self.queue_file):
            return False

        try:
            # 保存当前锁定状态
            locked_states = {}
            for task in self.tasks:
                if task.locked:
                    locked_states[task.task_id] = task.status

            # 重新加载任务
            with open(self.queue_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            task_ids_changed = self._replace_tasks_from_data(data)

            # 恢复锁定状态（通过持久化任务 ID 匹配）
            for new_task in self.tasks:
                if new_task.task_id in locked_states:
                    new_task.locked = True
                    new_task.status = locked_states[new_task.task_id]

            if contains_sensitive_data(data) or task_ids_changed:
                self.save_tasks()

            # 只有在非静默模式下才打印成功日志
            if not quiet:
                self.info("Queue hot reloaded successfully.")
            return True

        except Exception as e:
            self.error(f"Failed to hot reload queue: {e}")
            return False

    def get_queue_json(self):
        """Return the editable queue document without credential material."""
        return json.dumps(
            [task.to_persistent_dict() for task in self.tasks],
            indent=4,
            ensure_ascii=False,
        )

    def load_from_json(self, content):
        """Load a raw queue document while ensuring its persisted form is sanitized."""
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("Queue JSON must be an array")

        self._replace_tasks_from_data(data)
        self.save_tasks()
        return True

    def get_next_unlocked_task(self, start_index=0):
        """获取下一个未锁定的待执行任务"""
        for i in range(start_index, len(self.tasks)):
            task = self.tasks[i]
            if self._task_has_workflow(task):
                continue
            if not task.locked and task.status in ["waiting", "translated"]:
                return i, task
        return None, None

    def mark_task_executing(self, index):
        """标记任务为执行中并锁定 - 使用智能处理状态管理"""
        if 0 <= index < len(self.tasks):
            task = self.tasks[index]

            # 使用新的智能处理状态管理
            self.start_task_processing(index)

            # 设置合适的状态
            if task.status == "waiting":
                task.status = "translating"
            elif task.status == "translated":
                task.status = "polishing"

            self.current_task_index = index
            self.save_tasks()
            return True
        return False

    def mark_task_completed(self, index, final_status="completed", final_state: dict = None):
        """标记任务完成并解锁 - 使用智能处理状态管理"""
        if 0 <= index < len(self.tasks):
            task = self.tasks[index]

            # 使用新的智能处理状态管理
            self.stop_task_processing(index)

            task.status = final_status
            if final_status == "partial" and isinstance(final_state, dict):
                task.extra = getattr(task, "extra", {}) or {}
                task.extra["partial_step_type"] = final_state.get("step_type") or ""
                task.extra["partial_step_index"] = final_state.get("step_index") or 0
                task.extra["partial_message"] = final_state.get("message") or ""
            if final_status == "completed":
                self._collect_completed_automation_outputs(task)
            self.save_tasks()
            return True
        return False

    def _collect_completed_automation_outputs(self, task):
        try:
            from ModuleFolders.Infrastructure.Automation.OutputCollector import (
                AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY,
                collect_automation_outputs,
                normalize_output_collection_config,
                should_collect_automation_outputs,
            )

            if not should_collect_automation_outputs(task):
                return

            config = self.load_config()
            collection_config = normalize_output_collection_config(
                config.get(AUTOMATION_OUTPUT_COLLECTION_CONFIG_KEY, {})
            )
            if not collection_config["enabled"] or not collection_config["output_path"]:
                return

            copied = collect_automation_outputs(task, collection_config)
            file_name = os.path.basename(getattr(task, "input_path", "") or "-")
            if copied:
                self._log_queue_operation(
                    f"Automation output collection copied {len(copied)} file(s) for {file_name} -> {collection_config['output_path']}"
                )
            else:
                self._log_queue_operation(
                    f"Automation output collection found no product files for {file_name}"
                )
        except Exception as e:
            self._log_queue_operation(f"Automation output collection failed: {e}")

    def mark_automation_interrupted(self, run_id: str, final_status: str = "stopped") -> bool:
        if not run_id:
            return False
        updated = False
        for task in self.tasks:
            if getattr(task, "automation_run_id", None) == run_id:
                task.status = final_status
                task.locked = False
                task.is_processing = False
                task.process_start_time = None
                task.last_activity_time = None
                updated = True
        if updated:
            self.save_tasks()
        return updated

    def continue_partial_task(self, run_id: str = "", input_path: str = "") -> bool:
        target_input = os.path.abspath(input_path) if input_path else ""
        for task in self.tasks:
            matches_run = run_id and getattr(task, "automation_run_id", None) == run_id
            matches_path = target_input and os.path.abspath(getattr(task, "input_path", "") or "") == target_input
            if not (matches_run or matches_path):
                continue
            if getattr(task, "status", "") != "partial":
                continue
            task.status = "waiting"
            task.locked = False
            task.is_processing = False
            task.process_start_time = None
            task.last_activity_time = None
            task.workflow_steps = self._workflow_steps_for_partial_resume(task)
            task.automation_run_id = None
            task.automation_progress_file = None
            task.automation_worker_pid = None
            task.extra = getattr(task, "extra", {}) or {}
            task.extra.pop("partial_step_type", None)
            task.extra.pop("partial_step_index", None)
            task.extra.pop("partial_message", None)
            self.save_tasks()
            return True
        return False

    def resume_stopped_task(self, run_id: str = "", input_path: str = "") -> bool:
        target_input = os.path.abspath(input_path) if input_path else ""
        for task in self.tasks:
            matches_run = run_id and getattr(task, "automation_run_id", None) == run_id
            matches_path = target_input and os.path.abspath(getattr(task, "input_path", "") or "") == target_input
            if not (matches_run or matches_path):
                continue
            if getattr(task, "status", "") not in {"stopped", "interrupted"}:
                continue
            task.status = "waiting"
            task.locked = False
            task.is_processing = False
            task.process_start_time = None
            task.last_activity_time = None
            task.automation_run_id = None
            task.automation_progress_file = None
            task.automation_worker_pid = None
            task.extra = getattr(task, "extra", {}) or {}
            task.extra.pop("partial_step_type", None)
            task.extra.pop("partial_step_index", None)
            task.extra.pop("partial_message", None)
            self.save_tasks()
            return True
        return False

    def _workflow_steps_for_partial_resume(self, task):
        steps = [
            dict(step)
            for step in (getattr(task, "workflow_steps", None) or [])
            if isinstance(step, dict)
        ]
        step_types = [str(step.get("type") or "").strip().lower() for step in steps]
        try:
            partial_step = str((getattr(task, "extra", {}) or {}).get("partial_step_type") or "").strip().lower()
        except Exception:
            partial_step = ""
        if partial_step in step_types:
            index = step_types.index(partial_step)
            resume_steps = steps[index:]
        elif "translate" in step_types:
            index = step_types.index("translate")
            resume_steps = steps[index:]
        elif "all_in_one" in step_types:
            resume_steps = [
                {
                    **steps[step_types.index("all_in_one")],
                    "type": "all_in_one",
                }
            ]
        elif "polish" in step_types:
            index = step_types.index("polish")
            resume_steps = steps[index:]
        else:
            resume_steps = self._workflow_steps_for_background_task(task)

        for step in resume_steps:
            step.pop("series_incremental", None)
            step.pop("source_volume", None)
            step.pop("source_label", None)
            if step.get("type") in {"translate", "polish", "all_in_one"}:
                step["resume"] = True
        return resume_steps

    def find_task_by_file_path(self, file_path):
        """根据文件路径查找任务"""
        if not file_path:
            return None

        file_path = os.path.normpath(file_path)
        for i, task in enumerate(self.tasks):
            task_input_path = os.path.normpath(task.input_path)
            if task_input_path == file_path:
                return i, task
        return None, None

    def skip_task_to_end(self, file_path):
        """跳过任务并移动到队列末尾"""
        try:
            task_index, task = self.find_task_by_file_path(file_path)
            if task_index is None:
                return False, "Task not found in queue"

            if not task.locked:
                return False, "Task is not currently locked"

            # 解锁任务并重置状态
            task.locked = False
            task.status = "waiting"

            # 移动到队列末尾
            moved_task = self.tasks.pop(task_index)
            self.tasks.append(moved_task)

            # 保存队列
            self.save_tasks()

            file_name = os.path.basename(file_path)
            self.info(f"Task [{file_name}] skipped and moved to end of queue")
            return True, f"Task moved to position {len(self.tasks)}"

        except Exception as e:
            self.error(f"Failed to skip task: {e}")
            return False, str(e)

    def start_queue(self, cli_menu, automation_background=False):
        if self.is_running: return
        self.is_running = True
        self._automation_stop_requested = False
        threading.Thread(target=self._process_queue, args=(cli_menu, automation_background), daemon=True).start()

    def request_automation_stop(self):
        self._automation_stop_requested = True

    def _process_queue(self, cli_menu, automation_background=False):
        self.info("Starting task queue processing with full API overrides...")

        if automation_background:
            self._process_background_queue(cli_menu)
            return

        # Phase 0: Custom automation workflows
        self._process_workflow_tasks(cli_menu)

        # Phase 1: Translation
        while True:
            if Base.work_status == Base.STATUS.STOPING: break

            # 热重载队列
            self.hot_reload_queue(quiet=True)

            # 清理过期的锁定状态
            self.cleanup_stale_locks()

            # 优先处理运行期间新加入的自动化工作流任务
            self._process_workflow_tasks(cli_menu)

            # 查找下一个需要翻译的任务
            index, task = self.get_next_unlocked_task()
            if index is None:
                break  # 没有更多翻译任务

            if task.task_type not in [TaskType.TRANSLATION, TaskType.TRANSLATE_AND_POLISH]:
                # 标记为完成并继续
                self.mark_task_completed(index, "completed")
                continue

            # 标记任务为执行中
            self.mark_task_executing(index)

            if self._run_single_step(cli_menu, task, TaskType.TRANSLATION):
                # 完成后标记状态
                if task.task_type == TaskType.TRANSLATE_AND_POLISH:
                    self.mark_task_completed(index, "translated")
                else:
                    self.mark_task_completed(index, "completed")
            else:
                self.mark_task_completed(index, "error")

        # Phase 2: Polishing
        if Base.work_status != Base.STATUS.STOPING:
            while True:
                if Base.work_status == Base.STATUS.STOPING: break

                # 热重载队列
                self.hot_reload_queue()

                # 清理过期的锁定状态
                self.cleanup_stale_locks()

                # 优先处理运行期间新加入的自动化工作流任务
                self._process_workflow_tasks(cli_menu)

                # 查找下一个需要润色的任务
                found_task = False
                for i, task in enumerate(self.tasks):
                    if self._task_has_workflow(task):
                        continue
                    if (not task.locked and
                        task.status == "translated" and
                        task.task_type in [TaskType.POLISH, TaskType.TRANSLATE_AND_POLISH]):

                        found_task = True
                        self.mark_task_executing(i)

                        if self._run_single_step(cli_menu, task, TaskType.POLISH, resume=True):
                            self.mark_task_completed(i, "completed")
                        else:
                            self.mark_task_completed(i, "error")
                        break

                if not found_task:
                    break  # 没有更多润色任务

        self.is_running = False
        self.info("Task queue processing finished.")

    def _process_background_queue(self, cli_menu):
        try:
            idle_rounds = 0
            while True:
                if self._automation_stop_requested or Base.work_status == Base.STATUS.STOPING:
                    break

                self.hot_reload_queue(quiet=True)
                self.cleanup_stale_locks()
                self._ensure_background_workflow_tasks()
                self._process_workflow_tasks(cli_menu)

                self.hot_reload_queue(quiet=True)
                if self._has_background_pending_tasks():
                    idle_rounds = 0
                    continue

                idle_rounds += 1
                if idle_rounds >= 2:
                    break
                time.sleep(0.5)
        finally:
            self._automation_stop_requested = False
            self.is_running = False
            self.info("Background task queue processing finished.")

    def _ensure_background_workflow_tasks(self):
        changed = False
        for task in self.tasks:
            if task.locked or self._task_has_workflow(task):
                continue
            if task.status not in {"waiting", "translated"}:
                continue

            steps = self._workflow_steps_for_background_task(task)
            if not steps:
                continue
            task.workflow_steps = steps
            changed = True

        if changed:
            self.save_tasks()

    def _workflow_steps_for_background_task(self, task):
        if task.status == "translated":
            return [{"type": "polish", "resume": True}]

        if task.task_type == TaskType.TRANSLATE_AND_POLISH:
            return [{"type": "all_in_one"}]
        if task.task_type == TaskType.POLISH:
            return [{"type": "polish", "resume": True}]
        return [{"type": "translate"}]

    def _has_background_pending_tasks(self):
        for task in self.tasks:
            if task.locked:
                continue
            if task.status in {"waiting", "translated"}:
                return True
        return False

    def _task_has_workflow(self, task):
        return bool(getattr(task, "workflow_steps", None))

    def _get_next_workflow_task(self):
        for i, task in enumerate(self.tasks):
            if not task.locked and task.status in {"waiting", "translated"} and self._task_has_workflow(task):
                return i, task
        return None, None

    def _process_workflow_tasks(self, cli_menu):
        while True:
            if self._automation_stop_requested or Base.work_status == Base.STATUS.STOPING:
                break

            self.hot_reload_queue(quiet=True)
            self.cleanup_stale_locks()

            index, task = self._get_next_workflow_task()
            if index is None:
                break

            self.mark_task_executing(index)
            task.status = "workflow"
            self.save_tasks()

            workflow_status = self._run_workflow_task(cli_menu, task)
            if workflow_status in {"completed", "partial", "enqueued"}:
                self.mark_task_completed(index, workflow_status, getattr(task, "_automation_final_state", None))
            else:
                self.mark_task_completed(index, "stopped" if workflow_status == "interrupted" else "error")

    def _run_workflow_task(self, cli_menu, task):
        try:
            from ModuleFolders.Infrastructure.Automation.AutomationProcessRunner import AutomationProcessRunner

            task_config = task.to_runtime_dict()
            run_info = AutomationProcessRunner.start(
                task_config,
                project_root=getattr(cli_menu, "PROJECT_ROOT", None),
            )
            task.automation_run_id = run_info.get("run_id")
            task.automation_progress_file = run_info.get("progress_file")
            task.automation_worker_pid = run_info.get("pid")
            self.save_tasks()

            process = AutomationProcessRunner.get_process(task.automation_run_id)
            while process and process.poll() is None:
                if self._automation_stop_requested or Base.work_status == Base.STATUS.STOPING:
                    AutomationProcessRunner.terminate(task.automation_run_id, "Automation workflow interrupted by user")
                    return "interrupted"
                self.update_task_activity(self.current_task_index)
                time.sleep(0.5)
            from ModuleFolders.Infrastructure.Automation.AutomationProgress import read_progress_file

            state = read_progress_file(task.automation_progress_file)
            task._automation_final_state = state
            status = state.get("status")
            if status in {"completed", "partial", "interrupted", "enqueued"}:
                return status
            state_status = self._workflow_status_from_progress_state(state)
            if state_status:
                return state_status
            return "completed" if process and process.returncode == 0 else "error"
        except Exception as e:
            self.error(f"Workflow Task Error: {e}")
            task.status = "error"
            return "error"

    @staticmethod
    def _workflow_status_from_progress_state(state: dict):
        if not isinstance(state, dict):
            return None
        try:
            current = int(state.get("line") or state.get("completed") or 0)
            total = int(state.get("total_line") or state.get("total") or 0)
        except (TypeError, ValueError):
            return None
        if total <= 0:
            return None
        if current >= total:
            return "completed"
        if current > 0:
            return "partial"
        return None

    def _run_single_step(self, cli_menu, task, step_type, resume=False):
        if self._task_has_workflow(task):
            return True

        original_active_profile = cli_menu.active_profile_name
        original_rules_profile = cli_menu.active_rules_profile_name
        original_root_config = copy.deepcopy(getattr(cli_menu, "root_config", {}))
        original_config = copy.deepcopy(getattr(cli_menu, "config", {}))
        
        try:
            # 1. Apply Profile Base
            target_profile = task.profile or original_active_profile
            target_rules_profile = task.rules_profile or original_rules_profile
            cli_menu.load_config(
                active_profile_name=target_profile,
                active_rules_profile_name=target_rules_profile,
            )

            # 2. Apply Fine-grained Overrides
            cfg = cli_menu.config
            if task.source_lang: cfg["source_language"] = task.source_lang
            if task.target_lang: cfg["target_language"] = task.target_lang
            if task.project_type: cfg["translation_project"] = task.project_type
            if task.output_path: cfg["label_output_path"] = task.output_path
            
            # --- API Overrides ---
            if task.platform: cfg["target_platform"] = task.platform
            if task.api_url: cfg["base_url"] = task.api_url
            if task.api_key: 
                cfg["api_key"] = task.api_key
                # 同步到具体平台字典中
                tp = cfg.get("target_platform")
                if tp and tp in cfg.get("platforms", {}):
                    cfg["platforms"][tp]["api_key"] = task.api_key
                    
            if task.model: cfg["model"] = task.model
            
            # --- Performance Overrides ---
            if task.threads is not None: cfg["user_thread_counts"] = task.threads
            if task.retry is not None: cfg["retry_count"] = task.retry
            if task.timeout is not None: cfg["request_timeout"] = task.timeout
            if task.rounds is not None: cfg["round_limit"] = task.rounds
            if task.pre_lines is not None: cfg["pre_line_counts"] = task.pre_lines
            
            if task.lines_limit is not None:
                cfg["tokens_limit_switch"] = False
                cfg["lines_limit"] = task.lines_limit
            if task.tokens_limit is not None:
                cfg["tokens_limit_switch"] = True
                cfg["tokens_limit"] = max(400, int(task.tokens_limit))
                
            if task.think_depth is not None:
                cfg["think_depth"] = task.think_depth
                tp = cfg.get("target_platform")
                if tp and tp in cfg.get("platforms", {}):
                    cfg["platforms"][tp]["think_depth"] = task.think_depth
            if task.thinking_budget is not None:
                cfg["thinking_budget"] = task.thinking_budget
                tp = cfg.get("target_platform")
                if tp and tp in cfg.get("platforms", {}):
                    cfg["platforms"][tp]["thinking_budget"] = task.thinking_budget

            # 3. Execute
            # 更新活动时间（心跳）
            if self.current_task_index >= 0:
                self.update_task_activity(self.current_task_index)

            skip_prompt_validation = False
            if step_type == TaskType.TRANSLATION and task.task_type == TaskType.TRANSLATE_AND_POLISH:
                if not cli_menu.prompt_selection_guard.ensure_prompts_selected(
                    TaskType.TRANSLATE_AND_POLISH,
                    interactive=False,
                ):
                    raise RuntimeError("Required prompt selection is missing for all-in-one task.")
                skip_prompt_validation = True
            elif step_type == TaskType.POLISH and task.task_type == TaskType.TRANSLATE_AND_POLISH:
                skip_prompt_validation = True

            task_ok = cli_menu.run_task(
                step_type,
                target_path=task.input_path,
                continue_status=resume,
                non_interactive=True,
                from_queue=True,
                skip_prompt_validation=skip_prompt_validation,
                save_runtime_config=False,
            )
            if not task_ok:
                raise RuntimeError("Task blocked before start.")
            
            if Base.work_status != Base.STATUS.STOPING:
                if step_type == TaskType.TRANSLATION and task.task_type == TaskType.TRANSLATE_AND_POLISH:
                    task.status = "translated"
                else:
                    task.status = "completed"
            return True
        except Exception as e:
            self.error(f"Task Error: {e}")
            task.status = "error"
            return False
        finally:
            self.save_tasks()
            cli_menu.active_profile_name = original_active_profile
            cli_menu.active_rules_profile_name = original_rules_profile
            cli_menu.root_config = original_root_config
            cli_menu.config = original_config
